import asyncio
import os

# Avoid loading OpenCV's FFmpeg backend to prevent libavdevice conflicts with PyAV.
os.environ.setdefault("OPENCV_VIDEOIO_PRIORITY_FFMPEG", "0")

import cv2
import numpy as np
import json
import time
import logging
import traceback
import gc
import sys
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from aiortc.contrib.media import MediaBlackhole, MediaRelay
import requests
from dotenv import load_dotenv
from loguru import logger
import threading
import queue
import uuid
import psutil
from typing import Dict, Set, Optional, Tuple, List, Any
from pathlib import Path

# Ensure project root is on sys.path for imports like `src.*`
current_file = Path(__file__).resolve()
project_root = current_file.parents[2]
sys.path.insert(0, str(project_root))

# Import face recognition utilities
from src.services.face_utils import (
    preprocess_image,
    cosine_similarity,
    start_background_processing,
    stop_background_processing,
    ensure_insightface_initialized,
    FACE_RECOGNITION_THRESHOLD
)
from src.services.inference_service import get_inference_service

# Get the absolute path to the .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Configure logging with correct path
logging.basicConfig(level=logging.INFO)

def setup_webrtc_logging():
    """Setup WebRTC logging to the correct logs directory"""
    # Get the project root directory (mini_project1)
    current_file = os.path.abspath(__file__)
    # Go up: services -> src -> face-recognition-attendance-system -> mini_project1
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
    logs_dir = os.path.join(project_root, 'logs')

    # Create logs directory if it doesn't exist
    os.makedirs(logs_dir, exist_ok=True)

    # Set up loguru logging
    log_file = os.path.join(logs_dir, "webrtc_{time}.log")
    logger.add(
        log_file,
        rotation="10 MB",
        retention="1 week",
        level="INFO",
        backtrace=True,
        diagnose=True
    )

    print(f"🔧 WebRTC logging initialized: {logs_dir}")

# Initialize WebRTC logging
setup_webrtc_logging()

# Configuration
APP_API_URL = "http://127.0.0.1:5000"
logger.info(f"APP_API_URL at startup: {APP_API_URL}")
FRAME_SKIP = int(os.getenv('FRAME_SKIP', '3'))  # Reduced to process more frames for smoother video
USE_GPU = os.getenv('USE_GPU', 'True').lower() in ('true', '1', 't')
VERIFICATION_THRESHOLD = float(os.getenv('VERIFICATION_THRESHOLD', '0.1'))
ATTENDANCE_TIME_WINDOW = int(os.getenv('ATTENDANCE_TIME_WINDOW', '10'))
MAX_QUEUE_SIZE = int(os.getenv('MAX_QUEUE_SIZE', '10'))  # Increased queue size for faster registration
RESOURCE_LOG_INTERVAL = int(os.getenv('RESOURCE_LOG_INTERVAL', '300'))  # Log resource usage every 5 minutes
DETECTION_SCALE = float(os.getenv('DETECTION_SCALE', '0.3'))  # Reduced scale factor for faster detection
CONNECTION_TIMEOUT = int(os.getenv('CONNECTION_TIMEOUT', '5'))  # Reduced connection timeout for faster response
MAX_FPS = int(os.getenv('MAX_FPS', '30'))  # Increased maximum FPS for smoother video

# Global variables
pcs = set()
relay = MediaRelay()
frame_counter = 0
last_attendance_time = {}
active_connections = {}
processing_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
processing_thread = None
processing_active = False
last_resource_log = time.time()
student_cache = {}  # Cache for student embeddings
student_embeddings = {}  # Store student embeddings for immediate use
registration_complete = {}  # Track registration completion status
attendance_marker = None
face_registration_handler = None
students_provider = None


def configure_runtime_hooks(*, attendance_callback=None, register_face_callback=None, get_students_callback=None):
    global attendance_marker, face_registration_handler, students_provider
    attendance_marker = attendance_callback
    face_registration_handler = register_face_callback
    students_provider = get_students_callback

# Helper function to send registration complete message
async def send_registration_complete(data_channel, student_id):
    """Send registration complete message via data channel."""
    try:
        if data_channel and data_channel.readyState == 'open':
            message = json.dumps({
                'type': 'registration_complete',
                'success': True,
                'message': 'Face registration complete',
                'student_id': student_id,
                'capture_count': 5  # Add capture count to indicate completion
            })

            # Send only once to avoid multiple success messages
            await data_channel.send(message)

            # Small delay to ensure message is processed
            await asyncio.sleep(0.1)

            logger.info(f"Sent registration complete message for student {student_id}")
            return True
        else:
            logger.warning(f"Data channel not open for student {student_id}")
            return False
    except Exception as e:
        logger.error(f"Error sending registration complete message: {str(e)[:100]}")
        return False

