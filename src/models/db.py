from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from math import ceil
from typing import Any, Optional

import numpy as np
import psycopg2
from dotenv import load_dotenv
from loguru import logger as loguru_logger
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import (
    declarative_base,
    relationship,
    scoped_session,
    sessionmaker,
    backref,
)
from werkzeug.security import generate_password_hash


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def setup_db_logging() -> None:
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
    logs_dir = os.path.join(project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_file = os.path.join(logs_dir, "db_{time}.log")
    loguru_logger.add(
        log_file,
        rotation="10 MB",
        retention="1 week",
        level="INFO",
        backtrace=True,
        diagnose=True,
    )
    loguru_logger.info(f"🔧 Database logging initialized: {logs_dir}")


setup_db_logging()
load_dotenv()


class UserMixin:
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)


class Pagination:
    def __init__(self, items, page: int, per_page: int, total: int) -> None:
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = ceil(total / per_page) if per_page else 0
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1

    def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current - 1 < num < self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


class QueryCompat:
    def __init__(self, query) -> None:
        self._query = query

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._query, name)
        if not callable(attr):
            return attr

        def wrapper(*args: Any, **kwargs: Any):
            result = attr(*args, **kwargs)
            query_class = self._query.__class__
            return QueryCompat(result) if isinstance(result, query_class) else result

        return wrapper

    def get_or_404(self, ident):
        result = self._query.get(ident)
        if result is None:
            raise LookupError(f"Object {ident} not found")
        return result

    def paginate(self, page: int = 1, per_page: int = 20, error_out: bool = False):
        total = self._query.order_by(None).count()
        items = self._query.offset((page - 1) * per_page).limit(per_page).all()
        return Pagination(items, page, per_page, total)


class QueryProperty:
    def __get__(self, instance: Any, owner: type[Any]):
        return QueryCompat(db.session.query(owner))


Base = declarative_base()
Base.query = QueryProperty()


def build_database_uri() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgresql+asyncpg://"):
            return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql://", 1)
        return database_url

    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASS", "")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "attendance_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


class SQLAlchemyCompat:
    Column = Column
    Integer = Integer
    String = String
    Float = Float
    DateTime = DateTime
    LargeBinary = LargeBinary
    Boolean = Boolean
    ARRAY = ARRAY
    ForeignKey = ForeignKey
    relationship = staticmethod(relationship)
    backref = staticmethod(backref)
    func = func
    Model = Base

    def __init__(self) -> None:
        self._engine: Optional[Engine] = None
        self._database_uri: Optional[str] = None
        self._engine_options: dict[str, Any] = {}
        self.session = scoped_session(
            sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)
        )

    def init_app(self, app: Any | None = None) -> None:
        if app is not None:
            config = getattr(app, "config", {})
            self._database_uri = config.get("SQLALCHEMY_DATABASE_URI", build_database_uri())
            self._engine_options = config.get("SQLALCHEMY_ENGINE_OPTIONS", {})
        elif self._database_uri is None:
            self._database_uri = build_database_uri()

        if self._engine is None:
            self._engine = create_engine(self._database_uri, future=True, **self._engine_options)
            self.session.configure(bind=self._engine)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self.init_app()
        return self._engine

    def get_engine(self) -> Engine:
        return self.engine

    def create_all(self) -> None:
        Base.metadata.create_all(bind=self.engine)

    def inspect(self, engine: Engine):
        return inspect(engine)

    @contextmanager
    def app_context(self):
        try:
            yield self
        finally:
            self.session.remove()


db = SQLAlchemyCompat()


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    student_code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20))
    face_embedding_array = db.Column(db.ARRAY(db.Float), nullable=True)
    embedding_model_version = db.Column(db.String(100), nullable=True)
    detector_version = db.Column(db.String(100), nullable=True)
    template_version = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=db.func.now())

    def set_embedding(self, embedding):
        if embedding is not None:
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            self.face_embedding_array = embedding.tolist()
        else:
            self.face_embedding_array = None

    def get_embedding(self):
        if self.face_embedding_array is not None:
            return np.array(self.face_embedding_array)
        return None


