# Companion UI rewrite: QtWidgets to QML (Qt Quick)

Date: 2026-09-07
Status: approved design, not yet implemented
Branch: `ui/qml-rewrite`

## Problem

The companion's UI reads as amateur work, and the secondary windows are the worst offenders.
Three concrete complaints from the user:

1. It looks dated / cheap.
2. It is painful to restyle.
3. It feels limited — no real motion.

The root causes are visible in `companion/app.py`:

- **No design system.** Every window builds its own QSS string inline. `QtSettingsCenter._build`
  ends in a `setStyleSheet` blob using `#17171b` / `#1d1d22` / `#24242b` / `#3541a5`;
  `QtResultRowWidget.apply_state` builds a different token set per row; `QtHotkeyCatalogEditor`
  and `QtDebugWindow` each carry another blob. The palettes have drifted apart.
- **Secondary windows are raw `QDialog`s.** The palette (`QtPaletteWindow`) is frameless and
  translucent and has had attention. Settings, shortcut editor, alias picker and debug are plain
  OS-chrome dialogs with a stylesheet dumped on top. That contrast is what reads as amateur.
- **The ugliest default widgets are all in settings** — `QTabWidget`, `QTableWidget`,
  `QTreeWidget`, `QHeaderView`.
- **Motion is ad hoc.** `QtResultRowWidget.animate_in` / `animate_selection` and
  `_animate_window_height` / `_animate_window_geometry` / `_animate_window_opacity` each
  hand-roll a `QPropertyAnimation` with magic durations.

Separately, the product has **no app icon**: `packaging/pyinstaller/FXPalette.spec` passes no
`icon=` to `EXE(...)`, no `.ico` exists in the repo, and `FXPalette.iss` points
`UninstallDisplayIcon` at that icon-less exe — so Windows' "Installed apps" shows the generic
default glyph.

## Approach chosen

Rewrite the view layer in **QML (Qt Quick)**, keeping all Python logic. Considered and rejected:

- **QtWidgets design-system pass** — cheaper and lower risk, and it would fix complaints 1 and 2,
  but the ceiling on motion is exactly what the user wants. Rejected on the user's explicit
  preference for motion.
- **Non-Qt engine (WinUI 3 / web / Flutter)** — full rewrite of the Python and Win32 layers,
  eliminates the macOS option, no functional gain. Rejected.

Qt Quick keeps the language (Python), the fragile OS-integration code, the installer pipeline and
the cross-platform position, while giving a GPU-accelerated scenegraph, a declarative animation
system and real theming.

## Architecture

### Principle

All state and logic live in testable Python `QObject`s. QML is a pure projection with motion.
Nothing network-, Premiere- or Win32-facing moves.

### Stays pure Python, unchanged or nearly so

- `EffectsLoader`, `create_execution_adapter`, `companion/uxp_execution_adapter.py`
- `QtRootAdapter` (the `after` / `after_cancel` / `post` scheduler)
- `HotkeyListener`, `SystemTrayController`, the watchdog file watcher, the Premiere monitor
- Every Win32 and `.kys` helper, `beta_report`

### New headless view-model layer (pure Python `QObject`s, no QML import)

- **`PaletteViewModel`** — observable Qt properties (`query`, `activeCategory`,
  `connectionState`, `applyState` in {idle, busy, success, error}, `statusText`, `footerHint`,
  `selectedIndex`) plus `@Slot` methods QML calls: `setQuery`, `selectCategory`, `moveSelection`,
  `applySelected`, `confirmNest`, `cancelNest`, `manualRefresh`.
- **`ResultsModel(QAbstractListModel)`** — wraps the `ResultRowModel` list with named roles
  (title, subtitle, iconGlyph, typeLabel, accentKind, accentColor). A QML `ListView` binds to it,
  which makes add / remove / reorder motion declarative and replaces the chunked
  `_append_result_rows` renderer.
- **`ApplyController`** — the apply state machine lifted out verbatim: `_begin_apply`,
  `_begin_apply_with_track_check`, `_dispatch_apply`, `_poll_apply_status`, `_complete_apply`,
  `_finish_successful_apply`.
- **`PaletteWindowController`** — the native window layer: `_window_hwnd`,
  `_activate_window_native`, `_force_focus_attempt`, `_cancel_focus_attempts`,
  `_remember_previous_focus`, `_restore_previous_focus`, `_anchor_window_to_pointer`. Operates on
  a window handle it is given, so it is independent of whether that window is a `QWidget` or a
  `QQuickWindow`.

### Becomes QML

