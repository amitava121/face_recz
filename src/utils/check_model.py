#!/usr/bin/env python3
"""
Script to check which InsightFace model is being used in the application.
This will print out detailed information about the model pack and individual models.
"""

import os
import sys
import logging
import glob
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def check_dependencies():
    """Check if all required dependencies are available."""
    missing_deps = []

    try:
        import numpy as np
        logger.info("✅ NumPy available")
    except ImportError:
        missing_deps.append("numpy")
        logger.error("❌ NumPy not available")

    try:
        import cv2
        logger.info("✅ OpenCV available")
    except ImportError:
        missing_deps.append("opencv-python")
        logger.error("❌ OpenCV not available")

    try:
        import insightface
        logger.info("✅ InsightFace available")
    except ImportError:
        missing_deps.append("insightface")
        logger.error("❌ InsightFace not available")

    try:
        import onnxruntime
        logger.info("✅ ONNX Runtime available")
    except ImportError:
        missing_deps.append("onnxruntime")
        logger.error("❌ ONNX Runtime not available")

    return missing_deps

def check_insightface_model():
    """Check which InsightFace model is being used and print details."""
    try:
        # Import dependencies inside the function to avoid hanging on import
        import numpy as np
        import cv2
        from insightface.app import FaceAnalysis
        from insightface.utils import DEFAULT_MP_NAME

        # Initialize InsightFace with the same parameters as in the main app
        logger.info("Initializing InsightFace...")
        app = FaceAnalysis(providers=['CPUExecutionProvider'])

        # Print the default model pack name
        logger.info(f"Default model pack name: {DEFAULT_MP_NAME}")

        # Print the model directory
        model_dir = os.path.expanduser(f"~/.insightface/models/{DEFAULT_MP_NAME}")
        logger.info(f"Model directory: {model_dir}")

        # Check if model directory exists
        if not os.path.exists(model_dir):
            logger.warning(f"Model directory does not exist: {model_dir}")
            logger.info("Models will be downloaded on first use")
            return None

        # List all ONNX files in the model directory
        onnx_files = glob.glob(os.path.join(model_dir, "*.onnx"))
        logger.info(f"Found {len(onnx_files)} ONNX model files:")
        for onnx_file in onnx_files:
            logger.info(f"  - {os.path.basename(onnx_file)}")

        # Only prepare models if they exist
        if onnx_files:
            logger.info("Preparing InsightFace models...")
            app.prepare(ctx_id=0, det_size=(640, 640))

            # Print information about each loaded model
            logger.info("Loaded models:")
            for taskname, model in app.models.items():
                logger.info(f"  - Task: {taskname}")
                logger.info(f"    File: {os.path.basename(model.model_file)}")
                logger.info(f"    Input shape: {model.input_shape}")
                logger.info(f"    Input mean: {model.input_mean}")
                logger.info(f"    Input std: {model.input_std}")

            # Print detection model details
            det_model = app.det_model
            logger.info(f"Detection model type: {type(det_model).__name__}")

            # Test with a simple image
            test_img = np.zeros((100, 100, 3), dtype=np.uint8)
            cv2.rectangle(test_img, (30, 30), (70, 70), (255, 255, 255), -1)  # White square as a dummy face

            logger.info("Testing model with a dummy image...")
            try:
                faces = app.get(test_img)
                logger.info(f"Test result: detected {len(faces)} faces")
            except Exception as e:
                logger.warning(f"Test failed: {e}")

        return app
    except Exception as e:
        logger.error(f"Error checking InsightFace model: {e}")
        return None

def main():
    """Main function for health checks."""
    logger.info("🔍 Starting system health checks...")

    # Check dependencies first
    missing_deps = check_dependencies()

    if missing_deps:
        logger.error(f"❌ Missing dependencies: {', '.join(missing_deps)}")
        logger.error("Please install missing dependencies with: pip install -r config/requirements.txt")
        return False

    logger.info("✅ All dependencies are available")

    # Check database connection
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from src.models.db import build_database_uri

        database_uri = build_database_uri()
        parsed = urlparse(database_uri)
        db_host = parsed.hostname or os.getenv('DB_HOST', 'localhost')
        db_name = parsed.path.lstrip('/') or os.getenv('DB_NAME', 'attendance_db')
        db_user = parsed.username or os.getenv('DB_USER', 'postgres')

        logger.info(f"📊 Database configuration:")
        logger.info(f"  - Host: {db_host}")
        logger.info(f"  - Database: {db_name}")
        logger.info(f"  - User: {db_user}")

        # Try to connect to database
        try:
            import psycopg2
            conn = psycopg2.connect(database_uri)
            conn.close()
            logger.info("✅ Database connection successful")
        except Exception as e:
            logger.warning(f"⚠️ Database connection failed: {e}")
            logger.info("Make sure PostgreSQL is running and database exists")

    except Exception as e:
        logger.warning(f"⚠️ Could not check database: {e}")

    # Check InsightFace models (optional, might download models)
    logger.info("🤖 Checking InsightFace models...")
    try:
        app = check_insightface_model()
        if app:
            logger.info("✅ InsightFace models loaded successfully")
        else:
            logger.info("ℹ️ InsightFace models not yet downloaded (will download on first use)")
    except Exception as e:
        logger.warning(f"⚠️ InsightFace check failed: {e}")

    logger.info("🎉 System health check completed!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
