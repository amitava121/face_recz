from __future__ import annotations

import random
from datetime import datetime, timezone

import cv2
import numpy as np
from loguru import logger
from sqlalchemy import func

from src.models.db import Attendance, FaceImage, Student, db
from src.services.face_utils import ensure_insightface_initialized, preprocess_image
from src.services.inference_service import get_inference_service
from src.web.timezone_utils import local_day_bounds, today_local


CHALLENGE_EXPIRY_SECONDS = 15

# Simple in-memory cache for enrolled students to avoid repeated DB queries
_students_cache = None
_students_cache_time = 0
_STUDENTS_CACHE_TTL = 30  # seconds


POSE_CHALLENGE_DELTA = 0.12
DEPTH_CHALLENGE_RATIO = 0.18


def _get_enrolled_students():
    """Return students with face embeddings, using a short-lived cache."""
    global _students_cache, _students_cache_time
    now = datetime.now(timezone.utc).timestamp()
    if _students_cache is None or (now - _students_cache_time) > _STUDENTS_CACHE_TTL:
        _students_cache = Student.query.filter(Student.face_embedding_array.isnot(None)).all()
        _students_cache_time = now
    return _students_cache


def decode_image_bytes(image_data: bytes):
    nparr = np.frombuffer(image_data, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def process_registration_image(img, student_id: int, registration_complete: dict[int, bool]):
    student = Student.query.get(student_id)
    if not student:
        return {"error": "Student not found"}, 404

    service = get_inference_service()
    profile = service.profile()
    existing_images = FaceImage.query.filter_by(student_id=student_id).count()
    if existing_images >= profile.enrollment_samples:
        registration_complete[student_id] = True
        return {
            "success": True,
            "message": "Registration complete",
            "student_id": student_id,
            "capture_count": existing_images,
            "images_collected": existing_images,
            "accepted_samples": existing_images,
            "rejected_samples": 0,
            "template_version": profile.recognizer_version,
            "registration_complete": True,
            "faces_detected": 0,
        }, 200

    envelope = service.analyze_faces(
        img,
        students=[],
        attendance_running=False,
        frame_history=None,
    )[1]
    faces = envelope["faces"]
    if not faces:
        return {
            "error": "No face detected",
            "rejected_samples": 1,
            "accepted_samples": existing_images,
            "template_version": profile.recognizer_version,
            "reason": envelope["reason"],
        }, 400
    if len(faces) != 1:
        return {
            "error": "Please ensure only one face is visible",
            "rejected_samples": 1,
            "accepted_samples": existing_images,
            "template_version": profile.recognizer_version,
        }, 400

    face_info = faces[0]
    if face_info["type"] == "spoofing_detected":
        return {
            "error": "Liveness check failed",
            "images_collected": existing_images,
            "accepted_samples": existing_images,
            "rejected_samples": 1,
            "template_version": profile.recognizer_version,
            "reason": face_info["reason"],
            "liveness_score": face_info["liveness_score"],
        }, 400

    insight_app = ensure_insightface_initialized()
    processed = preprocess_image(img)
    detected = insight_app.get(processed) if insight_app else []
    if not detected or not hasattr(detected[0], "embedding") or detected[0].embedding is None:
        return {"error": "Failed to extract face embedding"}, 500

    bbox = detected[0].bbox.astype(int)
    x1, y1, x2, y2 = bbox
    quality_issues = service.enrollment_quality_issues(processed, [x1, y1, x2 - x1, y2 - y1])
    if quality_issues:
        return {
            "error": "Enrollment quality check failed",
            "accepted_samples": existing_images,
            "rejected_samples": 1,
            "template_version": profile.recognizer_version,
            "reason": ",".join(quality_issues),
        }, 400

    embedding = detected[0].embedding / np.linalg.norm(detected[0].embedding)
    h, w = img.shape[:2]
    x1 = max(0, min(int(x1), w - 1))
    x2 = max(0, min(int(x2), w))
    y1 = max(0, min(int(y1), h - 1))
    y2 = max(0, min(int(y2), h))
    face_img = img[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None
    if face_img is not None and face_img.size > 0:
        face_img = cv2.resize(face_img, (112, 112))
        encoded = cv2.imencode(".jpg", face_img)[1].tobytes()
        db.session.add(
            FaceImage(
                student_id=student_id,
                image_data=encoded,
                face_embedding_array=embedding.tolist(),
                embedding_model_version=profile.recognizer_version,
                detector_version=profile.detector_version,
                liveness_model_version=profile.liveness_version,
                liveness_score=float(face_info["liveness_score"]),
                decision_reason=face_info["reason"],
                template_version=profile.recognizer_version,
            )
        )

    embeddings = [np.array(face_image.face_embedding_array, dtype=np.float32) for face_image in student.face_images if face_image.face_embedding_array]
    embeddings.append(embedding.astype(np.float32))
    aggregated = service.aggregate_embedding(embeddings)
    student.face_embedding_array = aggregated.tolist() if aggregated is not None else embedding.tolist()
    student.embedding_model_version = profile.recognizer_version
    student.detector_version = profile.detector_version
    student.template_version = profile.recognizer_version
    db.session.commit()

    new_count = existing_images + 1
    is_complete = new_count >= profile.enrollment_samples
    if is_complete:
        registration_complete[student_id] = True
    return {
        "success": True,
        "message": "Registration complete" if is_complete else "Face captured",
        "student_id": student_id,
        "capture_count": new_count,
        "images_collected": new_count,
        "accepted_samples": new_count,
        "rejected_samples": 0,
        "images_needed": profile.enrollment_samples,
        "progress": int((new_count / profile.enrollment_samples) * 100),
        "template_version": profile.recognizer_version,
        "registration_complete": is_complete,
        "recognizer_version": profile.recognizer_version,
        "liveness_version": profile.liveness_version,
        "detector_version": profile.detector_version,
    }, 200


def _issue_or_update_challenge(face: dict, active_challenges: dict[int, dict]) -> dict:
    student_id = face.get("student_id")
    if not student_id:
        return face

    now = datetime.now(timezone.utc).timestamp()
    challenge = active_challenges.get(student_id)
    pose_nose_offset = face.get("pose_nose_offset")
    face_area_ratio = face.get("face_area_ratio")

    if challenge and now > challenge.get("expires_at", 0):
        active_challenges.pop(student_id, None)
        challenge = None

    if challenge is None:
        challenge_type = "turn_sideways"
        baseline_pose = pose_nose_offset
        baseline_area = face_area_ratio
        instruction = "Turn your face slightly left or right"
        required_delta = POSE_CHALLENGE_DELTA

        if pose_nose_offset is not None:
            instruction = random.choice(
                [
                    "Turn your face slightly left or right",
                    "Rotate your head a little to one side",
                ]
            )
        elif face_area_ratio is not None and face_area_ratio > 0:
            challenge_type = "move_closer"
            instruction = "Move your face a little closer to the camera"
            required_delta = DEPTH_CHALLENGE_RATIO

        challenge = {
            "student_id": student_id,
            "type": challenge_type,
            "issued_at": now,
            "expires_at": now + CHALLENGE_EXPIRY_SECONDS,
            "baseline_nose_offset": baseline_pose,
            "baseline_face_area_ratio": baseline_area,
            "required_delta": POSE_CHALLENGE_DELTA,
            "instruction": instruction,
        }
        challenge["required_delta"] = required_delta
        active_challenges[student_id] = challenge

    progress = 0.0
    if challenge["type"] == "move_closer":
        baseline_area = challenge.get("baseline_face_area_ratio")
        if baseline_area is not None and face_area_ratio is not None and float(baseline_area) > 1e-6:
            progress = max(0.0, float(face_area_ratio) - float(baseline_area))
            if progress >= challenge["required_delta"]:
                active_challenges.pop(student_id, None)
                face["challenge_required"] = False
                face["challenge_message"] = "Liveness challenge passed"
                face["challenge_progress"] = 1.0
                face["reason"] = "challenge_completed"
                face["label"] = face.get("name") or face.get("label") or "Attendance candidate"
                face["type"] = "attendance_candidate"
                return face
            face["challenge_progress"] = min(1.0, progress / challenge["required_delta"])
        else:
            face["challenge_progress"] = 0.0
    else:
        baseline = challenge.get("baseline_nose_offset")
        if baseline is not None and pose_nose_offset is not None:
            progress = abs(float(pose_nose_offset) - float(baseline))
            if progress >= challenge["required_delta"]:
                active_challenges.pop(student_id, None)
                face["challenge_required"] = False
                face["challenge_message"] = "Liveness challenge passed"
                face["challenge_progress"] = 1.0
                face["reason"] = "challenge_completed"
                face["label"] = face.get("name") or face.get("label") or "Attendance candidate"
                face["type"] = "attendance_candidate"
                return face
            face["challenge_progress"] = min(1.0, progress / challenge["required_delta"])
        else:
            face["challenge_progress"] = 0.0

    face["challenge_required"] = True
    face["type"] = "challenge_required"
    face["reason"] = "active_liveness_challenge_required"
    face["challenge_message"] = challenge["instruction"]
    face["label"] = challenge["instruction"]
    face["challenge_type"] = challenge["type"]
    return face


def process_attendance_image(img, attendance_running: bool, active_challenges: dict[int, dict] | None = None):
    if not attendance_running:
        return {"error": "Attendance not running"}, 400

    service = get_inference_service()
    students = _get_enrolled_students()
    _, envelope = service.analyze_faces(
        img,
        students=students,
        attendance_running=attendance_running,
        frame_history=None,
    )

    if envelope["faces_detected"] > 1:
        for face in envelope["faces"]:
            if face.get("type") == "spoofing_detected":
                continue
            face["type"] = "multiple_faces_blocked"
            face["decision"] = "blocked"
            face["reason"] = "multiple_faces_detected"
            face["challenge_required"] = False
            face["challenge_message"] = ""
            face["challenge_progress"] = 0.0
            face["label"] = "Only one face allowed"
        envelope["decision"] = "blocked"
        envelope["reason"] = "multiple_faces_detected"
        return envelope, 200

    if active_challenges is None:
        active_challenges = {}

    for face in envelope["faces"]:
        if face.get("student_id") and face.get("challenge_required"):
            _issue_or_update_challenge(face, active_challenges)

    today = today_local()
    day_start_utc, day_end_utc = local_day_bounds(today)
    for face in envelope["faces"]:
        if not face.get("student_id") or face.get("challenge_required"):
            continue
        if face["decision"] != "matched":
            continue
        existing_attendance = Attendance.query.filter(
            Attendance.student_id == face["student_id"],
            Attendance.timestamp >= day_start_utc,
            Attendance.timestamp <= day_end_utc,
        ).first()
        if existing_attendance:
            face["label"] = "Already Marked Today"
            face["type"] = "already_marked_today"
            face["reason"] = "duplicate_attendance_window"
            continue
        student = Student.query.get(face["student_id"])
        attendance = Attendance(
            student_id=face["student_id"],
            timestamp=datetime.now(timezone.utc),
            student_code=student.student_code if student else None,
            student_name=student.name if student else None,
            student_department=student.department if student else None,
            confidence_score=float(face["recognition_score"]),
            embedding_model_version=face["recognizer_version"],
            detector_version=face["detector_version"],
            liveness_model_version=face["liveness_version"],
            liveness_score=float(face["liveness_score"]),
            decision_reason=face["reason"],
        )
        db.session.add(attendance)
        db.session.commit()
        face["label"] = "Attendance Marked"
        face["type"] = "attendance_marked"
        face["name"] = student.name if student else face.get("name")

    if envelope["faces"]:
        primary = max(envelope["faces"], key=lambda item: (item.get("student_id") is not None, item.get("recognition_score", 0.0)))
        envelope["decision"] = primary.get("decision", envelope["decision"])
        envelope["reason"] = primary.get("reason", envelope["reason"])
    return envelope, 200


def register_face_embedding(student_id: int, embedding: list[float], registration_complete: dict[int, bool]):
    student = Student.query.get(student_id)
    if not student:
        return {"error": f"Student with ID {student_id} not found"}, 404

    service = get_inference_service()
    profile = service.profile()
    embedding_array = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(embedding_array)
    if norm > 0:
        embedding_array = embedding_array / norm
    student.face_embedding_array = embedding_array.tolist()
    student.embedding_model_version = profile.recognizer_version
    student.detector_version = profile.detector_version
    student.template_version = profile.recognizer_version
    db.session.commit()
    registration_complete[student_id] = True
    logger.info(f"Successfully registered face for student {student_id}")
    return {
        "success": True,
        "student_id": student_id,
        "message": "Face registered successfully",
        "template_version": profile.recognizer_version,
    }, 200


def mark_attendance_for_student(
    student_id: int,
    confidence: float = 0.0,
    device_id: str | None = None,
    *,
    liveness_score: float | None = None,
    detector_version: str | None = None,
    embedding_model_version: str | None = None,
    liveness_model_version: str | None = None,
    decision_reason: str | None = None,
):
    student = Student.query.get(student_id)
    if not student:
        return {"error": f"Student with ID {student_id} not found"}, 404

    created, record = Attendance.mark_attendance(
        db.session,
        student_id,
        confidence=confidence,
        device_id=device_id,
        min_interval_minutes=10,
        embedding_model_version=embedding_model_version,
        detector_version=detector_version,
        liveness_model_version=liveness_model_version,
        liveness_score=liveness_score,
        decision_reason=decision_reason,
    )
    payload = {
        "success": created,
        "student_id": student_id,
        "name": student.name,
        "message": "Attendance marked successfully" if created else "Attendance already marked today",
        "confidence": confidence,
        "recognizer_version": embedding_model_version,
        "liveness_version": liveness_model_version,
        "detector_version": detector_version,
        "liveness_score": liveness_score,
        "reason": decision_reason,
    }
    if created:
        payload["timestamp"] = record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return payload, 200
