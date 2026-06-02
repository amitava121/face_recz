import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _jpeg_bytes() -> bytes:
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_face_result_to_dict_normalizes_numpy_scalars():
    from src.services.inference_service import FaceResult

    result = FaceResult(
        x=np.int64(10),
        y=np.int64(20),
        width=np.int64(30),
        height=np.int64(40),
        label="Live person",
        type="attendance_candidate",
        student_id=np.int64(7),
        recognition_score=0.91,
        liveness_score=0.88,
        recognizer_version="buffalo_l:recognizer",
        liveness_version="minifasnet_v2:onnx",
        detector_version="buffalo_l:detector",
        decision="matched",
        reason="recognized",
    )

    payload = result.to_dict()

    assert payload["x"] == 10
    assert isinstance(payload["x"], int)
    assert payload["student_id"] == 7
    assert isinstance(payload["student_id"], int)


def test_passive_liveness_evaluator_uses_softmax_and_blocks_screen_replay(monkeypatch):
    import numpy as np

    from src.services import inference_service

    monkeypatch.setenv("REQUIRE_MOTION_FOR_ATTENDANCE", "True")
    monkeypatch.setattr(
        inference_service,
        "check_liveness_balanced",
        lambda image, face_bbox, frame_history=None: {
            "is_live": True,
            "confidence": 0.92,
            "spoofing_detection": {
                "screen_detected": True,
                "photo_detected": False,
                "sufficient_motion": False,
            },
        },
    )

    class FakeSession:
        def get_inputs(self):
            class Input:
                name = "input"
            return [Input()]

        def run(self, _outputs, _inputs):
            return [np.array([[8.0, 1.0, 1.0]], dtype=np.float32)]

    monkeypatch.setattr(inference_service.PassiveLivenessEvaluator, "_load_session", lambda self: None)

    evaluator = inference_service.PassiveLivenessEvaluator()
    evaluator._session = FakeSession()
    evaluator._input_name = "input"

    result = evaluator.evaluate(np.zeros((160, 160, 3), dtype=np.uint8), [20, 20, 60, 60], frame_history=[])

    assert result["is_live"] is False
    assert result["reason"] == "heuristic_replay_detected"
    assert result["class_probabilities"]["live"] > result["class_probabilities"]["replay_attack"]


def test_passive_liveness_evaluator_prefers_challenge_over_hard_block_for_real_face_signal(monkeypatch):
    import numpy as np

    from src.services import inference_service

    monkeypatch.setenv("REQUIRE_MOTION_FOR_ATTENDANCE", "True")
    monkeypatch.setattr(
        inference_service,
        "check_liveness_balanced",
        lambda image, face_bbox, frame_history=None: {
            "is_live": True,
            "confidence": 0.88,
            "spoofing_detection": {
                "screen_detected": False,
                "photo_detected": False,
                "sufficient_motion": True,
            },
        },
    )

    class FakeSession:
        def get_inputs(self):
            class Input:
                name = "input"
            return [Input()]

        def run(self, _outputs, _inputs):
            return [np.array([[0.0, 1.0, 6.0]], dtype=np.float32)]

    monkeypatch.setattr(inference_service.PassiveLivenessEvaluator, "_load_session", lambda self: None)

    evaluator = inference_service.PassiveLivenessEvaluator()
    evaluator._session = FakeSession()
    evaluator._input_name = "input"

    result = evaluator.evaluate(np.zeros((160, 160, 3), dtype=np.uint8), [20, 20, 60, 60], frame_history=[])

    assert result["is_live"] is True
    assert result["challenge_required"] is True
    assert result["reason"] == "model_spoof_but_heuristic_live_challenge_required"


def test_passive_liveness_evaluator_allows_motion_signal_to_reach_challenge(monkeypatch):
    import numpy as np

    from src.services import inference_service

    monkeypatch.setenv("REQUIRE_MOTION_FOR_ATTENDANCE", "True")
    monkeypatch.setattr(
        inference_service,
        "check_liveness_balanced",
        lambda image, face_bbox, frame_history=None: {
            "is_live": False,
            "confidence": 0.30,
            "spoofing_detection": {
                "screen_detected": False,
                "photo_detected": False,
                "sufficient_motion": True,
            },
        },
    )

    class FakeSession:
        def get_inputs(self):
            class Input:
                name = "input"
            return [Input()]

        def run(self, _outputs, _inputs):
            return [np.array([[0.0, 1.0, 6.0]], dtype=np.float32)]

    monkeypatch.setattr(inference_service.PassiveLivenessEvaluator, "_load_session", lambda self: None)

    evaluator = inference_service.PassiveLivenessEvaluator()
    evaluator._session = FakeSession()
    evaluator._input_name = "input"

    result = evaluator.evaluate(np.zeros((160, 160, 3), dtype=np.uint8), [20, 20, 60, 60], frame_history=[])

    assert result["is_live"] is True
    assert result["challenge_required"] is True
    assert result["reason"] == "model_spoof_but_heuristic_live_challenge_required"


