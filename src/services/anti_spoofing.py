import os
import cv2
import numpy as np
import time
import logging
from typing import Dict, List, Optional, Tuple, Any
import warnings
import insightface
from insightface.app import FaceAnalysis
import threading

# Suppress warnings for cleaner output
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for InsightFace model
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

# Initialize InsightFace model in a thread-safe way
model_lock = threading.Lock()
insightface_app = None

def get_insightface_model():
    global insightface_app
    with model_lock:
        if insightface_app is None:
            try:
                # Initialize InsightFace
                insightface_app = FaceAnalysis(name='buffalo_l', root=MODELS_DIR)
                insightface_app.prepare(ctx_id=0, det_size=(640, 640))
                logger.info("✅ InsightFace model loaded successfully")
            except Exception as e:
                logger.error(f"❌ Error loading InsightFace model: {e}")
                insightface_app = None
        return insightface_app


class SimpleAntiSpoofingDetector:
    """
    Enhanced anti-spoofing detector using both computer vision techniques and InsightFace AI model
    """

    def __init__(self):
        """Initialize the enhanced anti-spoofing detector with configurable strictness and AI capabilities"""
        # Initialize InsightFace model
        self.insightface_model = get_insightface_model()
        
        # Initialize anti-spoofing parameters
        self.use_ai_model = os.environ.get('USE_AI_ANTI_SPOOFING', 'True').lower() in ('true', '1', 't')
        self.ai_confidence_threshold = float(os.environ.get('AI_CONFIDENCE_THRESHOLD', '0.8'))
        
        logger.info(f"🤖 AI Anti-spoofing enabled: {self.use_ai_model}")
        if self.use_ai_model and self.insightface_model is None:
            logger.warning("⚠️ AI model not available, falling back to traditional methods")
        self.frame_history = []
        self.max_history = 8  # Keep more frames for better motion analysis

        # Get configuration from environment variables
        strictness = os.environ.get('ANTI_SPOOFING_STRICTNESS', 'high').lower()
        require_motion = os.environ.get('REQUIRE_MOTION_FOR_ATTENDANCE', 'True').lower() in ('true', '1', 't')
        consecutive_frames = int(os.environ.get('CONSECUTIVE_FRAMES_REQUIRED', '3'))
        motion_threshold = int(os.environ.get('MOTION_THRESHOLD', '800'))

        # Configure thresholds based on strictness level - BALANCED for real faces
        if strictness == 'low':
            self.motion_threshold = 200  # Very low motion requirement
            self.brightness_min = 30
            self.brightness_max = 240
            self.sharpness_threshold = 50
            self.contrast_threshold = 10
            self.required_consecutive_frames = 1
        elif strictness == 'medium':
            self.motion_threshold = 400  # Moderate motion requirement
            self.brightness_min = 40
            self.brightness_max = 220
            self.sharpness_threshold = 80
            self.contrast_threshold = 15
            self.required_consecutive_frames = 2
        elif strictness == 'maximum':
            self.motion_threshold = 800  # High motion requirement
            self.brightness_min = 60
            self.brightness_max = 180
            self.sharpness_threshold = 120
            self.contrast_threshold = 25
            self.required_consecutive_frames = 3
        else:  # high (default) - BALANCED for real faces
            self.motion_threshold = 300  # Reasonable motion requirement
            self.brightness_min = 35    # More permissive brightness
            self.brightness_max = 230   # More permissive brightness
            self.sharpness_threshold = 60  # Lower sharpness requirement
            self.contrast_threshold = 12   # Lower contrast requirement
            self.required_consecutive_frames = 2  # Reduced consecutive frames

        # Allow explicit environment overrides after choosing the base strictness preset.
        self.motion_threshold = motion_threshold
        self.required_consecutive_frames = consecutive_frames

        # Calibrated defaults for webcam attendance. The previous values were too
        # strict for real camera feeds and forced most real faces into spoof.
        self.uniformity_threshold = float(os.environ.get('UNIFORMITY_THRESHOLD', '5.5'))
        self.edge_density_threshold = float(os.environ.get('EDGE_DENSITY_THRESHOLD', '0.025'))
        self.texture_threshold = float(os.environ.get('TEXTURE_COMPLEXITY_THRESHOLD', '5.0'))
        self.gradient_threshold = float(os.environ.get('GRADIENT_MAGNITUDE_THRESHOLD', '4.0'))
        self.require_motion = require_motion

        # Consecutive frame tracking
        self.consecutive_passes = 0

        logger.info(f"🔧 Enhanced anti-spoofing detector initialized:")
        logger.info(f"   - Strictness: {strictness}")
        logger.info(f"   - Motion required: {require_motion}")
        logger.info(f"   - Motion threshold: {self.motion_threshold}")
        logger.info(f"   - Consecutive frames: {self.required_consecutive_frames}")
        logger.info(f"   - Brightness range: {self.brightness_min}-{self.brightness_max}")
        logger.info(f"   - Sharpness threshold: {self.sharpness_threshold}")

    def reset_consecutive_counter(self):
        """Reset the consecutive passes counter"""
        self.consecutive_passes = 0
        logger.debug("Reset consecutive passes counter")

    def calculate_motion(self, current_frame: np.ndarray, previous_frame: np.ndarray) -> float:
        """
        Calculate motion between two frames using frame differencing

        Args:
            current_frame: Current frame
            previous_frame: Previous frame

        Returns:
            Motion score (higher = more motion)
        """
        try:
            # Convert to grayscale if needed
            if len(current_frame.shape) == 3:
                current_gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            else:
                current_gray = current_frame

            if len(previous_frame.shape) == 3:
                previous_gray = cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
            else:
                previous_gray = previous_frame

            # Calculate absolute difference
            diff = cv2.absdiff(current_gray, previous_gray)

            # Apply threshold to get binary image
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

            # Count non-zero pixels as motion score
            motion_score = np.sum(thresh > 0)

            return float(motion_score)

        except Exception as e:
            logger.warning(f"Error calculating motion: {e}")
            return 0.0

    def analyze_face_quality(self, face_region: np.ndarray) -> Dict[str, float]:
        """
        Enhanced face region quality analysis with anti-spoofing features

        Args:
            face_region: Cropped face region

        Returns:
            Dictionary with quality metrics
        """
        try:
            # Convert to grayscale if needed
            if len(face_region.shape) == 3:
                gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)
            else:
                gray = face_region

            # Calculate brightness (mean intensity)
            brightness = np.mean(gray)

            # Calculate contrast (standard deviation)
            contrast = np.std(gray)

            # Calculate sharpness (Laplacian variance)
            laplacian = cv2.Laplacian(gray, cv2.CV_64F)
            sharpness = laplacian.var()

            # Calculate histogram uniformity (entropy-like measure)
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            hist_norm = hist / hist.sum()
            hist_norm = hist_norm[hist_norm > 0]  # Remove zeros
            uniformity = -np.sum(hist_norm * np.log2(hist_norm))

            # Additional anti-spoofing metrics

            # Edge density - real faces have more natural edges
            edges = cv2.Canny(gray, 50, 150)
            edge_density = np.sum(edges > 0) / (gray.shape[0] * gray.shape[1])

            # Texture analysis using Local Binary Pattern-like approach
            # Calculate variance in local neighborhoods
            kernel = np.ones((3, 3), np.float32) / 9
            local_mean = cv2.filter2D(gray.astype(np.float32), -1, kernel)
            local_variance = cv2.filter2D((gray.astype(np.float32) - local_mean) ** 2, -1, kernel)
            texture_complexity = np.mean(local_variance)

            # Color distribution analysis (if color image)
            color_variance = 0.0
            if len(face_region.shape) == 3:
                # Calculate variance across color channels
                b, g, r = cv2.split(face_region)
                color_variance = np.var([np.mean(b), np.mean(g), np.mean(r)])

            # Frequency domain analysis - real faces have specific frequency characteristics
            f_transform = np.fft.fft2(gray)
            f_shift = np.fft.fftshift(f_transform)
            magnitude_spectrum = np.log(np.abs(f_shift) + 1)
            frequency_energy = np.mean(magnitude_spectrum)

            # Gradient magnitude - real faces have natural gradients
            grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            gradient_magnitude = np.sqrt(grad_x**2 + grad_y**2)
            avg_gradient = np.mean(gradient_magnitude)

            return {
                'brightness': float(brightness),
                'contrast': float(contrast),
                'sharpness': float(sharpness),
                'uniformity': float(uniformity),
                'edge_density': float(edge_density),
                'texture_complexity': float(texture_complexity),
                'color_variance': float(color_variance),
                'frequency_energy': float(frequency_energy),
                'gradient_magnitude': float(avg_gradient)
            }

        except Exception as e:
            logger.warning(f"Error analyzing face quality: {e}")
            return {
                'brightness': 0.0,
                'contrast': 0.0,
                'sharpness': 0.0,
                'uniformity': 0.0,
                'edge_density': 0.0,
                'texture_complexity': 0.0,
                'color_variance': 0.0,
                'frequency_energy': 0.0,
                'gradient_magnitude': 0.0
            }

    def detect_spoofing_ai(self, image: np.ndarray, face_bbox: List[int]) -> Dict:
        """
        Detect spoofing using InsightFace AI model
        """
        try:
            if self.insightface_model is None:
                return None

            # Convert bbox format from [x, y, w, h] to [x1, y1, x2, y2]
            x, y, w, h = face_bbox
            det_bbox = np.array([x, y, x + w, y + h])

            # Process with InsightFace
            faces = self.insightface_model.get(image)
            
            if not faces:
                return None

            # Find the face that best matches our bbox
            best_match = None
            best_iou = 0
            
            for face in faces:
                bbox = face.bbox.astype(int)
                iou = self.calculate_iou(det_bbox, bbox)
                
                if iou > best_iou:
                    best_iou = iou
                    best_match = face

            if best_match is None or best_iou < 0.5:
                return None

            # Get anti-spoofing score
            anti_spoofing_score = best_match.get('anti_spoofing', None)
            
            if anti_spoofing_score is None:
                return None

            is_live = anti_spoofing_score >= self.ai_confidence_threshold
            
            return {
                'is_live': is_live,
                'confidence': float(anti_spoofing_score),
                'method': 'insightface',
                'iou_score': best_iou
            }

        except Exception as e:
            logger.error(f"Error in AI-based spoofing detection: {e}")
            return None

    def calculate_iou(self, box1: np.ndarray, box2: np.ndarray) -> float:
        """Calculate Intersection over Union between two bounding boxes"""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])

        intersection = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0

    def detect_spoofing(self, image: np.ndarray, face_bbox: List[int],
                       frame_history: Optional[List[np.ndarray]] = None) -> Dict:
        """
        Detect spoofing using both AI and computer vision techniques

        Args:
            image: Input image (numpy array)
            face_bbox: Face bounding box [x, y, w, h]
            frame_history: Optional frame history for motion analysis

        Returns:
            dict: Detection results with confidence scores
        """
        start_time = time.time()

        try:
            # Extract face region
            x, y, w, h = face_bbox
            face_region = image[y:y+h, x:x+w]

            if face_region.size == 0:
                return {
                    'is_live': False,
                    'confidence': 0.0,
                    'reason': 'Invalid face region',
                    'processing_time': time.time() - start_time,
                    'model_used': 'Simple-Anti-Spoofing'
                }

            # Analyze face quality
            quality_metrics = self.analyze_face_quality(face_region)

            # Calculate motion if frame history is available
            motion_score = 0.0
            if frame_history and len(frame_history) > 0:
                # Use the most recent frame from history
                previous_frame = frame_history[-1]
                motion_score = self.calculate_motion(image, previous_frame)

            # Update frame history
            self.frame_history.append(image.copy())
            if len(self.frame_history) > self.max_history:
                self.frame_history.pop(0)

            # Calculate motion from internal history if no external history provided
            if motion_score == 0.0 and len(self.frame_history) > 1:
                motion_score = self.calculate_motion(
                    self.frame_history[-1],
                    self.frame_history[-2]
                )

            # Enhanced spoofing detection logic with multiple checks
            brightness = quality_metrics['brightness']
            contrast = quality_metrics['contrast']
            sharpness = quality_metrics['sharpness']
            uniformity = quality_metrics['uniformity']
            edge_density = quality_metrics['edge_density']
            texture_complexity = quality_metrics['texture_complexity']
            color_variance = quality_metrics['color_variance']
            frequency_energy = quality_metrics['frequency_energy']
            gradient_magnitude = quality_metrics['gradient_magnitude']

            # Strict quality checks for anti-spoofing
            brightness_ok = self.brightness_min <= brightness <= self.brightness_max
            sharpness_ok = sharpness >= self.sharpness_threshold
            contrast_ok = contrast >= self.contrast_threshold
            motion_ok = motion_score >= self.motion_threshold
            uniformity_ok = uniformity >= self.uniformity_threshold
            edge_density_ok = edge_density >= self.edge_density_threshold
            texture_ok = texture_complexity >= self.texture_threshold
            gradient_ok = gradient_magnitude >= self.gradient_threshold

            # Balanced spoofing detection rules. A real webcam face can have low
            # texture and edge density in dark scenes, so do not call that a
            # screen attack unless the image also has display-like brightness.

            # Rule 1: Screen detection - require explicit display-like cues.
            screen_indicators = 0
            screen_like_brightness = brightness > 175 and contrast < 18
            if screen_like_brightness:
                screen_indicators += 1
            if screen_like_brightness and texture_complexity < max(4.0, self.texture_threshold * 0.8):
                screen_indicators += 1
            if screen_like_brightness and edge_density < max(0.015, self.edge_density_threshold * 0.8):
                screen_indicators += 1
            if screen_like_brightness and color_variance < 10:
                screen_indicators += 1

            # Rule 2: Photo detection - more lenient thresholds
            photo_indicators = 0
            if sharpness < 30:  # Very blurry (clearly printed photo)
                photo_indicators += 1
            if color_variance < 20:  # Very low color variation
                photo_indicators += 1
            if gradient_magnitude < 5:  # Very low gradient variation
                photo_indicators += 1

            # Rule 3: Motion requirement - real faces should show some natural micro-movements
            has_sufficient_motion = motion_score >= self.motion_threshold

            # Calculate confidence based on multiple factors with stricter weighting
            confidence_factors = []

            # Brightness factor (0.0 to 1.0) - stricter
            if brightness_ok:
                brightness_factor = 1.0
            else:
                brightness_factor = max(0.0, 1.0 - abs(brightness - 120) / 100)
            confidence_factors.append(brightness_factor * 0.15)

            # Sharpness factor (0.0 to 1.0) - more important
            sharpness_factor = min(1.0, sharpness / (self.sharpness_threshold * 1.5))
            confidence_factors.append(sharpness_factor * 0.20)

            # Contrast factor (0.0 to 1.0) - stricter
            contrast_factor = min(1.0, contrast / (self.contrast_threshold * 2))
            confidence_factors.append(contrast_factor * 0.15)

            # Motion factor (0.0 to 1.0) - most important for liveness
            motion_factor = min(1.0, motion_score / (self.motion_threshold * 1.5))
            confidence_factors.append(motion_factor * 0.25)

            # Texture complexity factor - important for detecting flat surfaces
            texture_factor = min(1.0, texture_complexity / 100.0)
            confidence_factors.append(texture_factor * 0.15)

            # Edge density factor - real faces have natural edges
            edge_factor = min(1.0, edge_density / (self.edge_density_threshold * 5))
            confidence_factors.append(edge_factor * 0.10)

            # Calculate overall confidence from traditional methods
            traditional_confidence = sum(confidence_factors)
            
            # Get AI-based detection result if enabled
            ai_result = None
            if self.use_ai_model and self.insightface_model is not None:
                ai_result = self.detect_spoofing_ai(image, face_bbox)
            
            # Combine traditional and AI confidence scores
            if ai_result is not None:
                ai_confidence = ai_result['confidence']
                # Weight AI more heavily (70%) when available
                confidence = (0.7 * ai_confidence) + (0.3 * traditional_confidence)
                is_live_ai = ai_result['is_live']
            else:
                confidence = traditional_confidence
                is_live_ai = None

            # Strict liveness determination with multiple requirements
            basic_quality_ok = brightness_ok and sharpness_ok and contrast_ok and uniformity_ok
            advanced_quality_ok = edge_density_ok and texture_ok and gradient_ok
            no_screen_detected = screen_indicators < 2
            no_photo_detected = photo_indicators < 2
            
            # If AI detection is available, it gets a strong vote
            if is_live_ai is not None:
                if not is_live_ai:
                    logger.info("🤖 AI model detected spoofing attempt")

            # Motion can strengthen a live decision, but it must never override
            # clear replay/photo indicators or failed quality gates.
            is_live = (
                basic_quality_ok and
                advanced_quality_ok and
                no_screen_detected and
                no_photo_detected and
                (has_sufficient_motion or not self.require_motion)
            )

            # Additional consecutive frame requirement
            if is_live:
                self.consecutive_passes += 1
                if self.consecutive_passes < self.required_consecutive_frames:
                    is_live = False  # Need consecutive passes
                    confidence = min(confidence, 0.6)
            else:
                self.consecutive_passes = 0  # Reset counter
                confidence = min(confidence, 0.3)  # Cap confidence for non-live faces

            # Create detailed reason with enhanced detection info
            issues = []
            if not brightness_ok:
                issues.append(f"brightness={brightness:.1f}")
            if not sharpness_ok:
                issues.append(f"sharpness={sharpness:.1f}")
            if not contrast_ok:
                issues.append(f"contrast={contrast:.1f}")
            if not motion_ok:
                issues.append(f"motion={motion_score:.1f}")
            if not uniformity_ok:
                issues.append(f"uniformity={uniformity:.1f}")
            if not edge_density_ok:
                issues.append(f"edge_density={edge_density:.3f}")
            if not texture_ok:
                issues.append(f"texture={texture_complexity:.1f}")
            if not gradient_ok:
                issues.append(f"gradient={gradient_magnitude:.1f}")
            if screen_indicators >= 2:
                issues.append(f"screen_detected({screen_indicators})")
            if photo_indicators >= 2:
                issues.append(f"photo_detected({photo_indicators})")
            if self.consecutive_passes < self.required_consecutive_frames:
                issues.append(f"consecutive_frames={self.consecutive_passes}/{self.required_consecutive_frames}")
            
            # Add AI result to reason if available
            ai_status = ""
            if ai_result is not None:
                ai_status = f", AI_confidence={ai_result['confidence']:.3f}"
                if not ai_result['is_live']:
                    issues.append("ai_spoofing_detected")

            if is_live:
                reason = f"LIVE FACE: confidence={confidence:.3f}{ai_status}, consecutive_passes={self.consecutive_passes}"
            else:
                reason = f"SPOOFING DETECTED: {', '.join(issues)}, confidence={confidence:.3f}{ai_status}"

            processing_time = time.time() - start_time

            result = {
                'is_live': is_live,
                'confidence': confidence,
                'reason': reason,
                'processing_time': processing_time,
                'model_used': 'Enhanced-Anti-Spoofing',
                'quality_metrics': quality_metrics,
                'motion_score': motion_score,
                'consecutive_passes': self.consecutive_passes,
                'screen_indicators': screen_indicators,
                'photo_indicators': photo_indicators,
                'thresholds_met': {
                    'brightness': brightness_ok,
                    'sharpness': sharpness_ok,
                    'contrast': contrast_ok,
                    'motion': motion_ok,
                    'uniformity': uniformity_ok,
                    'edge_density': edge_density_ok,
                    'texture': texture_ok,
                    'gradient': gradient_ok
                },
                'spoofing_detection': {
                    'screen_detected': screen_indicators >= 2,
                    'photo_detected': photo_indicators >= 2,
                    'sufficient_motion': has_sufficient_motion,
                    'consecutive_requirement_met': self.consecutive_passes >= self.required_consecutive_frames
                }
            }

            logger.info(f"🔍 Simple anti-spoofing result: {is_live}, confidence: {confidence:.2f}")
            return result

        except Exception as e:
            logger.error(f"❌ Error in simple anti-spoofing detection: {e}")
            return {
                'is_live': False,
                'confidence': 0.0,
                'reason': f"Detection error: {str(e)}",
                'processing_time': time.time() - start_time,
                'model_used': 'Simple-Anti-Spoofing-Error'
            }


