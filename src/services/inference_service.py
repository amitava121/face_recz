from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence, cast

import cv2
import numpy as np
from loguru import logger

from src.models.db import Student
from src.services.anti_spoofing import check_liveness_balanced
from src.services.face_utils import ensure_insightface_initialized, preprocess_image
from src.services.model_registry import ModelProfile, runtime_model_registry

try:
    import onnxruntime as ort
except Exception:  # pragma: no cover
    ort = None


@dataclass
class MatchDecision:
    student_id: int | None
    student_name: str | None
    recognition_score: float
    decision: str
    reason: str


@dataclass
class FaceResult:
    x: int
    y: int
    width: int
    height: int
    label: str
    type: str
    student_id: int | None = None
    name: str | None = None
    recognition_score: float = 0.0
    liveness_score: float = 0.0
    recognizer_version: str = ""
    liveness_version: str = ""
    detector_version: str = ""
    decision: str = ""
    reason: str = ""
    challenge_required: bool = False
    challenge_message: str = ""
    challenge_progress: float = 0.0
    pose_nose_offset: float | None = None
    face_area_ratio: float | None = None

    def to_dict(self) -> dict:
        payload = _normalize_json_value(asdict(self))
        payload["confidence"] = self.recognition_score
        payload["antispoofing_confidence"] = self.liveness_score
        return payload


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _coerce_optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _coerce_optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _as_embedding_sequence(value: Any) -> Sequence[float] | None:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return cast(Sequence[float], value.tolist())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(Sequence[float], value)
    return None


