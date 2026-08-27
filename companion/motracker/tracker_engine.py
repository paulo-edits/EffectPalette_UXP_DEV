"""Point-tracking engine - ported verbatim from pFX-Tracker_UXP/daemon/tracker_engine.py
(read-only reference project, never modified in place - see TECHNICAL_PLAN.md's Motion Tracker
slice). The CSRT-hybrid tracking algorithm itself is unchanged from the original CEP product's
tracker/tracker.py - same CSRT + Lucas-Kanade + NCC fusion, same confidence/occlusion-recovery
logic. This function is entirely host-agnostic (no CEP/UXP/Qt awareness at all): it takes a job
dict and two plain callables and returns a track list, so it runs unmodified whether called from
an asyncio worker thread (the original daemon) or a Qt QThread worker (here).
"""

from __future__ import annotations

import math
import os
import queue
import threading
from typing import Callable, Optional

import cv2
import numpy as np

try:
    cv2.setUseOptimized(True)
except Exception:
    pass


def load_pair(path):
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError("Cannot read frame: " + path)
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


class FramePrefetcher:
    def __init__(self, paths, bufsize=3):
        self._q = queue.Queue(maxsize=max(1, bufsize))
        self._paths = paths
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        for p in self._paths:
            try:
                bgr = cv2.imread(p)
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr is not None else None
            except Exception:
                bgr, gray = None, None
            self._q.put((bgr, gray))
        self._q.put(None)

    def get(self):
        return self._q.get()


def extract_patch(gray, cx, cy, radius):
    h, w = gray.shape
    x1, y1 = int(max(0, cx - radius)), int(max(0, cy - radius))
    x2, y2 = int(min(w, cx + radius)), int(min(h, cy + radius))
    p = gray[y1:y2, x1:x2]
    return p.copy() if p.size > 0 else None


def make_csrt():
    try:
        if hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        if hasattr(cv2, "TrackerCSRT") and hasattr(cv2.TrackerCSRT, "create"):
            return cv2.TrackerCSRT.create()
        if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
            return cv2.legacy.TrackerCSRT_create()
    except Exception:
        return None
    return None


def csrt_init(bgr, cx, cy, radius):
    h, w = bgr.shape[:2]
    x = max(0, int(cx - radius))
    y = max(0, int(cy - radius))
    bw = min(w - x, int(radius * 2))
    bh = min(h - y, int(radius * 2))
    if bw < 4 or bh < 4:
        return None
    trk = make_csrt()
    if trk is None:
        return None
    try:
        trk.init(bgr, (x, y, bw, bh))
    except Exception:
        return None
    return trk