`Palette.qml` (frameless root) composed of `SearchField.qml`, `CategoryBar.qml`,
`ResultList.qml` + `ResultRow.qml`, `NestPanel.qml`, `Footer.qml`; then `SettingsWindow.qml`,
`ShortcutEditor.qml`, `AliasPicker.qml`, `DebugWindow.qml`; plus the `Theme.qml` singleton.

### Engine wiring

One `QQmlApplicationEngine`. A root `App` `QObject` exposed as a context property hands QML the
view-models and the i18n callable. `QQuickStyle.setStyle("Basic")` is set explicitly so the
Controls style is deterministic rather than inheriting a native style. `main()` changes from
constructing widget classes to constructing view-models, loading QML, and connecting signals.

## Theme and motion

`Theme.qml` is a QML singleton (registered via `qmldir`) and the single source of truth for:

- **Color ramp** — surface levels (base, raised, overlay), border, text primary/secondary/muted,
  accent and variants, semantic success/warning/error, and the per-category accent colors that
  currently live in `get_row_visual_tokens`. Consolidates the drifted hexes listed under Problem.
- **Spacing scale** (4/8/12/16/20/24), **radii** (6/10/14/pill), **elevation** specs.
- **Type scale** (display/title/body/caption) with weights. The font family stays resolved in
  Python by `_choose_qt_font_family` (Google Sans Flex, else Segoe UI) and is exposed as a
  property.
- **Motion tokens** — durations (fast ~120 ms, base ~180 ms, slow ~260 ms) and named easing
  curves (standard, decelerate, accelerate, overshoot). No animation hardcodes a number.

The existing `animations` user preference (`load_app_preferences()["animations"]`, surfaced in
settings) becomes `Theme.animationsEnabled`, and every `Behavior` / `Transition` gates on it. This
preference must keep working.

### Motion inventory

| Interaction | Replaces |
| --- | --- |
| Window open / close: scale + fade | `_animate_window_opacity`, `_animate_window_geometry` |
| Window height follows result count | `_animate_window_height`, `_resize_to_content` |
| Staggered result-row entry, list add/remove/displaced transitions | `QtResultRowWidget.animate_in`, `_append_result_rows` chunking |
| Single highlight rectangle sliding between rows | `QtResultRowWidget.animate_selection`, per-row `apply_state` restyle |
| Category pill slides to the active filter | `_style_category_button` |
| Footer busy to success / error transition | `_set_apply_busy`, `_set_idle_state` |
| Nest panel expands in place | `_show_nest_options` inline panel |
| Connection dot pulses on state change | `_update_connection_indicator` |

### Translucency (experimental)

Transparent `QQuickWindow` plus a surface `Rectangle`, with an optional Windows 11 acrylic/mica
pass via `DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)` on the window HWND. Wrapped in
try/except, exposed as an experimental preference **defaulting off**. Explicitly a thing to play
with; the visual design must stand on the solid surface without it.

## App identity

One root cause, several outlets. Independent of the QML work and can land at any point.

- Assets `companion/assets/fx_palette.ico` (16/24/32/48/64/128/256) and `fx_palette.png` (512).
  **Placeholder art exists as of 2026-09-07** — generated by `scripts/make_app_icon.py`: the
  product's own prompt glyph (`>_`, the `>` the search field shows at `app.py:3680`) on a rounded
  indigo→violet tile. A shape rather than letterforms, so it stays legible in the 16 px "Installed
  apps" row. **The user will design the real mark later**; when that lands, drop in the replacement
  assets and delete the generator script.
- `packaging/pyinstaller/FXPalette.spec`: pass `icon=` to `EXE(...)`, and add a `version=`
  resource (`CompanyName` `paulo.edits`, `ProductName` `FX.palette`, `FileDescription`,
  `FileVersion`) so the exe's Properties → Details is clean.
- `packaging/inno/FXPalette.iss`: add `SetupIconFile`, bundle the `.ico`, and point
  `UninstallDisplayIcon` at the bundled `.ico` explicitly.
- `main()` / bootstrap: `app.setWindowIcon(...)` so taskbar, Alt-Tab and every window inherit it.
- `SystemTrayController` uses the shared asset instead of the PIL-drawn bitmap in
  `_make_icon_image`.
- **Publisher string is `paulo.edits`, not `Paulo Edits`.** Currently wrong at
  `packaging/inno/FXPalette.iss:2` (`#define MyAppPublisher`). The user's own preset catalog
  already brands it `paulo.edits`.

## Staging

Each step leaves a working app. No fallback flag: the widget path is deleted as each view lands,
following the precedent of the tkinter removal.

