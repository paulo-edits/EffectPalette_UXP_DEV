"""
Closed beta local reporting helpers.

Everything in this module stays on the user's machine. Reports are written to
Documents/FX.palette_Beta_Report so testers can inspect and send them
manually.
"""

from __future__ import annotations

import json
import locale
import os
import platform
import shutil
import socket
import sys
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


APP_NAME = "FX.palette"
REPORT_DIR_NAME = "FX.palette_Beta_Report"
SESSION_ID = datetime.now().strftime("%Y%m%d_%H%M%S")


def _documents_dir() -> Path:
    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    candidate = user_profile / "Documents"
    if candidate.exists():
        return candidate
    return Path.home()


REPORT_DIR = _documents_dir() / REPORT_DIR_NAME
APP_LOG_FILE = REPORT_DIR / "effect_palette_app.log"
EVENTS_FILE = REPORT_DIR / "telemetry_events.jsonl"
MAX_APP_LOG_BYTES = 256 * 1024
MAX_EVENTS_LOG_BYTES = 512 * 1024


def ensure_report_dir() -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return REPORT_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_bounded(file_path: Path, text: str, max_bytes: int) -> None:
    ensure_report_dir()
    try:
        if file_path.exists() and file_path.stat().st_size >= max_bytes:
            keep_bytes = max_bytes // 2
            with file_path.open("rb") as source:
                source.seek(max(0, file_path.stat().st_size - keep_bytes))
                tail = source.read()
            first_newline = tail.find(b"\n")
            if first_newline >= 0:
                tail = tail[first_newline + 1:]
            file_path.write_bytes(tail)
        with file_path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(text)
    except Exception:
        pass


def log_app(message: str, level: str = "INFO") -> None:
    try:
        line = f"{_now_iso()} [{level}] {message}\n"
        _append_bounded(APP_LOG_FILE, line, MAX_APP_LOG_BYTES)
    except Exception:
        pass


def write_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    try:
        event = {
            "timestamp": _now_iso(),
            "session_id": SESSION_ID,
            "event": event_type,
            "payload": payload or {},
        }
        _append_bounded(EVENTS_FILE, json.dumps(event, ensure_ascii=False) + "\n", MAX_EVENTS_LOG_BYTES)
    except Exception:
        pass


def start_session(extension_dir: Path, ext_data: Path) -> None:
    ensure_report_dir()
    log_app(f"{APP_NAME} beta session started: {SESSION_ID}")
    write_event("session_start", {
        "extension_dir": str(extension_dir),
        "data_dir": str(ext_data),
        "python": sys.version,
        "platform": platform.platform(),
    })


def log_exception(context: str, exc: BaseException) -> None:
    log_app(f"{context}: {exc}", "ERROR")
    try:
        _append_bounded(APP_LOG_FILE, traceback.format_exc() + "\n", MAX_APP_LOG_BYTES)
    except Exception:
        pass
    write_event("exception", {"context": context, "error": str(exc)})


def _file_info(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        }
    except Exception:
        return {"exists": False}


def _json_count(path: Path, key: str) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        value = data.get(key)
        return len(value) if isinstance(value, list) else None
    except Exception:
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_copy(src: Path, dest: Path) -> None:
    try:
        if src.exists() and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    except Exception as exc:
        log_app(f"Failed to copy {src}: {exc}", "WARN")


def _zip_dir(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in src_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_total_memory_bytes() -> int | None:
    if os.name != "nt":
        return None

    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    except Exception:
        return None
    return None


def _get_screen_info() -> dict[str, Any]:
    if os.name != "nt":
        return {}

    try:
        import ctypes

        user32 = ctypes.windll.user32
        return {
            "primary_width": int(user32.GetSystemMetrics(0)),
            "primary_height": int(user32.GetSystemMetrics(1)),
            "monitor_count": int(user32.GetSystemMetrics(80)),
        }
    except Exception:
        return {}


def _get_disk_info() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(Path.home().anchor or str(Path.home()))
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except Exception:
        return {}


def _detect_premiere_profile_versions() -> list[dict[str, Any]]:
    docs = _documents_dir()
    root = docs / "Adobe" / "Premiere Pro"
    if not root.exists():
        return []

    versions = []
    try:
        for version_dir in sorted(root.iterdir(), reverse=True):
            if not version_dir.is_dir():
                continue
            profiles = []
            try:
                for profile_dir in version_dir.iterdir():
                    if profile_dir.is_dir() and profile_dir.name.startswith("Profile-"):
                        preset_file = profile_dir / "Effect Presets and Custom Items.prfpset"
                        profiles.append({
                            "name": profile_dir.name,
                            "has_presets_file": preset_file.exists(),
                            "presets_file": _file_info(preset_file),
                        })
            except Exception:
                pass
            versions.append({
                "version_folder": version_dir.name,
                "path": str(version_dir),
                "profiles": profiles,
            })
    except Exception:
        return versions
    return versions


def _build_system_info(ext_data: Path, extension_dir: Path, reason: str) -> dict[str, Any]:
    host_info = _read_json(ext_data / "premiere_host_info.json")
    preferred_encoding = ""
    try:
        preferred_encoding = locale.getencoding()
    except Exception:
        try:
            preferred_encoding = locale.getpreferredencoding(False)
        except Exception:
            preferred_encoding = ""

    return {
        "generated_at": _now_iso(),
        "reason": reason,
        "session_id": SESSION_ID,
        "app": APP_NAME,
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "architecture": platform.architecture(),
        },
        "hardware": {
            "cpu_count_logical": os.cpu_count(),
            "processor_identifier": os.environ.get("PROCESSOR_IDENTIFIER", ""),
            "number_of_processors_env": os.environ.get("NUMBER_OF_PROCESSORS", ""),
            "total_memory_bytes": _get_total_memory_bytes(),
            "screen": _get_screen_info(),
            "home_drive_disk": _get_disk_info(),
        },
        "locale": {
            "default_locale": locale.getlocale(),
            "preferred_encoding": preferred_encoding,
            "timezone": time.tzname,
            "timezone_offset_seconds": -time.timezone,
        },
        "user": {
            "hostname": socket.gethostname(),
            "windows_user": os.environ.get("USERNAME", ""),
            "user_domain": os.environ.get("USERDOMAIN", ""),
        },
        "paths": {
            "extension_dir": str(extension_dir),
            "data_dir": str(ext_data),
            "report_dir": str(REPORT_DIR),
            "documents_dir": str(_documents_dir()),
        },
        "premiere": {
            "host_info_file": _file_info(ext_data / "premiere_host_info.json"),
            "host_info": host_info,
            "detected_profile_versions": _detect_premiere_profile_versions(),
        },
    }