# Start background processing with optimized performance
def start_processing():
    global processing_thread, processing_active

    if processing_active:
        return

    def process_queue():
        """Background thread for processing frames from the queue"""
        global processing_active
        while processing_active:
            try:
                # Get frame from queue
                frame_data = processing_queue.get(timeout=1.0)
                if frame_data is None:
                    continue

                pc, frame, is_registration, student_id = frame_data

                if is_registration:
                    # Handle registration mode
                    try:
                        # Process the face for registration
                        if not hasattr(pc, 'captured_faces'):
                            pc.captured_faces = []
                            pc.registration_complete = False

                        # Extract face embedding
                        h, w = frame.shape[:2]
                        small_frame = cv2.resize(frame, (int(w * DETECTION_SCALE), int(h * DETECTION_SCALE)))
                        processed_frame = preprocess_image(small_frame)
                        insight_app = ensure_insightface_initialized()
                        faces = insight_app.get(processed_frame) if insight_app is not None else []

                        if faces and len(faces) == 1:
                            # Get the face embedding
                            face = faces[0]
                            embedding = face.embedding

                            if embedding is not None:
                                # Add to captured faces
                                pc.captured_faces.append(embedding)
                                logger.info(f"Captured face {len(pc.captured_faces)}/5")

                                # If we have enough faces, register the student
                                if len(pc.captured_faces) >= 5 and not pc.registration_complete:
                                    try:
                                        # Average the embeddings
                                        avg_embedding = np.mean(pc.captured_faces, axis=0)

                                        # Save to database via API
                                        headers = {
                                            'Content-Type': 'application/json'
                                        }

                                        if face_registration_handler:
                                            result = face_registration_handler(student_id, avg_embedding.tolist())
                                            success = bool(result.get("success"))
                                        else:
                                            logger.info(f"Attempting to POST embedding to {APP_API_URL}/api/register_face for student {student_id}")
                                            response = requests.post(
                                                f"{APP_API_URL}/api/register_face",
                                                json={
                                                    "student_id": student_id,
                                                    "embedding": avg_embedding.tolist()
                                                },
                                                headers=headers,
                                                timeout=5.0
                                            )
                                            success = response.status_code == 200

                                        if success:
                                            logger.info(f"Successfully registered face for student {student_id}")
                                            pc.registration_complete = True
                                        else:
                                            logger.error(f"Failed to register face for student {student_id}")
                                    except Exception as e:
                                        logger.error(f"Error processing registration frame: {e}")

                    except Exception as e:
                        logger.error(f"Error processing registration frame: {e}")

                else:
                    # Process frame for attendance recognition with liveness detection
                    result = process_frame_for_attendance_with_liveness(frame, getattr(pc, 'connection_id', 'background'))
                    if result:
                        student_id, confidence, liveness_result, decision = result

                        # Only mark attendance if liveness check passed
                        if liveness_result['is_live']:
                            # Mark attendance via integrated app callback or API fallback
                            try:
                                if attendance_marker:
                                    resp_data = attendance_marker(
                                        student_id,
                                        float(confidence),
                                        str(student_id),
                                        liveness_score=float(liveness_result['confidence']),
                                        detector_version=decision.get('detector_version'),
                                        embedding_model_version=decision.get('recognizer_version'),
                                        liveness_model_version=decision.get('liveness_version'),
                                        decision_reason=decision.get('reason'),
                                    )
                                    success = bool(resp_data.get("success"))
                                else:
                                    response = requests.post(
                                        f"{APP_API_URL}/api/mark_attendance",
                                        json={
                                            "student_id": student_id,
                                            "confidence": float(confidence),
                                            "device_id": student_id,
                                            "liveness_confidence": float(liveness_result['confidence']),
                                            "liveness_details": liveness_result.get('details', {}),
                                        },
                                        timeout=3.0
                                    )
                                    success = response.status_code == 200
                                    resp_data = response.json() if success else {}
                                if success:
                                    student_name = resp_data.get('name', f"Student {student_id}")

                                    # Store last attendance time to prevent duplicates
                                    last_attendance_time[student_id] = time.time()

                                    # Log attendance with liveness info
                                    logger.info(f"Marked attendance for student ID {student_id} with confidence {confidence:.4f}, liveness: {liveness_result['confidence']:.2f}")
                                else:
                                    # Reduced logging for failures
                                    if frame_counter % 10 == 0:  # Log only every 10th failure
                                        logger.warning("Failed to mark attendance")
                            except Exception as e:
                                # Reduced error logging
                                if frame_counter % 10 == 0:  # Log only every 10th error
                                    logger.error(f"Error marking attendance via API: {str(e)[:100]}")  # Truncate long error messages
                        else:
                            # Log liveness detection failure
                            logger.warning(f"Attendance blocked - Liveness check failed: {liveness_result['reason']}")

                            # Send warning to client via data channel if available
                            if connection_id in active_connections:
                                pc = active_connections[connection_id]
                                if hasattr(pc, 'data_channel') and pc.data_channel:
                                    try:
                                        warning_msg = {
                                            'type': 'liveness_warning',
                                            'message': 'Live person required for attendance',
                                            'reason': liveness_result['reason']
                                        }
                                        pc.data_channel.send(json.dumps(warning_msg))
                                    except Exception as e:
                                        logger.error(f"Error sending liveness warning: {e}")

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in process_queue: {e}")
                continue

    # Start the processing thread
    processing_active = True
    processing_thread = threading.Thread(target=process_queue, daemon=True)
    processing_thread.start()
    logger.info("Started background processing thread")

