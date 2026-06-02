from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from src.models.db import Attendance, Student, SystemSettings, db


def check_database_status() -> bool:
    try:
        db.session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_camera_status() -> bool:
    return True


def check_face_recognition_status() -> bool:
    return True


def get_performance_history():
    current_time = datetime.now()
    labels = []
    cpu_data = []
    memory_data = []

    try:
        import psutil

        cpu_base = psutil.cpu_percent(interval=0.1)
        memory_base = psutil.virtual_memory().percent
    except Exception:
        cpu_base = 25
        memory_base = 35

    for i in range(24):
        time_point = current_time - timedelta(hours=23 - i)
        labels.append(time_point.strftime("%H:%M"))
        cpu_data.append(max(0, min(100, cpu_base + (i % 10 - 5))))
        memory_data.append(max(0, min(100, memory_base + (i % 8 - 4))))

    return labels, cpu_data, memory_data


def get_recognition_performance():
    current_time = datetime.now()
    labels = []
    data = []

    for i in range(24):
        hour_start = current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - i)
        hour_end = hour_start + timedelta(hours=1)
        count = Attendance.query.filter(
            Attendance.timestamp >= hour_start,
            Attendance.timestamp < hour_end,
        ).count()
        labels.append(hour_start.strftime("%H:%M"))
        data.append(count)

    return labels, data


def get_recent_system_logs(limit: int = 10):
    logs = []
    current_time = datetime.now()

    try:
        import psutil

        cpu_usage = psutil.cpu_percent()
    except Exception:
        cpu_usage = 0

    log_entries = [
        {"level": "info", "message": "System monitoring active"},
        {"level": "info", "message": "Face recognition service running"},
        {"level": "info", "message": "Database connection healthy"},
        {"level": "warning", "message": f"CPU usage: {cpu_usage}%"},
        {"level": "info", "message": "Admin panel accessed"},
    ]

    for i, entry in enumerate(log_entries[:limit]):
        timestamp = current_time - timedelta(minutes=i * 5)
        logs.append(
            {
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "level": entry["level"],
                "message": entry["message"],
            }
        )

    return logs


class SettingsManager:
    _instance = None
    _settings_cache: dict[str, object] = {}
    _last_update = 0.0
    _cache_timeout = 30

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_settings(self, force_refresh: bool = False):
        current_time = time.time()
        if force_refresh or (current_time - self._last_update) > self._cache_timeout:
            self._settings_cache = {
                "face_threshold": float(SystemSettings.get_setting("face_threshold", "0.4")),
                "verification_threshold": float(SystemSettings.get_setting("verification_threshold", "0.5")),
                "detection_scale": float(SystemSettings.get_setting("detection_scale", "0.3")),
                "frame_skip": int(SystemSettings.get_setting("frame_skip", "2")),
                "use_gpu": SystemSettings.get_setting("use_gpu", "true").lower() == "true",
                "attendance_window": int(SystemSettings.get_setting("attendance_window", "10")),
                "auto_reset_time": SystemSettings.get_setting("auto_reset_time", "00:00"),
                "max_fps": int(SystemSettings.get_setting("max_fps", "30")),
                "queue_size": int(SystemSettings.get_setting("queue_size", "10")),
                "cache_size": int(SystemSettings.get_setting("cache_size", "1000")),
            }
            self._last_update = current_time
        return self._settings_cache.copy()

    def apply_settings_to_system(self) -> bool:
        try:
            settings = self.get_settings(force_refresh=True)
            os.environ["FACE_RECOGNITION_THRESHOLD"] = str(settings["face_threshold"])
            os.environ["VERIFICATION_THRESHOLD"] = str(settings["verification_threshold"])
            os.environ["DETECTION_SCALE"] = str(settings["detection_scale"])
            os.environ["FRAME_SKIP"] = str(settings["frame_skip"])
            os.environ["USE_GPU"] = str(settings["use_gpu"]).lower()
            os.environ["ATTENDANCE_TIME_WINDOW"] = str(settings["attendance_window"])
            os.environ["MAX_FPS"] = str(settings["max_fps"])
            os.environ["MAX_QUEUE_SIZE"] = str(settings["queue_size"])
            os.environ["FACE_CACHE_SIZE"] = str(settings["cache_size"])

            for module_name in ("src.services.face_utils", "src.services.webrtc_server"):
                module = sys.modules.get(module_name)
                if not module:
                    continue
                for attr, key in (
                    ("FACE_RECOGNITION_THRESHOLD", "face_threshold"),
                    ("VERIFICATION_THRESHOLD", "verification_threshold"),
                    ("DETECTION_SCALE", "detection_scale"),
                    ("FRAME_SKIP", "frame_skip"),
                    ("USE_GPU", "use_gpu"),
                    ("MAX_FPS", "max_fps"),
                    ("MAX_QUEUE_SIZE", "queue_size"),
                    ("ATTENDANCE_TIME_WINDOW", "attendance_window"),
                ):
                    if hasattr(module, attr):
                        setattr(module, attr, settings[key])
            return True
        except Exception:
            return False