def build_report(
    feedback: dict[str, Any] | None,
    ext_data: Path,
    extension_dir: Path,
    reason: str = "manual",
) -> Path:
    ensure_report_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_name = f"FX.palette_Beta_Report_{timestamp}"
    report_dir = REPORT_DIR / report_name
    report_dir.mkdir(parents=True, exist_ok=True)

    write_event("report_requested", {"reason": reason})

    feedback_payload = feedback or {}
    if feedback_payload:
        _write_json(report_dir / "feedback.json", feedback_payload)
        feedback_txt = [
            "FX.palette - Closed Beta Feedback",
            "",
            f"Generated at: {_now_iso()}",
            f"Tester name: {feedback_payload.get('tester_name', '')}",
            "",
            "What did you think?",
            feedback_payload.get("impression", ""),
            "",
            "Bug report:",
            feedback_payload.get("bug_report", ""),
            "",
            "Feature suggestions:",
            feedback_payload.get("feature_suggestions", ""),
            "",
            "Additional comments:",
            feedback_payload.get("additional_comments", ""),
            "",
        ]
        (report_dir / "feedback.txt").write_text("\n".join(feedback_txt), encoding="utf-8")

    system_info = _build_system_info(ext_data, extension_dir, reason)
    _write_json(report_dir / "system_info.json", system_info)

    data_files = {
        "worker.log": ext_data / "worker.log",
        "premiere_host_info.json": ext_data / "premiere_host_info.json",
        "premiere_diagnose.txt": ext_data / "premiere_diagnose.txt",
        "current_selection.json": ext_data / "current_selection.json",
        "premiere_effects.json": ext_data / "premiere_effects.json",
        "premiere_project_items.json": ext_data / "premiere_project_items.json",
        "premiere_favorites.json": ext_data / "premiere_favorites.json",
        "premiere_sequences.json": ext_data / "premiere_sequences.json",
        "generic_item_templates.json": ext_data / "generic_item_templates.json",
    }
    for dest_name, src in data_files.items():
        _safe_copy(src, report_dir / "data" / dest_name)

    _safe_copy(APP_LOG_FILE, report_dir / "logs" / APP_LOG_FILE.name)
    _safe_copy(EVENTS_FILE, report_dir / "logs" / EVENTS_FILE.name)

    presets_file = ext_data / "premiere_presets.json"
    summary = {
        "files": {name: _file_info(path) for name, path in data_files.items()},
        "premiere_presets_json": {
            **_file_info(presets_file),
            "preset_count": _json_count(presets_file, "presets"),
            "note": "Full presets file is summarized instead of copied to keep the beta report lighter.",
        },
        "counts": {
            "effects": _json_count(ext_data / "premiere_effects.json", "effects"),
            "project_items": _json_count(ext_data / "premiere_project_items.json", "items"),
            "favorites": _json_count(ext_data / "premiere_favorites.json", "items"),
            "sequences": _json_count(ext_data / "premiere_sequences.json", "sequences"),
        },
    }
    _write_json(report_dir / "data_summary.json", summary)

    readme = (
        "FX.palette closed beta report\n\n"
        "This package was generated locally on the tester's PC. It includes logs,\n"
        "runtime summaries, optional feedback, and small JSON manifests useful for\n"
        "debugging. The full premiere_presets.json file is summarized, not copied.\n"
    )
    (report_dir / "README.txt").write_text(readme, encoding="utf-8")

    zip_path = REPORT_DIR / f"{report_name}.zip"
    _zip_dir(report_dir, zip_path)
    write_event("report_created", {"reason": reason, "zip_path": str(zip_path)})
    log_app(f"Beta report created: {zip_path}")
    return zip_path