def test_simple_anti_spoofing_detector_accepts_realistic_webcam_metrics(monkeypatch):
    from src.services import anti_spoofing

    monkeypatch.setenv("MOTION_THRESHOLD", "400")
    monkeypatch.setenv("CONSECUTIVE_FRAMES_REQUIRED", "3")
    monkeypatch.setattr(anti_spoofing, "get_insightface_model", lambda: None)
    detector = anti_spoofing.SimpleAntiSpoofingDetector()
    detector.use_ai_model = False
    monkeypatch.setattr(detector, "calculate_motion", lambda *_args, **_kwargs: 650.0)
    monkeypatch.setattr(
        detector,
        "analyze_face_quality",
        lambda _face_region: {
            "brightness": 118.0,
            "contrast": 22.0,
            "sharpness": 92.0,
            "uniformity": 6.3,
            "edge_density": 0.038,
            "texture_complexity": 7.4,
            "color_variance": 18.0,
            "frequency_energy": 3.0,
            "gradient_magnitude": 6.5,
        },
    )

    image = np.full((160, 160, 3), 120, dtype=np.uint8)
    for _ in range(detector.required_consecutive_frames + 1):
        result = detector.detect_spoofing(image, [20, 20, 80, 80])

    assert result["is_live"] is True
    assert result["spoofing_detection"]["screen_detected"] is False


def test_simple_anti_spoofing_detector_does_not_flag_dark_real_face_as_screen(monkeypatch):
    from src.services import anti_spoofing

    monkeypatch.setenv("MOTION_THRESHOLD", "400")
    monkeypatch.setattr(anti_spoofing, "get_insightface_model", lambda: None)
    detector = anti_spoofing.SimpleAntiSpoofingDetector()
    detector.use_ai_model = False
    monkeypatch.setattr(detector, "calculate_motion", lambda *_args, **_kwargs: 650.0)
    monkeypatch.setattr(
        detector,
        "analyze_face_quality",
        lambda _face_region: {
            "brightness": 90.0,
            "contrast": 21.0,
            "sharpness": 75.0,
            "uniformity": 6.0,
            "edge_density": 0.028,
            "texture_complexity": 5.6,
            "color_variance": 14.0,
            "frequency_energy": 3.0,
            "gradient_magnitude": 5.0,
        },
    )

    image = np.full((160, 160, 3), 90, dtype=np.uint8)
    result = detector.detect_spoofing(image, [20, 20, 80, 80])

    assert result["spoofing_detection"]["screen_detected"] is False


