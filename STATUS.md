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
- **The Motion Tracker was removed entirely** — the user rebuilt it as its own Premiere UXP panel
  plugin, with satisfactory performance and more features than this one had. Gone from here:
  `companion/motracker/` (engine, ffmpeg extraction, Qt window), the `TOOL_WINDOWS` palette entry
  and its `tool_window` dispatch, `show_motion_tracker`, the adapter's `get_clip_info` /
  `get_follow_target_native_size` / `begin_apply_track`, the three `motracker.*` actions, and the
  `opencv-contrib-python` + `numpy` dependencies, which nothing else in the companion used. The
  vendored ~200 MB `ffmpeg.exe` and its `.gitignore` entry went with it. `performNest` (formerly
  `motrackerPerformNest`) stays — it backs `timeline.createNest` and only carried that prefix
  because it was extracted during the tracker's work. `TECHNICAL_PLAN.md` and
  `CAPABILITY_MATRIX.md` keep the full history, since they are an append-only decision log and a
  dated evidence record.
- Deleted, kept only in git history: `tools/template_generator/` (throwaway CEP dev panel that had
  already generated the bundled template sequences), `experimental/preset-assist/` (the rejected
  native-drag preset workflow), `scripts/reference_transport_server.py` (a mock companion, obsolete
  now that the real one exists).

## Still to do

- Delete `PremiereExecutionAdapter` + the CEP bridge functions from `app.py` once native Nest is
  confirmed not to need them (or once that path is reworked to not use `BRIDGE_FILE`).
- `timeline.createSubsequence` / `timeline.insertGenericItem` are still in the allowlist but the
  companion routes through `createNest` / `insertProjectItem` instead — verify and likely drop.

## Planned: cleanup, optimisation and UI pass (user's call, 2026-08-27)

A deliberate pass over the codebase — dead code out, optimise what measurement shows needs it, and
rebuild the UI. The Motion Tracker was to be first; it has since left this repository entirely, so
the pass now covers the rest of the product. Concrete candidates already observed, so this does not
start from a blank page:

**Dead / redundant code**

- `companion/app.py` carries **two parallel UI implementations, Qt and tkinter**, kept in feature
  parity by hand (`EffectPalette`/`QtEffectPalette`, `DebugWindow`/`QtDebugWindow` and helpers).
  Qt is the real UI; tkinter is legacy and doubles the cost of every UI-facing fix. Dropping it is
  the single largest simplification available in this repository.
- `PremiereExecutionAdapter` + the `send_command` / `read_bridge_status` CEP bridge helpers, kept
  alive only because the native-Nest watch path still shares `BRIDGE_FILE` (see above).
- The two allowlist entries named directly above.

**UI rebuild**

The palette and settings UI are worth redesigning as a whole rather than continuing to append
controls. (The Motion Tracker window, originally the main example here, has left this repository.)
