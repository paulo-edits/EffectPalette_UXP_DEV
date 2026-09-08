"""Owns the QML engine for the palette.

Keeps every Qt Quick concern in one place so app.py only ever talks to `window` and
the view-models it already had.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtQml

from models import PaletteLayoutMetrics
from PySide6.QtQuickControls2 import QQuickStyle

QML_DIR = Path(__file__).resolve().parent / "qml"


class QmlPaletteHost(QtCore.QObject):
    def __init__(self, view_model, apply_controller, translate, *,
                 animations_enabled: bool = True, parent=None):
        super().__init__(parent)
        self._view_model = view_model
        self._apply_controller = apply_controller
        self._translate = translate
        self._animations_enabled = animations_enabled
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
        self._metrics = PaletteMetrics(animations_enabled=self._animations_enabled, parent=self)
        context.setContextProperty("metrics", self._metrics)

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


class PaletteMetrics(QtCore.QObject):
    """Geometry, motion and accent values that already exist in Python, handed to QML.

    Keeps the window size and animation timing defined in exactly one place.
    """

    def __init__(self, *, animations_enabled: bool, parent=None):
        super().__init__(parent)
        import app  # imported lazily: app.py imports this module

        self._window_width = app.FIXED_SEARCH_WINDOW_WIDTH
        self._results_height = app.RESULTS_EXPANDED_HEIGHT
        self._row_height = PaletteLayoutMetrics().row_height
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
