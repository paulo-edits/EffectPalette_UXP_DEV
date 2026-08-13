"""Read-only Windows diagnostic for the experimental native preset assistant.

This module never focuses a window and never synthesizes keyboard or mouse input.
It reports candidate Premiere windows and optionally captures the selected window.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
from ctypes import wintypes
from pathlib import Path


user32 = ctypes.windll.user32


def enable_per_monitor_dpi() -> str:
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    return "system-default"


def window_candidates() -> list[dict]:
    candidates: list[dict] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value
        if "Adobe Premiere Pro" not in title:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        candidates.append({
            "hwnd": int(hwnd),
            "title": title,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            },
            "foreground": int(user32.GetForegroundWindow()) == int(hwnd),
        })
        return True

    user32.EnumWindows(callback, 0)
    return candidates


def capture_window(candidate: dict, output: Path) -> dict:
    try:
        from PIL import ImageGrab
    except ImportError as error:
        raise RuntimeError("Pillow is required only when --output is used.") from error
    rect = candidate["rect"]
    image = ImageGrab.grab(
        bbox=(rect["left"], rect["top"], rect["right"], rect["bottom"]),
        all_screens=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {"path": str(output.resolve()), "width": image.width, "height": image.height, "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Premiere's top-level window without sending input.")
    parser.add_argument("--output", type=Path, help="Optional PNG path for a read-only window capture.")
    args = parser.parse_args()
    dpi_mode = enable_per_monitor_dpi()
    candidates = window_candidates()
    selected = next((item for item in candidates if item["foreground"]), candidates[0] if len(candidates) == 1 else None)
    result = {
        "ok": selected is not None,
        "schemaVersion": 1,
        "actionType": "nativePresetAssist.diagnoseWindow",
        "data": {
            "dpiAwareness": dpi_mode,
            "candidateCount": len(candidates),
            "candidates": candidates,
            "selectedWindow": selected,
            "capture": None,
            "inputSynthesized": False,
        },
    }
    if selected and args.output:
        try:
            result["data"]["capture"] = capture_window(selected, args.output)
        except Exception as error:  # diagnostic must remain serializable
            result["ok"] = False
            result["error"] = {"code": "CAPTURE_FAILED", "message": str(error)}
    elif not selected:
        result["error"] = {"code": "PREMIERE_WINDOW_NOT_UNIQUE", "message": "No unique/foreground Adobe Premiere Pro window was found."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
