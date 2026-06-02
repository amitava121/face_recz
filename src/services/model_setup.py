from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.services.model_registry import runtime_model_registry


BUFFALO_L_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
MINIFASNET_V2_ONNX_URL = (
    "https://huggingface.co/garciafido/minifasnet-v2-anti-spoofing-onnx/resolve/main/minifasnet_v2.onnx"
)


@dataclass(frozen=True)
class PreparedModelPaths:
    recognition_model_dir: Path
    liveness_model_path: Path


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _copy_legacy_model_dir(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def ensure_buffalo_l_model() -> Path:
    root = runtime_model_registry.model_root()
    model_dir = root / "buffalo_l"
    legacy_model_dir = root / "models" / "buffalo_l"
    required_files = {
        "1k3d68.onnx",
        "2d106det.onnx",
        "det_10g.onnx",
        "genderage.onnx",
        "w600k_r50.onnx",
    }

    if legacy_model_dir.exists():
        _copy_legacy_model_dir(legacy_model_dir, model_dir)

    if model_dir.exists() and required_files.issubset({path.name for path in model_dir.glob("*.onnx")}):
        return model_dir

    if os.environ.get("SKIP_MODEL_DOWNLOAD", "0").lower() in {"1", "true", "yes"}:
        raise RuntimeError("buffalo_l models are missing and downloads are disabled")

    with tempfile.TemporaryDirectory(prefix="buffalo_l-") as temp_dir:
        archive = Path(temp_dir) / "buffalo_l.zip"
        _download_file(BUFFALO_L_URL, archive)
        with zipfile.ZipFile(archive) as zip_handle:
            zip_handle.extractall(root)

    if model_dir.exists() and required_files.issubset({path.name for path in model_dir.glob("*.onnx")}):
        return model_dir
    raise RuntimeError("buffalo_l model pack is incomplete after download")


def ensure_minifasnet_model() -> Path:
    configured = runtime_model_registry.liveness_model_path()
    if configured and configured.exists():
        return configured

    target = runtime_model_registry.model_root() / "liveness" / "minifasnet_v2.onnx"
    if target.exists() and target.stat().st_size > 0:
        return target

    if os.environ.get("SKIP_MODEL_DOWNLOAD", "0").lower() in {"1", "true", "yes"}:
        raise RuntimeError("MiniFASNetV2 ONNX model is missing and downloads are disabled")

    _download_file(MINIFASNET_V2_ONNX_URL, target)
    if target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("Downloaded MiniFASNetV2 ONNX is empty")
    return target


def prepare_runtime_models() -> PreparedModelPaths:
    recognition_model_dir = ensure_buffalo_l_model()
    liveness_model_path = ensure_minifasnet_model()
    os.environ.setdefault("FACE_RECOGNITION_MODEL_PACK", "buffalo_l")
    os.environ.setdefault("LIVENESS_MODEL_NAME", "minifasnet_v2")
    os.environ.setdefault("LIVENESS_MODEL_PATH", str(liveness_model_path))
    return PreparedModelPaths(
        recognition_model_dir=recognition_model_dir,
        liveness_model_path=liveness_model_path,
    )