def ncc_at(gray, template, cx, cy, search_r):
    if template is None:
        return 0.0, None
    h, w = gray.shape
    th, tw = template.shape[:2]
    sx1, sy1 = int(max(0, cx - search_r)), int(max(0, cy - search_r))
    sx2, sy2 = int(min(w, cx + search_r)), int(min(h, cy + search_r))
    roi = gray[sy1:sy2, sx1:sx2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return 0.0, None
    res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    bx = float(sx1 + max_loc[0] + tw / 2.0)
    by = float(sy1 + max_loc[1] + th / 2.0)
    return max(0.0, float(max_val)), (bx, by)


def median_filter(values, window):
    """Odd-window median - kills single/double-frame outlier "pops" without blurring the
    surrounding trajectory. Ported from pFX-Tracker/js/main.js's medianFilterJS. window=3 here
    (not the original's 5) - a wider window flattens real small-amplitude motion along with
    jitter, which is exactly the complaint that prompted porting this at all; 3 still catches
    isolated spikes but preserves more genuine small movement."""
    if window < 3 or len(values) < window:
        return list(values)
    radius = (window - 1) // 2
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - radius), min(len(values) - 1, i + radius)
        out.append(sorted(values[lo:hi + 1])[(hi - lo) // 2])
    return out


def one_euro_pass(values, rate, mincutoff, beta, dcutoff):
    """Single causal pass of the One-Euro filter (Casiez et al.) - a velocity-adaptive low-pass
    that smooths hard when the point is nearly still (killing jitter) but opens up when it moves
    fast (so real pans stay crisp with little lag). Ported from main.js's oneEuroPass."""
    def alpha(cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / rate
        return 1.0 / (1.0 + tau / te)

    out = [0.0] * len(values)
    x_prev = values[0] if values else 0.0
    dx_prev = 0.0
    a_d = alpha(dcutoff)
    for i, x in enumerate(values):
        first = i == 0
        dx = 0.0 if first else (x - x_prev) * rate
        edx = dx if first else (a_d * dx + (1.0 - a_d) * dx_prev)
        cutoff = mincutoff + beta * abs(edx)
        a_c = alpha(cutoff)
        x_hat = x if first else (a_c * x + (1.0 - a_c) * x_prev)
        out[i] = x_hat
        x_prev, dx_prev = x_hat, edx
    return out


def one_euro_zero_phase(values, rate, mincutoff, beta):
    """Forward AND backward One-Euro, averaged - near zero-phase, so the smoothed track doesn't
    drift behind real motion. Ported from main.js's oneEuroZeroPhase."""
    if len(values) < 2:
        return list(values)
    dcutoff = 1.0
    fwd = one_euro_pass(values, rate, mincutoff, beta, dcutoff)
    bwd = list(reversed(one_euro_pass(list(reversed(values)), rate, mincutoff, beta, dcutoff)))
    return [(f + b) / 2.0 for f, b in zip(fwd, bwd)]


def deshake_tracks(tracks, strength, fps):
    """Post-track de-shake, applied as its own pass over already-tracked data - NOT during
    track() itself, mirroring pFX-Tracker's real "Smooth" button (main.js's smoothTrack), which
    always tracks raw (smoothVal always 0 into the tracker) and de-shakes separately so re-tuning
    the strength never compounds against an already-smoothed result. strength 0 is a no-op."""
    if strength <= 0 or len(tracks) < 2:
        return [dict(t) for t in tracks]
    rate = fps if fps and fps > 0 else 30.0
    mincutoff = 0.5 / strength
    beta = 0.02
    xs_raw = [t["x"] for t in tracks]
    ys_raw = [t["y"] for t in tracks]
    xs = one_euro_zero_phase(median_filter(xs_raw, 3), rate, mincutoff, beta)
    ys = one_euro_zero_phase(median_filter(ys_raw, 3), rate, mincutoff, beta)
    out = []
    for i, t in enumerate(tracks):
        entry = dict(t)
        entry["x"] = round(xs[i], 2)
        entry["y"] = round(ys[i], 2)
        out.append(entry)
    return out


class Cancelled(Exception):
    pass


def track(job: dict, on_progress: Optional[Callable[[dict], None]] = None,
          should_cancel: Optional[Callable[[], bool]] = None) -> dict:
    """Run one tracking pass. Returns {"tracks": [...], "engine": "..."}.

    Raises Cancelled if should_cancel() returns True mid-run, or RuntimeError
    on a hard failure (no frames, empty range) - mirrors tracker.py's
    {"type":"error"} emission, just as a Python exception instead.
    """
    frames_dir = job["framesDir"]
    start_frame = int(job.get("startFrame", 0))
    pt = job["point"]
    direction = job.get("direction", "forward")
    search_r = int(job.get("searchDiam", 200)) // 2
    feature_r = int(job.get("featureDiam", 60)) // 2
    conf_thresh = float(job.get("confThresh", 40)) / 100.0
    tol = int(job.get("tol", 8))

    files = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith(".jpg"))
    if not files:
        raise RuntimeError("No JPEG frames in: " + frames_dir)
    total = len(files)

    lk_params = dict(
        winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    subpix_win, subpix_zero = (5, 5), (-1, -1)
    subpix_crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)

    if direction == "forward":
        end_frame = int(job.get("endFrame", total - 1))
        indices = list(range(start_frame, min(total, end_frame + 1)))
    else:
        end_frame = int(job.get("endFrame", 0))
        indices = list(range(start_frame, max(-1, end_frame - 1), -1))
    if not indices:
        raise RuntimeError("Empty frame range.")

    x0, y0 = float(pt["x"]), float(pt["y"])
    prev_bgr, prev_gray = load_pair(os.path.join(frames_dir, files[indices[0]]))
    h, w = prev_gray.shape

    template = extract_patch(prev_gray, x0, y0, feature_r)
    curr_pt = np.array([[[x0, y0]]], dtype=np.float32)
    csrt = csrt_init(prev_bgr, x0, y0, feature_r)
    use_csrt = csrt is not None

    results = {indices[0]: {"x": x0, "y": y0, "conf": 1.0}}
    lost_streak = 0
    n_steps = len(indices) - 1
    prev_x, prev_y = x0, y0

    prefetch = FramePrefetcher([os.path.join(frames_dir, files[i]) for i in indices[1:]])

    for step, idx in enumerate(indices[1:], 1):
        if should_cancel and should_cancel():
            raise Cancelled()
        item = prefetch.get()
        if item is None:
            break
        curr_bgr, curr_gray = item
        if curr_gray is None:
            break

        cx_c = cy_c = None
        if csrt is not None:
            try:
                ok_csrt, box = csrt.update(curr_bgr)
            except Exception:
                ok_csrt, box = False, None
            if ok_csrt and box is not None:
                cx_c = float(box[0] + box[2] / 2.0)
                cy_c = float(box[1] + box[3] / 2.0)
        else:
            ok_csrt = False

        next_pt, status, err = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, curr_pt.copy(), None, **lk_params)
        lk_ok = next_pt is not None and status is not None and status[0, 0] == 1
        cx_l = float(next_pt[0, 0, 0]) if lk_ok else None
        cy_l = float(next_pt[0, 0, 1]) if lk_ok else None

        if cx_c is not None and lk_ok:
            dist = ((cx_c - cx_l) ** 2 + (cy_c - cy_l) ** 2) ** 0.5
            if dist <= max(4, feature_r):
                cx = 0.5 * cx_c + 0.5 * cx_l
                cy = 0.5 * cy_c + 0.5 * cy_l
            else:
                cx, cy = cx_c, cy_c
        elif cx_c is not None:
            cx, cy = cx_c, cy_c
        elif lk_ok:
            cx, cy = cx_l, cy_l
        else:
            cx, cy = prev_x, prev_y

        conf_r = max(feature_r + 8, search_r // 3) if (use_csrt and ok_csrt) else search_r
        ncc_conf, ncc_loc = ncc_at(curr_gray, template, cx, cy, conf_r)

        if use_csrt:
            if not ok_csrt and ncc_loc is not None and ncc_conf > 0.5:
                cx, cy = ncc_loc
                csrt = csrt_init(curr_bgr, cx, cy, feature_r)
        else:
            if ncc_loc is not None and ncc_conf > 0.35:
                alpha = min(1.0, (ncc_conf - 0.35) / 0.4)
                cx = cx * (1.0 - alpha) + ncc_loc[0] * alpha
                cy = cy * (1.0 - alpha) + ncc_loc[1] * alpha

        conf = ncc_conf
        if use_csrt and not ok_csrt and not lk_ok:
            conf *= 0.5
        conf = max(0.0, min(1.0, conf))

        if conf < conf_thresh:
            lost_streak += 1
        else:
            lost_streak = 0
        if lost_streak > tol:
            conf = 0.0

        cx = max(0.0, min(float(w - 1), cx))
        cy = max(0.0, min(float(h - 1), cy))

        if conf > 0.65:
            try:
                corner = np.array([[[cx, cy]]], dtype=np.float32)
                cv2.cornerSubPix(curr_gray, corner, subpix_win, subpix_zero, subpix_crit)
                rx, ry = float(corner[0, 0, 0]), float(corner[0, 0, 1])
                if abs(rx - cx) <= 2 and abs(ry - cy) <= 2:
                    cx, cy = rx, ry
            except Exception:
                pass
            new_tmpl = extract_patch(curr_gray, cx, cy, feature_r)
            if new_tmpl is not None:
                template = new_tmpl

        results[idx] = {"x": round(cx, 2), "y": round(cy, 2), "conf": round(conf, 3)}
        curr_pt = np.array([[[cx, cy]]], dtype=np.float32)
        prev_gray, prev_bgr = curr_gray, curr_bgr
        prev_x, prev_y = cx, cy

        if on_progress:
            pct = step / n_steps if n_steps > 0 else 1.0
            on_progress({"frame": idx, "total": total, "pct": round(pct, 4),
                         "x": round(cx, 2), "y": round(cy, 2), "conf": round(conf, 3)})

    frames_sorted = sorted(results.keys())
    tracks = [{"frame": f, **results[f]} for f in frames_sorted]
    return {"tracks": tracks, "engine": "csrt-hybrid" if use_csrt else "lk-template"}
