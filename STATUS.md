# FX.palette — current state

Short, current snapshot of what this repo is and what works. For the full decision trail see
`TECHNICAL_PLAN.md` (append-only slice log); for dated host evidence see `CAPABILITY_MATRIX.md`.
`UXP_HANDOFF.md`, `PRESET_UXP_RESEARCH.md` and `CEP_UXP_COMPARISON.md` are historical.

## What it is

FX.palette is a floating Windows companion for Adobe Premiere Pro:

- **Companion** (`companion/`, Python + PySide6) — the search palette, global hotkeys, settings,
  installer. `companion/app.py` is the whole app (Qt only); `companion/uxp_execution_adapter.py` is
  the Premiere-side boundary.
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
- **The 2026-09 audit changes below have not been host-tested yet.** Everything in that pass is
  covered by the off-host suites (`npm run validate`), which prove the transport/companion contract
  but not Premiere behavior. Host-confirmed so far (2026-09-04, user's session plus automation):
  catalogs listed after a fresh plugin connection, effect/preset apply, API Nest, native Nest
  (two runs, see below). Still to confirm: the debug window's Log / Diagnostico / Atualizar
  catalogos buttons, Label via the new focus-conditioned dispatch, and a UDT plugin reload keeping
  the companion connected.

## Automated checks

`npm run validate` runs, in order: manifest/JS syntax checks (`scripts/validate.js`), the plugin
tests (`tests/execution-adapter.test.js`, `tests/transport.test.js` — the latter drives
`transport.js` against a fake WebSocket), and the companion tests
(`companion/tests/run_tests.py` — the real `PremiereUxpExecutionAdapter` server against an
in-process WebSocket client, plus loader/search/helper tests, ~25 s, offscreen Qt). None of them
touch Premiere.

## Slop audit (2026-09-04)

A dead-code / reliability / performance pass over the whole repository. Details and evidence in
`TECHNICAL_PLAN.md` ("Slop audit"). Summary:

- **tkinter UI deleted.** `app.py` is Qt-only (8818 → ~5300 lines); the shared bits the Qt code
  borrowed from the tk class (`CATEGORY_TYPE_FILTERS`, the result-row model builder) are module
  level now. `EFFECT_PALETTE_UI=tk` is gone; `main()` exits with a message if PySide6 is missing.
- **CEP bridge deleted.** `PremiereExecutionAdapter`, `send_command`, `read_bridge_status`,
  `send_debug_command`, the `premiere_cmd.json` / `current_selection.json` paths, and the native-Nest
  "watch" that wrote to that bridge file. Nothing had read that file since the CEP worker left, so
  every native Nest paid a fixed 2 s wait and logged a bogus `native_nest_watch_arm_timeout`.
- **Catalog loading is UXP-only, and that fixed a real bug:** `EffectsLoader` required the CEP
  worker's `premiere_effects.json` to exist before it would read *any* catalog, so a fresh install
  without the old CEP product stayed on the fallback list forever, presets/project items/favorites
  included. It now reads `companion/data/uxp_*.json` directly.
- **Transport lifecycle** (`transport.js`): a superseded socket's late close/message events no
  longer touch the live connection, `stop()` can no longer be undone by the close event of the
  socket it closed, and a result whose socket died mid-handler is dropped instead of sent down the
  next connection. **Companion server**: a plugin reload (new connection before the old socket
  finished closing) no longer wipes the new client's state; catalog files are rewritten only when
  their content changed (favorites/project items were rewritten every 5 s, each rewrite firing the
  file watcher and a full search-index rebuild); a failed video-effect catalog pull is retried; a
  dropped client releases blocking requests immediately; unanswered requests are pruned.
- **Allowlist trimmed** to the actions the companion sends (`timeline.createSubsequence`,
  `timeline.insertGenericItem` and their handlers removed; tests assert they stay rejected). Now 16
  with `timeline.organizeNativeNest` (below).
- **Native Nest regression, same day, fixed and host-tested.** The audit called the bridge-file
  watch dead; on the dev machine the old CEP extension is still installed and was serving it,
  renaming the native Nest to `FXN-NNN` and filing it into "Nested Clips". That step now runs
  through the plugin: the companion resolves the codename up front, types it into Premiere's dialog,
  snapshots the project's sequence GUIDs, and after the dialog confirms polls the new
  `timeline.organizeNativeNest` action until the new sequence exists and is filed. Both native
  keystroke paths (Nest, Label) also wait until Premiere is actually foreground before sending.
  Two consecutive native Nests verified in Premiere (`TECHNICAL_PLAN.md`, last section).
- **Debug window** rebuilt around the companion's own log and live diagnostics (it read a CEP
  `worker.log` that no longer exists and sent commands to nobody). Beta reports bundle the
  `uxp_*.json` catalogs instead of the CEP files. `beta_report` no longer `mkdir`s and double-stats
  on every event.
- Removed leftovers: `capturedTransformCurveReference`, `QtHotkeyEditor` (superseded by
  `QtHotkeyCatalogEditor`), ~50 tk-era layout/colour constants, tk-compat shims on `QtRootAdapter`,
  write-only nest-mode preference, mojibake comments, `PIL.ImageTk` in the PyInstaller spec.

## Still to do

- Host-test the audit pass (see "In progress").
- **UI rewrite in QML** — branch `ui/qml-rewrite`. Phase 1 (view-model extraction into
  `companion/palette_view_models.py` and `companion/window_control.py`) is done and host-tested.
  Phase 2: the search palette is now QML (`companion/qml/`), with its shadow drawn in QML and
  click-through in the shadow margin. Host-tested 2026-09-11 on Premiere 26.5.0: rows render, no
  empty box on open, focused on the first press. Still to do in phase 2: PyInstaller QML packaging
  (plan Task 10), the host tests in plan Task 11 (margin click-through into Premiere, second
  monitor, native Nest and Label, animations off, the packaged build), and a visual design pass.
  The settings/shortcut/debug windows are phase 3. Recent actions and "Repeat last action" were
  removed on 2026-09-11. See `TECHNICAL_PLAN.md` (last slice),
  `docs/superpowers/plans/2026-09-08-qml-palette.md` and
  `docs/superpowers/specs/2026-09-07-companion-qml-ui-rewrite-design.md`.
