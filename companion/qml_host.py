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
        if self.engine is not None:
            self.engine.deleteLater()
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
