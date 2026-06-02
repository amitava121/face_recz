# Face Recognition Attendance System – AI Agent Guide

## Architecture overview (read this first)
- The system is a Flask web app + a separate WebRTC server. Entry points live in [main.py](main.py) (modes: app, webrtc, both, check, warmup, quick) and [start_servers.py](start_servers.py) (boots both services, preloads models, handles port cleanup).
- Flask app: routes, auth, templates, and API live in [src/app/app.py](src/app/app.py). It uses `Flask-SocketIO` (eventlet) and `Flask-Limiter`/`Flask-Talisman` for rate limiting and security headers.
- WebRTC server: real-time video ingest + face recognition is in [src/services/webrtc_server.py](src/services/webrtc_server.py) using aiohttp + aiortc. It processes frames in a background queue thread and calls Flask APIs with `requests`.

## Data flow + service boundaries
- Browser → WebRTC server for live video. The WebRTC server runs recognition and liveness checks, then calls Flask endpoints:
  - `POST /api/register_face` and `POST /api/mark_attendance` (see [src/services/webrtc_server.py](src/services/webrtc_server.py)).
  - Student cache is refreshed from `GET /api/students`.
- Flask handles DB writes and emits SocketIO events (see `mark_attendance()` in [src/services/video_service.py](src/services/video_service.py)).
- Face recognition uses InsightFace in [src/services/face_utils.py](src/services/face_utils.py). Models live under [models/models/buffalo_l](models/models/buffalo_l).

## Database + environment requirements
- PostgreSQL is mandatory. Importing [src/models/db.py](src/models/db.py) verifies DB connectivity immediately and reads `DB_USER`, `DB_PASS`, `DB_HOST`, `DB_PORT`, `DB_NAME` from environment.
- Attendance dedupe logic exists in `Attendance.mark_attendance()` (time window) in [src/models/db.py](src/models/db.py); a second path exists in `mark_attendance()` in [src/services/video_service.py](src/services/video_service.py) (10‑minute window).

## Key conventions and patterns
- InsightFace initialization is lazy: `ensure_insightface_initialized()` in [src/services/face_utils.py](src/services/face_utils.py). Respect `USE_GPU`, `FACE_RECOGNITION_THRESHOLD`, `FRAME_SKIP`, etc. from environment.
- WebRTC server uses an internal processing queue to keep frame handling off the main loop; avoid adding heavy work on the aiohttp callbacks (see `start_processing()` in [src/services/webrtc_server.py](src/services/webrtc_server.py)).
- Logs are written under logs/ via Loguru and standard logging; services compute the project root by walking up directories (see log setup in [src/services/webrtc_server.py](src/services/webrtc_server.py), [src/services/video_service.py](src/services/video_service.py), [src/models/db.py](src/models/db.py)).

## Developer workflows (local + Docker)
- Local runs: `python main.py --mode app|webrtc|both|check|warmup|quick` (see [main.py](main.py)).
- Full stack via Docker Compose: see [deployment/docker/README.md](deployment/docker/README.md) and [deployment/docker/docker-compose.yml](deployment/docker/docker-compose.yml).
- Dependencies are pinned in [config/requirements.txt](config/requirements.txt) (aiortc/aiohttp for WebRTC, insightface/onnxruntime for recognition, psycopg2 for Postgres).

## Where to look for UI + assets
- Jinja templates are in [src/app/templates](src/app/templates) and static assets in [src/app/static](src/app/static).

## When changing behavior
- If you modify recognition thresholds or liveness behavior, update both [src/services/face_utils.py](src/services/face_utils.py) and [src/services/webrtc_server.py](src/services/webrtc_server.py) to keep runtime behavior consistent.