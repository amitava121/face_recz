#!/usr/bin/env python3
"""Unified entrypoint for the native async FastAPI application."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

# Configure standard streams for UTF-8 and replacement of invalid characters on Windows/non-UTF8 systems
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn


sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.services.model_setup import prepare_runtime_models


def preload_models():
    """Preload face recognition models to avoid first-request latency."""
    print("🧠 Preloading face recognition models...")
    prepare_runtime_models()
    import numpy as np

    def timeout_handler(signum, frame):
        raise TimeoutError("Model initialization timed out")

    has_alarm = hasattr(signal, "SIGALRM")
    try:
        start_time = time.time()
        if has_alarm:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(30)
        try:
            from src.services.face_utils import app as face_app, initialize_insightface

            if face_app is None:
                face_app = initialize_insightface()
            if has_alarm:
                signal.alarm(0)
            if face_app is None:
                print("❌ Failed to initialize InsightFace model")
                return False

            for width, height in ((640, 640), (480, 480), (320, 320)):
                test_image = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
                test_image[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4] = 128
                try:
                    face_app.get(test_image)
                except Exception:
                    pass
            print(f"✅ Model preloading completed in {time.time() - start_time:.2f}s")
            return True
        except TimeoutError:
            print("⏰ Model initialization timed out after 30 seconds")
            return False
    except Exception as exc:
        print(f"❌ Error preloading models: {exc}")
        return False
    finally:
        try:
            if has_alarm:
                signal.alarm(0)
        except Exception:
            pass


def start_fastapi_app(port=5000):
    print(f"🚀 Starting FastAPI application on port {port}...")
    try:
        if os.getenv("PRELOAD_MODELS", "0").lower() in {"1", "true", "yes", "on"}:
            try:
                prepare_runtime_models()
            except Exception as exc:
                print(f"⚠️ Model preparation skipped: {exc}")
        from src.web.app import create_app

        app = create_app()
        app.state.settings.port = port
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
        return True
    except Exception as exc:
        print(f"❌ Error starting FastAPI app: {exc}")
        return False


def run_system_checks():
    print("🔍 Running system health checks...")
    try:
        from src.utils.check_model import main as check_main

        check_main()
        return True
    except Exception as exc:
        print(f"❌ Error running checks: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SmartCam+ System")
    parser.add_argument(
        "--mode",
        choices=["app", "fastapi", "webrtc", "both", "check", "warmup", "quick"],
        default="fastapi",
        help="Runtime mode. All web modes now resolve to the integrated FastAPI app.",
    )
    parser.add_argument("--port", type=int, help="Port to run the service on")
    args = parser.parse_args()

    if args.mode in {"app", "fastapi", "webrtc", "both", "quick"}:
        return start_fastapi_app(args.port or 5000)
    if args.mode == "check":
        return run_system_checks()
    if args.mode == "warmup":
        return preload_models()
    print(f"❌ Unknown mode: {args.mode}")
    return False


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        sys.exit(0)