class Attendance(db.Model):
    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    confidence_score = db.Column(db.Float, nullable=True)
    device_id = db.Column(db.String(50), nullable=True)
    student_code = db.Column(db.String(20), nullable=True)
    student_name = db.Column(db.String(100), nullable=True)
    student_department = db.Column(db.String(100), nullable=True)
    embedding_model_version = db.Column(db.String(100), nullable=True)
    detector_version = db.Column(db.String(100), nullable=True)
    liveness_model_version = db.Column(db.String(100), nullable=True)
    liveness_score = db.Column(db.Float, nullable=True)
    decision_reason = db.Column(db.String(255), nullable=True)
    student = db.relationship("Student", backref=db.backref("records", lazy=True))

    @classmethod
    def mark_attendance(
        cls,
        session,
        student_id,
        confidence=None,
        device_id=None,
        min_interval_minutes=5,
        *,
        embedding_model_version=None,
        detector_version=None,
        liveness_model_version=None,
        liveness_score=None,
        decision_reason=None,
    ):
        now = datetime.now(timezone.utc)
        latest = (
            session.query(cls)
            .filter(cls.student_id == student_id)
            .order_by(cls.timestamp.desc())
            .first()
        )

        if latest and (now - latest.timestamp).total_seconds() < (min_interval_minutes * 60):
            loguru_logger.info(
                f"Skipping duplicate attendance for student {student_id} - last marked {latest.timestamp}"
            )
            return False, latest

        student = session.query(Student).get(student_id)
        attendance = cls(
            student_id=student_id,
            timestamp=now,
            confidence_score=confidence,
            device_id=device_id,
            student_code=student.student_code if student else None,
            student_name=student.name if student else None,
            student_department=student.department if student else None,
            embedding_model_version=embedding_model_version,
            detector_version=detector_version,
            liveness_model_version=liveness_model_version,
            liveness_score=liveness_score,
            decision_reason=decision_reason,
        )
        session.add(attendance)
        session.commit()
        loguru_logger.info(f"Marked attendance for student {student_id} with confidence {confidence}")
        return True, attendance


class FaceImage(db.Model):
    __tablename__ = "face_images"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    image_data = db.Column(db.LargeBinary, nullable=False)
    face_embedding_array = db.Column(db.ARRAY(db.Float), nullable=True)
    embedding_model_version = db.Column(db.String(100), nullable=True)
    detector_version = db.Column(db.String(100), nullable=True)
    liveness_model_version = db.Column(db.String(100), nullable=True)
    liveness_score = db.Column(db.Float, nullable=True)
    decision_reason = db.Column(db.String(255), nullable=True)
    template_version = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    student = db.relationship("Student", backref=db.backref("face_images", lazy=True))

    def set_embedding(self, embedding_array):
        self.face_embedding_array = embedding_array.tolist() if embedding_array is not None else None

    def get_embedding(self):
        if self.face_embedding_array is not None:
            return np.array(self.face_embedding_array)
        return None


class Admin(db.Model, UserMixin):
    __tablename__ = "admin"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)

    def update_last_login(self):
        self.last_login = datetime.now(timezone.utc)


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    student = db.relationship("Student", backref=db.backref("user_account", uselist=False, lazy=True))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    def update_last_login(self):
        self.last_login = datetime.now(timezone.utc)

    def is_admin(self):
        return self.role == "admin"

    def is_viewer(self):
        return self.role == "viewer"

    def get_full_name(self):
        if self.is_viewer() and self.student:
            return self.student.name
        return self.username

    def can_modify_attendance(self):
        return self.is_admin()

    def can_modify_system_settings(self):
        return self.is_admin()

    def can_manage_users(self):
        return self.is_admin()


