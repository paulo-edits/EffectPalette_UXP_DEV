"""Plain data shapes shared by the catalog loader, the Qt views and the view-models.

Deliberately free of any Qt import so the view-models and their tests can use these
without an application object.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchInfo:
    score: float
    ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SearchResultSet:
    items: tuple[dict, ...]
    match_infos: tuple[MatchInfo, ...]
    total_count: int
    visible_count: int
    query: str


@dataclass(frozen=True)
class ResultRowModel:
    payload: dict
    title: str
    subtitle: str
    type_label: str
    icon_kind: str
    is_favorite: bool
    accent_kind: str
    accent_color: str | None = None


@dataclass(frozen=True)
class PaletteLayoutMetrics:
    row_height: int = 52
    row_gap: int = 3
    row_radius: int = 12
    row_pad_x: int = 8
    row_pad_y: int = 6
    icon_size: int = 22
    type_badge_height: int = 18
    type_badge_radius: int = 10
    chip_height: int = 26
    chip_radius: int = 13
    chip_pad_x: int = 12
    results_outer_pad: int = 6
    max_visible_rows: int = 7
