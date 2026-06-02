import sys
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_resolve_app_mode_defaults_to_fastapi(monkeypatch):
    monkeypatch.delenv("APP_RUNTIME", raising=False)

    import start_servers

    assert start_servers.resolve_app_mode() == "fastapi"


def test_resolve_app_mode_accepts_fastapi(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME", "fastapi")

    import start_servers

    assert start_servers.resolve_app_mode() == "fastapi"


def test_build_app_command_uses_selected_runtime(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME", "fastapi")

    import start_servers

    command = start_servers.build_app_command(5001)

    assert command == [start_servers.PYTHON_BIN, "main.py", "--mode", "fastapi", "--port", "5001"]


def test_find_missing_modules_reports_missing_package(monkeypatch):
    import start_servers

    monkeypatch.setattr(start_servers, "REQUIRED_MODULES", {"fastapi": "fastapi", "missing-pkg": "definitely_missing_mod"})

    missing, error = start_servers.find_missing_modules(start_servers.PYTHON_BIN)

    assert error is None
    assert missing == ["missing-pkg"]


def test_select_python_bin_prefers_candidate_with_dependencies(monkeypatch, tmp_path):
    import start_servers

    broken_python = tmp_path / "python-broken"
    working_python = tmp_path / "python-working"
    broken_python.write_text("", encoding="utf-8")
    working_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(start_servers, "get_python_candidates", lambda: [broken_python, working_python])

    def fake_find_missing_modules(candidate):
        if candidate == broken_python:
            return ["fastapi"], None
        return [], None

    monkeypatch.setattr(start_servers, "find_missing_modules", fake_find_missing_modules)

    selected_python, diagnostics = start_servers.select_python_bin()

    assert selected_python == str(working_python)
    assert diagnostics == [f"{broken_python} (missing: fastapi)"]


def test_build_browser_url_points_to_localhost_root():
    import start_servers

    assert start_servers.build_browser_url(5001) == "http://localhost:5001/"
    assert start_servers.build_browser_url(5001, "/attendance") == "http://localhost:5001/attendance"
    assert start_servers.build_browser_url(5001, "/") == "http://localhost:5001/"


def test_preload_models_uses_selected_python(monkeypatch):
    import start_servers

    captured = {}

    def fake_run(cmd, cwd, text, capture_output):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(start_servers.subprocess, "run", fake_run)

    assert start_servers.preload_models("/tmp/custom-python") is True
    assert captured["cmd"] == ["/tmp/custom-python", "main.py", "--mode", "warmup"]
    assert captured["cwd"] == start_servers.PROJECT_DIR


def test_wait_for_server_respects_configured_timeout(monkeypatch):
    import start_servers

    time_values = iter([0.0, 0.5, 1.0, 1.5, 2.1])

    monkeypatch.setattr(start_servers.time, "time", lambda: next(time_values))
    monkeypatch.setattr(start_servers.time, "sleep", lambda _: None)
    monkeypatch.setattr(start_servers, "is_port_in_use", lambda port: False)

    assert start_servers.wait_for_server(5001, timeout_seconds=2) is False
