# QML Palette Implementation Plan (UI rewrite, phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the companion's QtWidgets search palette with a Qt Quick one that has a real design system and real motion, driven by the view-models phase 1 already extracted.

**Architecture:** A `QQmlApplicationEngine` loads `companion/qml/Palette.qml`, which binds to the existing `PaletteViewModel`, `ResultsModel` and `ApplyController` exposed as context properties. `Theme.qml` is a singleton holding every colour, size and motion token. `QuickWindowAdapter` implements the existing `WindowAdapter` protocol for `QQuickWindow`, so the native focus/anchor layer built and host-tested in phase 1 carries over untouched. The secondary windows (settings, shortcut editor, alias picker, debug) stay QtWidgets in this plan.

**Tech Stack:** Python 3, PySide6 6.11.1 (QtQml, QtQuick, QtQuickControls2 — all confirmed present), QML, `unittest` with offscreen Qt, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-07-companion-qml-ui-rewrite-design.md`

**Predecessor:** `docs/superpowers/plans/2026-09-07-companion-view-model-extraction.md` (complete, host-tested on Premiere 26.3.2)

## Global Constraints

- **This plan covers spec phase 2 only.** Phases 3–4 (secondary windows, deleting the widget scaffolding) get their own plan.
- **This is a redesign, not a pixel-for-pixel port.** The palette should look better than it does today. Do not try to reproduce `get_row_visual_tokens`' blend maths exactly; `Theme.qml` replaces it.
- **`Qt.WindowStaysOnTopHint`, never `Qt.WindowStaysOnTop`.** The short name parses without warning and silently does nothing — the palette would fall behind Premiere. Verified 2026-09-08 on PySide6 6.11.1.
- **The native window layer is host-tested and must not be rewritten.** `PaletteWindowController` stays exactly as it is; only a new `WindowAdapter` implementation is added.
- **The `animations` preference must keep working.** `load_app_preferences()["animations"]` gates every animation via `Theme.animationsEnabled`.
- **Language applies on next launch.** Do not add live language switching.
- **The plugin side is untouched.** No changes to `index.js`, `execution-adapter.js`, `transport.js`, `manifest.json`, or `uxp_execution_adapter.py` behaviour.
- **`npm run validate` must pass at the end of every task.**
- **Never commit `companion/data/uxp_*.json`, `premiere_shortcut_configuration.log`, or `.gitattributes`.**
- **Off-host tests prove nothing about Premiere.** Task 11 ends with a host-test request to the user.
- Existing window geometry stays: `FIXED_SEARCH_WINDOW_WIDTH = 760`, `RESULTS_EXPANDED_HEIGHT = 394`, `OPEN_ANIMATION_MS = 140`.
- Work continues on branch `ui/qml-rewrite`.

---

## File Structure

**Created:**
- `companion/qml/qmldir` — registers `Theme` as a singleton (unnamed module; components use `import "."`)
- `companion/qml/Theme.qml` — every colour, spacing, radius, type and motion token
- `companion/qml/Palette.qml` — the frameless root `Window`
- `companion/qml/SearchField.qml` — prompt glyph + text input + refresh button
- `companion/qml/CategoryBar.qml` — the filter chips and the connection dot
- `companion/qml/ResultList.qml` — `ListView` over `ResultsModel`, sliding selection highlight
- `companion/qml/ResultRow.qml` — one row delegate
- `companion/qml/Footer.qml` — hint, apply progress, status text
- `companion/qml/NestPanel.qml` — the inline nest-options panel
- `companion/qml_host.py` — `QmlPaletteHost`: owns the engine, context properties and root window
- `companion/tests/test_qml_palette.py` — loads the real QML against real view-models

**Modified:**
- `companion/window_control.py` — add `QuickWindowAdapter`
- `companion/app.py` — `QtEffectPalette` drives the QML window; delete `QtPaletteWindow`, `QtResultRowWidget`, `_apply_styles`, `_style_category_button`, the chunked renderer
- `companion/tests/test_window_control.py` — cover `QuickWindowAdapter`
- `packaging/pyinstaller/FXPalette.spec` — QML hidden imports, plugin collection, ship `companion/qml`

**Unchanged (important):** `companion/palette_view_models.py`, `companion/models.py`, and `PaletteWindowController` in `companion/window_control.py`.

---

## Task 1: QML scaffolding, engine host, and a load smoke test

Establishes the engine and the test harness every later task depends on.

**Files:**
- Create: `companion/qml/qmldir`, `companion/qml/Theme.qml`, `companion/qml/Palette.qml`
- Create: `companion/qml_host.py`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: `palette_view_models.PaletteViewModel` (phase 1).
- Produces:
  - `qml_host.QML_DIR: Path` — `companion/qml`
  - `qml_host.QmlPaletteHost(view_model, apply_controller, translate, parent=None)`
    - `.load() -> None` — loads `Palette.qml`; raises `RuntimeError` on failure
    - `.window` — the root `QQuickWindow`, or `None` before `load()`
    - `.warnings: list[str]` — every engine warning seen, for the smoke test
    - `.engine` — the `QQmlApplicationEngine`

- [ ] **Step 1: Write the failing test**

Create `companion/tests/test_qml_palette.py`:

```python
"""Loads the real QML against real view-models, offscreen.

These assert that the QML parses with zero warnings and that its bindings actually
follow the view-model. They prove nothing about how it looks, and nothing about
Premiere.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from palette_view_models import ApplyController, PaletteViewModel
from qml_host import QmlPaletteHost
from test_view_models import CATALOG, FakeAdapter, FakeQueryServices, FakeScheduler


def qt_app():
    """One QApplication for the whole process; QML runs fine under it."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class QmlLoadTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)

    def tearDown(self):
        self.host.shutdown()

    def test_palette_qml_loads(self):
        self.host.load()
        self.assertIsNotNone(self.host.window)

    def test_palette_qml_loads_without_warnings(self):
        self.host.load()
        self.assertEqual(self.host.warnings, [])

    def test_root_is_frameless_tool_and_stays_on_top(self):
        from PySide6 import QtCore
        self.host.load()
        flags = self.host.window.flags()
        self.assertTrue(flags & QtCore.Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & QtCore.Qt.WindowType.Tool)
        # Qt.WindowStaysOnTop (no "Hint") parses silently and does nothing.
        self.assertTrue(flags & QtCore.Qt.WindowType.WindowStaysOnTopHint)

    def test_root_window_has_a_native_handle(self):
        self.host.load()
        self.host.window.show()
        self.assertIsInstance(int(self.host.window.winId()), int)

    def test_window_width_matches_the_python_constant(self):
        import app
        self.host.load()
        self.assertEqual(self.host.window.width(), app.FIXED_SEARCH_WINDOW_WIDTH)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'qml_host'`.

- [ ] **Step 3: Create the QML directory and a minimal Theme**

`companion/qml/qmldir`:

```
# No `module` line on purpose. A named module resolves to a directory matching the
# module name under an import path; with the qmldir sitting beside the components,
# Qt only finds the singleton via a directory import (`import "."`).
singleton Theme 1.0 Theme.qml
```

`companion/qml/Theme.qml` (filled out properly in Task 3; this is enough to load):

```qml
pragma Singleton
import QtQuick

QtObject {
    // Filled out in Task 3. Kept minimal here so Palette.qml can already reference it.
    readonly property color surface: "#0D0C14"
    readonly property color text: "#E8E8F0"
    property bool animationsEnabled: true
}
```

- [ ] **Step 4: Create a minimal Palette.qml**

```qml
import QtQuick
import QtQuick.Controls
import "."