class PassiveLivenessEvaluator:
    def __init__(self) -> None:
        self._session = None
        self._input_name = None
        self._profile = runtime_model_registry.current_profile()
        self._crop_scale = float(os.getenv("LIVENESS_FACE_CROP_SCALE", "2.7"))
        self._hard_spoof_threshold = float(os.getenv("LIVENESS_HARD_SPOOF_THRESHOLD", "0.995"))
        self._load_session()

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        shifted = logits - np.max(logits)
        exp = np.exp(shifted)
        return exp / np.sum(exp)

    def _load_session(self) -> None:
        model_path = runtime_model_registry.liveness_model_path()
        if ort is None or model_path is None or not model_path.exists():
            return
        try:
            self._session = ort.InferenceSession(str(model_path), providers=ort.get_available_providers())
            self._input_name = self._session.get_inputs()[0].name
        except Exception:
            self._session = None
            self._input_name = None

    def evaluate(self, image: np.ndarray, face_bbox: list[int], frame_history: list[np.ndarray] | None):
        fallback = check_liveness_balanced(image, face_bbox, frame_history=frame_history)
        if self._session is None or self._input_name is None:
            fallback["model_version"] = self._profile.liveness_version
            fallback["challenge_required"] = (
                self._profile.challenge_response_enabled
                and fallback["is_live"]
                and float(fallback.get("confidence", 0.0)) < self._profile.liveness_threshold
            )
            return fallback

        try:
            x, y, w, h = face_bbox
            h_img, w_img = image.shape[:2]
            center_x = x + (w / 2.0)
            center_y = y + (h / 2.0)
            crop_size = max(w, h) * self._crop_scale
            half_size = crop_size / 2.0
            x1 = max(0, min(int(round(center_x - half_size)), w_img - 1))
            y1 = max(0, min(int(round(center_y - half_size)), h_img - 1))
            x2 = max(0, min(int(round(center_x + half_size)), w_img))
            y2 = max(0, min(int(round(center_y + half_size)), h_img))
            face = image[y1:y2, x1:x2]
            if face.size == 0:
                raise ValueError("empty face crop")
            resized = cv2.resize(face, (80, 80)).astype(np.float32) / 255.0
            tensor = np.transpose(resized, (2, 0, 1))[None, ...]
            output = self._session.run(None, {self._input_name: tensor})[0]
            logits = np.asarray(output, dtype=np.float32).reshape(-1)
            probabilities = self._softmax(logits)
            if probabilities.shape[0] < 3:
                raise ValueError(f"unexpected MiniFASNet output shape: {probabilities.shape}")

            live_score = float(probabilities[0])
            print_score = float(probabilities[1])
            replay_score = float(probabilities[2])
            spoof_score = print_score + replay_score

            spoofing_detection = fallback.get("spoofing_detection", {})
            heuristic_attack_detected = bool(
                spoofing_detection.get("screen_detected") or spoofing_detection.get("photo_detected")
            )
            motion_required = bool(os.getenv("REQUIRE_MOTION_FOR_ATTENDANCE", "true").lower() in {"true", "1", "t"})
            insufficient_motion = motion_required and not bool(spoofing_detection.get("sufficient_motion", False))
            fallback_live = bool(fallback.get("is_live", False))
            hard_model_spoof = spoof_score >= self._hard_spoof_threshold
            model_supports_live = (
                live_score >= self._profile.liveness_threshold
                and live_score > max(print_score, replay_score)
            )
            soft_live_signal = fallback_live or (
                bool(spoofing_detection.get("sufficient_motion", False))
                and not heuristic_attack_detected
            )

            if heuristic_attack_detected:
                is_live = False
                challenge_required = False
                reason = "heuristic_replay_detected"
            elif insufficient_motion:
                is_live = False
                challenge_required = False
                reason = "motion_required_for_liveness"
            elif model_supports_live:
                is_live = True
                challenge_required = self._profile.challenge_response_enabled and live_score < 0.9
                reason = "passive_model_live"
            elif soft_live_signal and not hard_model_spoof:
                is_live = True
                challenge_required = self._profile.challenge_response_enabled
                reason = "heuristic_live_challenge_required"
            elif soft_live_signal and hard_model_spoof:
                is_live = True
                challenge_required = self._profile.challenge_response_enabled
                reason = "model_spoof_but_heuristic_live_challenge_required"
            else:
                is_live = False
                challenge_required = False
                reason = "passive_model_spoof"

            logger.info(
                "PAD fused decision: live={} challenge_required={} reason={} live={:.3f} print={:.3f} replay={:.3f} heuristic_live={} soft_live_signal={} heuristic_screen={} heuristic_photo={} sufficient_motion={}",
                is_live,
                challenge_required,
                reason,
                live_score,
                print_score,
                replay_score,
                fallback_live,
                soft_live_signal,
                spoofing_detection.get("screen_detected"),
                spoofing_detection.get("photo_detected"),
                spoofing_detection.get("sufficient_motion"),
            )

            return {
                "is_live": is_live,
                "confidence": live_score,
                "reason": reason,
                "model_version": self._profile.liveness_version,
                "challenge_required": challenge_required,
                "class_probabilities": {
                    "live": live_score,
                    "print_attack": print_score,
                    "replay_attack": replay_score,
                    "spoof_total": spoof_score,
                },
                "fallback": fallback,
            }
        except Exception:
            fallback["model_version"] = self._profile.liveness_version
            fallback["challenge_required"] = (
                self._profile.challenge_response_enabled
                and fallback["is_live"]
                and float(fallback.get("confidence", 0.0)) < self._profile.liveness_threshold
            )
            return fallback


