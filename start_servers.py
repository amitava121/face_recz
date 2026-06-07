#!/usr/bin/env python3

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 5000
PYTHON_BIN = sys.executable
_ACTIVE_PYTHON_BIN = PYTHON_BIN

REQUIRED_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-dotenv": "dotenv",
    "opencv-contrib-python": "cv2",
    "numpy": "numpy",
    "insightface": "insightface",
    "mediapipe": "mediapipe",
    "aiortc": "aiortc",
    "asyncpg": "asyncpg",
    "SQLAlchemy": "sqlalchemy",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "cryptography": "cryptography",
    "PyJWT": "jwt",
    "bcrypt": "bcrypt",
    "psutil": "psutil",
    "loguru": "loguru",
    "onnxruntime": "onnxruntime",
}


def resolve_app_mode():
    """The project now always boots through FastAPI."""
    return "fastapi"


def get_python_candidates():
    candidates = []
    env_python = os.environ.get("APP_PYTHON")
    if env_python:
        candidates.append(Path(env_python).expanduser())
    candidates.append(Path(sys.executable))
    candidates.append(PROJECT_DIR / ".venv" / "bin" / "python")
    candidates.append(PROJECT_DIR / "venv" / "bin" / "python")
    python3_path = shutil.which("python3")
    if python3_path:
        candidates.append(Path(python3_path))

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve() if candidate.exists() else candidate
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        unique_candidates.append(candidate)
    return unique_candidates