Window {
    id: root

    width: 760
    height: 120
    color: "transparent"
    // MUST be WindowStaysOnTopHint. The short "Qt.WindowStaysOnTop" parses without a
    // warning and silently does nothing, which drops the palette behind Premiere.
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint

    // Called by QuickWindowAdapter (Task 2).
    function focusSearch() { searchInput.forceActiveFocus() }
    readonly property bool searchHasFocus: searchInput.activeFocus

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: Theme.surface
        TextInput {
            id: searchInput
            anchors.centerIn: parent
            color: Theme.text
            text: vm.query
        }
    }
}
```

- [ ] **Step 5: Create `companion/qml_host.py`**

```python
"""Owns the QML engine for the palette.

Keeps every Qt Quick concern in one place so app.py only ever talks to `window` and
the view-models it already had.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtQml
from PySide6.QtQuickControls2 import QQuickStyle

QML_DIR = Path(__file__).resolve().parent / "qml"


class QmlPaletteHost(QtCore.QObject):
    def __init__(self, view_model, apply_controller, translate, parent=None):
        super().__init__(parent)
        self._view_model = view_model
        self._apply_controller = apply_controller
        self._translate = translate
        self.warnings: list[str] = []
        self.engine: QtQml.QQmlApplicationEngine | None = None

    @property
    def window(self):
        if self.engine is None:
            return None
        roots = self.engine.rootObjects()
        return roots[0] if roots else None

    def load(self) -> None:
        # "Basic" is the unstyled Controls style. Without pinning it, Qt may pick a
        # native style whose look we do not control.
        QQuickStyle.setStyle("Basic")

        self.engine = QtQml.QQmlApplicationEngine()
        self.engine.warnings.connect(self._collect_warnings)
        self.engine.addImportPath(str(QML_DIR))

        context = self.engine.rootContext()
        context.setContextProperty("vm", self._view_model)
        context.setContextProperty("applyState", self._apply_controller)
        context.setContextProperty("i18n", _Translator(self._translate, self))

        self.engine.load(QtCore.QUrl.fromLocalFile(str(QML_DIR / "Palette.qml")))
        if self.window is None:
            raise RuntimeError(f"Palette.qml failed to load: {self.warnings}")

    def shutdown(self) -> None:
        """Tear the engine down deterministically.

        deleteLater() alone is not enough: without a running event loop the deletion
        never happens, so the QQuickWindow outlives its engine and the two are then
        destroyed in whatever order the interpreter picks at exit -- which segfaults.
        Close the window, drain the deferred-delete queue, then drop the engine.
        """
        if self.engine is None:
            return
        window = self.window
        if window is not None:
            window.close()
        self.engine.clearComponentCache()
        self.engine.deleteLater()
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            app.processEvents()
        self.engine = None

    def _collect_warnings(self, warnings) -> None:
        self.warnings.extend(w.toString() for w in warnings)


class _Translator(QtCore.QObject):
    """Exposes the companion's tr() to QML as i18n.t("key")."""

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self._translate = translate

    @QtCore.Slot(str, result=str)
    def t(self, key: str) -> str:
        return self._translate(key)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all five `QmlLoadTests`.

If `test_window_width_matches_the_python_constant` fails, the hardcoded `width: 760` in
`Palette.qml` has drifted from `FIXED_SEARCH_WINDOW_WIDTH`. Task 3 removes the duplication.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/qml companion/qml_host.py companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the QML engine host and a loading palette shell"
```

---

## Task 2: `QuickWindowAdapter`

The one new class the host-tested native layer needs.

**Files:**
- Modify: `companion/window_control.py`
- Test: `companion/tests/test_window_control.py`

**Interfaces:**
- Consumes: `window_control.WindowAdapter` protocol, `qml_host.QmlPaletteHost` (Task 1).
- Produces: `window_control.QuickWindowAdapter(window)` — implements `WindowAdapter` over a `QQuickWindow` whose root declares `focusSearch()` and `searchHasFocus`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_window_control.py`:

```python
class QuickWindowAdapterTests(unittest.TestCase):
    """QQuickWindow speaks a different dialect than QWidget: requestActivate instead of
    activateWindow, setPosition instead of move. The adapter absorbs that."""

    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        from palette_view_models import ApplyController, PaletteViewModel
        from qml_host import QmlPaletteHost
        from test_view_models import CATALOG, FakeAdapter, FakeQueryServices
        from test_view_models import FakeScheduler as VmScheduler

        services = FakeQueryServices(CATALOG)
        self.host = QmlPaletteHost(
            PaletteViewModel(services), ApplyController(FakeAdapter(), VmScheduler()),
            services.translate,
        )
        self.host.load()
        from window_control import QuickWindowAdapter
        self.adapter = QuickWindowAdapter(self.host.window)

    def tearDown(self):
        self.host.shutdown()

    def test_handle_is_a_real_window_id(self):
        self.adapter.show()
        self.assertIsInstance(self.adapter.handle(), int)
        self.assertNotEqual(self.adapter.handle(), 0)

    def test_handle_is_cached(self):
        self.adapter.show()
        self.assertEqual(self.adapter.handle(), self.adapter.handle())

    def test_move_sets_the_window_position(self):
        self.adapter.show()
        self.adapter.move(300, 210)
        self.assertEqual((self.host.window.x(), self.host.window.y()), (300, 210))

    def test_width_reports_the_window_width(self):
        self.assertEqual(self.adapter.width(), self.host.window.width())

    def test_height_hint_is_the_current_height(self):
        self.assertEqual(self.adapter.height_hint(), self.host.window.height())

    def test_focus_input_gives_the_search_field_focus(self):
        self.adapter.show()
        self.assertFalse(self.adapter.has_input_focus())
        self.adapter.focus_input()
        self.app.processEvents()
        self.assertTrue(self.adapter.has_input_focus())

    def test_raise_and_activate_do_not_raise(self):
        self.adapter.show()
        self.adapter.raise_window()
        self.adapter.activate()

    def test_it_satisfies_the_controller(self):
        # The whole point: PaletteWindowController must drive it unchanged.
        from window_control import PaletteWindowController

        class Native:
            def __init__(self): self.activated = []
            def foreground_handle(self): return 9999
            def activate_handle(self, h): self.activated.append(h)

        controller = PaletteWindowController(self.adapter, FakeScheduler(), Native())
        controller.is_open = True
        self.adapter.show()
        controller.begin_focus_attempts(max_attempts=3)
        self.app.processEvents()
        self.assertTrue(self.adapter.has_input_focus())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ImportError: cannot import name 'QuickWindowAdapter'`.

- [ ] **Step 3: Implement `QuickWindowAdapter`**

Append to `companion/window_control.py`:

```python
class QuickWindowAdapter:
    """WindowAdapter over a QQuickWindow.

    QQuickWindow is a QWindow, not a QWidget: it activates with requestActivate() rather
    than activateWindow(), positions with setPosition() rather than move(), and has no
    sizeHint(). Focus lives in QML, so the root object must declare a focusSearch()
    function and a searchHasFocus property.
    """

    def __init__(self, window):
        self._window = window
        self._handle: int | None = None

    def show(self):
        self._window.show()

    def raise_window(self):
        self._window.raise_()

    def activate(self):
        self._window.requestActivate()

    def handle(self):
        if self._handle:
            return self._handle
        try:
            self._handle = int(self._window.winId())
        except Exception:
            return None
        return self._handle

    def move(self, x, y):
        self._window.setPosition(int(x), int(y))

    def width(self):
        return self._window.width()

    def height_hint(self):
        # QML sizes itself from its content, so the current height is the hint.
        return self._window.height()

    def focus_input(self):
        self._window.focusSearch()

    def has_input_focus(self):
        return bool(self._window.property("searchHasFocus"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all eight `QuickWindowAdapterTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/window_control.py companion/tests/test_window_control.py
