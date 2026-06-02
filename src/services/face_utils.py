import numpy as np
import json
import logging
import os
import threading
import gc
import cv2
from functools import lru_cache
from typing import List, Tuple, Optional
import queue

from src.services.model_registry import runtime_model_registry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Face recognition configuration - Real-world production settings
FACE_RECOGNITION_THRESHOLD = float(os.environ.get('FACE_RECOGNITION_THRESHOLD', '0.5'))  # Stricter threshold
FACE_DETECTION_SIZE = int(os.environ.get('FACE_DETECTION_SIZE', '640'))
FACE_CACHE_SIZE = int(os.environ.get('FACE_CACHE_SIZE', '1000'))  # Cache size for face embeddings
FRAME_SKIP = int(os.environ.get('FRAME_SKIP', '2'))  # Process 1 frame every N frames (reduced for smoother detection)
USE_GPU = os.environ.get('USE_GPU', 'True').lower() in ('true', '1', 't')  # Use GPU if available
DETECTION_FREQUENCY = int(os.environ.get('DETECTION_FREQUENCY', '10'))  # Detect faces every N frames
VERIFICATION_THRESHOLD = float(os.environ.get('VERIFICATION_THRESHOLD', '0.6'))  # Stricter verification threshold



# Initialize InsightFace face detector
app = None

def initialize_insightface():
    """Initialize InsightFace face detector and recognition model."""
    global app
    if app is not None:
        logger.info("InsightFace already initialized")
        return app

    try:
        import insightface
        from insightface.app import FaceAnalysis
        import platform
        import onnxruntime as ort

        # Check if USE_GPU is enabled
        USE_GPU = os.environ.get('USE_GPU', 'True').lower() in ('true', '1', 't')

        # Get available providers
        available_providers = ort.get_available_providers()
        logger.info(f"Available ONNX Runtime providers: {available_providers}")

        # Determine the appropriate provider based on platform and USE_GPU setting
        if USE_GPU:
            if platform.system() == 'Darwin' and platform.processor() == 'arm':
                # Mac M1/M2 (Apple Silicon)
                if 'CoreMLExecutionProvider' in available_providers:
                    logger.info("Using CoreML provider for GPU acceleration on Mac M1/M2")
                    providers = ['CoreMLExecutionProvider', 'CPUExecutionProvider']
                else:
                    logger.info("CoreML provider not available, falling back to CPU")
                    providers = ['CPUExecutionProvider']
            else:
                # Other platforms with GPU
                if 'CUDAExecutionProvider' in available_providers:
                    logger.info("Using CUDA provider for GPU acceleration")
                    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                else:
                    logger.info("CUDA provider not available, falling back to CPU")
                    providers = ['CPUExecutionProvider']
        else:
            # CPU only
            logger.info("Using CPU only as specified by USE_GPU setting")
            providers = ['CPUExecutionProvider']

        model_root = runtime_model_registry.model_root()
        model_pack = runtime_model_registry.current_profile().model_pack

        # Initialize FaceAnalysis with the appropriate providers
        app = FaceAnalysis(name=model_pack, root=str(model_root), providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))

        # Log which provider is being used
        logger.info(f"Initialized InsightFace face detector and recognition with providers: {providers}")

        # Test the initialization with a simple image
        test_img = np.zeros((100, 100, 3), dtype=np.uint8)
        try:
            test_result = app.get(test_img)
            logger.info(f"InsightFace test: {test_result}")
        except Exception as test_e:
            logger.warning(f"InsightFace test failed (non-critical): {test_e}")

        return app
    except Exception as e:
        logger.error(f"Failed to initialize InsightFace face detector: {e}")
        return None

# Initialize InsightFace lazily (only when needed)
app = None

def ensure_insightface_initialized():
    """Ensure InsightFace is initialized, initialize if needed."""
    global app
    if app is None:
        logger.info("Initializing InsightFace on first use...")
        app = initialize_insightface()
    return app

# Global variables
processing_queue = queue.Queue(maxsize=10)
processing_thread = None
processing_active = False
frame_counter = 0