@pytest.mark.asyncio
async def test_process_image_attendance_returns_canonical_envelope(monkeypatch):
    from src.web.app import create_app
    from src.web.routes import attendance as attendance_routes

    monkeypatch.setattr(
        attendance_routes,
        "process_attendance_image",
        lambda img, attendance_running, active_challenges=None: (
            {
                "faces": [],
                "faces_detected": 0,
                "recognition_score": 0.0,
                "liveness_score": 0.0,
                "recognizer_version": "buffalo_l:recognizer",
                "liveness_version": "minifasnet_v2:heuristic-fallback",
                "detector_version": "buffalo_l:detector",
                "decision": "none",
                "reason": "no_faces_detected",
            },
            200,
        ),
    )

    app = create_app()
    app.state.attendance_running = True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/process_image",
            files={"image": ("frame.jpg", _jpeg_bytes(), "image/jpeg")},
            data={"mode": "attendance"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recognizer_version"] == "buffalo_l:recognizer"
    assert payload["liveness_version"] == "minifasnet_v2:heuristic-fallback"
    assert payload["detector_version"] == "buffalo_l:detector"
    assert payload["decision"] == "none"
    assert payload["reason"] == "no_faces_detected"


def test_process_attendance_image_requires_and_completes_active_challenge(monkeypatch):
    from src.web.services import attendance as attendance_service

    monkeypatch.setattr(attendance_service.random, "choice", lambda options: "turn_sideways")

    class StubQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

        def get(self, student_id):
            return type("StudentRow", (), {"id": student_id, "name": "Amit", "student_code": "S001", "department": "CSE"})()

    monkeypatch.setattr(attendance_service.Student, "query", StubQuery())
    monkeypatch.setattr(attendance_service.Attendance, "query", StubQuery())
    monkeypatch.setattr(attendance_service.db.session, "add", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(attendance_service.db.session, "commit", lambda: None)

    class StubService:
        def analyze_faces(self, img, *, students, attendance_running, frame_history):
            return [], {
                "faces": [
                    {
                        "student_id": 7,
                        "name": "Amit",
                        "type": "attendance_candidate",
                        "decision": "matched",
                        "reason": "recognized",
                        "recognition_score": 0.93,
                        "liveness_score": 0.84,
                        "recognizer_version": "buffalo_l:recognizer",
                        "liveness_version": "minifasnet_v2:onnx",
                        "detector_version": "buffalo_l:detector",
                        "challenge_required": True,
                        "pose_nose_offset": 0.02,
                        "face_area_ratio": 0.12,
                        "label": "Amit - confirm liveness",
                    }
                ],
                "faces_detected": 1,
                "recognition_score": 0.93,
                "liveness_score": 0.84,
                "recognizer_version": "buffalo_l:recognizer",
                "liveness_version": "minifasnet_v2:onnx",
                "detector_version": "buffalo_l:detector",
                "decision": "matched",
                "reason": "recognized",
            }

    monkeypatch.setattr(attendance_service, "get_inference_service", lambda: StubService())

    active_challenges = {}
    first_body, first_status = attendance_service.process_attendance_image(
        np.zeros((32, 32, 3), dtype=np.uint8),
        True,
        active_challenges,
    )

    assert first_status == 200
    assert first_body["faces"][0]["type"] == "challenge_required"
    assert active_challenges[7]["type"] == "turn_sideways"

    class CompletingStubService(StubService):
        def analyze_faces(self, img, *, students, attendance_running, frame_history):
            result = super().analyze_faces(img, students=students, attendance_running=attendance_running, frame_history=frame_history)
            result[1]["faces"][0]["pose_nose_offset"] = 0.20
            return result

    monkeypatch.setattr(attendance_service, "get_inference_service", lambda: CompletingStubService())

    second_body, second_status = attendance_service.process_attendance_image(
        np.zeros((32, 32, 3), dtype=np.uint8),
        True,
        active_challenges,
    )

    assert second_status == 200
    assert second_body["faces"][0]["challenge_required"] is False
    assert second_body["faces"][0]["reason"] == "challenge_completed"
    assert 7 not in active_challenges


def test_process_attendance_image_completes_move_closer_challenge(monkeypatch):
    from src.web.services import attendance as attendance_service

    monkeypatch.setattr(attendance_service.random, "choice", lambda options: "move_closer")

    class StubQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

        def get(self, student_id):
            return type("StudentRow", (), {"id": student_id, "name": "Amit", "student_code": "S001", "department": "CSE"})()

    monkeypatch.setattr(attendance_service.Student, "query", StubQuery())
    monkeypatch.setattr(attendance_service.Attendance, "query", StubQuery())
    monkeypatch.setattr(attendance_service.db.session, "add", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(attendance_service.db.session, "commit", lambda: None)

    class StubService:
        def __init__(self, area_ratio):
            self._area_ratio = area_ratio

        def analyze_faces(self, img, *, students, attendance_running, frame_history):
            return [], {
                "faces": [
                    {
                        "student_id": 7,
                        "name": "Amit",
                        "type": "attendance_candidate",
                        "decision": "matched",
                        "reason": "recognized",
                        "recognition_score": 0.93,
                        "liveness_score": 0.84,
                        "recognizer_version": "buffalo_l:recognizer",
                        "liveness_version": "minifasnet_v2:onnx",
                        "detector_version": "buffalo_l:detector",
                        "challenge_required": True,
                        "pose_nose_offset": None,
                        "face_area_ratio": self._area_ratio,
                        "label": "Amit - confirm liveness",
                    }
                ],
                "faces_detected": 1,
                "recognition_score": 0.93,
                "liveness_score": 0.84,
                "recognizer_version": "buffalo_l:recognizer",
                "liveness_version": "minifasnet_v2:onnx",
                "detector_version": "buffalo_l:detector",
                "decision": "matched",
                "reason": "recognized",
            }

    monkeypatch.setattr(attendance_service, "get_inference_service", lambda: StubService(0.10))
    active_challenges = {}
    first_body, first_status = attendance_service.process_attendance_image(
        np.zeros((32, 32, 3), dtype=np.uint8),
        True,
        active_challenges,
    )

    assert first_status == 200
    assert first_body["faces"][0]["challenge_type"] == "move_closer"
    assert active_challenges[7]["type"] == "move_closer"

    monkeypatch.setattr(attendance_service, "get_inference_service", lambda: StubService(0.32))
    second_body, second_status = attendance_service.process_attendance_image(
        np.zeros((32, 32, 3), dtype=np.uint8),
        True,
        active_challenges,
    )

    assert second_status == 200
    assert second_body["faces"][0]["challenge_required"] is False
    assert second_body["faces"][0]["reason"] == "challenge_completed"


@pytest.mark.asyncio
async def test_process_image_registration_returns_template_metadata(monkeypatch):
    from src.web.app import create_app
    from src.web.routes import attendance as attendance_routes

    monkeypatch.setattr(
        attendance_routes,
        "process_registration_image",
        lambda img, student_id, registration_complete: (
            {
                "success": True,
                "student_id": student_id,
                "images_collected": 4,
                "accepted_samples": 4,
                "rejected_samples": 0,
                "template_version": "buffalo_l:recognizer",
                "registration_complete": False,
            },
            200,
        ),
    )

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/process_image",
            files={"image": ("frame.jpg", _jpeg_bytes(), "image/jpeg")},
            data={"mode": "registration", "student_id": "123"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted_samples"] == 4
    assert payload["rejected_samples"] == 0
    assert payload["template_version"] == "buffalo_l:recognizer"
