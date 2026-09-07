"""Headless view-models for the palette.

These hold every piece of UI state and every decision the palette makes. The Qt widgets
(and later the QML views) are a projection of these objects and own nothing themselves.
Nothing here imports QtWidgets, so all of it is unit-testable without a display.
"""

from __future__ import annotations

from typing import Protocol

from PySide6 import QtCore

from models import MatchInfo, ResultRowModel, SearchResultSet


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


class QueryServices(Protocol):
    """The pure query-interpretation helpers, injected so the view-model stays testable.

    app.py supplies the real implementation; the tests supply a fake.
    """

    def resolve_alias(self, raw_query: str) -> str: ...
    def parse_label_command(self, query: str) -> str | None: ...
    def build_label_color_items(self, label_filter: str) -> list[dict]: ...
    def parse_slash_command(self, query: str) -> tuple[str, str | None, bool]: ...
    def build_recent_action_items(self) -> tuple[dict, ...]: ...
    def search(self, query: str, type_filters: set[str] | None) -> SearchResultSet: ...
    def build_row_model(self, payload: dict) -> ResultRowModel: ...
    def category_type_filters(self, category: str) -> set[str] | None: ...


def _unscored(items) -> SearchResultSet:
    """A result set for items that bypassed the search index (labels, recent actions)."""
    items = tuple(items)
    return SearchResultSet(
        items=items,
        match_infos=tuple(MatchInfo(score=0.0, ranges=()) for _ in items),
        total_count=len(items),
        visible_count=len(items),
        query="",
    )


class PaletteViewModel(QtCore.QObject):
    queryChanged = QtCore.Signal()
    activeCategoryChanged = QtCore.Signal()
    selectedIndexChanged = QtCore.Signal()
    viewStateChanged = QtCore.Signal()

    def __init__(self, services: QueryServices, parent=None):
        super().__init__(parent)
        self._services = services
        self._results = ResultsModel(self)
        self._result_set = _unscored(())
        self._query = ""
        self._raw_query = ""
        self._active_category: str | None = None
        self._selected_index = -1
        self._view_state = "idle"

    # --- properties -----------------------------------------------------------------

    @QtCore.Property(str, notify=queryChanged)
    def query(self) -> str:
        return self._query

    @QtCore.Property("QVariant", notify=activeCategoryChanged)
    def activeCategory(self):
        return self._active_category

    @QtCore.Property(int, notify=selectedIndexChanged)
    def selectedIndex(self) -> int:
        return self._selected_index

    @QtCore.Property(str, notify=viewStateChanged)
    def viewState(self) -> str:
        return self._view_state

    @QtCore.Property(QtCore.QObject, constant=True)
    def results(self) -> ResultsModel:
        return self._results

    @property
    def resultSet(self) -> SearchResultSet:
        return self._result_set

    # --- commands -------------------------------------------------------------------

    @QtCore.Slot(str)
    def set_query(self, raw_query: str) -> None:
        self._raw_query = raw_query
        self._recompute()

    @QtCore.Slot(str)
    def select_category(self, category: str) -> None:
        self._set_active_category(None if category == "Todos" else category)
        self._recompute()

    @QtCore.Slot(int)
    def move_selection(self, direction: int) -> None:
        count = self._results.rowCount()
        if count == 0:
            self._set_selected_index(-1)
            return
        current = self._selected_index if self._selected_index >= 0 else 0
        self._set_selected_index(max(0, min(current + direction, count - 1)))

    @QtCore.Slot(int)
    def set_selected_index(self, index: int) -> None:
        self._set_selected_index(index)

    @QtCore.Slot(result="QVariant")
    def selected_payload(self) -> dict | None:
        return self._results.payload_at(self._selected_index)

    @QtCore.Slot()
    def refresh(self) -> None:
        self._recompute()

    # --- internals ------------------------------------------------------------------

    def _recompute(self) -> None:
        raw = self._services.resolve_alias(self._raw_query.strip())
        label_filter = self._services.parse_label_command(raw)

        if label_filter is not None:
            self._result_set = _unscored(self._services.build_label_color_items(label_filter))
            query = label_filter
        else:
            query, slash_category, matched = self._services.parse_slash_command(raw)
            if matched and slash_category != self._active_category:
                self._set_active_category(slash_category)
            if query:
                self._result_set = self._services.search(
                    query, type_filters=self._resolve_type_filters()
                )
            else:
                self._result_set = _unscored(self._services.build_recent_action_items())

        self._set_query(query)
        rows = [self._services.build_row_model(item) for item in self._result_set.items]
        self._results.set_rows(rows)

        if rows:
            self._set_selected_index(0)
            self._set_view_state("results")
        else:
            self._set_selected_index(-1)
            # An empty query with nothing to show is the resting state, not a failed search.
            self._set_view_state("idle" if not query else "message")

    def _resolve_type_filters(self) -> set[str] | None:
        if self._active_category is None:
            return None
        return self._services.category_type_filters(self._active_category)

    def _set_query(self, value: str) -> None:
        if value != self._query:
            self._query = value
            self.queryChanged.emit()

    def _set_active_category(self, value: str | None) -> None:
        if value != self._active_category:
            self._active_category = value
            self.activeCategoryChanged.emit()

    def _set_selected_index(self, value: int) -> None:
        if value != self._selected_index:
            self._selected_index = value
            self.selectedIndexChanged.emit()

    def _set_view_state(self, value: str) -> None:
        if value != self._view_state:
            self._view_state = value
            self.viewStateChanged.emit()


class ApplyController(QtCore.QObject):
    """The apply lifecycle: idle -> busy -> success | error -> idle.

    Owns no widgets and does no I/O beyond polling the adapter it is given, so the whole
    state machine is testable with a fake adapter and a fake scheduler.
    """

    stateChanged = QtCore.Signal()
    succeeded = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, adapter, scheduler, parent=None):
        super().__init__(parent)
        self._adapter = adapter
        self._scheduler = scheduler
        self._state = "idle"
        self._last_status = ""
        self._active_effect: dict = {}
        self._command_timestamp: float | None = None

    @QtCore.Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @QtCore.Property(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._state == "busy"

    @QtCore.Property(str, notify=stateChanged)
    def lastStatus(self) -> str:
        return self._last_status

    @QtCore.Property("QVariant", notify=stateChanged)
    def activeEffect(self) -> dict:
        return self._active_effect

    def begin(self, effect: dict, command_timestamp: float) -> None:
        self._active_effect = dict(effect)
        self._command_timestamp = command_timestamp
        self._last_status = ""
        self._set_state("busy")

    def complete(self, status: str) -> None:
        # A late status for an apply that already finished must not resurrect the machine.
        if self._state != "busy":
            return
        self._last_status = status
        if self._adapter.is_success(status):
            self._set_state("success")
            self.succeeded.emit(status)
        else:
            self._set_state("error")
            self.failed.emit(status)

    def reset(self) -> None:
        self._active_effect = {}
        self._command_timestamp = None
        self._set_state("idle")

    @property
    def command_timestamp(self) -> float | None:
        return self._command_timestamp

    def _set_state(self, value: str) -> None:
        if value != self._state:
            self._state = value
            self.stateChanged.emit()
