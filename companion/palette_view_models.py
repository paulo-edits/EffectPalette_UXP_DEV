"""Headless view-models for the palette.

These hold every piece of UI state and every decision the palette makes. The Qt widgets
(and later the QML views) are a projection of these objects and own nothing themselves.
Nothing here imports QtWidgets, so all of it is unit-testable without a display.
"""

from __future__ import annotations

from PySide6 import QtCore

from models import ResultRowModel


class ResultsModel(QtCore.QAbstractListModel):
    """Exposes the current result rows to a QListWidget today and a QML ListView later."""

    TitleRole = QtCore.Qt.ItemDataRole.UserRole + 1
    SubtitleRole = QtCore.Qt.ItemDataRole.UserRole + 2
    TypeLabelRole = QtCore.Qt.ItemDataRole.UserRole + 3
    IconKindRole = QtCore.Qt.ItemDataRole.UserRole + 4
    IsFavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 5
    AccentKindRole = QtCore.Qt.ItemDataRole.UserRole + 6
    AccentColorRole = QtCore.Qt.ItemDataRole.UserRole + 7
    PayloadRole = QtCore.Qt.ItemDataRole.UserRole + 8

    _ROLE_ATTRIBUTES = {
        TitleRole: "title",
        SubtitleRole: "subtitle",
        TypeLabelRole: "type_label",
        IconKindRole: "icon_kind",
        IsFavoriteRole: "is_favorite",
        AccentKindRole: "accent_kind",
        AccentColorRole: "accent_color",
        PayloadRole: "payload",
    }

    _ROLE_QML_NAMES = {
        TitleRole: b"title",
        SubtitleRole: b"subtitle",
        TypeLabelRole: b"typeLabel",
        IconKindRole: b"iconKind",
        IsFavoriteRole: b"isFavorite",
        AccentKindRole: b"accentKind",
        AccentColorRole: b"accentColor",
        PayloadRole: b"payload",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ResultRowModel] = []

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        row = self.row_at(index.row()) if index.isValid() else None
        if row is None:
            return None
        attribute = self._ROLE_ATTRIBUTES.get(role)
        if attribute is None:
            return None
        return getattr(row, attribute)

    def roleNames(self) -> dict:
        return dict(self._ROLE_QML_NAMES)

    def set_rows(self, rows: list[ResultRowModel]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, index: int) -> ResultRowModel | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def payload_at(self, index: int) -> dict | None:
        row = self.row_at(index)
        return row.payload if row is not None else None
