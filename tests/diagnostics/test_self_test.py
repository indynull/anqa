"""Host self-test checks."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from anqa.diagnostics.self_test import CheckResult, SelfTestReport, run_self_test

_HOST_CHECK_IDS = {
    "app_home",
    "catalog",
    "control_owner",
    "session_display",
    "sway_socket",
    "hud_summon",
    "config-toml",
}


def test_control_owner_reports_active_rpc_and_failure(monkeypatch) -> None:
    from anqa.diagnostics import self_test as st

    monkeypatch.setattr(
        "anqa.control.daemon.control_socket_accepts",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "anqa.control.daemon.owner_diagnostics_probe",
        lambda *_a, **_k: {
            "active": [{"method": "notes/list", "elapsedMs": 12.0, "session": "s1"}],
            "failures": [
                {
                    "method": "notes/list",
                    "code": -32603,
                    "message": "notes timed out",
                    "elapsedMs": 2000.0,
                }
            ],
            "catalogBuilding": True,
        },
    )
    result = st._check_control_owner()
    assert result.id == "control_owner"
    assert result.ok is True
    assert "notes/list" in result.detail
    assert "timed out" in result.detail
    assert "catalog building" in result.detail


def test_self_test_probes_config_catalog_and_seat(tmp_path: Path):
    """Doctor is config home, catalog, control owner, and HUD seat."""
    catalog = tmp_path / "sessions"
    catalog.mkdir()
    report = run_self_test(catalog_root=catalog)
    ids = {c.id for c in report.checks}
    assert ids == _HOST_CHECK_IDS
    assert report.ok is True


def test_session_display_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DISPLAY", ":0")
    from anqa.diagnostics import self_test as st

    r = st._check_session_display()
    assert r.ok is True
    assert r.required is False
    assert "Wayland" in r.detail
    assert "toggle" in r.detail
    assert "XDG_ACTIVATION_TOKEN" in r.detail


def test_sway_socket_names_place_not_focus(tmp_path: Path, monkeypatch):
    sock = tmp_path / "sway-ipc.sock"
    sock.write_bytes(b"")
    monkeypatch.setenv("SWAYSOCK", str(sock))
    from anqa.diagnostics import self_test as st

    r = st._check_sway_socket()
    assert r.ok is True
    assert "place" in r.detail
    assert "xdg-activation" in r.detail


def test_hud_summon_socket_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("ANQA_HUD_SUMMON_SOCKET", raising=False)
    from anqa.diagnostics import self_test as st

    r = st._check_hud_summon_socket()
    assert r.ok is False
    assert r.required is False
    assert "not listening" in r.detail


def test_report_lines_and_fail():
    rep = SelfTestReport(
        checks=[
            CheckResult("a", "A", True),
            CheckResult("b", "B", False, detail="nope", required=True),
            CheckResult("c", "C", False, required=False),
        ]
    )
    assert rep.ok is False
    assert rep.fail_count == 1
    assert rep.warn_count == 1
    text = "\n".join(rep.lines())
    assert "FAIL" in text
    assert "WARN" in text
    assert CheckResult("x", "X", True).level == "ok"
    assert CheckResult("y", "Y", False, required=False).level == "warn"
    assert CheckResult("z", "Z", False, required=True).level == "error"


def test_app_home_not_writable(tmp_path: Path, monkeypatch):
    from anqa.diagnostics import self_test as st

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    real_write = Path.write_text

    def boom(self, *a, **k):
        if self.name == ".anqa-write-probe":
            raise OSError("read-only")
        return real_write(self, *a, **k)

    monkeypatch.setattr("anqa.paths.APP_HOME", blocked)
    monkeypatch.setattr(Path, "write_text", boom)
    r = st._check_app_home()
    assert r.ok is False


def test_hud_summon_doctor_ok_when_fake_server_accepts(monkeypatch):
    import socket

    path = Path(tempfile.mkdtemp(prefix="anqa-hud-")) / "hud-summon.sock"
    monkeypatch.setenv("ANQA_HUD_SUMMON_SOCKET", str(path))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def _accept() -> None:
        conn, _ = server.accept()
        conn.close()

    th = threading.Thread(target=_accept, daemon=True)
    th.start()
    from anqa.diagnostics import self_test as st

    r = st._check_hud_summon_socket()
    th.join(timeout=1)
    server.close()
    assert r.ok is True
    assert r.required is False
    assert "toggle" in r.detail
