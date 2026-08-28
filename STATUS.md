# FX.palette — current state

Short, current snapshot of what this repo is and what works. For the full decision trail see
`TECHNICAL_PLAN.md` (append-only slice log); for dated host evidence see `CAPABILITY_MATRIX.md`.
`UXP_HANDOFF.md`, `PRESET_UXP_RESEARCH.md` and `CEP_UXP_COMPARISON.md` are historical.

## What it is

FX.palette is a floating Windows companion for Adobe Premiere Pro:

- **Companion** (`companion/`, Python + PySide6) — the search palette, global hotkeys, settings,
  installer. `companion/app.py` is the whole app; `companion/uxp_execution_adapter.py` is the
  Premiere-side boundary.
- **UXP plugin** (`index.js`, `execution-adapter.js`, `transport.js`, `manifest.json`) — runs
  headless inside Premiere 25.6+. **No panel**: the only entrypoint is one headless command; all
  real work arrives over the transport. `index.html` is a bootstrap shell with no UI.

They talk over an outbound-only WebSocket the plugin opens to the companion at
`ws://localhost:58756`, token-authenticated (`transport.js` ⇄ `uxp_execution_adapter.py`, same
constants). Every message after the handshake is one `execution-adapter.js` action.

Plugin version: see `manifest.json`. Permissions requested: `clipboard: readAndWrite`,
`localFileSystem: fullAccess` (auto-locates the user's `.prfpset`), and the single localhost
network domain.

## What works (host-tested, per `CAPABILITY_MATRIX.md` / `TECHNICAL_PLAN.md` stage-4 assessment)

UXP has parity or better for everything the product uses:

- video / audio effect apply (identity-verified)
- effect preset apply, reconstructed from the `.prfpset` — static values, animated scalar/Point
  easing (`reconstructEasing`, on by default), overshoot, curved paths, intrinsic effects
  (Motion/Opacity), multiple clips, slider/toggle audio effects
- video transition apply
- Nest (auto-routed native/API), default `FXN-NNN` codename, filing into a bin
- insert existing project item, favorite item (media), generic item (Adjustment Layer, Bars and
  Tone, Black Video, Transparent Video) via bundled template `.prproj`
- create a Timeline track when none is free (drives Premiere's own "Add Tracks…" dialog)
- Timeline-clip Label / Label group — companion sends the bound `.kys` shortcut (never was a
  DOM/UXP operation on either platform)
- runs with no panel open

Confirmed platform gaps (no UXP path, and no CEP path either): audio transitions; Color Matte /
Universal Counting Leader creation; preset reconstruction of effects with a graphical curve UI
(Lumetri Color, Sapphire, …) — these fail closed with a named error rather than a bad result.

## In progress / not yet verified

- **Motion Tracker** (`companion/motracker/`, `motracker.*` actions) — active development.
  2026-08-27: a restructure that nested the apply target was tried and **reverted**. `applyTrack`
  applies the Transform directly to the selected clip again, matching the stable CEP tool: Anchor
  Point is normalised over the clip's own frame, which is exactly the space the tracker measures in,
  so mixed aspect ratios need no sequence math and nesting actively breaks it. Removed for good:
  the `motracker.testNest` / `[TESTE]` scaffolding. Changed and kept: the apply target is always the
  current Timeline selection in both modes. **Root cause found and host-confirmed 2026-08-27: VFR
  source footage.** The failing clip is CFR 60 throughout except one 22.2 ms interval; transcoded to
  true CFR it locks perfectly with the same track and the same code. A second, smaller artifact was
  keyframe phase slip - timing keyframes by the container's own pts put them 16.695 ms apart on a
  16.667 ms sequence grid, drifting 0.35 frame across the clip and flipping the rounding once, which
  produced a single visible deviation. Uniform timing at `frameCount / durationSec` is now the
  default and removed it. Verified along the way: the Anchor Point coordinate math, the tracker's
  accuracy (sub-2 px over 205 frames), and that Effect Controls stores exactly the computed values.
  Irregular source timing is now **detected and warned about** (`analyse_frame_timing`, flagging any
  inter-frame interval more than 20% off the clip's own median), so the user is told to convert the
  clip instead of getting a silently wrong track. **Still open:** the conversion itself is manual;
  automating it means conforming the source during extraction, deliberately deferred.
  **Seguir Rastro fixed and host-confirmed 2026-08-27**: its coordinate conversion had the same
  per-axis "footage fills the frame" assumption, measured 1.33x too far in X and 2.37x too short in
  Y; it now converts via the tracked clip's Motion Scale, the object's real pixel size and the
  object's own Motion Scale. The object-scale term and
  `MOTRACKER_GEOMETRY2_USE_COMP_SHUTTER_INDEX` (= 9) were both host-confirmed the same day, closing
  the last two small unknowns. Also still open: PyInstaller packaging with `cv2` + bundled `ffmpeg.exe` (~200 MB) not re-validated.
- **Favorite item that is a whole sequence** — built, imports as a nested clip, not host-tested.

## Recent cleanup (2026-08, this pass)

- The former diagnostics **panel** was removed from the shipped plugin (`index.html` body,
  `styles.css`, `wirePanel`, the `run*` wrappers). The plugin declares no `panel` entrypoint; it
  runs headless.
- The **research / diagnostic probe actions** (`probe*`, `captureTransformCurveReference`,
  `applyTransformCurveReference`, `compareImportedTransform`, `inspectSelectedVideoComponents`,
  `catalog.videoEffects.resolve`, `inspectImported`) and the rejected preset **bridge**
  (`buildImportedPresetBridge` / `exportBridge` / `inspectBridgeCandidate`) were removed from
  `index.js` and the allowlist. `execution-adapter.js` `SUPPORTED_ACTIONS` is now 21, matching
  what the companion sends. What each probe found is preserved in `TECHNICAL_PLAN.md` /
  `CAPABILITY_MATRIX.md`; the code itself is in git history.
- `companion/app.py`: `create_execution_adapter()` no longer falls back to the CEP bridge — if the
  UXP transport can't start it returns a stub that fails actions closed. `PremiereExecutionAdapter`
  and the `send_command` / `read_bridge_status` bridge helpers are still physically present because
  the **native-Nest watch path** (`arm_native_nest_watch` / `dispatch_when_native_nest_watch_ready`)
  shares the same `BRIDGE_FILE`. Fully deleting them needs the native-Nest path re-tested first —
  separate task.
- Deleted, kept only in git history: `tools/template_generator/` (throwaway CEP dev panel that had
  already generated the bundled template sequences), `experimental/preset-assist/` (the rejected
  native-drag preset workflow), `scripts/reference_transport_server.py` (a mock companion, obsolete
  now that the real one exists).

## Still to do

- Delete `PremiereExecutionAdapter` + the CEP bridge functions from `app.py` once native Nest is
  confirmed not to need them (or once that path is reworked to not use `BRIDGE_FILE`).
- `timeline.createSubsequence` / `timeline.insertGenericItem` are still in the allowlist but the
  companion routes through `createNest` / `insertProjectItem` instead — verify and likely drop.