# Singleton pattern for detector instance
class AntiSpoofingService:
    _detector_instance: Optional[SimpleAntiSpoofingDetector] = None

    @classmethod
    def get_detector(cls) -> SimpleAntiSpoofingDetector:
        """Get a singleton instance of the simple anti-spoofing detector."""
        if cls._detector_instance is None:
            logger.info("🚀 Initializing Simple Anti-Spoofing Detector for the first time...")
            cls._detector_instance = SimpleAntiSpoofingDetector()
            logger.info("✅ Simple Anti-Spoofing Detector initialized and cached.")
        return cls._detector_instance


# Utility functions for easy integration
def check_liveness(image: np.ndarray, face_bbox: List[int],
                  frame_history: Optional[List[np.ndarray]] = None) -> Dict:
    """
    Check if face is live using simple anti-spoofing techniques.
    Uses a cached detector instance for high performance.
    """
    detector = AntiSpoofingService.get_detector()
    return detector.detect_spoofing(image, face_bbox, frame_history)


def check_liveness_balanced(image: np.ndarray, face_bbox: List[int],
                          frame_history: Optional[List[np.ndarray]] = None) -> Dict:
    """
    Check if face is live using a balanced approach optimized for attendance systems.
    Uses a cached detector instance for high performance.
    """
    detector = AntiSpoofingService.get_detector()
    result = detector.detect_spoofing(image, face_bbox, frame_history)

    # Log detailed liveness check results
    if result['is_live']:
        logger.info(f"✅ LIVENESS PASSED: confidence={result['confidence']:.2f}, "
                   f"consecutive_passes={result.get('consecutive_passes', 0)}")
    else:
        logger.warning(f"❌ SPOOFING DETECTED: {result['reason']}")

        # Provide specific guidance based on detection type
        spoofing_info = result.get('spoofing_detection', {})
        if spoofing_info.get('screen_detected'):
            logger.warning("📱 Screen/display detected - use a real person, not a photo on screen")
        if spoofing_info.get('photo_detected'):
            logger.warning("📄 Photo detected - use a live person, not a printed photo")
        if not spoofing_info.get('sufficient_motion'):
            logger.warning("� Insufficient motion - move your head slightly to prove liveness")

    return result


