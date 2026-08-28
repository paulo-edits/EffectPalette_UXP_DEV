"""Frame extraction via ffmpeg - ported from pFX-Tracker_UXP/daemon/exporter.py (read-only
reference, never modified in place). parse_source_meta() (VFR detection) and the ffmpeg argument
list (two-stage -ss seek, aspect-preserving scale filter) are ported verbatim - pure logic with no
host awareness. The process-invocation layer is rebuilt on Qt's QProcess instead of
asyncio.create_subprocess_exec, since this companion runs a Qt event loop, not asyncio: QProcess is
fully async/non-blocking and integrates with that event loop directly via signals, so no worker
thread is needed for this I/O-bound (waiting on an external process) part of the feature - compare
with tracker_engine.track(), which genuinely is CPU-bound and does need a QThread (see
qt_tracker_window.py).
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PySide6 import QtCore

_STANDARD_RATES = [23.976, 24, 25, 29.97, 30, 47.952, 48, 50, 59.94, 60, 90, 100, 119.88, 120, 144, 240]


def ffmpeg_path(motracker_dir: Path) -> Path:
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    return motracker_dir / "bin" / name


def make_job_dir() -> tuple[str, Path]:
    job_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:5]}"
    job_dir = Path(tempfile.gettempdir()) / "fxpalette_motracker" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


def parse_source_meta(stderr_text: str) -> dict:
    """Sniff source frame timing from ffmpeg's input-stream description.

    Variable-frame-rate captures (OBS/screen/game recordings) report a non-standard AVERAGE rate -
    ported verbatim from exporter.py, same rationale.
    """
    fps = 0.0
    tbr = 0.0
    m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps\b", stderr_text)
    m_tbr = re.search(r"(\d+(?:\.\d+)?)\s*tbr\b", stderr_text)
    if m_fps:
        fps = float(m_fps.group(1))
    if m_tbr:
        tbr = float(m_tbr.group(1))

    def is_std(rate: float) -> bool:
        return any(abs(rate - s) < 0.06 for s in _STANDARD_RATES)

    vfr_suspect = False
    if fps > 0 and not is_std(fps):
        vfr_suspect = True
    if fps > 0 and tbr > 0 and abs(fps - tbr) > 0.5 and abs(tbr - 2 * fps) > 0.5:
        vfr_suspect = True

    return {"fps": fps, "tbr": tbr, "vfrSuspect": vfr_suspect}


def build_ffmpeg_args(media_path: str, src_start: float, duration: float, out_pattern: str) -> list[str]:
    """Two-stage seek (coarse before -i, fine after), no scale filter at all - extracts at the
    source's own true native pixel resolution. The original exporter.py (and this file, until a
    real host test traced a "slight but real" Stabilize misalignment report to it - see
    TECHNICAL_PLAN.md's Motion Tracker slice) capped the longer axis at 1280px to keep extraction/
    CSRT tracking fast; that downscale turned out to be lossy enough to matter: for a 1440-wide
    source it meant tracking could only resolve position to about +-1px in a 720px-wide working
    image, i.e. +-2px of TRUE source resolution, which survives (scaled down again) as a small but
    visible residual error once Stabilize composites it precisely. Native-resolution extraction
    trades some extraction/tracking speed for eliminating that error at the root - proportion-based
    math elsewhere (coordScaleX/Y in applyTrack) needs no changes, since it already expresses
    everything as a fraction of whatever the extraction size turns out to be, not a fixed constant.
    Same ",fps={fps}" resample removal as before (a separate, also real, already-fixed bug) still
    applies - parse_source_meta() sniffs the source's true fps from ffmpeg's own stderr."""
    src_start = max(0.0, src_start)
    coarse = max(0.0, src_start - 5)
    fine = src_start - coarse
    return [
        "-hide_banner",
        "-ss", str(coarse),
        "-i", media_path,
        "-ss", str(fine),
        "-t", str(duration),
        # "showinfo" prints each output frame's real decoded pts_time to stderr - the only
        # trustworthy per-frame timestamp for genuinely variable-frame-rate source (confirmed via
        # Premiere's own Properties panel - "Variable Frame Rate Detected" - on a real host report
        # this exporter's own regex-based VFR heuristic missed: fps/tbr looked close enough to a
        # round 60 to pass, but real per-frame duration still wobbled by about +-1.5%, plenty to
        # explain a small but real cumulative Stabilize drift over 100+ frames). See
        # FrameExtractor._on_stderr/_PTS_RE for how these get collected.
        "-vf", "showinfo",
        "-q:v", "3",
        "-f", "image2",
        out_pattern,
    ]


def analyse_frame_timing(pts_times: list[float]) -> dict:
    """Flag source footage whose decoded frames are not evenly spaced in time.

    This is the failure mode that cost a whole debugging session (TECHNICAL_PLAN.md's Motion Tracker
    slice): Premiere conforms variable-frame-rate footage to a constant rate on import, duplicating
    or dropping frames to fill the irregular gaps, while this extractor decodes the source's own
    frames as they are. Wherever Premiere inserts or drops one, the plugin's frame *i* stops being
    the frame Premiere shows at that moment, and every keyframe after it is placed against the wrong
    picture. Nothing errors - the track simply never quite locks, which is indistinguishable by eye
    from a coordinate bug and was chased as one for hours.

    The signal is a single inter-frame interval that departs from the clip's own median, not the
    accumulated drift: the failing clip measured 16.667 ms throughout with ONE interval of 22.2 ms
    (+33%), which is exactly one place for Premiere to conform differently. Accumulated deviation
    stayed under half a frame overall and would not have caught it.

    Compares each interval against the median rather than a nominal rate, so it needs no assumption
    about what the file claims to be - the container's own advertised rate was 59.97 on a file whose
    real frames sit at 60.000.
    """
    if len(pts_times) < 10:
        return {"irregular": False, "reason": "too few frames to judge"}
    intervals = [pts_times[i + 1] - pts_times[i] for i in range(len(pts_times) - 1)]
    ordered = sorted(intervals)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return {"irregular": False, "reason": "no usable median interval"}
    # 20%: comfortably above the sub-1% jitter of genuinely constant-rate files (a transcoded CFR
    # control measured 16.666-16.667 ms), well below the +33% the real VFR case produced.
    offenders = [v for v in intervals if abs(v - median) / median > 0.20]
    worst = max(intervals, key=lambda v: abs(v - median)) if intervals else median
    return {
        "irregular": bool(offenders),
        "medianMs": round(median * 1000, 3),
        "worstMs": round(worst * 1000, 3),
        "worstDeviationPct": round(abs(worst - median) / median * 100, 1),
        "irregularCount": len(offenders),
        "totalIntervals": len(intervals),
    }


class FrameExtractor(QtCore.QObject):
    """Runs ffmpeg via QProcess for one extraction job. Emits `progress` zero or more times, then
    `finished` exactly once with either {"ok": True, "jobId", "framesDir", "frameCount", "frameW",
    "frameH", "srcFps", "vfrSuspect"} or {"ok": False, "error"}."""

    progress = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)

    _FRAME_RE = re.compile(r"frame=\s*(\d+)")
    # One "showinfo" line per output frame (see build_ffmpeg_args's own docstring for why this
    # exists at all - genuinely variable per-frame duration on real VFR source footage, confirmed
    # via Premiere's own "Variable Frame Rate Detected" on a real host report this file's own
    # fps/tbr-based heuristic below had missed).
    _PTS_RE = re.compile(r"pts_time:(-?\d+(?:\.\d+)?)")

    def __init__(self, motracker_dir: Path, parent=None):
        super().__init__(parent)
        self._motracker_dir = motracker_dir
        self._process: QtCore.QProcess | None = None
        self._stderr_head = ""
        self._stderr_partial_line = ""
        self._pts_times: list[float] = []
        self._expected_frames = 1
        self._job_dir: Path | None = None
        self._job_id = ""

    def start(self, media_path: str, src_start: float, duration: float, fps: float) -> None:
        ff = ffmpeg_path(self._motracker_dir)
        if not ff.exists():
            self.finished.emit({"ok": False, "error": f"FFmpeg not found at: {ff}"})
            return

        self._job_id, self._job_dir = make_job_dir()
        out_pattern = str(self._job_dir / "%06d.jpg")
        self._expected_frames = max(1, int((duration * fps) + 0.999999))
        self._stderr_head = ""
        self._stderr_partial_line = ""
        self._pts_times = []

        args = build_ffmpeg_args(media_path, src_start, duration, out_pattern)

        self._process = QtCore.QProcess(self)
        self._process.setProgram(str(ff))
        self._process.setArguments(args)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start()

    def cancel(self) -> None:
        if self._process is not None and self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.kill()

    def _on_stderr(self):
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardError()).decode(errors="replace")
        if len(self._stderr_head) < 16384:
            self._stderr_head += chunk
        # Line-buffered pts_time scanning - a raw per-chunk regex would occasionally miss or
        # truncate a pts_time value split across two reads (QProcess delivers arbitrary byte
        # chunks, not necessarily aligned to ffmpeg's own stderr line boundaries). Only complete
        # lines get scanned; any trailing partial line is held over for the next chunk.
        combined = self._stderr_partial_line + chunk
        lines = combined.split("\n")
        self._stderr_partial_line = lines.pop()
        for line in lines:
            pts_match = self._PTS_RE.search(line)
            if pts_match:
                self._pts_times.append(float(pts_match.group(1)))
        match = self._FRAME_RE.search(chunk)
        if match:
            done = int(match.group(1))
            pct = min(0.99, done / self._expected_frames)
            self.progress.emit({"pct": pct, "framesDone": done, "framesTotal": self._expected_frames})

    def _on_error(self, error):
        self.finished.emit({"ok": False, "error": f"Failed to run FFmpeg: {error}"})

    def _on_finished(self, exit_code: int, exit_status):
        if self._job_dir is None:
            return
        if exit_code != 0:
            self.finished.emit({"ok": False, "error": f"FFmpeg exited with code {exit_code}.\nCheck the media file is accessible."})
            return

        frames = sorted(str(p) for p in self._job_dir.glob("*.jpg"))
        if not frames:
            self.finished.emit({"ok": False, "error": "FFmpeg produced no frames. Verify the source range overlaps real video."})
            return

        # Read the actual extracted pixel size from the first frame rather than trusting a
        # dynamically-created <img>'s naturalWidth - moot in a native Qt UI (no <img> involved at
        # all), kept anyway since cv2 is already a dependency and this is authoritative either way.
        frame_w, frame_h = 0, 0
        try:
            import cv2
            first = cv2.imread(frames[0])
            if first is not None:
                frame_h, frame_w = first.shape[:2]
        except Exception:
            pass

        # Catch a trailing pts_time line that never got a final newline before the process exited.
        pts_match = self._PTS_RE.search(self._stderr_partial_line)
        if pts_match:
            self._pts_times.append(float(pts_match.group(1)))

        meta = parse_source_meta(self._stderr_head)
        # Real per-frame timestamps (seconds, 0-based relative to the FIRST extracted frame) -
        # the authoritative way to convert a tracked frame index into real elapsed time on genuinely
        # variable-frame-rate source, where "frame index / a single constant fps" silently drifts
        # (see build_ffmpeg_args's own docstring). showinfo sits upstream of the image2 muxer's own
        # "-t duration" cutoff, so it reliably logs exactly one MORE frame than actually gets
        # written whenever the requested duration lands just short of a frame boundary (confirmed
        # against a real clip: the extra timestamp's pts exceeds the requested duration) - drop
        # that trailing one. Any other mismatch falls back to None (caller uses a constant fps
        # instead) rather than handing over a list that might be misaligned in some other way.
        frame_timestamps = None
        if len(self._pts_times) == len(frames) + 1:
            self._pts_times = self._pts_times[:len(frames)]
        if len(self._pts_times) == len(frames):
            base = self._pts_times[0]
            frame_timestamps = [round(t - base, 6) for t in self._pts_times]

        # Irregular source timing is reported, never corrected here - see analyse_frame_timing.
        timing = analyse_frame_timing(self._pts_times)

        self.finished.emit({
            "ok": True,
            "timing": timing,
            "jobId": self._job_id,
            "framesDir": str(self._job_dir),
            "frameCount": len(frames),
            "frameW": frame_w,
            "frameH": frame_h,
            "srcFps": meta["fps"],
            "vfrSuspect": meta["vfrSuspect"],
            "frameTimestamps": frame_timestamps,
        })