@lru_cache(maxsize=FACE_CACHE_SIZE)
def _get_cached_embedding(embedding_key):
    """Cache for face embeddings to avoid repeated processing.

    Args:
        embedding_key: Can be either a JSON string or a tuple of (student_id, timestamp)
                      for retrieving from database

    Returns:
        numpy.ndarray: The face embedding as a numpy array
    """
    try:
        if isinstance(embedding_key, str):
            # Legacy JSON format
            return np.array(json.loads(embedding_key))
        elif isinstance(embedding_key, tuple):
            # Database lookup format (student_id, timestamp)
            from src.models.db import db, Student
            student_id = embedding_key[0]
            student = db.session.query(Student).get(student_id)
            if student and student.face_embedding_array is not None:
                return np.array(student.face_embedding_array)
        return None
    except Exception as e:
        logger.error(f"Error retrieving cached embedding: {e}")
        return None

def preprocess_image(image: np.ndarray) -> np.ndarray:
    """Preprocess image for better face detection.

    Args:
        image: Input image as numpy array

    Returns:
        Preprocessed image
    """
    # Check if image needs resizing
    if image.shape[0] > FACE_DETECTION_SIZE or image.shape[1] > FACE_DETECTION_SIZE:
        scale = FACE_DETECTION_SIZE / max(image.shape[0], image.shape[1])
        new_size = (int(image.shape[1] * scale), int(image.shape[0] * scale))
        image = cv2.resize(image, new_size)

    # Apply histogram equalization to improve contrast
    if len(image.shape) == 3 and image.shape[2] == 3:
        # Convert to YUV and equalize the Y channel
        yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)
        yuv[:,:,0] = cv2.equalizeHist(yuv[:,:,0])
        image = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)

    return image

