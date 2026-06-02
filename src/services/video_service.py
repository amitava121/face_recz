import logging
import time
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
from src.models.db import Student, Attendance
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

# Load environment variables
load_dotenv()

# Configure logging with correct path
def setup_video_logging():
    """Setup video service logging to the correct logs directory"""
    # Get the project root directory (mini_project1)
    current_file = os.path.abspath(__file__)
    # Go up: services -> src -> face-recognition-attendance-system -> mini_project1
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
    logs_dir = os.path.join(project_root, 'logs')

    # Create logs directory if it doesn't exist
    os.makedirs(logs_dir, exist_ok=True)

    # Set up logging
    log_file = os.path.join(logs_dir, "video_service.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        filename=log_file,
        filemode="a"
    )

    print(f"🔧 Video service logging initialized: {log_file}")

# Initialize video service logging
setup_video_logging()
logger = logging.getLogger(__name__)

# Database configuration from environment
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')

# Validate required environment variables
required_vars = ['DB_USER', 'DB_PASS', 'DB_NAME', 'DB_HOST', 'DB_PORT']
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

class VideoService:
    def __init__(self, app=None, socketio=None):
        self.app = app
        self.socketio = socketio
        self.is_running = False
        self.stop_event = False
        self.recognized_students = set()
        self.last_attendance_time = {}

    def start_recognition(self, recognition_callback):
        """Start face recognition for WebRTC"""
        logger.info("Starting recognition...")
        self.is_running = True
        self.stop_event = False
        return True

    def stop_recognition(self):
        """Stop face recognition"""
        self.is_running = False
        self.stop_event = True
        logger.info("Recognition stopped")
        return True

    def force_release_camera(self):
        """Placeholder for compatibility"""
        logger.info("Force release camera called (WebRTC mode)")
        return True

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up resources")
        return True

# Create a singleton instance
video_service = VideoService()

def get_frame_base64():
    """Placeholder for compatibility"""
    return None

def mark_attendance(session, student_id, recognized_students, app=None, socketio=None, student_name=None):
    """Mark a student as present in the database."""
    try:
        # Check if student exists
        student = session.query(Student).filter_by(id=student_id).first()
        if not student:
            logger.error(f"Student with ID {student_id} not found")
            return None

        # Use student name from database if not provided
        if not student_name:
            student_name = student.name

        # Check if student was already marked present today
        today = datetime.now().date()
        existing_attendance = (
            session.query(Attendance)
            .filter(
                Attendance.student_id == student_id,
                Attendance.timestamp >= datetime.combine(today, datetime.min.time()),
                Attendance.timestamp <= datetime.combine(today, datetime.max.time())
            )
            .first()
        )

        # If student was already marked present today, check the time difference
        if existing_attendance:
            time_diff = datetime.now() - existing_attendance.timestamp
            # If less than 10 minutes have passed, don't mark again
            if time_diff.total_seconds() < 600:  # 10 minutes
                logger.info(f"Student {student_name} already marked present less than 10 minutes ago")
                return existing_attendance

        # Create new attendance record
        attendance = Attendance(
            student_id=student_id,
            timestamp=datetime.now(timezone.utc),
            student_code=student.student_code,
            student_name=student.name,
            student_department=student.department
        )
        session.add(attendance)
        session.commit()

        # Add to recognized students set
        recognized_students.add(student_id)

        # Log the attendance
        logger.info(f"Marked attendance for {student_name} (ID: {student_id})")

        # Emit event via socketio if available
        if socketio:
            socketio.emit('attendance_marked', {
                'student_id': student_id,
                'name': student_name,
                'timestamp': attendance.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })

        return attendance

    except Exception as e:
        logger.error(f"Error marking attendance: {e}")
        session.rollback()
        return None

def process_frame(frame, students, recognized_students, session, app=None, socketio=None):
    """Placeholder for compatibility - WebRTC server handles this now"""
    logger.info("process_frame called (WebRTC mode)")
    return None