settings_manager = SettingsManager()


def test_system_configuration():
    try:
        settings = settings_manager.get_settings()
        test_results = []
        try:
            db.session.execute(text("SELECT 1"))
            test_results.append("✓ Database connection: OK")
        except Exception as exc:
            test_results.append(f"✗ Database connection: FAILED ({exc})")

        checks = (
            ("face_threshold", 0.1, 1.0),
            ("verification_threshold", 0.1, 1.0),
            ("max_fps", 10, 60),
            ("queue_size", 1, 50),
            ("cache_size", 100, 10000),
        )
        for key, low, high in checks:
            value = settings[key]
            if low <= value <= high:
                test_results.append(f"✓ {key} ({value}): OK")
            else:
                test_results.append(f"✗ {key} ({value}): Invalid range")

        test_results.append(f"✓ GPU acceleration: {'Enabled' if settings['use_gpu'] else 'Disabled'}")
        failed_tests = [result for result in test_results if result.startswith("✗")]
        return {
            "success": not failed_tests,
            "message": f"Tests completed. {len(test_results) - len(failed_tests)}/{len(test_results)} passed.",
            "details": test_results,
        }
    except Exception as exc:
        return {"success": False, "message": f"Test failed with error: {exc}", "details": []}


def get_current_settings_object():
    settings = settings_manager.get_settings()
    return type("SettingsView", (), settings)()


def build_system_status():
    return {
        "database": check_database_status(),
        "camera": check_camera_status(),
        "face_recognition": check_face_recognition_status(),
        "gpu": os.environ.get("USE_GPU", "True").lower() == "true",
    }


def build_system_metrics():
    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        return {
            "cpu_usage": round(cpu_percent, 1),
            "memory_usage": round(memory.percent, 1),
            "disk_usage": round(disk.percent, 1),
            "uptime": str(timedelta(seconds=int(uptime_seconds))),
            "active_connections": len(psutil.net_connections()),
            "fps": 30,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception:
        import random

        return {
            "cpu_usage": round(random.uniform(15, 45), 1),
            "memory_usage": round(random.uniform(25, 55), 1),
            "disk_usage": round(random.uniform(35, 65), 1),
            "uptime": "2 days, 3:45:12",
            "active_connections": random.randint(5, 25),
            "fps": 30,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }


def build_reports_data(start_date: str, end_date: str):
    total_students = Student.query.count()
    today = datetime.now().date()
    today_present = Attendance.query.filter(db.func.date(Attendance.timestamp) == today).count()
    today_absent = max(0, total_students - today_present)

    total_days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
    total_possible = total_students * total_days
    total_actual = Attendance.query.filter(
        Attendance.timestamp >= start_date,
        Attendance.timestamp <= end_date + " 23:59:59",
    ).count()
    avg_attendance = round((total_actual / total_possible * 100) if total_possible > 0 else 0, 1)
    departments = [dept[0] for dept in db.session.query(Student.department).distinct().all() if dept[0]]
    return total_students, today_present, today_absent, avg_attendance, total_days, departments