class UnifiedInferenceService:
    def __init__(self) -> None:
        self.liveness = PassiveLivenessEvaluator()

    def profile(self) -> ModelProfile:
        return runtime_model_registry.current_profile()

    def collect_embeddings(self, student: Student) -> list[np.ndarray]:
        embeddings: list[np.ndarray] = []
        student_embedding = _as_embedding_sequence(getattr(student, "face_embedding_array", None))
        if student_embedding is not None and len(student_embedding) > 0:
            embeddings.append(np.array(student_embedding, dtype=np.float32))
        for image in student.face_images:
            image_embedding = _as_embedding_sequence(getattr(image, "face_embedding_array", None))
            if image_embedding is not None and len(image_embedding) > 0:
                embeddings.append(np.array(image_embedding, dtype=np.float32))
        return embeddings

    def aggregate_embedding(self, embeddings: Iterable[np.ndarray]) -> np.ndarray | None:
        items = [embedding for embedding in embeddings if embedding is not None and embedding.size > 0]
        if not items:
            return None
        stacked = np.stack(items)
        mean_embedding = np.mean(stacked, axis=0)
        norm = np.linalg.norm(mean_embedding)
        return mean_embedding / norm if norm > 0 else None

    def enrollment_quality_issues(self, image: np.ndarray, face_bbox: list[int]) -> list[str]:
        x, y, w, h = face_bbox
        crop = image[max(0, y): max(0, y + h), max(0, x): max(0, x + w)]
        issues: list[str] = []
        if crop.size == 0:
            return ["invalid_face_crop"]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()
        brightness = float(np.mean(gray))
        if blur < 50:
            issues.append("blur")
        if brightness < 35 or brightness > 220:
            issues.append("lighting")
        if w < 80 or h < 80:
            issues.append("small_face")
        return issues

    def _match_embedding(self, embedding: np.ndarray, students: Iterable[Student]) -> MatchDecision:
        profile = self.profile()
        best_student = None
        best_score = -1.0
        second_best = -1.0
        for student in students:
            student_model_version = _coerce_optional_str(getattr(student, "embedding_model_version", None))
            if student_model_version and student_model_version != profile.recognizer_version:
                continue
            template = self.aggregate_embedding(self.collect_embeddings(student))
            if template is None:
                continue
            score = float(np.dot(embedding, template))
            if score > best_score:
                second_best = best_score
                best_score = score
                best_student = student
            elif score > second_best:
                second_best = score

        if best_student is None:
            return MatchDecision(None, None, 0.0, "unknown", "no_enrolled_templates")
        if best_score < profile.recognizer_threshold:
            return MatchDecision(None, None, best_score, "unknown", "below_recognition_threshold")
        if (best_score - second_best) < profile.verification_margin:
            return MatchDecision(None, None, best_score, "uncertain", "verification_margin_too_small")
        return MatchDecision(
            _coerce_optional_int(getattr(best_student, "id", None)),
            _coerce_optional_str(getattr(best_student, "name", None)),
            best_score,
            "matched",
            "recognized",
        )

    def _estimate_nose_offset(self, face) -> float | None:
        if not hasattr(face, "kps") or face.kps is None:
            return None
        try:
            keypoints = np.asarray(face.kps, dtype=np.float32)
            if keypoints.shape[0] < 3:
                return None
            left_eye = keypoints[0]
            right_eye = keypoints[1]
            nose = keypoints[2]
            eye_distance = float(np.linalg.norm(right_eye - left_eye))
            if eye_distance <= 1e-6:
                return None
            eye_mid_x = float((left_eye[0] + right_eye[0]) / 2.0)
            return float((float(nose[0]) - eye_mid_x) / eye_distance)
        except Exception:
            return None

    @staticmethod
    def _estimate_face_area_ratio(face_bbox: list[int], image_shape: tuple[int, ...]) -> float | None:
        try:
            _, _, width, height = face_bbox
            frame_height, frame_width = image_shape[:2]
            frame_area = float(frame_width * frame_height)
            if frame_area <= 1e-6:
                return None
            face_area = float(max(width, 0) * max(height, 0))
            return face_area / frame_area
        except Exception:
            return None

    def analyze_faces(
        self,
        image: np.ndarray,
        *,
        students: Iterable[Student],
        attendance_running: bool,
        frame_history: list[np.ndarray] | None,
    ) -> tuple[list[FaceResult], dict[str, Any]]:
        profile = self.profile()
        insight_app = ensure_insightface_initialized()
        if insight_app is None:
            envelope = self._empty_envelope(profile, reason="recognition_model_unavailable")
            return [], envelope

        processed = preprocess_image(image)
        faces = insight_app.get(processed)
        if not faces:
            envelope = self._empty_envelope(profile, reason="no_faces_detected")
            envelope["faces_detected"] = 0
            return [], envelope

        results: list[FaceResult] = []
        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox
            face_bbox = [x1, y1, x2 - x1, y2 - y1]
            pose_nose_offset = self._estimate_nose_offset(face)
            face_area_ratio = self._estimate_face_area_ratio(face_bbox, processed.shape)
            liveness = self.liveness.evaluate(processed, face_bbox, frame_history)
            liveness_score = float(liveness.get("confidence", 0.0))
            if not liveness.get("is_live", False):
                results.append(
                    FaceResult(
                        x=x1,
                        y=y1,
                        width=x2 - x1,
                        height=y2 - y1,
                        label="Spoofing detected",
                        type="spoofing_detected",
                        liveness_score=liveness_score,
                        recognizer_version=profile.recognizer_version,
                        liveness_version=str(liveness.get("model_version", profile.liveness_version)),
                        detector_version=profile.detector_version,
                        decision="blocked",
                        reason=str(liveness.get("reason", "liveness_failed")),
                        pose_nose_offset=pose_nose_offset,
                        face_area_ratio=face_area_ratio,
                    )
                )
                continue

            if not hasattr(face, "embedding") or face.embedding is None:
                results.append(
                    FaceResult(
                        x=x1,
                        y=y1,
                        width=x2 - x1,
                        height=y2 - y1,
                        label="Live person - embedding unavailable",
                        type="unknown_face_live",
                        liveness_score=liveness_score,
                        recognizer_version=profile.recognizer_version,
                        liveness_version=str(liveness.get("model_version", profile.liveness_version)),
                        detector_version=profile.detector_version,
                        decision="unknown",
                        reason="embedding_unavailable",
                        pose_nose_offset=pose_nose_offset,
                        face_area_ratio=face_area_ratio,
                    )
                )
                continue

            embedding = face.embedding / np.linalg.norm(face.embedding)
            decision = self._match_embedding(embedding, students)
            challenge_required = bool(liveness.get("challenge_required", False))
            face_type = "attendance_candidate" if decision.student_id and attendance_running else "unknown_face_live"
            label = decision.student_name or "Live person - unknown face"
            if challenge_required and decision.student_id:
                face_type = "challenge_required"
                label = f"{decision.student_name} - confirm liveness"
            elif decision.decision == "uncertain":
                face_type = "uncertain_match"
                label = "Live person - uncertain match"
            elif decision.student_id and attendance_running:
                face_type = "attendance_candidate"
                label = decision.student_name or "Attendance candidate"

            results.append(
                FaceResult(
                    x=x1,
                    y=y1,
                    width=x2 - x1,
                    height=y2 - y1,
                    label=label,
                    type=face_type,
                    student_id=decision.student_id,
                    name=decision.student_name,
                    recognition_score=decision.recognition_score,
                    liveness_score=liveness_score,
                    recognizer_version=profile.recognizer_version,
                    liveness_version=str(liveness.get("model_version", profile.liveness_version)),
                    detector_version=profile.detector_version,
                    decision=decision.decision,
                    reason=decision.reason,
                    challenge_required=challenge_required,
                    pose_nose_offset=pose_nose_offset,
                    face_area_ratio=face_area_ratio,
                )
            )

        envelope = self._empty_envelope(profile, reason="processed")
        envelope["faces_detected"] = len(results)
        envelope["faces"] = [result.to_dict() for result in results]
        if results:
            envelope["recognition_score"] = max(result.recognition_score for result in results)
            envelope["liveness_score"] = max(result.liveness_score for result in results)
            primary = max(results, key=lambda item: (item.student_id is not None, item.recognition_score, item.liveness_score))
            envelope["decision"] = primary.decision
            envelope["reason"] = primary.reason
        return results, _normalize_json_value(envelope)

    def _empty_envelope(self, profile: ModelProfile, *, reason: str) -> dict:
        return {
            "faces": [],
            "faces_detected": 0,
            "recognition_score": 0.0,
            "liveness_score": 0.0,
            "recognizer_version": profile.recognizer_version,
            "liveness_version": profile.liveness_version,
            "detector_version": profile.detector_version,
            "decision": "none",
            "reason": reason,
        }


_service: UnifiedInferenceService | None = None


def get_inference_service() -> UnifiedInferenceService:
    global _service
    if _service is None:
        _service = UnifiedInferenceService()
    return _service