def find_missing_modules(python_bin):
    command = [
        str(python_bin),
        "-c",
        (
            "import importlib.util, json; "
            f"modules={json.dumps(REQUIRED_MODULES)}; "
            "missing=[pkg for pkg, mod in modules.items() if importlib.util.find_spec(mod) is None]; "
            "print(json.dumps(missing))"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_DIR)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown interpreter failure"
        return None, stderr
    try:
        return json.loads(result.stdout.strip() or "[]"), None
    except json.JSONDecodeError as exc:
        return None, f"invalid dependency probe output: {exc}"


def select_python_bin():
    diagnostics = []
    for candidate in get_python_candidates():
        if not candidate.exists():
            diagnostics.append(f"{candidate} (missing)")
            continue
        missing, error = find_missing_modules(candidate)
        if error:
            diagnostics.append(f"{candidate} ({error})")
            continue
        if not missing:
            return str(candidate), diagnostics
        diagnostics.append(f"{candidate} (missing: {', '.join(missing)})")
    return None, diagnostics


def build_app_command(port):
    return [_ACTIVE_PYTHON_BIN, "main.py", "--mode", resolve_app_mode(), "--port", str(port)]


def build_browser_url(port, path="/"):
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"http://127.0.0.1:{port}{normalized_path}"


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def list_pids_on_port(port):
    lsof_path = shutil.which("lsof")
    if not lsof_path:
        return []
    result = subprocess.run(
        [lsof_path, "-ti", f"tcp:{port}"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode not in (0, 1):
        return []
    return [int(pid) for pid in result.stdout.split() if pid.strip().isdigit()]


def kill_process_on_port(port):
    pids = list_pids_on_port(port)
    if not pids:
        return False

    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                continue
        time.sleep(0.5)
        if not is_port_in_use(port):
            return True
    return not is_port_in_use(port)


def print_colored(text, color):
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "end": "\033[0m",
    }
    print(f"{colors.get(color, '')}{text}{colors['end']}")


def is_apple_silicon():
    return platform.system() == "Darwin" and platform.processor() == "arm"


def setup_gpu_acceleration():
    if not is_apple_silicon():
        return False
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        if "USE_GPU=True" not in content:
            with env_file.open("a", encoding="utf-8") as handle:
                handle.write("\n# GPU Acceleration\nUSE_GPU=True\n")
    try:
        ort = importlib.import_module("onnxruntime")
        print_colored(f"Available ONNX Runtime providers: {', '.join(ort.get_available_providers())}", "blue")
    except Exception:
        pass
    return True


def preload_models(python_bin):
    warmup_timeout = int(os.environ.get("MODEL_WARMUP_TIMEOUT_SECONDS", "45"))
    try:
        result = subprocess.run(
            [python_bin, "main.py", "--mode", "warmup"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            timeout=warmup_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if stdout:
            for line in stdout.splitlines():
                print(f"WARMUP: {line}")
        if stderr:
            for line in stderr.splitlines():
                print(f"WARMUP_ERR: {line}")
        print(f"WARMUP: ⏰ Launcher stopped warmup after {warmup_timeout} seconds")
        return False
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f"WARMUP: {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            print(f"WARMUP_ERR: {line}")
    return result.returncode == 0


def prepare_models(python_bin):
    result = subprocess.run(
        [
            python_bin,
            "-c",
            (
                "from src.services.model_setup import prepare_runtime_models; "
                "prepared = prepare_runtime_models(); "
                "print(f'Prepared buffalo_l: {prepared.recognition_model_dir}'); "
                "print(f'Prepared liveness: {prepared.liveness_model_path}')"
            ),
        ],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f"MODELS: {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            print(f"MODELS_ERR: {line}")
    return result.returncode == 0


def verify_runtime_dependencies():
    selected_python, diagnostics = select_python_bin()
    if not selected_python:
        print_colored("Missing runtime dependencies. No package installation was attempted.", "red")
        for diagnostic in diagnostics:
            print_colored(f" - {diagnostic}", "yellow")
        print_colored(
            f"Install once into a chosen interpreter, for example: python3 -m pip install -r {PROJECT_DIR / 'config' / 'requirements.txt'}",
            "yellow",
        )
        return None

    if diagnostics:
        print_colored("Interpreter selection:", "blue")
        for diagnostic in diagnostics:
            print_colored(f" - skipped {diagnostic}", "yellow")
    print_colored(f"Using Python interpreter: {selected_python}", "green")
    return selected_python


def wait_for_server(port, timeout_seconds=45):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_in_use(port):
            return True
        time.sleep(0.25)
    return False


def stream_process_output(process):
    if process.stdout is None:
        return
    for line in iter(process.stdout.readline, ""):
        print(f"APP: {line.strip()}")
        if process.poll() is not None and not line:
            break


def maybe_open_browser(port):
    if os.environ.get("OPEN_ATTENDANCE_BROWSER", "1").lower() in {"0", "false", "no"}:
        return
    url = build_browser_url(port, "/")
    try:
        webbrowser.open(url)
        print_colored(f"Opened attendance page: {url}", "green")
    except Exception as exc:
        print_colored(f"Could not open browser automatically: {exc}", "yellow")


def start_servers():
    global _ACTIVE_PYTHON_BIN

    print("\n" + "=" * 80)
    print_colored("SmartCam+ System", "blue")
    print("=" * 80 + "\n")

    selected_python = verify_runtime_dependencies()
    if not selected_python:
        return 1
    _ACTIVE_PYTHON_BIN = selected_python

    if is_apple_silicon():
        setup_gpu_acceleration()
        print("")

    print_colored("Preparing runtime models...", "blue")
    if not prepare_models(_ACTIVE_PYTHON_BIN):
        print_colored("Warning: model preparation failed. Existing local models will be used if available.", "yellow")

    print_colored("Initializing system components...", "blue")
    if not preload_models(_ACTIVE_PYTHON_BIN):
        print_colored("Warning: model preloading failed, continuing with startup.", "yellow")

    if is_port_in_use(DEFAULT_PORT):
        print_colored(f"Port {DEFAULT_PORT} is busy. Clearing it before startup...", "yellow")
        if not kill_process_on_port(DEFAULT_PORT):
            print_colored(f"Unable to free port {DEFAULT_PORT}. Stop the existing process and retry.", "red")
            return 1
    time.sleep(0.5)

    process = subprocess.Popen(
        build_app_command(DEFAULT_PORT),
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    print_colored("FASTAPI SERVICE IS STARTING", "green")
    print_colored(f"Web Interface: {build_browser_url(DEFAULT_PORT, '/')}", "cyan")
    print_colored(f"Attendance Camera: {build_browser_url(DEFAULT_PORT, '/attendance')}", "cyan")
    print_colored(f"WebRTC endpoints: http://127.0.0.1:{DEFAULT_PORT}/webrtc/offer", "cyan")
    print_colored("Press Ctrl+C to stop the server", "yellow")
    output_thread = threading.Thread(target=stream_process_output, args=(process,), daemon=True)
    output_thread.start()
    startup_timeout = int(os.environ.get("SERVER_STARTUP_TIMEOUT_SECONDS", "120"))
    if wait_for_server(DEFAULT_PORT, timeout_seconds=startup_timeout):
        maybe_open_browser(DEFAULT_PORT)
    else:
        print_colored(
            f"Server did not bind to port {DEFAULT_PORT} within {startup_timeout} seconds.",
            "yellow",
        )

    try:
        output_thread.join(timeout=0.1)
        return process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        print("Server stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(start_servers())