git commit -m "feat(window): add QuickWindowAdapter for QQuickWindow"
```

---

## Task 3: `Theme.qml` — the design and motion tokens

**Files:**
- Modify: `companion/qml/Theme.qml`, `companion/qml/Palette.qml`
- Create: `companion/qml_host.py` addition — `PaletteMetrics`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: Task 1's host.
- Produces:
  - `qml_host.PaletteMetrics(QObject)` exposed as context property `metrics`, with constant
    properties `windowWidth: int`, `resultsHeight: int`, `rowHeight: int`, `openAnimationMs: int`,
    `animationsEnabled: bool`, and `accentFor(kind: str) -> str` as a `@Slot`.
  - `Theme.qml` singleton properties: `surface`, `surfaceRaised`, `surfaceOverlay`, `border`,
    `text`, `textMuted`, `textFaint`, `accent`, `success`, `warning`, `offline`;
    `spaceXs/Sm/Md/Lg/Xl`; `radiusSm/Md/Lg/Pill`; `fontFamily`, `sizeCaption/Body/Title/Display`;
    `durFast/durBase/durSlow`; `easeStandard/easeDecel/easeOvershoot`; `animationsEnabled`;
    and `function mix(a, b, t)`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class ThemeTokenTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()

    def tearDown(self):
        self.host.shutdown()

    def _theme(self):
        return self.host.window.property("themeProbe")

    def test_theme_exposes_every_documented_token(self):
        theme = self._theme()
        self.assertIsNotNone(theme, "Palette.qml must expose Theme as themeProbe")
        for name in (
            "surface", "surfaceRaised", "surfaceOverlay", "border",
            "text", "textMuted", "textFaint", "accent", "success", "warning", "offline",
            "spaceXs", "spaceSm", "spaceMd", "spaceLg", "spaceXl",
            "radiusSm", "radiusMd", "radiusLg", "radiusPill",
            "fontFamily", "sizeCaption", "sizeBody", "sizeTitle", "sizeDisplay",
            "durFast", "durBase", "durSlow",
        ):
            self.assertIsNotNone(theme.property(name), f"Theme.{name} is missing")

    def test_motion_durations_are_ordered(self):
        theme = self._theme()
        self.assertLess(theme.property("durFast"), theme.property("durBase"))
        self.assertLess(theme.property("durBase"), theme.property("durSlow"))

    def test_animations_flag_follows_the_preference(self):
        theme = self._theme()
        self.assertIsInstance(theme.property("animationsEnabled"), bool)


class PaletteMetricsTests(unittest.TestCase):
    def test_metrics_mirror_the_python_constants(self):
        import app
        from qml_host import PaletteMetrics
        m = PaletteMetrics(animations_enabled=True)
        self.assertEqual(m.windowWidth, app.FIXED_SEARCH_WINDOW_WIDTH)
        self.assertEqual(m.resultsHeight, app.RESULTS_EXPANDED_HEIGHT)
        self.assertEqual(m.openAnimationMs, app.OPEN_ANIMATION_MS)
        self.assertTrue(m.animationsEnabled)

    def test_accent_for_returns_a_colour_string(self):
        from qml_host import PaletteMetrics
        m = PaletteMetrics(animations_enabled=False)
        for kind in ("video", "audio", "preset", "project", "favorite"):
            self.assertTrue(m.accentFor(kind).startswith("#"), kind)

    def test_animations_flag_is_carried_through(self):
        from qml_host import PaletteMetrics
        self.assertFalse(PaletteMetrics(animations_enabled=False).animationsEnabled)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `ImportError: cannot import name 'PaletteMetrics'`, and `themeProbe` is `None`.

- [ ] **Step 3: Add `PaletteMetrics` to `qml_host.py`**

```python
class PaletteMetrics(QtCore.QObject):
    """Geometry, motion and accent values that already exist in Python, handed to QML.

    Keeps the window size and animation timing defined in exactly one place.
    """

    def __init__(self, *, animations_enabled: bool, parent=None):
        super().__init__(parent)
        import app  # imported lazily: app.py imports this module

        self._window_width = app.FIXED_SEARCH_WINDOW_WIDTH
        self._results_height = app.RESULTS_EXPANDED_HEIGHT
        self._row_height = app.PaletteLayoutMetrics().row_height
        self._open_animation_ms = app.OPEN_ANIMATION_MS
        self._animations_enabled = animations_enabled

    @QtCore.Property(int, constant=True)
    def windowWidth(self) -> int:
        return self._window_width

    @QtCore.Property(int, constant=True)
    def resultsHeight(self) -> int:
        return self._results_height

    @QtCore.Property(int, constant=True)
    def rowHeight(self) -> int:
        return self._row_height

    @QtCore.Property(int, constant=True)
    def openAnimationMs(self) -> int:
        return self._open_animation_ms

    @QtCore.Property(bool, constant=True)
    def animationsEnabled(self) -> bool:
        return self._animations_enabled

    @QtCore.Slot(str, result=str)
    def accentFor(self, kind: str) -> str:
        import app
        return app.get_filter_palette_color(app.filter_key_for_item_type(kind))
```

Register it in `QmlPaletteHost.load()`, next to the other context properties:

```python
        self._metrics = PaletteMetrics(animations_enabled=self._animations_enabled, parent=self)
        context.setContextProperty("metrics", self._metrics)
```

and extend the constructor signature:

```python
    def __init__(self, view_model, apply_controller, translate, *, animations_enabled=True, parent=None):
```

storing `self._animations_enabled = animations_enabled`.

- [ ] **Step 4: Fill out `Theme.qml`**

```qml
pragma Singleton
import QtQuick

QtObject {
    // ---- surfaces -------------------------------------------------------------
    readonly property color surface:        "#0D0C14"
    readonly property color surfaceRaised:  "#15142099"
    readonly property color surfaceOverlay: "#1C1B2B"
    readonly property color border:         "#2A2A35"

    // ---- text -----------------------------------------------------------------
    readonly property color text:      "#E8E8F0"
    readonly property color textMuted: "#88889B"
    readonly property color textFaint: "#5E5E70"

    // ---- semantic -------------------------------------------------------------
    readonly property color accent:  "#7278F0"
    readonly property color success: "#3DD68C"
    readonly property color warning: "#F5A623"
    readonly property color offline: "#7E8698"

    // ---- spacing scale --------------------------------------------------------
    readonly property int spaceXs: 4
    readonly property int spaceSm: 8
    readonly property int spaceMd: 12
    readonly property int spaceLg: 16
    readonly property int spaceXl: 24

    // ---- radii ----------------------------------------------------------------
    readonly property int radiusSm:   6
    readonly property int radiusMd:   10
    readonly property int radiusLg:   16
    readonly property int radiusPill: 999

    // ---- type -----------------------------------------------------------------
    readonly property string fontFamily: "Google Sans Flex"
    readonly property int sizeCaption: 11
    readonly property int sizeBody:    13
    readonly property int sizeTitle:   15
    readonly property int sizeDisplay: 20

    // ---- motion ---------------------------------------------------------------
    // Every animation references these. No magic numbers in components.
    readonly property int durFast: 120
    readonly property int durBase: 180
    readonly property int durSlow: 260
    readonly property int easeStandard:  Easing.OutCubic
    readonly property int easeDecel:     Easing.OutQuint
    readonly property int easeOvershoot: Easing.OutBack

    // Mirrors the user's "Use interface animations" preference. Every Behavior and
    // Transition gates on this.
    property bool animationsEnabled: true

    // Linear colour mix, the QML counterpart of app.blend_colors.
    function mix(a, b, t) {
        return Qt.rgba(a.r + (b.r - a.r) * t,
                       a.g + (b.g - a.g) * t,
                       a.b + (b.b - a.b) * t,
                       a.a + (b.a - a.a) * t)
    }
}
```

- [ ] **Step 5: Wire Theme into Palette.qml and expose it for the test**

Replace the body of `companion/qml/Palette.qml` with:

```qml
import QtQuick
import QtQuick.Controls
import "."