def detect_faces(image: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """Detect faces in an image using InsightFace.

    Args:
        image: Input image as numpy array

    Returns:
        List of face bounding boxes (x, y, w, h)
    """
    if image is None:
        logger.error("Received None image for face detection")
        return []

    try:
        # Use InsightFace for detection
        app = ensure_insightface_initialized()
        if app is not None:
            try:
                # Use InsightFace for detection
                faces = app.get(image)
                if faces and len(faces) > 0:
                    # Convert InsightFace format to (x, y, w, h)
                    result = []
                    for face in faces:
                        bbox = face.bbox.astype(int)
                        x1, y1, x2, y2 = bbox

                        # Add a small margin around the face for better visibility
                        margin_x = int((x2 - x1) * 0.1)  # 10% margin
                        margin_y = int((y2 - y1) * 0.1)  # 10% margin

                        # Ensure we don't go out of bounds
                        x1 = max(0, x1 - margin_x)
                        y1 = max(0, y1 - margin_y)
                        x2 = min(image.shape[1], x2 + margin_x)
                        y2 = min(image.shape[0], y2 + margin_y)

                        w = x2 - x1
                        h = y2 - y1
                        result.append((x1, y1, w, h))

                    logger.info(f"InsightFace detected {len(result)} faces")
                    print(f"InsightFace detected {len(result)} faces")
                    return result
            except Exception as e:
                logger.error(f"Error using InsightFace: {e}")
                print(f"Error using InsightFace: {e}")
                return []
        else:
            logger.warning("InsightFace not initialized")
            return []

        logger.warning("No faces detected")
        return []
    except Exception as e:
        logger.error(f"Error in detect_faces: {e}")
        print(f"Error in detect_faces: {e}")
        return []

def extract_face_embedding(image: np.ndarray) -> Optional[List[float]]:
    """Extract embedding from a face image using InsightFace.

    Args:
        image: Input image as numpy array

    Returns:
        List of floats representing the face embedding, or None if no face detected
    """
    try:
        if image is None:
            logger.warning("Received None image for face embedding extraction")
            return None

        # Debug: Log image properties
        logger.info(f"extract_face_embedding: image shape={image.shape}, dtype={image.dtype}, sample_pixel={image[0,0] if image.size > 0 else 'empty'}")

        # Preprocess image for better detection
        processed_image = preprocess_image(image)

        # Debug: Log processed image properties
        logger.info(f"extract_face_embedding: processed_image shape={processed_image.shape}, dtype={processed_image.dtype}, sample_pixel={processed_image[0,0] if processed_image.size > 0 else 'empty'}")

        # Make sure InsightFace is initialized
        app = ensure_insightface_initialized()
        if app is None:
            logger.error("Failed to initialize InsightFace in extract_face_embedding")
            return None

        # Use InsightFace for embedding extraction
        try:
            # Use InsightFace for detection and embedding
            faces = app.get(processed_image)

            # Log detection results
            if faces:
                logger.info(f"InsightFace detected {len(faces)} faces")
                for i, face in enumerate(faces):
                    bbox = face.bbox
                    logger.info(f"Face {i+1}: bbox={bbox}, has_embedding={hasattr(face, 'embedding')}")
            else:
                logger.warning("No faces detected by InsightFace")
                return None

            if faces and len(faces) > 0:
                # Get the largest face
                largest_face = max(faces, key=lambda face: (face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))

                # Get embedding from InsightFace
                if hasattr(largest_face, 'embedding') and largest_face.embedding is not None:
                    # Normalize the embedding
                    embedding = largest_face.embedding / np.linalg.norm(largest_face.embedding)
                    logger.info("Successfully extracted face embedding using InsightFace")
                    return embedding.tolist()
                else:
                    logger.warning("InsightFace detected face but no embedding available")
                    return None
            else:
                logger.warning("No faces detected by InsightFace")
                return None
        except Exception as e:
            logger.error(f"Error using InsightFace for embedding: {e}")
            logger.error("Failed to extract embedding with InsightFace")
            return None
    except Exception as e:
        logger.error(f"Error extracting face embedding: {e}")
        return None

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity value between 0 and 1
    """
    try:
        if a is None or b is None:
            logger.warning("Received None vector in cosine_similarity")
            return 0.0

        # Convert to numpy arrays if they aren't already
        if not isinstance(a, np.ndarray):
            a = np.array(a)
        if not isinstance(b, np.ndarray):
            b = np.array(b)

        # Log vector information
        logger.info(f"Vector shapes: a={a.shape}, b={b.shape}")

        # Ensure vectors have the same shape
        if a.shape != b.shape:
            logger.warning(f"Shape mismatch in cosine_similarity: {a.shape} vs {b.shape}")
            # Try to reshape if possible
            if len(a.shape) == 1 and len(b.shape) == 1:
                # If they're both 1D arrays, we can try to reshape
                if a.shape[0] > b.shape[0]:
                    a = a[:b.shape[0]]
                    logger.info(f"Reshaped a to {a.shape}")
                else:
                    b = b[:a.shape[0]]
                    logger.info(f"Reshaped b to {b.shape}")
            else:
                logger.error("Cannot reshape vectors with different dimensions")
                return 0.0

        # Calculate dot product
        dot_product = np.dot(a, b)

        # Calculate magnitudes
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        # Avoid division by zero
        if norm_a == 0 or norm_b == 0:
            logger.warning("Zero norm detected in cosine_similarity")
            return 0.0

        # Calculate cosine similarity
        similarity = dot_product / (norm_a * norm_b)

        # Ensure result is in valid range (0 to 1)
        result = max(0.0, min(1.0, similarity))
        logger.info(f"Cosine similarity: {result:.4f}")
        return result
    except Exception as e:
        logger.error(f"Error in cosine_similarity: {e}")
        return 0.0

def start_background_processing():
    """Start background thread for face processing"""
    global processing_thread, processing_active

    if processing_active:
        return

    def process_queue():
        while processing_active:
            try:
                # Get item from queue with timeout
                item = processing_queue.get(timeout=1.0)
                if item is None:
                    continue

                # Process the item (frame, callback, args)
                frame, callback, args = item
                result = process_frame_for_recognition(frame)
                if result and callback:
                    callback(result, *args)

                # Mark task as done
                processing_queue.task_done()
            except queue.Empty:
                # Queue is empty, just continue
                pass
            except Exception as e:
                logger.error(f"Error in background processing: {e}")

    processing_active = True
    processing_thread = threading.Thread(target=process_queue, daemon=True)
    processing_thread.start()
    logger.info("Started background processing thread")

def stop_background_processing():
    """Stop background processing thread"""
    global processing_active
    processing_active = False
    if processing_thread:
        processing_thread.join(timeout=2.0)
        logger.info("Stopped background processing thread")

def process_frame_for_recognition(frame: np.ndarray) -> Optional[Tuple[int, float]]:
    """Process a frame for face recognition.

    Args:
        frame: Input frame as numpy array

    Returns:
        Tuple of (student_id, confidence) or None if no face recognized
    """
    try:
        if frame is None:
            return None

        # Preprocess image
        processed_frame = preprocess_image(frame)

        # Detect faces
        faces = detect_faces(processed_frame)
        if len(faces) == 0:
            return None

        # Get the largest face
        if len(faces) > 1:
            largest_face = max(faces, key=lambda face: face[2] * face[3])
            face_rect = largest_face
        else:
            face_rect = faces[0]

        # Extract the face region
        x, y, w, h = face_rect
        face_region = processed_frame[y:y+h, x:x+w]

        # Resize to a standard size
        face_region = cv2.resize(face_region, (100, 100))

        # Convert to grayscale
        gray_face = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)

        # Flatten the image to create a simple embedding
        embedding = gray_face.flatten().astype(float)

        # Normalize the embedding
        embedding = embedding / np.linalg.norm(embedding)

        # Get all students from database
        from src.models.db import db, Student
        students = db.session.query(Student).filter(Student.face_embedding_array.isnot(None)).all()

        # Find best match
        best_sim = -1
        best_student = None
        second_best_sim = -1

        for student in students:
            try:
                # Get student embedding
                student_embedding = student.get_embedding()
                if student_embedding is None:
                    continue

                # Calculate similarity
                sim = cosine_similarity(embedding, student_embedding)

                # Track best and second-best matches
                if sim > best_sim:
                    second_best_sim = best_sim
                    best_sim = sim
                    best_student = student
                elif sim > second_best_sim:
                    second_best_sim = sim
            except Exception as e:
                logger.error(f"Error comparing with student {student.student_code}: {e}")

        # Check if similarity is above threshold
        if best_student and best_sim > FACE_RECOGNITION_THRESHOLD:
            # Additional verification: check if there's a clear winner
            # If the difference between best and second-best is small, it might be uncertain
            if (best_sim - second_best_sim) < VERIFICATION_THRESHOLD:
                logger.warning(f"Uncertain recognition: best={best_sim:.4f}, second={second_best_sim:.4f}, diff={best_sim-second_best_sim:.4f}")
                # For uncertain matches, require a higher threshold
                if best_sim > (FACE_RECOGNITION_THRESHOLD + 0.1):  # Higher threshold for uncertain matches
                    logger.info(f"Recognized student despite uncertainty: {best_student.student_code} (similarity: {best_sim:.4f})")
                    return (best_student.id, best_sim)
                else:
                    return None
            else:
                logger.info(f"Recognized student: {best_student.student_code} (similarity: {best_sim:.4f})")
                return (best_student.id, best_sim)
        else:
            return None
    except Exception as e:
        logger.error(f"Error in face recognition: {e}")
        return None
    finally:
        # Clean up
        if 'faces' in locals():
            del faces
        if 'embedding' in locals():
            del embedding
        gc.collect()

def recognize_face(frame, students):
    """Recognize a face in the frame by comparing with student embeddings.

    Args:
        frame: Input frame as numpy array
        students: List of Student objects to compare against

    Returns:
        Tuple of (student_id, confidence) or None if no face recognized
    """
    try:
        if frame is None:
            return None

        # Preprocess image
        processed_frame = preprocess_image(frame)

        # Make sure InsightFace is initialized
        app = ensure_insightface_initialized()
        if app is None:
            logger.error("Failed to initialize InsightFace in recognize_face")
            return None

        # Use InsightFace for face recognition
        try:
            # Detect faces using InsightFace
            faces = app.get(processed_frame)

            if not faces or len(faces) == 0:
                logger.warning("No faces detected in frame")
                return None

            # Get the largest face
            largest_face = max(faces, key=lambda face: (face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))
            
            # Convert bbox to [x, y, w, h] format for liveness check
            bbox = largest_face.bbox.astype(int)
            x1, y1, x2, y2 = bbox
            face_bbox = [x1, y1, x2-x1, y2-y1]
            
            # Perform liveness check BEFORE face recognition
            from src.services.anti_spoofing import check_liveness_balanced
            liveness_result = check_liveness_balanced(processed_frame, face_bbox, frame_history=None)
            
            # If liveness check fails, return early
            if not liveness_result['is_live']:
                logger.warning(f"Liveness check failed during recognition: {liveness_result['reason']}")
                return None

            # Get embedding directly from InsightFace only if liveness check passes
            if not hasattr(largest_face, 'embedding') or largest_face.embedding is None:
                logger.warning("Face detected but no embedding available")
                return None

            # Normalize the embedding
            face_embedding = largest_face.embedding / np.linalg.norm(largest_face.embedding)

            # Find best match among students
            best_sim = -1
            best_student = None
            second_best_sim = -1

            for student in students:
                try:
                    # Get student embedding
                    student_embedding = student.get_embedding()
                    if student_embedding is None:
                        logger.warning(f"Student {student.student_code} has no embedding")
                        continue

                    # Calculate similarity
                    sim = cosine_similarity(face_embedding, student_embedding)
                    logger.info(f"Similarity with student {student.student_code}: {sim:.4f}")

                    # Track best and second-best matches
                    if sim > best_sim:
                        second_best_sim = best_sim
                        best_sim = sim
                        best_student = student
                    elif sim > second_best_sim:
                        second_best_sim = sim
                except Exception as e:
                    logger.error(f"Error comparing with student {student.student_code}: {e}")

            # Use a lower threshold for better recognition with InsightFace
            threshold = 0.3

            # Check if we have a good match
            if best_student and best_sim > threshold:
                # If second best is close, we might have an ambiguous match
                if second_best_sim > 0 and (best_sim - second_best_sim) < 0.1:
                    logger.warning("Ambiguous match - second best match too close")
                    return None

                logger.info(f"Recognized student {best_student.student_code} with confidence {best_sim:.4f}")
                return best_student.id, best_sim
            else:
                logger.info("No good match found")
                return None

        except Exception as e:
            logger.error(f"Error in face recognition: {e}")
            return None

    except Exception as e:
        logger.error(f"Error in recognize_face: {e}")
        return None

def register_student_with_embedding(session, student, images):
    """Register a new student and store the average face embedding.

    Args:
        session: Database session
        student: Student object to register
        images: List of face images

    Returns:
        bool: True if registration was successful, False otherwise
    """
    try:
        if not images:
            raise ValueError("No images provided for registration")

        logger.info(f"Registering student {student.student_code} with {len(images)} images")

        # Extract embeddings from provided images
        embeddings = []
        for i, img in enumerate(images):
            # Preprocess image for better detection
            processed_img = preprocess_image(img)

            # Extract embedding
            emb = extract_face_embedding(processed_img)
            if emb:
                embeddings.append(emb)
                logger.debug(f"Extracted embedding from image {i+1}")
            else:
                logger.warning(f"Could not extract embedding from image {i+1}")

        if not embeddings:
            raise ValueError("No valid face embeddings found for registration")

        # Calculate average embedding
        avg_embedding = np.mean(np.array(embeddings), axis=0)

        # Store embedding in the new ARRAY(Float) column
        student.set_embedding(avg_embedding)

        # Also store face images with their embeddings
        from src.models.db import FaceImage
        for i, (img, emb) in enumerate(zip(images, embeddings)):
            # Convert image to bytes for storage
            _, img_bytes = cv2.imencode('.jpg', img)

            # Create face image record
            face_image = FaceImage(
                student_id=student.id,
                image_data=img_bytes.tobytes()
            )
            face_image.set_embedding(np.array(emb))
            session.add(face_image)

        # Commit changes
        session.commit()

        # Clear embedding cache
        _get_cached_embedding.cache_clear()

        logger.info(f"Successfully registered student {student.student_code} with {len(embeddings)} valid embeddings")
        return True
    except Exception as e:
        logger.error(f"Error in register_student_with_embedding: {e}")
        session.rollback()
        return False
    finally:
        # Clean up
        if 'embeddings' in locals():
            del embeddings
        if 'avg_embedding' in locals():
            del avg_embedding
        gc.collect()
























