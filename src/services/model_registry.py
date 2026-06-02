from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    detector_version: str
    recognizer_version: str
    liveness_version: str
    model_pack: str
    recognizer_threshold: float
    verification_margin: float
    liveness_threshold: float
    enrollment_samples: int
    challenge_response_enabled: bool


class RuntimeModelRegistry:
    def __init__(self) -> None:
        self._project_root = Path(__file__).resolve().parents[2]

    def current_profile(self) -> ModelProfile:
        model_pack = os.getenv("FACE_RECOGNITION_MODEL_PACK", "buffalo_l").strip() or "buffalo_l"
        detector_version = f"{model_pack}:detector"
        recognizer_version = f"{model_pack}:recognizer"
        liveness_model_name = os.getenv("LIVENESS_MODEL_NAME", "minifasnet_v2")
        liveness_model_path = os.getenv("LIVENESS_MODEL_PATH", "").strip()
        liveness_version = (
            f"{liveness_model_name}:onnx"
            if liveness_model_path
            else f"{liveness_model_name}:heuristic-fallback"
        )
        return ModelProfile(
            detector_version=detector_version,
            recognizer_version=recognizer_version,
            liveness_version=liveness_version,
            model_pack=model_pack,
            recognizer_threshold=float(os.getenv("FACE_RECOGNITION_THRESHOLD", "0.55")),
            verification_margin=float(os.getenv("VERIFICATION_THRESHOLD", "0.08")),
            liveness_threshold=float(os.getenv("LIVENESS_CONFIDENCE_THRESHOLD", "0.80")),
            enrollment_samples=int(os.getenv("ENROLLMENT_SAMPLE_TARGET", "10")),
            challenge_response_enabled=os.getenv("ENABLE_CHALLENGE_RESPONSE", "true").lower() in {"true", "1", "t"},
        )

    def model_root(self) -> Path:
        configured = os.getenv("FACE_MODELS_ROOT", "").strip()
        if configured:
            return Path(configured).expanduser()
        return self._project_root / "models"

    def liveness_model_path(self) -> Path | None:
        configured = os.getenv("LIVENESS_MODEL_PATH", "").strip()
        if configured:
            return Path(configured).expanduser()
        default_path = self.model_root() / "liveness" / "minifasnet_v2.onnx"
        return default_path if default_path.exists() else None


runtime_model_registry = RuntimeModelRegistry()