Window {
    id: root

    width: metrics.windowWidth
    height: shell.implicitHeight
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint

    // Lets the Python tests read the singleton's tokens.
    readonly property var themeProbe: Theme

    function focusSearch() { searchInput.forceActiveFocus() }
    readonly property bool searchHasFocus: searchInput.activeFocus

    Component.onCompleted: Theme.animationsEnabled = metrics.animationsEnabled

    Rectangle {
        id: shell
        anchors.fill: parent
        implicitHeight: 120
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.border

        TextInput {
            id: searchInput
            anchors.centerIn: parent
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeBody
            text: vm.query
        }
    }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including `ThemeTokenTests` and `PaletteMetricsTests`.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/qml companion/qml_host.py companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the Theme singleton with design and motion tokens"
```

---

## Task 4: `SearchField.qml` and `CategoryBar.qml`

**Files:**
- Create: `companion/qml/SearchField.qml`, `companion/qml/CategoryBar.qml`
- Modify: `companion/qml/Palette.qml`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: `Theme`, `metrics`, `vm` (`query`, `set_query`, `activeCategory`, `select_category`, `connectionState`).
- Produces:
  - `SearchField.qml` — `property alias text`, `signal accepted()`, `signal moveSelection(int delta)`, `signal dismissed()`, `function takeFocus()`, `property bool inputHasFocus`
  - `CategoryBar.qml` — `property string activeCategory`, `signal categoryPicked(string category)`

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class SearchAndCategoryTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_search_field_exists(self):
        self.assertIsNotNone(self._child("searchField"))

    def test_typing_in_qml_updates_the_view_model(self):
        self._child("searchField").setProperty("text", "gaussian")
        self.app.processEvents()
        self.assertEqual(self.vm.query, "gaussian")
        self.assertEqual(self.vm.results.rowCount(), 2)

    def test_category_bar_reflects_the_active_category(self):
        bar = self._child("categoryBar")
        self.assertIsNotNone(bar)
        self.vm.select_category("Audio")
        self.app.processEvents()
        self.assertEqual(bar.property("activeCategory"), "Audio")

    def test_todos_is_shown_when_no_category_is_active(self):
        bar = self._child("categoryBar")
        self.vm.select_category("Todos")
        self.app.processEvents()
        self.assertEqual(bar.property("activeCategory"), "Todos")

    def test_connection_dot_colour_tracks_the_state(self):
        dot = self._child("connectionDot")
        self.assertIsNotNone(dot)
        self.vm.set_connection_state("connected")
        self.app.processEvents()
        connected = dot.property("color")
        self.vm.set_connection_state("offline")
        self.app.processEvents()
        self.assertNotEqual(connected, dot.property("color"))
```

Add `from PySide6 import QtCore` to the imports at the top of the file.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `self._child("searchField")` is `None`.

- [ ] **Step 3: Create `SearchField.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property alias text: input.text
    property bool inputHasFocus: input.activeFocus

    signal accepted()
    signal moveSelection(int delta)
    signal dismissed()
    signal refreshRequested()

    function takeFocus() { input.forceActiveFocus() }

    implicitHeight: 52

    Row {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceLg
        anchors.rightMargin: Theme.spaceMd
        spacing: Theme.spaceSm

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: ">"
            color: Theme.accent
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeTitle
            font.bold: true
        }

        TextInput {
            id: input
            width: parent.width - 90
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeTitle
            selectionColor: Theme.accent
            clip: true

            Keys.onDownPressed: control.moveSelection(1)
            Keys.onUpPressed: control.moveSelection(-1)
            Keys.onEscapePressed: control.dismissed()
            onAccepted: control.accepted()
        }

        Rectangle {
            id: refreshButton
            width: 28; height: 28
            anchors.verticalCenter: parent.verticalCenter
            radius: Theme.radiusSm
            color: refreshArea.containsMouse ? Theme.surfaceOverlay : "transparent"
            border.width: 1
            border.color: Theme.border

            Behavior on color {
                enabled: Theme.animationsEnabled
                ColorAnimation { duration: Theme.durFast }
            }

            Text {
                anchors.centerIn: parent
                text: "↻"
                color: Theme.textMuted
                font.family: Theme.fontFamily
            }

            MouseArea {
                id: refreshArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: control.refreshRequested()
            }
        }
    }
}
```

- [ ] **Step 4: Create `CategoryBar.qml`**

The active chip is a single pill that **slides** between categories rather than each chip
restyling — that is the motion this replaces `_style_category_button` with.

```qml
import QtQuick
import "."

Item {
    id: control

    property string activeCategory: "Todos"
    property string connectionState: "offline"
    signal categoryPicked(string category)

    readonly property var categories: ["Todos", "Video", "Audio", "Presets", "Projeto", "Favoritos"]
    readonly property int activeIndex: categories.indexOf(activeCategory)

    implicitHeight: 40

    // One pill that slides between chips. The chips themselves stay transparent, so the
    // pill is the only thing carrying "active" state -- that is what makes it read as
    // movement rather than six independent colour changes.
    Rectangle {
        id: activePill

        readonly property Item target: control.activeIndex >= 0 ? chipRepeater.itemAt(control.activeIndex) : null

        visible: target !== null
        x: target ? chips.x + target.x : 0
        y: target ? chips.y + target.y : 0
        width: target ? target.width : 0
        height: target ? target.height : 0
        radius: Theme.radiusPill
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.20)
        border.width: 1
        border.color: Theme.accent

        Behavior on x {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
        Behavior on width {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
    }

    Row {
        id: chips
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spaceSm

        Repeater {
            id: chipRepeater
            model: control.categories
            delegate: Rectangle {
                id: chip
                required property string modelData
                readonly property bool active: modelData === control.activeCategory

                height: 26
                width: label.implicitWidth + Theme.spaceMd * 2
                radius: Theme.radiusPill
                // Only hover paints here; "active" belongs to the sliding pill above.
                color: (!active && chipArea.containsMouse) ? Theme.surfaceOverlay : "transparent"
                border.width: 1
                border.color: active ? "transparent" : Theme.border

                Behavior on color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                Text {
                    id: label
                    anchors.centerIn: parent
                    text: chip.modelData
                    color: chip.active ? Theme.text : Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    font.bold: chip.active

                    Behavior on color {
                        enabled: Theme.animationsEnabled
                        ColorAnimation { duration: Theme.durFast }
                    }
                }

                MouseArea {
                    id: chipArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: control.categoryPicked(chip.modelData)
                }
            }
        }
    }

    Rectangle {
        id: connectionDot
        objectName: "connectionDot"
        width: 10; height: 10
        radius: 5
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        color: control.connectionState === "connected" ? Theme.success
             : control.connectionState === "problem"   ? Theme.warning
             : Theme.offline

        Behavior on color {
            enabled: Theme.animationsEnabled
            ColorAnimation { duration: Theme.durBase }
        }

        // A short pulse on every state change, so a connect/disconnect is noticeable
        // without the dot animating forever in the corner of the user's eye.
        SequentialAnimation {
            id: connectionPulse
            running: false
            NumberAnimation {
                target: connectionDot; property: "scale"
                to: 1.6; duration: Theme.durFast; easing.type: Theme.easeStandard
            }
            NumberAnimation {
                target: connectionDot; property: "scale"
                to: 1.0; duration: Theme.durBase; easing.type: Theme.easeOvershoot
            }
        }

        onColorChanged: if (Theme.animationsEnabled) connectionPulse.restart()
    }
}
```

- [ ] **Step 5: Compose them in `Palette.qml`**

Replace the `Rectangle { id: shell ... }` body with:

```qml
    Rectangle {
        id: shell
        anchors.fill: parent
        implicitHeight: content.implicitHeight
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
        clip: true

        Column {
            id: content
            width: parent.width

            SearchField {
                id: searchField
                objectName: "searchField"
                width: parent.width
                onAccepted: root.applyRequested()
                onMoveSelection: (delta) => vm.move_selection(delta)
                onDismissed: root.dismissed()
                onRefreshRequested: root.refreshRequested()
                onTextChanged: vm.set_query(text)
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.border
            }

            CategoryBar {
                id: categoryBar
                objectName: "categoryBar"
                width: parent.width
                activeCategory: vm.activeCategory ? vm.activeCategory : "Todos"
                connectionState: vm.connectionState
                onCategoryPicked: (category) => vm.select_category(category)
            }
        }
    }
```

Add these to the root `Window`, above `Component.onCompleted`:

```qml
    signal dismissed()
    signal refreshRequested()
    signal applyRequested()
```

and change `focusSearch()` / `searchHasFocus` to delegate to the component:

```qml
    function focusSearch() { searchField.takeFocus() }
    readonly property bool searchHasFocus: searchField.inputHasFocus
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all five `SearchAndCategoryTests`.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/qml companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the search field and category bar"
```

---

## Task 5: `ResultRow.qml` and `ResultList.qml`

Where the biggest visual and motion win lands: a `ListView` bound to `ResultsModel`, with a
single highlight that slides between rows and staggered row entry.

**Files:**
- Create: `companion/qml/ResultRow.qml`, `companion/qml/ResultList.qml`
- Modify: `companion/qml/Palette.qml`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: `vm.results` (`ResultsModel` roles `title`, `subtitle`, `typeLabel`, `iconKind`, `isFavorite`, `accentKind`, `accentColor`), `vm.selectedIndex`, `vm.set_selected_index`, `metrics.accentFor`, `metrics.rowHeight`.
- Produces: `ResultList.qml` — `property int currentIndex`, `signal rowActivated(int index)`, `signal rowClicked(int index)`; `ResultRow.qml` — delegate with `required property` bindings.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class ResultListTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_list_exists(self):
        self.assertIsNotNone(self._child("resultList"))

    def test_list_count_follows_the_model(self):
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 2)

    def test_list_empties_when_nothing_matches(self):
        self.vm.set_query("nothing matches this")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 0)

    def test_current_index_follows_the_view_model(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("currentIndex"), 1)

    def test_selection_survives_a_new_query(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.vm.set_query("studio")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("currentIndex"), 0)

    def test_all_rows_are_present_without_chunked_rendering(self):
        # The widget palette rendered 16 rows then chunked the rest via a timer.
        # A ListView is virtualised, so count is exact immediately.
        big = [{"name": f"Blur {i}", "type": "effect_video"} for i in range(200)]
        self.services.catalog = big
        self.vm.set_query("blur")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 200)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `self._child("resultList")` is `None`.