def stop_processing():
    """Stop the background processing thread"""
    global processing_active

    if not processing_active:
        return

    logger.info("Stopping background processing thread...")
    processing_active = False

    if processing_thread:
        # Wait for thread to finish with timeout
        processing_thread.join(timeout=2.0)
        if processing_thread.is_alive():
            logger.warning("Background processing thread did not terminate gracefully")
        else:
            logger.info("Background processing thread stopped successfully")

    # Clear the queue
    try:
        while not processing_queue.empty():
            processing_queue.get_nowait()
            processing_queue.task_done()
    except Exception:
        pass

def process_frame_for_recognition(frame: np.ndarray, connection_id: str = None) -> Optional[Tuple[int, float]]:
    """Process a frame for face recognition with optimized performance.

    Args:
        frame: Input frame as numpy array
        connection_id: ID of the connection for registration mode

    Returns:
        Tuple of (student_id, confidence) or None if no face recognized
    """
    global student_cache, last_resource_log

    try:
        # Log resource usage less frequently
        current_time = time.time()
        if current_time - last_resource_log > RESOURCE_LOG_INTERVAL:
            cpu_percent = psutil.cpu_percent()
            memory_info = psutil.virtual_memory()
            logger.info(f"Resource usage - CPU: {cpu_percent}%, Memory: {memory_info.percent}%")
            last_resource_log = current_time

        if frame is None:
            return None

        # Resize frame for faster processing
        h, w = frame.shape[:2]
        small_frame = cv2.resize(frame, (int(w * DETECTION_SCALE), int(h * DETECTION_SCALE)))

        # Preprocess image for better detection
        processed_frame = preprocess_image(small_frame)

        # Detect faces
        faces = insight_app.get(processed_frame)
        if not faces:
            return None

        # Get the largest face if multiple faces detected
        if len(faces) > 1:
            largest_face = max(faces, key=lambda face:
                              (face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))
            face = largest_face
        else:
            face = faces[0]
            
        # Convert bbox to [x, y, w, h] format for liveness check
        bbox = face.bbox.astype(int)
        x1, y1, x2, y2 = bbox
        face_bbox = [x1, y1, x2-x1, y2-y1]
        
        # Get frame history for motion analysis
        frame_history = None
        if connection_id in active_connections:
            pc = active_connections[connection_id]
            if hasattr(pc, 'frame_history'):
                frame_history = pc.frame_history
            else:
                # Initialize frame history
                pc.frame_history = []
                frame_history = pc.frame_history

            # Add current frame to history (keep last 10 frames)
            frame_history.append(processed_frame.copy())
            if len(frame_history) > 10:
                frame_history.pop(0)
        
        # Perform liveness check BEFORE face recognition
        from src.services.anti_spoofing import check_liveness_balanced
        liveness_result = check_liveness_balanced(processed_frame, face_bbox, frame_history)
        
        # If liveness check fails, return early
        if not liveness_result['is_live']:
            logger.warning(f"Liveness check failed during recognition: {liveness_result['reason']}")
            return None

        # Get embedding only if liveness check passes
        embedding = face.embedding

        # Check if we need to refresh the student cache (less frequently)
        if not student_cache or current_time - student_cache.get('last_updated', 0) > 300:  # Refresh every 5 minutes
            try:
                if students_provider:
                    students = students_provider()
                    student_cache = {
                        'students': students,
                        'last_updated': current_time
                    }
                else:
                    response = requests.get(f"{APP_API_URL}/api/students", timeout=3.0)
                    if response.status_code != 200:
                        if not student_cache:
                            return None
                    else:
                        students = response.json()
                        student_cache = {
                            'students': students,
                            'last_updated': current_time
                        }
                        logger.info(f"Updated student cache with {len(students)} students")
            except Exception as e:
                # Use existing cache if available
                if not student_cache:
                    return None

        # Get students from cache
        students = student_cache.get('students', [])
        if not students:
            return None

        # Find best match (optimized)
        best_sim = -1
        best_student_id = None
        second_best_sim = -1
        best_student_name = None

        # Process students in batches for better performance
        for student in students:
            try:
                # Get student embedding
                student_embedding = np.array(student.get('face_embedding'))
                if student_embedding is None or len(student_embedding) == 0:
                    continue

                # Calculate similarity
                sim = cosine_similarity(embedding, student_embedding)

                # Track best and second-best matches
                if sim > best_sim:
                    second_best_sim = best_sim
                    best_sim = sim
                    best_student_id = student.get('id')
                    best_student_name = student.get('name')
                elif sim > second_best_sim:
                    second_best_sim = sim
            except Exception:
                continue

        # Check if similarity is above threshold
        if best_student_id and best_sim > FACE_RECOGNITION_THRESHOLD:
            # Check if this student was recently recognized (time window logic)
            if best_student_id in last_attendance_time:
                time_since_last = current_time - last_attendance_time[best_student_id]
                if time_since_last < (ATTENDANCE_TIME_WINDOW * 60):  # Convert minutes to seconds
                    return None

            # Additional verification: check if there's a clear winner
            if (best_sim - second_best_sim) < VERIFICATION_THRESHOLD:
                # For uncertain matches, require a higher threshold
                if best_sim > (FACE_RECOGNITION_THRESHOLD + 0.1):  # Higher threshold for uncertain matches
                    logger.info(f"Recognized student ID {best_student_id} with similarity {best_sim:.4f}")
                    return (best_student_id, best_sim)
                else:
                    return None
            else:
                logger.info(f"Recognized student ID {best_student_id} with similarity {best_sim:.4f}")
                return (best_student_id, best_sim)

        return None
    except Exception as e:
        logger.error(f"Error in face recognition: {e}")
        return None
    finally:
        # Clean up to prevent memory leaks
        if 'faces' in locals():
            del faces
        if 'embedding' in locals():
            del embedding
        if 'processed_frame' in locals():
            del processed_frame
        if 'small_frame' in locals():
            del small_frame
        gc.collect()

