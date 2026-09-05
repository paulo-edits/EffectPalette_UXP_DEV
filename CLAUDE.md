# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FX.palette is a floating search-palette companion for Adobe Premiere Pro. It was migrated from a
CEP extension to UXP; the migration is complete and the CEP bridge is gone. Two processes:

- **UXP plugin** — repo root (`index.js`, `execution-adapter.js`, `transport.js`, `manifest.json`,
  `index.html`). Runs headless inside Premiere 25.6+; no panel UI.
- **Python companion** — `companion/`. The actual palette UI (PySide6/Qt only), global hotkeys,
  settings, installer.

They connect over an outbound-only, token-authenticated WebSocket the plugin opens to the companion
at `ws://localhost:58756`. The **companion is the server**, the plugin is the client that reconnects.

**Read `STATUS.md` first** for the current state. `TECHNICAL_PLAN.md` is the append-only decision
log (every "slice" of work, with host-test evidence); `CAPABILITY_MATRIX.md` is the dated evidence
table. Early sections of `TECHNICAL_PLAN.md` and `README.md` still say "proof of concept" and cite
stale numbers — trust `STATUS.md`.

## Commands

```bash
npm run validate                       # manifest checks + plugin tests + companion tests (the automated suite)
npm run test:plugin                    # node tests/execution-adapter.test.js && node tests/transport.test.js
npm run test:companion                 # python companion/tests/run_tests.py (unittest, offscreen Qt, ~25 s)
python -m pyflakes companion/*.py      # lint (pyflakes/vulture are dev-only, not in requirements.txt)
```

No build step for the plugin — it loads from source via the UXP Developer Tool (UDT 2.2+):
Add Plugin → `manifest.json` → Load & Watch against a running Premiere. Manifest changes need
Unload + Load & Watch; JS/HTML changes hot-reload.

```bash
cd companion && pip install -r requirements.txt
python app.py                          # Qt UI (the only UI; exits with a message if PySide6 is missing)
```
Single-instance mutex — a second launch just exits. Palette hotkey Ctrl+Space, quit Ctrl+Q.

Installer (Windows, `packaging/`): `packaging/build_release.ps1` (PyInstaller + Inno Setup + Adobe
UPIA silent install). The plugin `.ccx` is packaged manually via UDT's Package menu — no CLI for it.

## Architecture

### The action boundary

Every Premiere operation is one serializable action `{ schemaVersion, type, requestId, payload }`
returning `{ ok, actionType, requestId, data | error }`. Failures echo `requestId` too — the
companion correlates responses by it alone.

- **`execution-adapter.js`** — `SUPPORTED_ACTIONS` allowlist (16 actions) + `normalizeAction`/
  `execute`. Unknown or unhandled actions fail closed. This list must stay equal to what the
  companion actually sends — `tests/execution-adapter.test.js` asserts both the supported set and
  that removed actions stay rejected.
- **`index.js`** — `ACTION_HANDLERS` maps every action type to its handler; all handlers live here.
  `entrypoints.plugin.create()` starts the transport and restores the persisted `.prfpset` access
  token. The one visible entrypoint (`headlessSetVioletLabel` command) exists only as the
  "runs with no panel open" proof.
- **`transport.js`** — the WebSocket client. Dispatches every post-handshake message through
  `executionAdapter.execute(msg, ACTION_HANDLERS)` — the same map the plugin uses internally, so a
  network caller can never reach a path the plugin doesn't already expose. Every socket listener
  checks it still belongs to the live socket, and `stop()` is final (no reconnect after it);
  `tests/transport.test.js` covers both against a fake WebSocket.
- **`companion/uxp_execution_adapter.py`** — the WebSocket **server** and request/response
  bookkeeping (`_pending`, `poll_status`, `is_terminal`). Per-effect deadlines live only in its
  `EFFECT_TIMEOUT_SECONDS`. Also runs the catalog pulls (effects, transitions, favorites, project
  items, presets) that it writes to `companion/data/uxp_*.json` (gitignored) — only when the
  content changed, since favorites/project items are re-polled every 5 s.

### Companion

- **`companion/app.py`** (~5300 lines) is the whole app: settings/i18n helpers, `EffectsLoader`
  (catalog loading + tiered search index), Win32 helpers (hotkeys, `.kys` shortcut synthesis,
  native dialog driving), then the Qt classes (`QtEffectPalette`, `QtSettingsCenter`,
  `QtHotkeyCatalogEditor`, `QtDebugWindow`), `SystemTrayController`, `HotkeyListener`, `main()`.
  `QtRootAdapter` is the small `after`/`after_cancel`/`post` scheduler the non-Qt helpers use.