- [ ] **Step 3: Create `ResultRow.qml`**

```qml
import QtQuick
import "."

Item {
    id: row

    required property string title
    required property string subtitle
    required property string typeLabel
    required property string iconKind
    required property string accentKind
    required property var accentColor
    required property bool isFavorite
    required property bool selected

    signal clicked()
    signal activated()

    readonly property color accent: accentColor ? accentColor : metrics.accentFor(accentKind)

    height: metrics.rowHeight
    width: ListView.view ? ListView.view.width : 0

    Rectangle {
        id: surface
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        anchors.topMargin: 2
        anchors.bottomMargin: 2
        radius: Theme.radiusMd
        color: hoverArea.containsMouse && !row.selected
               ? Qt.rgba(row.accent.r, row.accent.g, row.accent.b, 0.10)
               : "transparent"

        Behavior on color {
            enabled: Theme.animationsEnabled
            ColorAnimation { duration: Theme.durFast }
        }

        Row {
            anchors.fill: parent
            anchors.leftMargin: Theme.spaceMd
            anchors.rightMargin: Theme.spaceMd
            spacing: Theme.spaceMd

            Rectangle {
                width: 26; height: 26
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusSm
                color: Qt.rgba(row.accent.r, row.accent.g, row.accent.b, row.selected ? 0.35 : 0.18)

                Behavior on color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                Text {
                    anchors.centerIn: parent
                    text: row.iconKind === "preset"   ? "✎"
                        : row.iconKind === "project"  ? "▣"
                        : row.iconKind === "favorite" ? "★"
                        : "✦"
                    color: row.accent
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 26 - badge.width - Theme.spaceMd * 3
                spacing: 1

                Text {
                    width: parent.width
                    text: row.title
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                    font.bold: true
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    text: row.subtitle
                    color: row.selected ? Theme.textMuted : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                id: badge
                anchors.verticalCenter: parent.verticalCenter
                width: badgeLabel.implicitWidth + Theme.spaceMd
                height: 18
                radius: Theme.radiusSm
                color: Qt.rgba(row.accent.r, row.accent.g, row.accent.b, 0.22)

                Text {
                    id: badgeLabel
                    anchors.centerIn: parent
                    text: row.typeLabel
                    color: row.accent
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    font.bold: true
                }
            }
        }
    }

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.clicked()
        onDoubleClicked: row.activated()
    }
}
```

- [ ] **Step 4: Create `ResultList.qml`**

```qml
import QtQuick
import "."

ListView {
    id: list

    signal rowActivated(int index)
    signal rowClicked(int index)

    clip: true
    boundsBehavior: Flickable.StopAtBounds
    cacheBuffer: metrics.rowHeight * 6
    highlightMoveDuration: Theme.animationsEnabled ? Theme.durFast : 0
    highlightResizeDuration: 0

    // One highlight that slides between rows, instead of restyling every row.
    highlight: Rectangle {
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        radius: Theme.radiusMd
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
        border.width: 1
        border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
    }
    highlightFollowsCurrentItem: true

    // Staggered entry for a new result set.
    add: Transition {
        enabled: Theme.animationsEnabled
        NumberAnimation {
            property: "opacity"
            from: 0; to: 1
            duration: Theme.durBase
            easing.type: Theme.easeStandard
        }
        NumberAnimation {
            property: "y"
            from: 8
            duration: Theme.durBase
            easing.type: Theme.easeDecel
        }
    }

    displaced: Transition {
        enabled: Theme.animationsEnabled
        NumberAnimation {
            properties: "x,y"
            duration: Theme.durFast
            easing.type: Theme.easeStandard
        }
    }

    delegate: ResultRow {
        // `index` and `model` come from the ListView delegate context.
        title: model.title
        subtitle: model.subtitle
        typeLabel: model.typeLabel
        iconKind: model.iconKind
        accentKind: model.accentKind
        accentColor: model.accentColor
        isFavorite: model.isFavorite
        selected: index === list.currentIndex
        onClicked: list.rowClicked(index)
        onActivated: list.rowActivated(index)
    }
}
```

- [ ] **Step 5: Add it to `Palette.qml`**

Inside `Column { id: content ... }`, after `CategoryBar`:

```qml
            ResultList {
                id: resultList
                objectName: "resultList"
                width: parent.width
                height: vm.viewState === "results" ? metrics.resultsHeight : 0
                visible: height > 0
                model: vm.results
                currentIndex: vm.selectedIndex
                onRowClicked: (index) => vm.set_selected_index(index)
                onRowActivated: (index) => { vm.set_selected_index(index); root.applyRequested() }

                Behavior on height {
                    enabled: Theme.animationsEnabled
                    NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
                }
            }
```

Add `signal applyRequested()` to the root `Window` alongside the other signals, and change
`SearchField`'s handler to `onAccepted: root.applyRequested()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all six `ResultListTests`.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/qml companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the result list with a sliding highlight and staggered entry"
```

---

## Task 6: `Footer.qml` and the empty state

**Files:**
- Create: `companion/qml/Footer.qml`
- Modify: `companion/qml/Palette.qml`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: `vm.statusText`, `vm.footerHint`, `vm.viewState`, `applyState.state`, `applyState.busy`.
- Produces: `Footer.qml` — `property string hint`, `property string status`, `property bool busy`, `property string applyPhase`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class FooterTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_footer_exists(self):
        self.assertIsNotNone(self._child("footer"))

    def test_status_text_follows_the_view_model(self):
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertEqual(self._child("footer").property("status"), self.vm.statusText)
        self.assertEqual(self._child("footer").property("status"), "2/2")

    def test_hint_follows_the_view_model(self):
        self.assertEqual(self._child("footer").property("hint"), self.vm.footerHint)

    def test_busy_follows_the_apply_controller(self):
        footer = self._child("footer")
        self.assertFalse(footer.property("busy"))
        self.apply.begin({"name": "X"})
        self.app.processEvents()
        self.assertTrue(footer.property("busy"))
        self.apply.complete("error")
        self.app.processEvents()
        self.assertFalse(footer.property("busy"))

    def test_empty_state_shows_only_in_message_state(self):
        empty = self._child("emptyState")
        self.assertIsNotNone(empty)
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertFalse(empty.property("visible"))
        self.vm.set_query("nothing matches this")
        self.app.processEvents()
        self.assertTrue(empty.property("visible"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `self._child("footer")` is `None`.

- [ ] **Step 3: Create `Footer.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property string hint: ""
    property string status: ""
    property bool busy: false
    // Named applyPhase, not applyState: `applyState` is the context property holding
    // the ApplyController, and shadowing it here would make the binding ambiguous.
    property string applyPhase: "idle"

    implicitHeight: 36

    Rectangle {
        anchors.fill: parent
        color: Theme.surfaceOverlay
        opacity: 0.5
    }

    Text {
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        text: control.hint
        color: Theme.textFaint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.sizeCaption
    }

    Row {
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spaceSm

        ProgressBar {
            id: progress
            width: 72
            anchors.verticalCenter: parent.verticalCenter
            indeterminate: true
            visible: control.busy
            opacity: control.busy ? 1 : 0

            Behavior on opacity {
                enabled: Theme.animationsEnabled
                NumberAnimation { duration: Theme.durFast }
            }
        }

        Text {
            id: statusLabel
            anchors.verticalCenter: parent.verticalCenter
            text: control.status
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeCaption
            color: control.applyPhase === "success" ? Theme.success
                 : control.applyPhase === "error"   ? Theme.warning
                 : Theme.textMuted

            Behavior on color {
                enabled: Theme.animationsEnabled
                ColorAnimation { duration: Theme.durBase }
            }
        }
    }
}
```

- [ ] **Step 4: Add the footer and empty state to `Palette.qml`**

Inside `Column { id: content ... }`, after `ResultList`:

```qml
            Item {
                id: emptyState
                objectName: "emptyState"
                width: parent.width
                height: visible ? 84 : 0
                visible: vm.viewState === "message"

                Text {
                    anchors.centerIn: parent
                    text: i18n.t("no_results_helper")
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                }

                Behavior on height {
                    enabled: Theme.animationsEnabled
                    NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
                }
            }

            Footer {
                id: footer
                objectName: "footer"
                width: parent.width
                hint: vm.footerHint
                status: vm.statusText
                busy: applyState.busy
                applyPhase: applyState.state
            }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all five `FooterTests`.