1. **Extract the view-models while still on QtWidgets.** `PaletteViewModel`, `ResultsModel`,
   `ApplyController`, `PaletteWindowController` land as pure Python; the existing widgets bind to
   them. No visual change. This proves `PaletteWindowController` against the known-good widget
   window before QML is involved, and adds the headless tests.
2. **QML palette.** Replace `QtPaletteWindow` and `QtEffectPalette._build` with `Palette.qml`
   driven by the same view-models. Motion arrives here. Secondary windows stay QtWidgets and are
   temporarily mismatched. **Verify a packaged PyInstaller build at this step**, not at the end.
3. **QML secondary windows** — settings, shortcut editor, alias picker, debug — one at a time,
   each deleting its QtWidgets class as its `.qml` lands.
4. **Delete the widget scaffolding** — `_apply_styles`, the QSS blobs, `QtResultRowWidget`,
   `_style_category_button`, and the layout constants that survive only for widgets.
5. **App identity.** Independent; can land at any point.

If step 2 hits a wall on the native window layer, steps 1 and 5 still ship.

## Risks

Ranked.

1. **Native window layer.** `_force_focus_attempt` (retry loop bounded by `OPEN_FOCUS_ATTEMPTS`),
   `_activate_window_native`, `_anchor_window_to_pointer`, previous-focus save/restore. If this
   breaks, the core hotkey → palette-focused-over-Premiere flow breaks. `QQuickWindow` exposes
   `winId()` the same way a `QWidget` does, so it should port. Mitigated by extracting
   `PaletteWindowController` in step 1 and only swapping the handle source in step 2.
2. **Window flags.** `FramelessWindowHint | Tool | WindowStaysOnTopHint` plus
   `WA_TranslucentBackground` must compose the same on `QQuickWindow`. Needs verification.
3. **PyInstaller + QML.** Requires `PySide6.QtQml`, `QtQuick`, `QtQuickControls2` hidden imports,
   collection of the QML runtime plugin directories, and shipping the `.qml` files as data. A
   known sharp edge. Expect roughly +30–60 MB bundle. Verified at step 2.
4. **Offscreen tests.** The suite runs with `QT_QPA_PLATFORM=offscreen`; QML needs a scenegraph,
   so the software backend (`QSG_RHI_BACKEND=software`) may be required. Mitigated because the
   view-model tests never load QML.
5. **`QtRootAdapter` call sites.** Unchanged in behavior, but `after` / `post` callbacks that
   poked widgets now set view-model properties. Mechanical, but touches many call sites.
6. **i18n.** `tr()` and `CURRENT_LANGUAGE` are currently interpolated at widget-build time.
   Exposed to QML through a context object so strings come from the same Python catalogs.
   "Language applies on next launch" stays as-is — live language switching is out of scope.

## Testing

- Existing `companion/tests/test_app.py` logic tests are untouched; they test the loader, search
  tiers, slash-command parsing, alias resolution, row models and apply-status timeouts, not widget
  construction.
- **New headless view-model tests**: query → results, category filtering, selection movement,
  apply state transitions, `ResultsModel` roles and row counts. No QML loaded.
- **New QML load smoke test**: the engine loads every `.qml` and asserts zero warnings or errors;
  skipped when no scenegraph is available.
- Packaging assertion: the `.ico` is referenced by the spec and the `.iss`.
- `npm run validate` stays green throughout.
- **Host testing is required before this is called done.** Per `CLAUDE.md`, the off-host suites
  prove the transport and companion contract, not Premiere behavior. The frameless / focus /
  anchor flow, the palette rendering over Premiere, and the packaged installer's icon must be
  verified by the user in a real Premiere session, and any `CAPABILITY_MATRIX.md` row only flips
  with recorded evidence.

## Rollout

- Full folder copy plus a git tag and a `git bundle` before starting — the user's standing rule
  for large refactors.
- Branch `ui/qml-rewrite` off `main`; one commit per staging step so each is revertable.
- `.gitattributes` stays untracked, per `CLAUDE.md`.
- `STATUS.md` "Still to do" currently reads "UI redesign of the palette and settings (user's call,
  2026-08-27) — now unblocked"; update it when this lands.
- Merge only after host testing in Premiere.

## Out of scope

- Live (no-restart) language switching.
- macOS support. The QML choice keeps it possible; nothing here targets it.
- Any change to the UXP plugin, the action allowlist, or `uxp_execution_adapter.py` behavior.
- Redesign of the tray menu structure (the tray keeps `QSystemTrayIcon`; only its icon asset
  changes).
