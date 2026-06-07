#!/usr/bin/env python3

from __future__ import annotations

import importlib
import os
import platform
import socket
import subprocess
import sys
import time


PYTHON_BIN = sys.executable


def resolve_app_mode():
    """The project now always boots through FastAPI."""
    return "fastapi"


def build_app_command(port):
    return [PYTHON_BIN, "main.py", "--mode", resolve_app_mode(), "--port", str(port)]


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def kill_process_on_port(port):
    try:
        result = subprocess.run(f"lsof -i :{port} -t", shell=True, capture_output=True, text=True)
        for pid in filter(None, result.stdout.strip().splitlines()):
            subprocess.run(f"kill {pid}", shell=True)
        time.sleep(0.5)
        return True
    except Exception:
        return False


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
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as handle:
            content = handle.read()
        if "USE_GPU=True" not in content:
            with open(env_file, "a", encoding="utf-8") as handle:
                handle.write("\n# GPU Acceleration\nUSE_GPU=True\n")
    try:
        ort = importlib.import_module("onnxruntime")
        print_colored(f"Available ONNX Runtime providers: {', '.join(ort.get_available_providers())}", "blue")
    except Exception:
        pass
    return True


def preload_models():
    try:
        from main import preload_models as warmup

        return warmup()
    except Exception:
        return False


def start_servers():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    print("\n" + "=" * 80)
    print_colored("SmartCam+ System", "blue")
    print("=" * 80 + "\n")

    if is_apple_silicon():
        setup_gpu_acceleration()
        print("")

    print_colored("Initializing system components...", "blue")
    if not preload_models():
        print_colored("Warning: model preloading failed, continuing with startup.", "yellow")

    kill_process_on_port(5000)
    time.sleep(1)
    process = subprocess.Popen(
        build_app_command(5000),
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    print_colored("🎉 FASTAPI SERVICE IS STARTING", "green")
    print_colored("🌐 Web Interface: http://127.0.0.1:5000", "cyan")
    print_colored("🎥 WebRTC endpoints: http://127.0.0.1:5000/webrtc/offer", "cyan")
    print_colored("🛑 Press Ctrl+C to stop the server", "yellow")

    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            print(f"APP: {line.strip()}")
            if process.poll() is not None:
                break
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
        print("Server stopped")


if __name__ == "__main__":
    start_servers()