- [ ] **Step 6: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add companion/qml companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the footer and the empty state"
```

---

## Task 7: `NestPanel.qml`

Replaces `_show_nest_options`' inline widget panel.

**Files:**
- Create: `companion/qml/NestPanel.qml`
- Modify: `companion/qml/Palette.qml`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Consumes: `Theme`, `i18n`.
- Produces: `NestPanel.qml` — `property bool open`, `property string nestName`, `signal confirmed(string name)`, `signal cancelled()`, `function takeFocus()`.
- Root `Palette.qml` gains `property bool nestPanelOpen` and `signal nestConfirmed(string name)`, `signal nestCancelled()`, plus `function openNestPanel()` / `function closeNestPanel()`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class NestPanelTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_panel_starts_closed(self):
        panel = self._child("nestPanel")
        self.assertIsNotNone(panel)
        self.assertFalse(panel.property("open"))

    def test_open_nest_panel_opens_it(self):
        self.root.openNestPanel()
        self.app.processEvents()
        self.assertTrue(self._child("nestPanel").property("open"))
        self.assertTrue(self.root.property("nestPanelOpen"))

    def test_close_nest_panel_closes_it(self):
        self.root.openNestPanel()
        self.root.closeNestPanel()
        self.app.processEvents()
        self.assertFalse(self._child("nestPanel").property("open"))

    def test_confirming_emits_the_typed_name(self):
        seen = []
        self.root.nestConfirmed.connect(seen.append)
        self.root.openNestPanel()
        self._child("nestPanel").setProperty("nestName", "My Nest")
        self._child("nestPanel").confirm()
        self.app.processEvents()
        self.assertEqual(seen, ["My Nest"])

    def test_cancelling_emits_and_closes(self):
        seen = []
        self.root.nestCancelled.connect(lambda: seen.append(True))
        self.root.openNestPanel()
        self._child("nestPanel").cancel()
        self.app.processEvents()
        self.assertEqual(seen, [True])
        self.assertFalse(self._child("nestPanel").property("open"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `self._child("nestPanel")` is `None`.

- [ ] **Step 3: Create `NestPanel.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property bool open: false
    property alias nestName: nameInput.text

    signal confirmed(string name)
    signal cancelled()

    function takeFocus() { nameInput.forceActiveFocus() }
    function confirm() { control.confirmed(nameInput.text.trim()); control.open = false }
    function cancel() { control.open = false; control.cancelled() }

    implicitHeight: open ? 96 : 0
    clip: true

    Behavior on implicitHeight {
        enabled: Theme.animationsEnabled
        NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: Theme.spaceSm
        radius: Theme.radiusMd
        color: Theme.surfaceOverlay
        border.width: 1
        border.color: Theme.border
        opacity: control.open ? 1 : 0

        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durFast }
        }

        Column {
            anchors.fill: parent
            anchors.margins: Theme.spaceMd
            spacing: Theme.spaceSm

            Text {
                text: i18n.t("nest_options_title")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.sizeBody
                font.bold: true
            }

            Rectangle {
                width: parent.width
                height: 30
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: nameInput.activeFocus ? Theme.accent : Theme.border

                Behavior on border.color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                TextInput {
                    id: nameInput
                    anchors.fill: parent
                    anchors.margins: Theme.spaceSm
                    verticalAlignment: TextInput.AlignVCenter
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                    onAccepted: control.confirm()
                    Keys.onEscapePressed: control.cancel()
                }
            }
        }
    }
}
```

- [ ] **Step 4: Wire it into `Palette.qml`**

Inside `Column { id: content ... }`, between `CategoryBar` and `ResultList`:

```qml
            NestPanel {
                id: nestPanel
                objectName: "nestPanel"
                width: parent.width
                onConfirmed: (name) => root.nestConfirmed(name)
                onCancelled: root.nestCancelled()
            }
```

Add to the root `Window`:

```qml
    property alias nestPanelOpen: nestPanel.open
    signal nestConfirmed(string name)
    signal nestCancelled()

    function openNestPanel() { nestPanel.open = true; nestPanel.takeFocus() }
    function closeNestPanel() { nestPanel.open = false }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all five `NestPanelTests`.

- [ ] **Step 6: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add companion/qml companion/tests/test_qml_palette.py
git commit -m "feat(qml): add the inline nest options panel"
```

---

## Task 8: Window open/close motion

**Files:**
- Modify: `companion/qml/Palette.qml`
- Test: `companion/tests/test_qml_palette.py`

**Interfaces:**
- Produces: `Palette.qml` gains `function playOpen()`, `function playClose()`, `property bool shellVisible`, and `signal closeFinished()`. `QtEffectPalette` uses these instead of `_animate_window_opacity` / `_animate_window_geometry`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_qml_palette.py`:

```python
class OpenCloseMotionTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def test_play_open_marks_the_shell_visible(self):
        self.root.playOpen()
        self.app.processEvents()
        self.assertTrue(self.root.property("shellVisible"))

    def test_play_close_clears_it(self):
        self.root.playOpen()
        self.root.playClose()
        self.app.processEvents()
        self.assertFalse(self.root.property("shellVisible"))

    def test_close_finished_fires(self):
        import time
        seen = []
        self.root.closeFinished.connect(lambda: seen.append(True))
        self.root.playOpen()
        self.app.processEvents()
        self.root.playClose()
        deadline = time.time() + 3.0
        while time.time() < deadline and not seen:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(seen, [True])

    def test_window_height_tracks_the_content(self):
        self.vm.set_query("nothing matches this")
        self.app.processEvents()
        message_height = self.root.height()
        self.vm.set_query("gaussian")
        deadline_events = 200
        for _ in range(deadline_events):
            self.app.processEvents()
        self.assertGreater(self.root.height(), message_height)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `AttributeError`/`RuntimeError`: `playOpen` is not a method of the root object.

- [ ] **Step 3: Add the motion to `Palette.qml`**

Add to the root `Window`:

```qml
    property bool shellVisible: false
    signal closeFinished()

    function playOpen() { shellVisible = true }
    function playClose() { shellVisible = false }
```

Wrap the shell's transform and opacity:

```qml
        opacity: root.shellVisible ? 1 : 0
        scale: root.shellVisible ? 1 : 0.97
        transformOrigin: Item.Center

        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation {
                duration: metrics.openAnimationMs
                easing.type: Theme.easeStandard
                onFinished: if (!root.shellVisible) root.closeFinished()
            }
        }
        Behavior on scale {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: metrics.openAnimationMs; easing.type: Theme.easeOvershoot }
        }