def get_spoofing_feedback(result: Dict) -> str:
    """
    Generate user-friendly feedback about spoofing detection results

    Args:
        result: Result dictionary from anti-spoofing detection

    Returns:
        User-friendly feedback message
    """
    if result['is_live']:
        return "✅ Live person detected - attendance allowed"

    spoofing_info = result.get('spoofing_detection', {})
    feedback_messages = []

    if spoofing_info.get('screen_detected'):
        feedback_messages.append("📱 Screen/display detected - please use a real person")

    if spoofing_info.get('photo_detected'):
        feedback_messages.append("📄 Photo detected - please use a live person")

    if not spoofing_info.get('sufficient_motion'):
        feedback_messages.append("🚫 Please move your head slightly to prove you're real")

    if not spoofing_info.get('consecutive_requirement_met'):
        passes = result.get('consecutive_passes', 0)
        required = 3  # Default requirement
        feedback_messages.append(f"⏳ Need {required - passes} more consistent frames")

    if not feedback_messages:
        feedback_messages.append("❌ Spoofing detected - please ensure you're using a live person")

    return " | ".join(feedback_messages)


# Test function
if __name__ == "__main__":
    print("🛡️ Testing Simple Anti-Spoofing System")
    print("=" * 50)

    try:
        # Create a dummy test image and bounding box
        test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        test_bbox = [150, 150, 200, 200]  # A reasonably sized face bbox

        print("\n[INFO] Running liveness check...")

        # Use the check_liveness function to test the service
        result = check_liveness(test_image, test_bbox)

        print("\n[TEST RESULT]")
        for key, value in result.items():
            if isinstance(value, float):
                print(f"- {key}: {value:.4f}")
            else:
                print(f"- {key}: {value}")

        print("\n✅ Simple anti-spoofing test completed successfully.")

    except Exception as e:
        print(f"\n❌ An error occurred during the test: {e}")
        import traceback
        traceback.print_exc()
