import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_runtime_model_registry_defaults_to_buffalo_l(monkeypatch):
    from src.services.model_registry import runtime_model_registry

    monkeypatch.delenv("FACE_RECOGNITION_MODEL_PACK", raising=False)

    profile = runtime_model_registry.current_profile()

    assert profile.model_pack == "buffalo_l"
    assert profile.recognizer_version == "buffalo_l:recognizer"
    assert profile.detector_version == "buffalo_l:detector"


def test_prepare_runtime_models_reuses_legacy_buffalo_layout(monkeypatch, tmp_path):
    from src.services import model_setup

    models_root = tmp_path / "models"
    legacy_dir = models_root / "models" / "buffalo_l"
    legacy_dir.mkdir(parents=True)
    for name in ("1k3d68.onnx", "2d106det.onnx", "det_10g.onnx", "genderage.onnx", "w600k_r50.onnx"):
        (legacy_dir / name).write_bytes(b"onnx")

    liveness_dir = models_root / "liveness"
    liveness_dir.mkdir(parents=True)
    liveness_path = liveness_dir / "minifasnet_v2.onnx"
    liveness_path.write_bytes(b"onnx")

    monkeypatch.setenv("FACE_MODELS_ROOT", str(models_root))
    monkeypatch.delenv("FACE_RECOGNITION_MODEL_PACK", raising=False)
    monkeypatch.delenv("LIVENESS_MODEL_PATH", raising=False)

    prepared = model_setup.prepare_runtime_models()

    assert prepared.recognition_model_dir == models_root / "buffalo_l"
    assert prepared.recognition_model_dir.exists()
    assert (prepared.recognition_model_dir / "w600k_r50.onnx").exists()
    assert prepared.liveness_model_path == liveness_path