- `EffectsLoader` reads **only** the `uxp_*.json` catalogs. With none present it shows the small
  built-in `FALLBACK_EFFECTS` list (`source == "fallback"`, connection dot "offline").
- `create_execution_adapter()` returns `PremiereUxpExecutionAdapter`, or a stub that fails every
  action closed if the server can't bind its port.
- Operations with no UXP API stay companion-side: Timeline-clip Label / Label group (reads the
  bound shortcut from the user's Premiere `.kys` and synthesizes the keystroke — provisioned by
  `scripts/configure_premiere_shortcuts.ps1`), native Nest (keystroke + typing the name into
  Premiere's own dialog, then `timeline.organizeNativeNest` renames/files the result into the bin),
  creating a Timeline track (drives Premiere's own "Add Tracks…" dialog), and global hotkeys
  (Win32 `RegisterHotKey`). Synthesized keystrokes go through `dispatch_when_window_foreground`.
- The dev machine still has the old **CEP extension installed and running** inside Premiere. It
  writes its own files under `%APPDATA%\Adobe\CEP\extensions\EffectPalette\data`; nothing in this
  repo reads them any more, but don't mistake its side effects (or its absence on a user's PC) for
  this product's behavior.
- `beta_report.py` writes local-only logs/telemetry under `Documents/FX.palette_Beta_Report`
  (`FX_PALETTE_REPORT_DIR` overrides it; the tests point it at a temp folder).

### Preset application

`timeline.applyImportedEffectPreset` reconstructs a preset from the user's `.prfpset` — Premiere
exposes no native preset-apply API. Static values plus animated easing decoded from the file's own
speed/influence fields and frame-sampled (`reconstructEasing`, on by default). Presets that can't be
reconstructed faithfully (opaque `ArbVideoComponentParam` — Lumetri Color, Sapphire, etc.) fail
closed with a named error rather than applying a wrong result.

## Hard constraints

- **The plugin is official-UXP-only.** No CEP, ExtendScript, QE DOM (`qe.*`), `app.executeCommand`,
  arbitrary script execution, or unreviewed filesystem permissions in `index.js` / `transport.js`.
  The stable CEP source (`paulo-edits/Effect-Palette*`) may be read as a behavior reference, never
  as license to add a fallback.
- **Never modify the stable CEP repositories.** Files may be copied FROM them INTO this repo; the
  originals stay untouched.
- **The product must work with zero / near-zero user configuration** — no setup beyond the one-time
  install and a single `.prfpset` file grant.
- **Do not report a Premiere/UXP behavior, or a finished feature, without a real host test in
  Premiere.** A `CAPABILITY_MATRIX.md` row only becomes "Tested in Premiere" with a recorded
  version and evidence. Never present unverified API behavior as confirmed. The automated suites
  are off-host: they prove the transport/companion contract, not Premiere behavior.

## Conventions

- Adding an action: extend `SUPPORTED_ACTIONS` (`execution-adapter.js`) + `ACTION_HANDLERS`
  (`index.js`) + the assertions in `tests/execution-adapter.test.js`. Keep the allowlist minimal —
  only what the companion sends. Removed actions go into the test's "must stay rejected" list.
- `companion/data/uxp_*.json` and `premiere_shortcut_configuration.log` are runtime-generated and
  gitignored — never commit them.
- Settings still live at `%APPDATA%\Adobe\CEP\extensions\EffectPalette\settings.json` (the path the
  CEP product used) so existing users keep their aliases/hotkeys/language. Moving them is a product
  decision, not a cleanup.
- The Motion Tracker was removed from this repository in 2026-08 and now ships as its own
  Premiere UXP plugin; `TECHNICAL_PLAN.md` and `CAPABILITY_MATRIX.md` keep its history. The tkinter
  UI and the CEP bridge were removed in the 2026-09 audit (git history keeps them).
- `EFFECT_IDENTITY_MATRIX.json` is a required fixture for `validate.js` (asserts 829 entries), not
  loaded by the plugin.
- `.gitattributes` is intentionally left untracked — a repo-wide `eol=lf` renormalization is a
  pending decision. Keep it out of commits until the user resolves it.