class UserActivity(db.Model):
    __tablename__ = "user_activities"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(255), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    user = db.relationship("User", backref=db.backref("activities", lazy=True))

    @classmethod
    def log_activity(cls, session, user_id, username, action, action_type, ip_address=None, user_agent=None):
        activity = cls(
            user_id=user_id,
            username=username,
            action=action,
            action_type=action_type,
            ip_address=ip_address,
            user_agent=user_agent,
            timestamp=datetime.now(timezone.utc),
        )
        session.add(activity)
        session.commit()
        loguru_logger.info(f"Logged activity: {username} - {action}")
        return activity


class SystemSettings(db.Model):
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @staticmethod
    def get_setting(key, default=None):
        setting = SystemSettings.query.filter_by(key=key).first()
        return setting.value if setting else default

    @staticmethod
    def set_setting(key, value):
        setting = SystemSettings.query.filter_by(key=key).first()
        if setting:
            setting.value = str(value)
        else:
            setting = SystemSettings(key=key, value=str(value))
            db.session.add(setting)
        db.session.commit()


def _connection_params() -> dict[str, Any]:
    return {
        "dbname": "postgres",
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASS", ""),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
    }


def verify_postgres_connection() -> bool:
    try:
        conn = psycopg2.connect(**_connection_params())
        conn.close()
        logger.info("Successfully connected to PostgreSQL server")
        return True
    except Exception as e:
        logger.error(f"Could not connect to PostgreSQL database: {e}")
        return False


def ensure_database_exists():
    try:
        params = _connection_params()
        target_db = os.getenv("DB_NAME", "attendance_db")
        conn = psycopg2.connect(**params)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
        exists = cur.fetchone()
        if not exists:
            cur.execute(f'CREATE DATABASE "{target_db}"')
            logger.info(f"Created database '{target_db}'")
        else:
            logger.info(f"Database '{target_db}' already exists")
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error ensuring database exists: {e}")
        raise