def process_frame_for_attendance_with_liveness(frame, connection_id):
    """Process a frame for attendance with the canonical inference pipeline."""
    try:
        if frame is None:
            return None

        current_time = time.time()
        frame_history = None
        if connection_id in active_connections:
            pc = active_connections[connection_id]
            if hasattr(pc, 'frame_history'):
                frame_history = pc.frame_history
            else:
                # Initialize frame history
                pc.frame_history = []
                frame_history = pc.frame_history

            processed_frame = preprocess_image(frame)
            # Add current frame to history (keep last 10 frames)
            frame_history.append(processed_frame.copy())
            if len(frame_history) > 10:
                frame_history.pop(0)
        else:
            processed_frame = preprocess_image(frame)

        # Check if we need to refresh the student cache
        if not student_cache or current_time - student_cache.get('last_updated', 0) > 300:
            try:
                if students_provider:
                    students = students_provider()
                    student_cache = {
                        'students': students,
                        'last_updated': current_time
                    }
                    logger.info(f"Updated student cache with {len(students)} students")
                else:
                    response = requests.get(f"{APP_API_URL}/api/students", timeout=3.0)
                    if response.status_code == 200:
                        students = response.json()
                        student_cache = {
                            'students': students,
                            'last_updated': current_time
                        }
                        logger.info(f"Updated student cache with {len(students)} students")
            except Exception as e:
                if not student_cache:
                    return None

        students = student_cache.get('students', [])
        if not students:
            return None

        model_students = []
        for student in students:
            embedding = student.get('face_embedding')
            if not embedding:
                continue
            model_students.append(
                type(
                    "StudentStub",
                    (),
                    {
                        "id": student.get("id"),
                        "name": student.get("name"),
                        "face_embedding_array": embedding,
                        "embedding_model_version": student.get("embedding_model_version"),
                        "face_images": [],
                    },
                )()
            )

        service = get_inference_service()
        faces, envelope = service.analyze_faces(
            frame,
            students=model_students,
            attendance_running=True,
            frame_history=frame_history,
        )
        candidates = [
            face.to_dict()
            for face in faces
            if face.student_id is not None
            and face.decision == "matched"
            and not face.challenge_required
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda item: item["recognition_score"])
        if best["student_id"] in last_attendance_time:
            time_since_last = current_time - last_attendance_time[best["student_id"]]
            if time_since_last < (ATTENDANCE_TIME_WINDOW * 60):
                return None
        liveness_result = {
            "is_live": True,
            "confidence": float(best["liveness_score"]),
            "reason": best["reason"],
        }
        logger.info(
            f"Attendance: Recognized student: {best['student_id']} "
            f"(similarity: {best['recognition_score']:.4f}, liveness: {best['liveness_score']:.2f})"
        )
        return (best["student_id"], best["recognition_score"], liveness_result, best)
    except Exception as e:
        logger.error(f"Error in attendance recognition with liveness: {e}")
        return None
    finally:
        # Clean up to prevent memory leaks
        if 'faces' in locals():
            del faces
        if 'embedding' in locals():
            del embedding
        if 'processed_frame' in locals():
            del processed_frame
        gc.collect()