```

**Do not signal the close from the fade's `onFinished`.** An animation inside a `Behavior` does
not reliably emit `finished`, and with animations disabled the `Behavior` is skipped entirely --
either way the window would never hide. Use an explicit `Timer` on the root `Window`:

```qml
    Timer {
        id: closeTimer
        interval: Theme.animationsEnabled ? metrics.openAnimationMs : 0
        repeat: false
        onTriggered: root.closeFinished()
    }
```

with `playOpen()` stopping it and `playClose()` restarting it.

**Also:** bind `Window.height` to `shell.height` and let the shell size itself
(`width: parent.width; height: content.implicitHeight`) with the `Behavior on height` on the
shell. Anchoring the shell to fill the window while binding the window's height back to the
shell does not resolve -- the window collapses to 1px.

And make the window follow its content height:

```qml
    height: shell.implicitHeight

    Behavior on height {
        enabled: Theme.animationsEnabled
        NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all four `OpenCloseMotionTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/qml companion/tests/test_qml_palette.py
git commit -m "feat(qml): add open/close motion and content-driven window height"
```

---

## Task 9: Swap `QtEffectPalette` onto the QML window

The task with real user-visible risk. After it, the palette you see is QML.

**Files:**
- Modify: `companion/app.py` — `QtEffectPalette._build`, `show`, `hide`, `_render_view_model`, `_populate_results`, `_append_result_rows`, `_cancel_render_chunk`, `_set_idle_state`, `_set_message_state`, `_set_results_state`, `_resize_to_content`, `_animate_window_*`, `_show_nest_options`, `_close_nest_options`, `_update_category_buttons`, `_update_connection_indicator`, `_apply_styles`, `_style_category_button`, `_on_apply_state_changed`
- Delete from `companion/app.py`: `QtPaletteWindow`, `QtResultRowWidget`

**Interfaces:**
- Consumes: `QmlPaletteHost` (Task 1), `QuickWindowAdapter` (Task 2), all the QML signals from Tasks 4–8.
- Produces: `QtEffectPalette.qml_host: QmlPaletteHost`; `self.window` is now the `QQuickWindow`.

- [ ] **Step 1: Replace `_build`'s widget construction**

Replace the body of `QtEffectPalette._build` from `self.window = QtPaletteWindow(self)` through
`self.window.hide()` with:

```python
    def _build(self):
        self._load_qt_fonts()
        self.ui_font_family = self._choose_qt_font_family()

        self.qml_host = QmlPaletteHost(
            self.view_model,
            self.apply_controller,
            tr,
            animations_enabled=self.animations_enabled,
        )
        self.qml_host.load()
        self.window = self.qml_host.window

        self.window.applyRequested.connect(self._apply_selected)
        self.window.dismissed.connect(self.hide)
        self.window.refreshRequested.connect(self._manual_refresh)
        self.window.nestConfirmed.connect(self._confirm_nest_options)
        self.window.nestCancelled.connect(self._cancel_nest_options)
        self.window.closeFinished.connect(self._on_close_finished)

        self.window_controller = PaletteWindowController(
            QuickWindowAdapter(self.window),
            self.root,
            _NativeWindowCalls(),
            on_focus_acquired=self._report_focus_acquired,
        )

        self.view_model.set_connection_state(self.loader.snapshot.connection_state)
        self._idle_window_height = self.window.height()
```

Add the imports:

```python
from qml_host import QmlPaletteHost
from window_control import PaletteWindowController, QuickWindowAdapter, WidgetWindowAdapter
```

- [ ] **Step 2: Delete the widget-only rendering machinery**

Delete these methods from `QtEffectPalette` entirely — QML binds to the view-model directly, so
none of them have a job any more:

`_render_view_model`, `_populate_results`, `_append_result_rows`, `_cancel_render_chunk`,
`_set_idle_state`, `_set_message_state`, `_set_results_state`, `_resize_to_content`,
`_animate_window_height`, `_animate_window_geometry`, `_animate_window_opacity`,
`_apply_styles`, `_style_category_button`, `_update_category_buttons`,
`_update_connection_indicator`, `_sync_row_selection`, `_on_apply_state_changed`.

Delete the classes `QtPaletteWindow` and `QtResultRowWidget`.

Delete these instance attributes from `__init__`: `_row_widgets`, `_render_chunk_job`,
`_render_generation`, `_opacity_animation`, `_geometry_animation`, `_qt_middle_height`.

- [ ] **Step 3: Repoint the remaining callers**

`_refresh_list` loses its rendering call:

```python
    def _refresh_list(self):
        self._search_job = None
        self.view_model.set_query(self.window.property("searchText") or "")
```

Add to `Palette.qml`'s root, so Python can read and write the search text:

```qml
    property alias searchText: searchField.text
```

`_on_search_change` is no longer needed — QML's `onTextChanged` already calls `vm.set_query`.
Delete it, and delete `_on_category_click` (the `CategoryBar` calls `vm.select_category` directly).

`_on_loader_snapshot_ready` drops `self._update_connection_indicator()`; it keeps
`self.view_model.set_connection_state(snapshot.connection_state)`.

`_move_selection` becomes:

```python
    def _move_selection(self, direction: int):
        self.view_model.move_selection(direction)
```

`_show_nest_options` / `_close_nest_options` become:

```python
    def _show_nest_options(self, effect: dict) -> None:
        self._pending_nest_effect = effect
        self.window.openNestPanel()

    def _close_nest_options(self, *, restore: bool) -> None:
        self.window.closeNestPanel()
        self._pending_nest_effect = None
        if restore:
            self.window.focusSearch()
```

`_confirm_nest_options` takes the name from the signal instead of reading a widget:

```python
    def _confirm_nest_options(self, nest_name: str) -> None:
        effect = self._pending_nest_effect
        self._close_nest_options(restore=False)
        if not effect:
            return
        payload = dict(effect)
        payload["nestName"] = nest_name
        self._dispatch_apply(payload)
```

Initialise `self._pending_nest_effect = None` in `__init__` and delete `_nest_inline_panel` /
`_nest_inline_name_entry` everywhere they appear.

- [ ] **Step 4: Repoint `show()` and `hide()` onto the QML motion**

In `show()`, replace the widget calls (`self.entry.blockSignals(...)`, `self.entry.clear()`,
`self.results_list.clear()`, `self.window.setFixedSize(...)`, `self.window.setWindowOpacity(...)`,
the geometry animation) with:

```python
        self.window.setProperty("searchText", "")
        self.view_model.select_category("Todos")
        self._anchor_window_to_pointer()
        self.window.show()
        self.window.playOpen()
        self._refresh_list()
        apply_windows_11_window_effects(self._window_hwnd())
```

In `hide()`, replace the widget teardown with:

```python
        self.window.playClose()
```

and add the completion handler:

```python
    def _on_close_finished(self):
        self.window.hide()
        self._restore_previous_focus()
```

- [ ] **Step 5: Confirm no widget references survive**

Run: `grep -nE "self\.(entry|results_list|status_label|help_label|conn_dot|refresh_btn|category_buttons|top_card|body_card|footer|empty_label|apply_progress|prompt)\b" companion/app.py`
Expected: matches only inside `QtSettingsCenter`, `QtHotkeyCatalogEditor`, `QtAliasTargetPicker`
and `QtDebugWindow` — never inside `QtEffectPalette`.

Run: `grep -n "QtPaletteWindow\|QtResultRowWidget\|_row_widgets\|_render_chunk_job\|_qt_middle_height" companion/app.py`
Expected: no matches.

- [ ] **Step 6: Run the tests**

Run: `python companion/tests/run_tests.py`
Expected: PASS.

- [ ] **Step 7: Lint and full suite**

Run: `python -m pyflakes companion/*.py && npm run validate`
Expected: clean, PASS.

- [ ] **Step 8: Smoke-test the real app by hand**

Run: `cd companion && python app.py`

Check: the palette opens on `Ctrl+Space` with the caret in the field; typing filters; Up/Down
slides the highlight; clicking a row selects it; double-click applies; the chips filter and the
active pill animates; the window grows and shrinks smoothly with the result count; Escape closes
with a fade; the connection dot shows the right colour.

Anything broken here must be fixed before committing.

- [ ] **Step 9: Commit**

```bash
git add companion/app.py companion/qml
git commit -m "feat(qml): replace the QtWidgets palette with the QML one"
```

---

## Task 10: PyInstaller packaging for QML

Verified with a real packaged build, per the spec's risk 3.

**Files:**
- Modify: `packaging/pyinstaller/FXPalette.spec`
- Test: `companion/tests/test_packaging.py`

**Interfaces:**
- Consumes: `companion/qml` (Tasks 1–8).
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_packaging.py`:

```python
QML_DIR = REPO / "companion" / "qml"


class QmlPackagingTests(unittest.TestCase):
    def test_qml_sources_exist(self):
        self.assertTrue((QML_DIR / "Palette.qml").exists())
        self.assertTrue((QML_DIR / "Theme.qml").exists())
        self.assertTrue((QML_DIR / "qmldir").exists())

    def test_spec_declares_the_qml_hidden_imports(self):
        text = SPEC.read_text(encoding="utf-8")
        for module in ("PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickControls2"):
            self.assertIn(module, text)

    def test_spec_ships_the_qml_directory(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("companion_qml", text)
        self.assertIn('"qml"', text)

    def test_every_qml_file_is_reachable_from_the_spec_datas(self):
        # A .qml added later but never shipped would only fail at runtime in the
        # packaged build, which is the slowest possible place to find out.
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("collect_qml", text)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — the spec has no QML hidden imports and no `companion_qml`.

- [ ] **Step 3: Update the PyInstaller spec**

In `packaging/pyinstaller/FXPalette.spec`, extend the hidden imports list:

```python
hiddenimports += [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtOpenGL",
    "PySide6.QtNetwork",
]
```

Add the QML data collection next to `companion_assets`:

```python
companion_qml = ROOT / "companion" / "qml"


def collect_qml():
    """Ship every .qml plus the qmldir. PyInstaller does not discover QML imports,
    so anything missing here fails only at runtime in the packaged build."""
    if not companion_qml.exists():
        return []
    return [(str(companion_qml), "qml")]
```

and extend `Analysis(datas=...)`:

```python
    datas=([(str(companion_assets), "assets")] if companion_assets.exists() else []) + collect_qml(),
```

Qt's own QML runtime plugins are collected by PyInstaller's PySide6 hook once `QtQuick` is a
hidden import.

- [ ] **Step 4: Make `QML_DIR` work inside a PyInstaller bundle**

In `companion/qml_host.py`, replace the `QML_DIR` definition:

```python
def _qml_dir() -> Path:
    """PyInstaller unpacks datas next to the executable under sys._MEIPASS."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "qml"
    return Path(__file__).resolve().parent / "qml"


QML_DIR = _qml_dir()
```

and add `import sys` at the top.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all four `QmlPackagingTests`.

- [ ] **Step 6: Build the installer for real**

Run: `powershell -ExecutionPolicy Bypass -File packaging/build_release.ps1`

Expected: the build completes and `release/` contains `FX.palette_Setup_<version>.exe`.

- [ ] **Step 7: Run the packaged executable**

Run: `release/staging/FXPalette/FX.palette.exe`

Expected: the palette opens and looks identical to `python app.py`. A QML packaging failure
usually shows as an empty/never-appearing window with a `module "FxPalette" is not installed`
or `QQmlApplicationEngine failed to load component` message. If that happens, the `qmldir` or
the `.qml` files did not reach the bundle — check `release/staging/FXPalette/qml/`.

Note the bundle size before and after for the record; the spec predicted +30–60 MB.

- [ ] **Step 8: Commit**

```bash
git add packaging/pyinstaller/FXPalette.spec companion/qml_host.py companion/tests/test_packaging.py
git commit -m "build: package the QML sources and Qt Quick runtime"
```

---

## Task 11: Translucency experiment and the host test

**Files:**
- Modify: `companion/app.py` (`load_app_preferences`, `QtSettingsCenter._build`, `_save`), `companion/qml/Palette.qml`
- Test: `companion/tests/test_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces: preference key `"translucency"`, default `False`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_app.py`:

```python
class TranslucencyPreferenceTests(unittest.TestCase):
    def test_translucency_defaults_to_off(self):
        import app
        self.assertIn("translucency", app.load_app_preferences())
        self.assertFalse(app.DEFAULT_APP_PREFERENCES["translucency"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `KeyError: 'translucency'`.

- [ ] **Step 3: Add the preference**

Find the preferences default dict in `companion/app.py` (the one `load_app_preferences` reads,
holding `animations` and `reconstructEasing`) and add `"translucency": False`. If that dict has
no module-level name, give it one — `DEFAULT_APP_PREFERENCES` — and have `load_app_preferences`
read from it, so the test can assert the default without touching the user's settings file.

- [ ] **Step 4: Apply the Windows 11 backdrop when the preference is on**

`apply_windows_11_window_effects(hwnd)` already exists and is already called from `show()`. Gate
it on the preference — inside that function, at the top:

```python
    if not load_app_preferences().get("translucency", False):
        return
```

In `companion/qml/Palette.qml`, make the shell semi-transparent only when translucency is on, by
adding to `PaletteMetrics` a `translucencyEnabled` constant property (mirroring the preference)
and binding:

```qml
        color: metrics.translucencyEnabled
               ? Qt.rgba(Theme.surface.r, Theme.surface.g, Theme.surface.b, 0.82)
               : Theme.surface
```

- [ ] **Step 5: Expose it in settings**

In `QtSettingsCenter._build`, next to `self.animations_check`:

```python
        self.translucency_check = QtWidgets.QCheckBox(self._text(
            "Fundo translúcido (experimental, Windows 11)",
            "Translucent background (experimental, Windows 11)"))
        self.translucency_check.setChecked(load_app_preferences()["translucency"])
        form.addRow("", self.translucency_check)
```

and persist it in `_save` alongside the other preference writes.

- [ ] **Step 6: Run the tests**

Run: `python companion/tests/run_tests.py`
Expected: PASS.

- [ ] **Step 7: Full suite and lint**

Run: `python -m pyflakes companion/*.py && npm run validate`
Expected: clean, PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/app.py companion/qml companion/tests/test_app.py
git commit -m "feat(qml): add the opt-in Windows 11 translucency experiment"
```

- [ ] **Step 9: Request the host test — do not skip this**

Phase 1's host test covered focus acquisition on the *widget* window. The palette is now a
`QQuickWindow`, so that evidence does not carry over. Stop and ask the user to verify, in a real
Premiere session, running the packaged build from Task 10 as well as `python app.py`:

1. Premiere foreground → palette hotkey. Appears **focused**, caret in the field, first press.
   **Repeat ten times** — this is the one that matters; `QQuickWindow` activates through
   `requestActivate()` rather than `activateWindow()`.
2. The palette renders **above** Premiere and does not fall behind it (the
   `WindowStaysOnTopHint` trap).
3. Apply an effect; focus returns to Premiere afterwards.
4. Native Nest (including typing a name into the new QML panel) and Timeline Label.
5. Second monitor: open with the pointer there — appears on that monitor, fully on screen.
6. Settings → turn "Use interface animations" off; confirm the palette still opens, closes and
   filters with no animation and no stuck state.
7. The packaged `FX.palette.exe` behaves the same as `python app.py`.

Report the Premiere version with the result. Record the evidence in `TECHNICAL_PLAN.md` and
update `STATUS.md` only after the user confirms.

---

## After this plan

Spec phase 2 is done: the palette is QML with a real theme and real motion, and the native window
layer is unchanged behind `QuickWindowAdapter`. Phase 3 (the next plan) ports the secondary
windows — `QtSettingsCenter`, `QtHotkeyCatalogEditor`, `QtAliasTargetPicker`, `QtDebugWindow` —
which are the ones the user called "REALLY REALLY CHEAP", and phase 4 deletes the remaining widget
scaffolding.