def create_tables_if_missing():
    try:
        engine = db.get_engine()
        inspector = inspect(engine)
        db.create_all()

        def run_migrations(conn, migrations, *, label: str) -> None:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            for migration in migrations:
                try:
                    conn.execute(text(migration))
                except Exception as e:
                    loguru_logger.warning(f"Skipping {label} migration due to database lock or error: {migration} ({e})")

        def table_has_rows(table_name: str) -> bool | None:
            try:
                with engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '2s'"))
                    conn.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return bool(conn.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{table_name}" LIMIT 1)')).scalar())
            except Exception as e:
                loguru_logger.warning(f"Skipping startup bootstrap query for {table_name} due to database lock or error: {e}")
                return None

        def system_setting_exists(setting_key: str) -> bool | None:
            try:
                with engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '2s'"))
                    conn.execute(text("SET LOCAL statement_timeout = '5s'"))
                    result = conn.execute(
                        text('SELECT EXISTS (SELECT 1 FROM system_settings WHERE key = :setting_key)'),
                        {"setting_key": setting_key},
                    ).scalar()
                    return bool(result)
            except Exception as e:
                loguru_logger.warning(
                    f"Skipping startup system_settings bootstrap for {setting_key} due to database lock or error: {e}"
                )
                return None

        if "students" in inspector.get_table_names():
            with engine.begin() as conn:
                migrations = [
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS department VARCHAR(100)",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20)",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS face_embedding_array FLOAT[]",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS embedding_model_version VARCHAR(100)",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS detector_version VARCHAR(100)",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS template_version VARCHAR(100)",
                    "ALTER TABLE students ADD COLUMN IF NOT EXISTS face_embedding JSONB",
                    "ALTER TABLE students DROP COLUMN IF EXISTS face_embedding_json",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS face_embedding_array FLOAT[]",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS embedding_model_version VARCHAR(100)",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS detector_version VARCHAR(100)",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS liveness_model_version VARCHAR(100)",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS liveness_score FLOAT",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS decision_reason VARCHAR(255)",
                    "ALTER TABLE face_images ADD COLUMN IF NOT EXISTS template_version VARCHAR(100)",
                    "ALTER TABLE face_images DROP COLUMN IF EXISTS face_embedding",
                ]
                run_migrations(conn, migrations, label="students/face_images")
                try:
                    conn.execute(
                        text(
                            """
                            UPDATE students
                            SET face_embedding = to_jsonb(face_embedding_array)
                            WHERE face_embedding_array IS NOT NULL AND face_embedding IS NULL
                            """
                        )
                    )
                except Exception as e:
                    loguru_logger.warning(f"Error migrating embeddings: {e}")

        if "attendance" in inspector.get_table_names():
            with engine.begin() as conn:
                migrations = [
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS confidence_score FLOAT",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS device_id VARCHAR(50)",
                    "ALTER TABLE attendance ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE",
                    "ALTER TABLE attendance ALTER COLUMN student_id DROP NOT NULL",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_code VARCHAR(20)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_name VARCHAR(100)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_department VARCHAR(100)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS embedding_model_version VARCHAR(100)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS detector_version VARCHAR(100)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS liveness_model_version VARCHAR(100)",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS liveness_score FLOAT",
                    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS decision_reason VARCHAR(255)",
                ]
                run_migrations(conn, migrations, label="attendance")
                try:
                    conn.execute(
                        text(
                            """
                            UPDATE attendance
                            SET student_code = students.student_code,
                                student_name = students.name,
                                student_department = students.department
                            FROM students
                            WHERE attendance.student_id = students.id
                              AND (
                                  attendance.student_code IS NULL
                                  OR attendance.student_name IS NULL
                                  OR attendance.student_department IS NULL
                              )
                            """
                        )
                    )
                except Exception as e:
                    loguru_logger.warning(f"Error populating student info: {e}")

        # The admin table is a frequent lock hotspot from older startup code.
        # Its required columns are already present in current deployments, so
        # avoid DDL on every boot and let explicit migrations handle future changes.

        if "users" in inspector.get_table_names():
            with engine.begin() as conn:
                migrations = [
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'viewer'",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS student_id INTEGER",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP WITH TIME ZONE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
                ]
                run_migrations(conn, migrations, label="users")

        if "user_activities" not in inspector.get_table_names():
            UserActivity.__table__.create(engine, checkfirst=True)

        if "system_settings" not in inspector.get_table_names():
            SystemSettings.__table__.create(engine, checkfirst=True)

        sleep_timer_exists = system_setting_exists("sleep_timer_seconds")
        if sleep_timer_exists is False:
            db.session.add(SystemSettings(key="sleep_timer_seconds", value="300"))
            db.session.commit()

        admin_has_rows = table_has_rows("admin")
        if admin_has_rows is False:
            default_admin = Admin(
                username=os.getenv("ADMIN_USERNAME", "admin"),
                password_hash=generate_password_hash(os.getenv("ADMIN_PASSWORD", "admin123")),
                email=os.getenv("ADMIN_EMAIL", "admin@example.com"),
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(default_admin)
            db.session.commit()

        with engine.begin() as conn:
            try:
                conn.execute(
                    text(
                        """
                        CREATE OR REPLACE FUNCTION check_db_health() RETURNS boolean AS $$
                        BEGIN
                            RETURN true;
                        END;
                        $$ LANGUAGE plpgsql;
                        """
                    )
                )
            except Exception as e:
                loguru_logger.warning(f"Error creating health check function: {e}")

        loguru_logger.info("Database tables and columns verified successfully")
    except OperationalError as e:
        loguru_logger.error(f"Database connection error: {e}")
        raise
    except Exception as e:
        loguru_logger.error(f"Database setup error: {e}")
        raise