class VideoTransformTrack(VideoStreamTrack):
    """Video stream track that transforms frames from another track with optimized performance."""

    def __init__(self, track, connection_id):
        super().__init__()
        self.track = track
        self.connection_id = connection_id
        self.frame_counter = 0
        self.last_recognition_time = 0
        self.last_processed_time = 0
        self.last_frame_time = time.time()
        self.processing_interval = 1.0 / 20.0  # Process at most 20 frames per second for faster registration
        self.fps_values = []  # Store recent FPS values for smoothing
        self.max_fps_values = 10  # Number of FPS values to keep for averaging
        self.recognized_faces = {}  # Store recently recognized faces with timestamps
        self.last_faces = None  # Cache detected faces
        self.last_detection_time = 0
        self.detection_interval = 0.2  # Detect faces every 0.2 seconds for faster registration
        self.fps_update_time = time.time()  # Time of last FPS update
        self.data_channel = None  # Data channel for sending messages to client
        self.mode = None  # Mode: 'attendance' or 'registration'
        self.student_id = None  # Student ID for registration mode
        self.captured_faces_count = 0  # Number of captured faces for registration
        self.fps_update_interval = 0.5  # Update FPS display every 0.5 seconds
        self.current_fps_display = 0  # Current FPS value to display
        self.frame_count_since_update = 0  # Frames since last FPS update
        self.last_frame_display_time = time.time()  # Time of last frame display
        logger.info(f"Created video transform track for connection {connection_id}")

    async def recv(self):
        global frame_counter
        frame = None  # Initialize frame to None to handle exceptions properly

        try:
            # Get frame from track
            frame = await self.track.recv()

            # Convert to numpy array
            img = frame.to_ndarray(format="bgr24")

            # Calculate FPS more accurately
            current_time = time.time()
            frame_time = current_time - self.last_frame_time
            self.last_frame_time = current_time

            # Count frames for FPS calculation
            self.frame_count_since_update += 1

            # Update FPS at regular intervals to avoid fluctuations
            if current_time - self.fps_update_time >= self.fps_update_interval:
                elapsed = current_time - self.fps_update_time
                if elapsed > 0 and self.frame_count_since_update > 0:
                    # Calculate actual FPS based on frames processed in the interval
                    actual_fps = self.frame_count_since_update / elapsed
                    # Apply a cap to prevent unrealistic values
                    capped_fps = min(actual_fps, 60.0)  # Cap at 60 FPS for display

                    # Apply smoothing
                    self.fps_values.append(capped_fps)
                    if len(self.fps_values) > self.max_fps_values:
                        self.fps_values.pop(0)

                    # Update the display value
                    self.current_fps_display = sum(self.fps_values) / len(self.fps_values) if self.fps_values else 0

                    # Reset counters
                    self.fps_update_time = current_time
                    self.frame_count_since_update = 0

            # Increment frame counters
            self.frame_counter += 1
            frame_counter += 1

            # Throttle processing to maintain consistent frame rate
            # Skip processing if frames are coming too quickly
            should_process = False
            if current_time - self.last_processed_time >= self.processing_interval:
                if self.frame_counter % FRAME_SKIP == 0:
                    should_process = True
                    self.last_processed_time = current_time

            # Process frame for recognition if it's time
            faces = None
            if should_process:
                # Only detect faces at regular intervals to improve performance
                if current_time - self.last_detection_time > self.detection_interval:
                    self.last_detection_time = current_time

                    try:
                        # Resize image for faster processing
                        h, w = img.shape[:2]
                        small_img = cv2.resize(img, (int(w * DETECTION_SCALE), int(h * DETECTION_SCALE)))

                        # Use InsightFace for face detection on smaller image
                        processed_frame = preprocess_image(small_img)
                        faces = insight_app.get(processed_frame)
                        self.last_faces = faces
                    except Exception as e:
                        logger.error(f"Error detecting faces: {e}")
                else:
                    # Use cached faces
                    faces = self.last_faces

                # Draw faces if available
                if faces:
                    # Draw green boxes around detected faces
                    for face in faces:
                        bbox = face.bbox.astype(int)
                        # Scale bbox back to original image size
                        x1, y1, x2, y2 = [int(coord / DETECTION_SCALE) for coord in bbox]

                        # Draw green rectangle around face
                        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)

                    # Get the mode and student_id from the connection
                    for conn_id, pc in active_connections.items():
                        if conn_id == self.connection_id:
                            self.mode = getattr(pc, 'mode', 'attendance')
                            self.student_id = getattr(pc, 'student_id', None)
                            break

                    # Handle based on mode
                    if self.mode == 'registration' and self.student_id:
                        # For registration mode, we need to capture multiple face embeddings
                        if len(faces) == 1:  # We need exactly one face for registration
                            # Add to processing queue if not full and not too frequent (faster for registration)
                            if not processing_queue.full() and current_time - self.last_recognition_time > 0.3:
                                # Make a copy to avoid race conditions
                                processing_queue.put((img.copy(), self.connection_id))
                                self.last_recognition_time = current_time

                                # Get the face count from the peer connection
                                for conn_id, pc in active_connections.items():
                                    if conn_id == self.connection_id:
                                        if hasattr(pc, 'captured_faces'):
                                            self.captured_faces_count = len(pc.captured_faces)

                                            # Send capture count to client via data channel
                                            try:
                                                if self.data_channel and self.data_channel.readyState == 'open':
                                                    asyncio.ensure_future(self.data_channel.send(json.dumps({
                                                        'type': 'face_detected',
                                                        'count': 1,
                                                        'capture_count': self.captured_faces_count
                                                    })))
                                                    logger.info(f"Sent capture count {self.captured_faces_count}/5 to client")
                                            except Exception as dc_error:
                                                logger.error(f"Error sending capture count: {str(dc_error)[:100]}")
                                        break

                                # Display captured count with progress bar
                                # Draw progress bar background
                                cv2.rectangle(img, (10, 70), (210, 90), (50, 50, 50), -1)

                                # Draw progress bar fill based on captured count
                                progress_width = int((self.captured_faces_count / 5) * 200)
                                cv2.rectangle(img, (10, 70), (10 + progress_width, 90), (0, 255, 0), -1)

                                # Draw text
                                cv2.putText(
                                    img,
                                    f"Captured: {self.captured_faces_count}/5",
                                    (10, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.6,
                                    (0, 255, 0),
                                    2
                                )

                                # Check if registration is complete - do this check earlier and more aggressively
                                registration_complete_flag = False
                                for conn_id, pc in active_connections.items():
                                    if conn_id == self.connection_id:
                                        # Check both the PC object and the global dictionary
                                        if (hasattr(pc, 'registration_complete') and pc.registration_complete) or \
                                           (self.student_id and self.student_id in registration_complete):
                                            registration_complete_flag = True

                                            # Draw a success message directly on the frame
                                            # This ensures the user sees it even if the data channel fails
                                            cv2.putText(
                                                img,
                                                "Registration Complete!",
                                                (int(img.shape[1]/2) - 150, int(img.shape[0]/2)),
                                                cv2.FONT_HERSHEY_SIMPLEX,
                                                1.0,
                                                (0, 255, 0),  # Green color
                                                3
                                            )

                                            # Also add a message to redirect
                                            cv2.putText(
                                                img,
                                                "Redirecting...",
                                                (int(img.shape[1]/2) - 100, int(img.shape[0]/2) + 40),
                                                cv2.FONT_HERSHEY_SIMPLEX,
                                                0.8,
                                                (0, 255, 0),  # Green color
                                                2
                                            )
                                            # Create success frame
                                            success_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
                                            success_img[:, :] = (0, 120, 0)  # Green background

                                            # Add success message with larger font and better positioning
                                            # Add white background box for better visibility
                                            text = "Registration Complete!"
                                            font = cv2.FONT_HERSHEY_DUPLEX
                                            font_scale = 1.2
                                            thickness = 2
                                            text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]

                                            # Center position
                                            text_x = int((img.shape[1] - text_size[0]) / 2)
                                            text_y = int(img.shape[0] / 2)

                                            # Draw white background box
                                            cv2.rectangle(
                                                success_img,
                                                (text_x - 20, text_y - text_size[1] - 20),
                                                (text_x + text_size[0] + 20, text_y + 20),
                                                (255, 255, 255),
                                                -1
                                            )

                                            # Draw text
                                            cv2.putText(
                                                success_img,
                                                text,
                                                (text_x, text_y),
                                                font,
                                                font_scale,
                                                (0, 100, 0),  # Dark green text
                                                thickness
                                            )

                                            # Add checkmark icon
                                            check_radius = 30
                                            check_center = (text_x - 50, text_y)
                                            cv2.circle(success_img, check_center, check_radius, (255, 255, 255), -1)

                                            # Draw checkmark
                                            check_pts = np.array([
                                                [check_center[0] - 15, check_center[1]],
                                                [check_center[0] - 5, check_center[1] + 10],
                                                [check_center[0] + 15, check_center[1] - 10]
                                            ], np.int32)
                                            cv2.polylines(success_img, [check_pts], False, (0, 100, 0), 5)

                                            # Add "Redirecting..." text
                                            cv2.putText(
                                                success_img,
                                                "Redirecting...",
                                                (text_x, text_y + 40),
                                                cv2.FONT_HERSHEY_SIMPLEX,
                                                0.7,
                                                (255, 255, 255),
                                                1
                                            )

                                            # Replace the image with success message
                                            img = success_img

                                            # Try to send registration complete message via data channel
                                            try:
                                                if self.data_channel and self.data_channel.readyState == 'open':
                                                    # Send only once to avoid multiple messages
                                                    asyncio.ensure_future(self.data_channel.send(json.dumps({
                                                        'type': 'registration_complete',
                                                        'success': True,
                                                        'message': 'Face registration complete',
                                                        'capture_count': 5
                                                    })))

                                                    # Release WebRTC resources after a short delay
                                                    async def release_resources():
                                                        await asyncio.sleep(1.0)
                                                        try:
                                                            # Send a request to release resources
                                                            await asyncio.shield(release_webrtc(self.student_id, 'registration'))
                                                            logger.info(f"Released WebRTC resources for student {self.student_id}")
                                                        except Exception as e:
                                                            logger.error(f"Error releasing resources: {str(e)[:100]}")

                                                    asyncio.ensure_future(release_resources())
                                                    logger.info(f"Sent registration complete message to client for connection {self.connection_id}")
                                                else:
                                                    logger.warning(f"Data channel not available for connection {self.connection_id}")

                                                    # Try to send via the peer connection's data channel as fallback
                                                    for conn_id, pc in active_connections.items():
                                                        if conn_id == self.connection_id and hasattr(pc, 'data_channel'):
                                                            asyncio.ensure_future(pc.data_channel.send(json.dumps({
                                                                'type': 'registration_complete',
                                                                'success': True,
                                                                'message': 'Face registration complete',
                                                                'capture_count': 5
                                                            })))
                                                            logger.info(f"Sent registration complete message via peer connection data channel")
                                                            break
                                            except Exception as dc_error:
                                                logger.error(f"Error sending registration complete message: {str(dc_error)[:100]}")
                                        break
                        else:
                            # Display message to show only one face
                            cv2.putText(
                                img,
                                "Please show only one face",
                                (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (0, 0, 255),
                                2
                            )
                    else:
                        # For attendance mode, add to processing queue
                        if not processing_queue.full() and current_time - self.last_recognition_time > 1.0:
                            # Make a copy to avoid race conditions
                            processing_queue.put((img.copy(), self.connection_id))
                            self.last_recognition_time = current_time

                            # Log face detection (reduced logging)
                            if self.frame_counter % 30 == 0:  # Log only every 30th processed frame
                                logger.info(f"Detected {len(faces)} faces")

            # Draw FPS with the smoothed value
            cv2.putText(
                img,
                f"FPS: {self.current_fps_display:.1f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

            # Add timestamp
            timestamp = time.strftime("%H:%M:%S")
            cv2.putText(
                img,
                timestamp,
                (10, img.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1
            )

            # Create new frame from the processed image
            new_frame = frame.from_ndarray(img, format="bgr24")
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base

            return new_frame

        except MediaStreamError:
            # This is a normal exception when the track ends
            logger.info(f"Media stream ended for connection {self.connection_id}")
            raise  # Re-raise the exception to properly end the track

        except Exception as e:
            logger.error(f"Error in VideoTransformTrack.recv: {e}")

            # If we have a frame, return it; otherwise re-raise the exception
            if frame is not None:
                return frame
            else:
                raise

async def process_offer_payload(params):
    """Handle WebRTC offer payload with optimized connection setup."""
    try:
        # Start timing the connection process
        start_time = time.time()

        # Generate a unique connection ID
        connection_id = str(uuid.uuid4())

        # Get offer parameters
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        # Get mode and student_id from params
        mode = params.get("mode", "attendance")
        student_id = params.get("student_id")

        logger.info(f"Received WebRTC offer for connection {connection_id} in mode {mode}")

        # Create peer connection with standard configuration
        pc = RTCPeerConnection()
        # Store mode and student_id in the peer connection object
        pc.mode = mode
        pc.student_id = student_id

        # Create a data channel for sending messages to the client
        pc.data_channel = pc.createDataChannel('events')

        # Log when data channel is open
        @pc.data_channel.on("open")
        def on_datachannel_open():
            logger.info(f"Data channel opened for connection {connection_id}")
            # If this is a registration connection, store the data channel reference
            if mode == 'registration':
                pc.data_channel_ready = True
                pc.registration_complete_sent = False

                # If registration is already complete, send the message immediately
                if hasattr(pc, 'registration_complete') and pc.registration_complete:
                    # Create a task to send the message
                    asyncio.create_task(send_registration_complete(pc.data_channel, student_id))
                    logger.info(f"Sending registration complete message for student {student_id} on data channel open")

        pcs.add(pc)
        active_connections[connection_id] = pc

        # Handle ICE connection state change
        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(f"ICE connection state for {connection_id}: {pc.iceConnectionState}")
            if pc.iceConnectionState == "failed" or pc.iceConnectionState == "closed":
                await pc.close()
                pcs.discard(pc)
                if connection_id in active_connections:
                    del active_connections[connection_id]

        # Handle track from client
        @pc.on("track")
        def on_track(track):
            logger.info(f"Track received from client {connection_id}: {track.kind}")
            if track.kind == "video":
                # Create a video transform track with the data channel
                video_track = VideoTransformTrack(relay.subscribe(track), connection_id)
                # Store the data channel in the video track
                video_track.data_channel = pc.data_channel
                # Add transformed track to peer connection
                pc.addTrack(video_track)

                @track.on("ended")
                async def on_ended():
                    logger.info(f"Track ended for connection {connection_id}")

        # Set remote description with simplified timeout
        try:
            # Set remote description
            await pc.setRemoteDescription(offer)

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception as e:
            logger.error(f"Error establishing connection for {connection_id}: {e}")
            await pc.close()
            pcs.discard(pc)
            if connection_id in active_connections:
                del active_connections[connection_id]
            return {"error": str(e)}, 500

        # Log connection time
        connection_time = time.time() - start_time
        logger.info(f"Created WebRTC connection for {connection_id} in {connection_time:.2f} seconds")

        # Return answer to client
        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "connection_time": connection_time
        }, 200
    except Exception as e:
        logger.error(f"Error handling offer: {e}")
        traceback.print_exc()
        return {"error": str(e)}, 500


async def shutdown_webrtc():
    """Clean up WebRTC resources."""
    # Close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()

    # Stop background processing
    stop_processing()

    logger.info("Shutdown complete")

async def release_active_connections(params):
    """Release active WebRTC peer connections."""
    try:
        connection_id = params.get("student_id", "unknown")
        mode = params.get("mode", "unknown")

        logger.info(f"Received release request for connection {connection_id} in mode {mode}")

        # Close any active connections for this student/connection
        closed_count = 0
        for conn_id, pc in list(active_connections.items()):
            try:
                await pc.close()
                pcs.discard(pc)
                del active_connections[conn_id]
                closed_count += 1
                logger.info(f"Closed connection {conn_id}")
            except Exception as e:
                logger.error(f"Error closing connection {conn_id}: {e}")

        return {
            "success": True,
            "message": f"Released {closed_count} connections",
            "connection_id": connection_id
        }, 200
    except Exception as e:
        logger.error(f"Error handling release request: {e}")
        traceback.print_exc()
        return {"error": str(e)}, 500
