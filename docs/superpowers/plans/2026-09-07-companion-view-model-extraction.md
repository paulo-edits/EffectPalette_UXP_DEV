# Companion View-Model Extraction + App Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the companion's UI state and logic out of the QtWidgets classes into headless, unit-tested Python `QObject`s, and give the product a real app icon — so the QML rewrite that follows has a tested seam to build on.

**Architecture:** `companion/app.py`'s `QtEffectPalette` is a ~1,200-line god object mixing view construction, view state, a search pipeline, an apply state machine, and native Win32 window control. This plan extracts the last four into new modules (`models.py`, `palette_view_models.py`, `window_control.py`) while the existing QtWidgets views stay in place and simply bind to them. Nothing visual changes. The riskiest piece — the native window layer — is extracted behind a `WindowAdapter` protocol and proven against the current, known-good widget window, so the later QML swap is a one-class change.

**Tech Stack:** Python 3, PySide6 (QtCore/QtGui/QtWidgets), `unittest` (offscreen Qt), Pillow, PyInstaller, Inno Setup.

**Spec:** `docs/superpowers/specs/2026-09-07-companion-qml-ui-rewrite-design.md`

## Global Constraints

- **This plan covers spec phases 1 and 5 only.** Phases 2–4 (the QML views) get their own plan, written after this one lands, because their tasks must reference the interfaces created here.
- **No visual change.** Every task in this plan leaves the UI looking and behaving exactly as it does today. If a change is visible to the user, it is a bug in that task.
- **Publisher string is `paulo.edits`** — lowercase, with a dot. Never `Paulo Edits`.
- **Product name is `FX.palette`.**
- **The plugin side is untouched.** No changes to `index.js`, `execution-adapter.js`, `transport.js`, `manifest.json`, `companion/uxp_execution_adapter.py` behavior, or the action allowlist.
- **`npm run validate` must pass at the end of every task.** It runs manifest checks, the plugin tests, and the companion tests.
- **Never commit `companion/data/uxp_*.json`, `premiere_shortcut_configuration.log`, or `.gitattributes`.** They are runtime-generated or an open decision.
- **Off-host tests prove nothing about Premiere.** Per `CLAUDE.md`, no task here may be reported as "working in Premiere". Task 9 ends with an explicit host-test request to the user.
- **Settings path stays** `%APPDATA%\Adobe\CEP\extensions\EffectPalette\settings.json`. Do not move it.
- Work on branch `ui/qml-rewrite`, cut from `main`.

---

## File Structure

**Created:**
- `companion/models.py` — the frozen dataclasses shared by the loader, the views, and the new view-models. Pure Python, no Qt import.
- `companion/palette_view_models.py` — `ResultsModel`, `PaletteViewModel`, `ApplyController`. Qt, but no widgets.
- `companion/window_control.py` — `WindowAdapter` protocol, `WidgetWindowAdapter`, `PaletteWindowController`.
- `companion/tests/test_models.py`
- `companion/tests/test_view_models.py`
- `companion/tests/test_window_control.py`
- `companion/tests/test_packaging.py`

**Modified:**
- `companion/app.py` — imports the new modules, deletes the moved code, binds widgets to the view-models.
- `packaging/pyinstaller/FXPalette.spec` — icon + version resource.
- `packaging/inno/FXPalette.iss` — `SetupIconFile`, `UninstallDisplayIcon`, publisher.

**Already present (from the design session, do not regenerate):**
- `companion/assets/fx_palette.ico`, `companion/assets/fx_palette.png`, `scripts/make_app_icon.py`

`companion/tests/run_tests.py` auto-discovers `test_*.py`, so new test files need no registration.

---

## Task 1: App identity — icon wiring and publisher fix

Independent of every other task. Fixes the generic icon in Windows "Installed apps".

**Files:**
- Modify: `packaging/pyinstaller/FXPalette.spec:30-63`
- Modify: `packaging/inno/FXPalette.iss:2`, `:43`
- Modify: `companion/app.py` (the `QtEffectPalette.__init__` bootstrap and `SystemTrayController._make_icon_image`)
- Test: `companion/tests/test_packaging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.APP_ICON_ICO: Path` and `app.APP_ICON_PNG: Path` — module-level constants pointing at `companion/assets/fx_palette.ico` / `.png`. Later tasks do not depend on these.

- [ ] **Step 1: Write the failing test**

Create `companion/tests/test_packaging.py`:

```python
"""Guards the packaging wiring that gives the product a real identity in Windows."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "packaging" / "pyinstaller" / "FXPalette.spec"
ISS = REPO / "packaging" / "inno" / "FXPalette.iss"
ICO = REPO / "companion" / "assets" / "fx_palette.ico"
PNG = REPO / "companion" / "assets" / "fx_palette.png"


class AppIdentityTests(unittest.TestCase):
    def test_icon_assets_exist(self):
        self.assertTrue(ICO.exists(), "companion/assets/fx_palette.ico is missing")
        self.assertTrue(PNG.exists(), "companion/assets/fx_palette.png is missing")

    def test_pyinstaller_spec_sets_the_exe_icon(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("fx_palette.ico", text)
        self.assertIn("icon=", text)

    def test_pyinstaller_spec_declares_a_version_resource(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("paulo.edits", text)
        self.assertIn("FX.palette", text)

    def test_inno_publisher_is_the_brand_spelling(self):
        text = ISS.read_text(encoding="utf-8")
        self.assertIn('#define MyAppPublisher "paulo.edits"', text)
        self.assertNotIn("Paulo Edits", text)

    def test_inno_uses_the_icon_for_setup_and_uninstall(self):
        text = ISS.read_text(encoding="utf-8")
        self.assertIn("SetupIconFile=", text)
        # The uninstall entry must point at a real .ico, not the icon-less exe.
        self.assertIn(r"UninstallDisplayIcon={app}\fx_palette.ico", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `test_pyinstaller_spec_sets_the_exe_icon`, `test_pyinstaller_spec_declares_a_version_resource`, `test_inno_publisher_is_the_brand_spelling`, and `test_inno_uses_the_icon_for_setup_and_uninstall` all fail. `test_icon_assets_exist` passes (the assets were generated during the design session).

- [ ] **Step 3: Add the icon and version resource to the PyInstaller spec**

In `packaging/pyinstaller/FXPalette.spec`, after the `companion_assets = ...` line, add:

```python
APP_ICON = companion_assets / "fx_palette.ico"

# Written into the exe's Properties -> Details tab. Without this the file shows up as a
# nameless PyInstaller bootloader.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=(0, 54, 0, 0), prodvers=(0, 54, 0, 0)),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "paulo.edits"),
                StringStruct("FileDescription", "FX.palette - search palette for Adobe Premiere Pro"),
                StringStruct("FileVersion", "0.54.0"),
                StringStruct("InternalName", "FX.palette"),
                StringStruct("LegalCopyright", "Copyright (c) paulo.edits"),
                StringStruct("OriginalFilename", "FX.palette.exe"),
                StringStruct("ProductName", "FX.palette"),
                StringStruct("ProductVersion", "0.54.0"),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
```

Add the import that those classes need, at the top of the file next to the existing `from PyInstaller.utils.hooks import collect_submodules`:

```python
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)
```

Then in the `EXE(...)` call, add these two arguments after `console=False`:

```python
    icon=str(APP_ICON),
    version=version_info,
```

- [ ] **Step 4: Fix the Inno publisher and icons**

In `packaging/inno/FXPalette.iss`, change line 2 from `#define MyAppPublisher "Paulo Edits"` to:

```
#define MyAppPublisher "paulo.edits"
```

In the `[Setup]` section, replace the `UninstallDisplayIcon={app}\{#MyAppExeName}` line with:

```
SetupIconFile=..\..\companion\assets\fx_palette.ico
UninstallDisplayIcon={app}\fx_palette.ico
```

In the `[Files]` section, add a line so that `.ico` actually reaches `{app}`:

```
Source: "..\..\companion\assets\fx_palette.ico"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 5: Set the Qt window icon and the tray icon**

In `companion/app.py`, next to the other asset path constants (near `GOOGLE_SANS_FLEX_REGULAR`), add:

```python
APP_ICON_ICO = ASSETS_DIR / "fx_palette.ico"
APP_ICON_PNG = ASSETS_DIR / "fx_palette.png"
```

Use whatever the file already calls the assets directory; if there is no such constant, derive it the same way `GOOGLE_SANS_FLEX_REGULAR` is derived.

In `QtEffectPalette.__init__`, immediately after `self.app.setQuitOnLastWindowClosed(False)`, add:

```python
if APP_ICON_PNG.exists():
    self.app.setWindowIcon(QtGui.QIcon(str(APP_ICON_PNG)))
```

In `SystemTrayController._make_icon_image`, return the shipped asset instead of drawing one, falling back to the existing drawing code if the asset is missing:

```python
def _make_icon_image(self):
    if APP_ICON_PNG.exists():
        return Image.open(APP_ICON_PNG).convert("RGBA")
    return self._draw_fallback_icon_image()
```

Rename the existing body of `_make_icon_image` to `_draw_fallback_icon_image` and leave it otherwise untouched.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all five `AppIdentityTests`.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/assets/fx_palette.ico companion/assets/fx_palette.png scripts/make_app_icon.py \
        packaging/pyinstaller/FXPalette.spec packaging/inno/FXPalette.iss \
        companion/app.py companion/tests/test_packaging.py
git commit -m "feat(identity): ship an app icon and fix the publisher spelling"
```

**Note for the reviewer:** the icon only appears in Windows "Installed apps" after a real installer build (`packaging/build_release.ps1`). That is a host verification, not something this suite proves.

---

## Task 2: Extract the shared dataclasses into `companion/models.py`

Pure move. No behavior change. This gives the view-models something to import without pulling in `app.py`.

**Files:**
- Create: `companion/models.py`
- Modify: `companion/app.py` (delete the four dataclass definitions, import them instead)
- Test: `companion/tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `models.MatchInfo(score: float, ranges: tuple[tuple[int, int], ...])` — frozen
  - `models.SearchResultSet(items: tuple[dict, ...], match_infos: tuple[MatchInfo, ...], total_count: int, visible_count: int, query: str)` — frozen
  - `models.ResultRowModel(payload: dict, title: str, subtitle: str, type_label: str, icon_kind: str, is_favorite: bool, accent_kind: str, accent_color: str | None = None)` — frozen
  - `models.PaletteLayoutMetrics(...)` — frozen, all fields defaulted
  - `app.MatchInfo`, `app.SearchResultSet`, `app.ResultRowModel`, `app.PaletteLayoutMetrics` remain importable (re-exported), because `companion/tests/test_app.py` uses them.

- [ ] **Step 1: Write the failing test**

Create `companion/tests/test_models.py`:

```python
"""The shared dataclasses must live in models.py and stay importable from app.py."""

from __future__ import annotations

import dataclasses
import unittest

import models


class ModelsModuleTests(unittest.TestCase):
    def test_dataclasses_are_frozen(self):
        for cls in (models.MatchInfo, models.SearchResultSet, models.ResultRowModel, models.PaletteLayoutMetrics):
            self.assertTrue(dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass")
            self.assertTrue(cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen")

    def test_models_does_not_import_qt(self):
        # The point of this module is that it is importable without a Qt application.
        import inspect
        source = inspect.getsource(models)
        self.assertNotIn("PySide6", source)

    def test_result_row_model_accent_color_defaults_to_none(self):
        row = models.ResultRowModel(
            payload={}, title="t", subtitle="s", type_label="L",
            icon_kind="effect", is_favorite=False, accent_kind="video",
        )
        self.assertIsNone(row.accent_color)

    def test_app_still_re_exports_them(self):
        import app
        self.assertIs(app.ResultRowModel, models.ResultRowModel)
        self.assertIs(app.SearchResultSet, models.SearchResultSet)
        self.assertIs(app.MatchInfo, models.MatchInfo)
        self.assertIs(app.PaletteLayoutMetrics, models.PaletteLayoutMetrics)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'models'`.

- [ ] **Step 3: Create `companion/models.py`**

Move the four dataclass definitions out of `companion/app.py` verbatim. The file is:

```python
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
```

- [ ] **Step 4: Import them in `app.py`**

Delete the four `@dataclass` blocks from `companion/app.py` (they sit around lines 1257–1300). In their place put:

```python
from models import MatchInfo, PaletteLayoutMetrics, ResultRowModel, SearchResultSet
```

Put that import with the other local-module imports near the top of the file, not in the middle. Leave `IndexedItem` and `LoaderSnapshot` where they are — the loader owns them and nothing outside `app.py` needs them.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS. `test_app.py`'s existing `test_result_row_model` must still pass — that is the signal the re-export works.

- [ ] **Step 6: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add companion/models.py companion/app.py companion/tests/test_models.py
git commit -m "refactor: move the shared dataclasses into companion/models.py"
```

---

## Task 3: `ResultsModel` — a `QAbstractListModel` over the row models

**Files:**
- Create: `companion/palette_view_models.py`
- Test: `companion/tests/test_view_models.py`

**Interfaces:**
- Consumes: `models.ResultRowModel` (Task 2).
- Produces:
  - `palette_view_models.ResultsModel(QtCore.QAbstractListModel)`
  - `ResultsModel.Roles` — an `IntEnum`-like set of ints: `TitleRole`, `SubtitleRole`, `TypeLabelRole`, `IconKindRole`, `IsFavoriteRole`, `AccentKindRole`, `AccentColorRole`, `PayloadRole`
  - `ResultsModel.set_rows(rows: list[ResultRowModel]) -> None`
  - `ResultsModel.row_at(index: int) -> ResultRowModel | None`
  - `ResultsModel.payload_at(index: int) -> dict | None`
  - `ResultsModel.rowCount(parent=QModelIndex()) -> int`
  - `ResultsModel.data(index, role) -> object`
  - `ResultsModel.roleNames() -> dict[int, bytes]` — QML binds by these names

- [ ] **Step 1: Write the failing test**

Create `companion/tests/test_view_models.py`:

```python
"""Headless tests for the palette's view-models. No widgets, no QML, no Premiere."""

from __future__ import annotations

import unittest

from models import ResultRowModel
from palette_view_models import ResultsModel


def make_row(title: str, *, accent: str = "video", payload: dict | None = None) -> ResultRowModel:
    return ResultRowModel(
        payload=payload if payload is not None else {"name": title, "type": "effect_video"},
        title=title,
        subtitle=f"{title} subtitle",
        type_label="VIDEO",
        icon_kind="effect",
        is_favorite=False,
        accent_kind=accent,
        accent_color=None,
    )


class ResultsModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ResultsModel()

    def test_starts_empty(self):
        self.assertEqual(self.model.rowCount(), 0)

    def test_set_rows_reports_the_new_count(self):
        self.model.set_rows([make_row("Gaussian Blur"), make_row("Warp Stabilizer")])
        self.assertEqual(self.model.rowCount(), 2)

    def test_data_returns_the_mapped_role(self):
        self.model.set_rows([make_row("Gaussian Blur")])
        index = self.model.index(0, 0)
        self.assertEqual(self.model.data(index, ResultsModel.TitleRole), "Gaussian Blur")
        self.assertEqual(self.model.data(index, ResultsModel.SubtitleRole), "Gaussian Blur subtitle")
        self.assertEqual(self.model.data(index, ResultsModel.TypeLabelRole), "VIDEO")
        self.assertEqual(self.model.data(index, ResultsModel.AccentKindRole), "video")
        self.assertIs(self.model.data(index, ResultsModel.IsFavoriteRole), False)

    def test_data_for_an_out_of_range_index_is_none(self):
        self.model.set_rows([make_row("Gaussian Blur")])
        self.assertIsNone(self.model.data(self.model.index(5, 0), ResultsModel.TitleRole))

    def test_role_names_are_camel_case_for_qml(self):
        names = self.model.roleNames()
        self.assertEqual(names[ResultsModel.TitleRole], b"title")
        self.assertEqual(names[ResultsModel.AccentColorRole], b"accentColor")
        self.assertEqual(names[ResultsModel.PayloadRole], b"payload")

    def test_payload_at_returns_the_underlying_dict(self):
        payload = {"name": "Warp Stabilizer", "type": "effect_video"}
        self.model.set_rows([make_row("Warp Stabilizer", payload=payload)])
        self.assertIs(self.model.payload_at(0), payload)

    def test_payload_at_out_of_range_is_none(self):
        self.assertIsNone(self.model.payload_at(0))

    def test_set_rows_replaces_rather_than_appends(self):
        self.model.set_rows([make_row("A"), make_row("B")])
        self.model.set_rows([make_row("C")])
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.data(self.model.index(0, 0), ResultsModel.TitleRole), "C")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'palette_view_models'`.

- [ ] **Step 3: Create `companion/palette_view_models.py` with `ResultsModel`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all eight `ResultsModelTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/palette_view_models.py companion/tests/test_view_models.py
git commit -m "feat(view-models): add ResultsModel over the palette result rows"
```

---

## Task 4: `PaletteViewModel` — query, category and result state

This holds the logic currently tangled inside `QtEffectPalette._refresh_list`. The query-interpretation helpers stay in `app.py`; the view-model receives them through one injected object, which is also what makes it testable.

**Files:**
- Modify: `companion/palette_view_models.py`
- Test: `companion/tests/test_view_models.py`

**Interfaces:**
- Consumes: `ResultsModel` (Task 3), `models.SearchResultSet` / `models.MatchInfo` / `models.ResultRowModel` (Task 2).
- Produces:
  - `palette_view_models.QueryServices` — a `Protocol` with:
    - `resolve_alias(raw_query: str) -> str`
    - `parse_label_command(query: str) -> str | None`
    - `build_label_color_items(label_filter: str) -> list[dict]`
    - `parse_slash_command(query: str) -> tuple[str, str | None, bool]`
    - `build_recent_action_items() -> tuple[dict, ...]`
    - `search(query: str, type_filters: set[str] | None) -> SearchResultSet`
    - `build_row_model(payload: dict) -> ResultRowModel`
    - `category_type_filters(category: str) -> set[str] | None`
  - `palette_view_models.PaletteViewModel(QtCore.QObject)` with:
    - constructor `PaletteViewModel(services: QueryServices, parent=None)`
    - property `query: str` (signal `queryChanged`)
    - property `activeCategory: str | None` (signal `activeCategoryChanged`)
    - property `selectedIndex: int` (signal `selectedIndexChanged`)
    - property `viewState: str` — one of `"idle"`, `"message"`, `"results"` (signal `viewStateChanged`)
    - read-only property `results: ResultsModel`
    - read-only property `resultSet: SearchResultSet`
    - `set_query(raw_query: str) -> None`
    - `select_category(category: str) -> None`
    - `move_selection(direction: int) -> None`
    - `set_selected_index(index: int) -> None` — added in Task 5 Step 5, for click-to-select
    - `selected_payload() -> dict | None`
    - `refresh() -> None` — re-runs the current query

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_view_models.py`:

```python
from models import MatchInfo, SearchResultSet
from palette_view_models import PaletteViewModel


class FakeQueryServices:
    """Stands in for the pure helpers that live in app.py."""

    CATEGORY_FILTERS = {"Video": {"effect_video"}, "Audio": {"effect_audio"}}

    def __init__(self, catalog=None):
        self.catalog = catalog if catalog is not None else []
        self.recent = ({"name": "Recent One", "type": "effect_video"},)
        self.label_items = [{"name": "Violet", "type": "timeline_action"}]
        self.aliases = {}
        self.label_queries = set()
        self.slash = {}
        self.last_type_filters = "unset"

    def resolve_alias(self, raw_query):
        return self.aliases.get(raw_query, raw_query)

    def parse_label_command(self, query):
        return "violet" if query in self.label_queries else None

    def build_label_color_items(self, label_filter):
        return list(self.label_items)

    def parse_slash_command(self, query):
        if query in self.slash:
            return self.slash[query]
        return query, None, False

    def build_recent_action_items(self):
        return self.recent

    def search(self, query, type_filters=None):
        self.last_type_filters = type_filters
        items = tuple(e for e in self.catalog if query.lower() in e["name"].lower())
        if type_filters is not None:
            items = tuple(e for e in items if e["type"] in type_filters)
        return SearchResultSet(
            items=items,
            match_infos=tuple(MatchInfo(score=1.0, ranges=()) for _ in items),
            total_count=len(items),
            visible_count=len(items),
            query=query,
        )

    def build_row_model(self, payload):
        return make_row(payload["name"], payload=payload)

    def category_type_filters(self, category):
        return self.CATEGORY_FILTERS.get(category)


CATALOG = [
    {"name": "Gaussian Blur", "type": "effect_video"},
    {"name": "Gaussian Sharpen", "type": "effect_video"},
    {"name": "Studio Reverb", "type": "effect_audio"},
]


class PaletteViewModelTests(unittest.TestCase):
    def setUp(self):
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)

    def test_empty_query_shows_recent_actions_and_results_state(self):
        self.vm.set_query("")
        self.assertEqual(self.vm.results.rowCount(), 1)
        self.assertEqual(self.vm.viewState, "results")

    def test_empty_query_with_no_recent_actions_is_idle(self):
        self.services.recent = ()
        self.vm.set_query("")
        self.assertEqual(self.vm.results.rowCount(), 0)
        self.assertEqual(self.vm.viewState, "idle")

    def test_query_filters_the_catalog(self):
        self.vm.set_query("gaussian")
        self.assertEqual(self.vm.results.rowCount(), 2)
        self.assertEqual(self.vm.viewState, "results")

    def test_query_with_no_match_is_message_state(self):
        self.vm.set_query("nothing matches this")
        self.assertEqual(self.vm.results.rowCount(), 0)
        self.assertEqual(self.vm.viewState, "message")

    def test_category_narrows_the_type_filters(self):
        self.vm.select_category("Audio")
        self.vm.set_query("e")
        self.assertEqual(self.services.last_type_filters, {"effect_audio"})

    def test_todos_category_clears_the_filter(self):
        self.vm.select_category("Audio")
        self.vm.select_category("Todos")
        self.vm.set_query("e")
        self.assertIsNone(self.vm.activeCategory)
        self.assertIsNone(self.services.last_type_filters)

    def test_slash_command_switches_the_category(self):
        self.services.slash["/audio reverb"] = ("reverb", "Audio", True)
        self.vm.set_query("/audio reverb")
        self.assertEqual(self.vm.activeCategory, "Audio")

    def test_alias_is_resolved_before_searching(self):
        self.services.aliases["gb"] = "gaussian blur"
        self.vm.set_query("gb")
        self.assertEqual(self.vm.results.rowCount(), 1)

    def test_label_command_bypasses_the_search_index(self):
        self.services.label_queries.add("/label")
        self.vm.set_query("/label")
        self.assertEqual(self.vm.results.rowCount(), 1)
        self.assertEqual(self.vm.viewState, "results")

    def test_selection_starts_at_the_first_row(self):
        self.vm.set_query("gaussian")
        self.assertEqual(self.vm.selectedIndex, 0)

    def test_move_selection_clamps_at_both_ends(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(-1)
        self.assertEqual(self.vm.selectedIndex, 0)
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selectedIndex, 1)
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selectedIndex, 1)

    def test_selected_payload_follows_the_selection(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selected_payload()["name"], "Gaussian Sharpen")

    def test_selected_payload_is_none_when_there_are_no_results(self):
        self.vm.set_query("nothing matches this")
        self.assertIsNone(self.vm.selected_payload())

    def test_query_changed_signal_fires(self):
        seen = []
        self.vm.queryChanged.connect(lambda: seen.append(self.vm.query))
        self.vm.set_query("gaussian")
        self.assertEqual(seen, ["gaussian"])

    def test_view_state_changed_signal_fires_on_transition(self):
        seen = []
        self.vm.viewStateChanged.connect(lambda: seen.append(self.vm.viewState))
        self.vm.set_query("gaussian")
        self.vm.set_query("nothing matches this")
        self.assertEqual(seen, ["results", "message"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ImportError: cannot import name 'PaletteViewModel'`.

- [ ] **Step 3: Implement `QueryServices` and `PaletteViewModel`**

Append to `companion/palette_view_models.py`:

```python
from typing import Protocol

from models import MatchInfo, SearchResultSet


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all fourteen `PaletteViewModelTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/palette_view_models.py companion/tests/test_view_models.py
git commit -m "feat(view-models): add PaletteViewModel for query, category and selection state"
```

---

## Task 5: Bind the palette widgets to the view-model

The first task with user-visible risk. The widgets stop owning state and become a projection. **Nothing about the UI may look or behave differently.**

**Files:**
- Modify: `companion/app.py` — `QtEffectPalette.__init__`, `_build`, `_on_search_change`, `_refresh_list`, `_on_category_click`, `_move_selection`, `_selected_payload`, `_resolve_type_filters`, `_build_result_row_model`
- Test: `companion/tests/test_view_models.py` (one new test)

**Interfaces:**
- Consumes: `PaletteViewModel`, `ResultsModel` (Tasks 3–4).
- Produces: `app.AppQueryServices` — the concrete `QueryServices` implementation wrapping `app.py`'s existing module-level helpers and the `EffectsLoader` instance. Constructor: `AppQueryServices(loader)`.

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_view_models.py`:

```python
class AppQueryServicesTests(unittest.TestCase):
    """app.py's real services object must satisfy the QueryServices protocol."""

    def test_app_exposes_a_services_implementation(self):
        import app
        self.assertTrue(hasattr(app, "AppQueryServices"))

    def test_services_methods_are_all_present(self):
        import app
        required = (
            "resolve_alias", "parse_label_command", "build_label_color_items",
            "parse_slash_command", "build_recent_action_items", "search",
            "build_row_model", "category_type_filters",
        )
        for name in required:
            self.assertTrue(
                callable(getattr(app.AppQueryServices, name, None)),
                f"AppQueryServices is missing {name}()",
            )

    def test_category_type_filters_matches_the_palette_table(self):
        import app
        services = app.AppQueryServices(loader=None)
        for category, expected in app.CATEGORY_TYPE_FILTERS.items():
            self.assertEqual(services.category_type_filters(category), expected)
        self.assertIsNone(services.category_type_filters("Todos"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python companion/tests/run_tests.py`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'AppQueryServices'`.

- [ ] **Step 3: Add `AppQueryServices` to `app.py`**

Place it just above `class QtEffectPalette`:

```python
class AppQueryServices:
    """Binds the module-level query helpers and the loader into one injectable object.

    Exists so PaletteViewModel can be unit-tested against a fake instead of the whole app.
    """

    def __init__(self, loader):
        self.loader = loader

    def resolve_alias(self, raw_query: str) -> str:
        return resolve_alias_query(raw_query)

    def parse_label_command(self, query: str):
        return parse_label_command(query)

    def build_label_color_items(self, label_filter: str):
        return list(build_label_color_items(label_filter))

    def parse_slash_command(self, query: str):
        return parse_slash_command(query)

    def build_recent_action_items(self):
        return build_recent_action_items()

    def search(self, query: str, type_filters=None):
        return self.loader.search(query, type_filters=type_filters)

    def build_row_model(self, payload: dict) -> ResultRowModel:
        return build_result_row_model(payload)

    def category_type_filters(self, category: str):
        return CATEGORY_TYPE_FILTERS.get(category)
```

- [ ] **Step 4: Construct the view-model in `QtEffectPalette.__init__`**

After `self.loader = EffectsLoader()`, add:

```python
self.view_model = PaletteViewModel(AppQueryServices(self.loader))
```

Add the import near the other local-module imports:

```python
from palette_view_models import PaletteViewModel, ResultsModel
```

Delete these now-redundant instance attributes from `__init__`: `self._active_category`, `self._current_results`, `self._current_row_models`, `self._current_result_set`. Every read of them becomes a read through `self.view_model` (next step).

- [ ] **Step 5: Rewrite the palette's list plumbing to project the view-model**

Replace `_refresh_list`, `_on_category_click`, `_on_search_change`, `_move_selection`, `_selected_payload`, `_resolve_type_filters` and `_build_result_row_model` with:

```python
    def _on_category_click(self, category: str):
        self.view_model.select_category(category)
        self._update_category_buttons()
        self._render_view_model()

    def _on_search_change(self, *_args):
        if self._search_job is not None:
            self.root.after_cancel(self._search_job)
            self._search_job = None
        self._refresh_list()

    def _refresh_list(self):
        self._search_job = None
        self.view_model.set_query(self.entry.text())
        self._update_category_buttons()
        self._render_view_model()

    def _render_view_model(self):
        state = self.view_model.viewState
        if state == "idle":
            self._cancel_render_chunk()
            self._row_widgets = []
            self.results_list.clear()
            self.status_label.setText("")
            self._set_idle_state()
            self._resize_to_content()
            return
        if state == "message":
            self.status_label.setText(tr("status_no_results"))
            self._set_message_state()
            self._resize_to_content()
            return
        self._populate_results()
        result_set = self.view_model.resultSet
        self.status_label.setText(tr(
            "status_results_count",
            visible=result_set.visible_count,
            total=result_set.total_count,
        ))
        self._set_results_state()
        self._resize_to_content()

    def _move_selection(self, direction: int):
        self.view_model.move_selection(direction)
        self.results_list.setCurrentRow(self.view_model.selectedIndex)

    def _selected_payload(self):
        return self.view_model.selected_payload()
```

`_append_result_rows` reads the row models from the view-model now. Change its two references:

- `end = min(len(self._current_row_models), start + count)` becomes
  `end = min(self.view_model.results.rowCount(), start + count)`
- `for model in self._current_row_models[start:end]:` becomes
  `for index in range(start, end):` with `model = self.view_model.results.row_at(index)` as the first line of the loop body
- the trailing `if end < len(self._current_row_models):` becomes
  `if end < self.view_model.results.rowCount():`

`_sync_row_selection` must keep the view-model in step when the user clicks a row. Add as its first line:

```python
        self.view_model._set_selected_index(selected_row)
```

Replace that private call with a public `set_selected_index` slot on `PaletteViewModel` — add to `palette_view_models.py`:

```python
    @QtCore.Slot(int)
    def set_selected_index(self, index: int) -> None:
        self._set_selected_index(index)
```

and call `self.view_model.set_selected_index(selected_row)` instead.

- [ ] **Step 6: Fix the remaining references to the deleted attributes**

Search for them and repoint each one:

Run: `grep -n "_current_row_models\|_current_results\|_current_result_set\|self._active_category" companion/app.py`

Expected after the edits: no matches. `_current_results` readers become `self.view_model.resultSet.items`; `_active_category` readers become `self.view_model.activeCategory`. `_update_category_buttons` compares against `self.view_model.activeCategory`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including the three new `AppQueryServicesTests`.

- [ ] **Step 8: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 9: Smoke-test the real app**

Run: `cd companion && python app.py`

Then check by hand, because no automated test covers these:
- Ctrl+Space opens the palette.
- Typing filters results; the count in the footer matches.
- Up/Down moves the selection highlight; clicking a row selects it.
- Clicking each category chip filters, and the chip highlights.
- An empty query shows recent actions; a nonsense query shows the "no results" message.
- A `/`-prefixed slash command switches the category chip.

Expected: identical to before this task. Any difference is a bug — fix it before committing.

- [ ] **Step 10: Commit**

```bash
git add companion/app.py companion/palette_view_models.py companion/tests/test_view_models.py
git commit -m "refactor(palette): project the view-model instead of owning result state"
```

---

## Task 6: `ApplyController` — the apply state machine

**Files:**
- Modify: `companion/palette_view_models.py`
- Test: `companion/tests/test_view_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `palette_view_models.ApplyController(QtCore.QObject)`
    - constructor `ApplyController(adapter, scheduler, parent=None)` where `adapter` has `is_success(status) -> bool`, `is_terminal(status) -> bool`, `poll_status(timestamp) -> str | None`, `backend_name: str`; and `scheduler` has `after(delay_ms, callback) -> job` and `after_cancel(job) -> None`
    - property `state: str` — `"idle" | "busy" | "success" | "error"` (signal `stateChanged`)
    - property `busy: bool` (signal `stateChanged`)
    - property `lastStatus: str` (signal `stateChanged`)
    - property `activeEffect: dict` (signal `stateChanged`)
    - `begin(effect: dict, command_timestamp: float) -> None`
    - `complete(status: str) -> None`
    - `reset() -> None`
    - read-only Python property `command_timestamp: float | None`
    - signals `succeeded(str)` and `failed(str)` carrying the raw status

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_view_models.py`:

```python
from palette_view_models import ApplyController


class FakeAdapter:
    backend_name = "fake"

    def __init__(self):
        self.statuses = []

    def is_success(self, status):
        return status == "ok"

    def is_terminal(self, status):
        return status in {"ok", "error"}

    def poll_status(self, timestamp):
        return self.statuses.pop(0) if self.statuses else None


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.next_id = 1
        self.cancelled = []

    def after(self, delay_ms, callback):
        job_id = self.next_id
        self.next_id += 1
        self.jobs[job_id] = callback
        return job_id

    def after_cancel(self, job_id):
        self.cancelled.append(job_id)
        self.jobs.pop(job_id, None)


class ApplyControllerTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAdapter()
        self.scheduler = FakeScheduler()
        self.controller = ApplyController(self.adapter, self.scheduler)

    def test_starts_idle(self):
        self.assertEqual(self.controller.state, "idle")
        self.assertFalse(self.controller.busy)

    def test_begin_moves_to_busy_and_records_the_effect(self):
        effect = {"name": "Gaussian Blur", "type": "effect_video"}
        self.controller.begin(effect, command_timestamp=123.0)
        self.assertEqual(self.controller.state, "busy")
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller.activeEffect["name"], "Gaussian Blur")

    def test_success_status_moves_to_success_and_emits(self):
        seen = []
        self.controller.succeeded.connect(seen.append)
        self.controller.begin({"name": "X"}, command_timestamp=1.0)
        self.controller.complete("ok")
        self.assertEqual(self.controller.state, "success")
        self.assertEqual(seen, ["ok"])

    def test_failure_status_moves_to_error_and_emits(self):
        seen = []
        self.controller.failed.connect(seen.append)
        self.controller.begin({"name": "X"}, command_timestamp=1.0)
        self.controller.complete("error")
        self.assertEqual(self.controller.state, "error")
        self.assertFalse(self.controller.busy)
        self.assertEqual(seen, ["error"])

    def test_reset_returns_to_idle(self):
        self.controller.begin({"name": "X"}, command_timestamp=1.0)
        self.controller.complete("error")
        self.controller.reset()
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.controller.activeEffect, {})

    def test_complete_while_idle_is_ignored(self):
        self.controller.complete("ok")
        self.assertEqual(self.controller.state, "idle")

    def test_last_status_is_retained(self):
        self.controller.begin({"name": "X"}, command_timestamp=1.0)
        self.controller.complete("error")
        self.assertEqual(self.controller.lastStatus, "error")

    def test_state_changed_fires_on_each_transition(self):
        seen = []
        self.controller.stateChanged.connect(lambda: seen.append(self.controller.state))
        self.controller.begin({"name": "X"}, command_timestamp=1.0)
        self.controller.complete("ok")
        self.controller.reset()
        self.assertEqual(seen, ["busy", "success", "idle"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ImportError: cannot import name 'ApplyController'`.

- [ ] **Step 3: Implement `ApplyController`**

Append to `companion/palette_view_models.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all eight `ApplyControllerTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/palette_view_models.py companion/tests/test_view_models.py
git commit -m "feat(view-models): add ApplyController for the apply lifecycle"
```

---

## Task 7: Bind the apply flow to `ApplyController`

**Files:**
- Modify: `companion/app.py` — `QtEffectPalette.__init__`, `_set_apply_busy`, `_complete_apply`, `_finish_successful_apply`, `_poll_apply_status`, `_begin_apply`, `_dispatch_apply`

**Interfaces:**
- Consumes: `ApplyController` (Task 6).
- Produces: `QtEffectPalette.apply_controller: ApplyController`. `self._apply_busy`, `self._apply_finishing`, `self._apply_last_status`, `self._current_apply_effect` and `self._apply_command_timestamp` are removed; readers go through `apply_controller`.

- [ ] **Step 1: Construct the controller**

In `QtEffectPalette.__init__`, after `self.execution_adapter = create_execution_adapter()`, add:

```python
self.apply_controller = ApplyController(self.execution_adapter, self.root)
self.apply_controller.stateChanged.connect(self._on_apply_state_changed)
```

Extend the existing import:

```python
from palette_view_models import ApplyController, PaletteViewModel, ResultsModel
```

Delete `self._apply_busy`, `self._apply_finishing`, `self._apply_last_status`, `self._current_apply_effect` and `self._apply_command_timestamp` from `__init__`.

- [ ] **Step 2: Add the state projection**

Add to `QtEffectPalette`:

```python
    def _on_apply_state_changed(self):
        """Widgets follow the controller; the controller never touches widgets."""
        busy = self.apply_controller.state in {"busy", "success"}
        self.apply_progress.setVisible(self.apply_controller.busy)
        self.entry.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        for button in self.category_buttons.values():
            button.setEnabled(not busy)
```

- [ ] **Step 3: Repoint the apply methods at the controller**

`_set_apply_busy(busy, label)` keeps its signature (callers are unchanged) but delegates:

```python
    def _set_apply_busy(self, busy: bool, label: str = ""):
        if not busy:
            self.apply_controller.reset()
        if label:
            self.status_label.setText(label)
```

In `_complete_apply`, replace every `self._apply_busy = ...` / `self._apply_finishing = ...` assignment and the `self.execution_adapter.is_success(status)` branch with a single call, keeping all the existing `beta_report.write_event` and `record_successful_action` calls exactly where they are:

```python
    def _complete_apply(self, status: str):
        self._apply_poll_job = None
        elapsed_ms = None
        if self._apply_started_at is not None:
            elapsed_ms = round((time.perf_counter() - self._apply_started_at) * 1000.0, 2)
        effect = self.apply_controller.activeEffect
        effect_name = effect.get("name", "") if effect else ""
        self.apply_progress.hide()

        self.apply_controller.complete(status)

        if self.apply_controller.state == "success":
            self.status_label.setText(tr("status_applied", name=effect_name))
            record_successful_action(effect, confirmed_by=self.execution_adapter.backend_name)
            beta_report.write_event("apply_completed", {
                "name": effect_name, "status": status, "elapsed_ms": elapsed_ms,
            })
            self._apply_close_job = self.root.after(
                APPLY_SUCCESS_CLOSE_DELAY_MS, self._finish_successful_apply,
            )
            return

        self.status_label.setText(format_apply_failure(status))
        beta_report.write_event("apply_failed", {
            "name": effect_name, "status": status, "elapsed_ms": elapsed_ms,
        })
        self.entry.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
```

In `_poll_apply_status`, replace `if not self._apply_busy: return` with `if not self.apply_controller.busy: return`, replace `self._apply_command_timestamp` with `self.apply_controller.command_timestamp`, and replace `self._apply_last_status` with `self.apply_controller.lastStatus`.

In `_begin_apply` / `_dispatch_apply`, replace the assignments to `self._current_apply_effect` and `self._apply_command_timestamp` with one call at the point the command is sent:

```python
self.apply_controller.begin(effect, command_timestamp=command_timestamp)
```

using whatever local variable already holds the timestamp.

- [ ] **Step 4: Confirm no stale references remain**

Run: `grep -n "_apply_busy\|_apply_finishing\|_apply_last_status\|_current_apply_effect\|_apply_command_timestamp" companion/app.py`
Expected: matches only inside `_set_apply_busy`'s parameter name. Anything else must be repointed.

- [ ] **Step 5: Run the tests**

Run: `python companion/tests/run_tests.py`
Expected: PASS. `test_app.py`'s `test_apply_status_timeout_uses_adapter_table_plus_grace` and `test_format_apply_failure_maps_known_codes` must still pass.

- [ ] **Step 6: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 7: Smoke-test the real app**

Run: `cd companion && python app.py`

Without Premiere connected, apply an effect and confirm: the progress bar appears, the input and chips disable while busy, the failure message appears, and the controls re-enable afterwards. Expected: identical to before this task.

- [ ] **Step 8: Commit**

```bash
git add companion/app.py
git commit -m "refactor(palette): drive the apply lifecycle through ApplyController"
```

---

## Task 8: `window_control.py` — the native window layer behind an adapter

The highest-risk extraction, done against the current widget window so it is proven before QML exists.

**Files:**
- Create: `companion/window_control.py`
- Test: `companion/tests/test_window_control.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `window_control.WindowAdapter` — a `Protocol` with `show()`, `raise_window()`, `activate()`, `handle() -> int | None`, `move(x: int, y: int)`, `width() -> int`, `height_hint() -> int`, `focus_input()`, `has_input_focus() -> bool`
  - `window_control.WidgetWindowAdapter(window, focus_widget)` — implements `WindowAdapter` for a `QWidget` palette window
  - `window_control.PaletteWindowController(adapter, scheduler, native, on_focus_acquired=None)` where `native` has `foreground_handle() -> int | None` and `activate_handle(handle: int | None) -> None`
    - `remember_previous_focus() -> None`
    - `restore_previous_focus() -> None`
    - `begin_focus_attempts(max_attempts: int) -> None`
    - `cancel_focus_attempts() -> None`
    - `anchor_to_pointer(pointer, available, position_chooser) -> None`
    - `handle() -> int | None` — the window's native handle, forwarded from the adapter
    - property `is_open: bool` (settable)

- [ ] **Step 1: Write the failing test**

Create `companion/tests/test_window_control.py`:

```python
"""Headless tests for the native window layer, driven through a fake adapter.

These prove the retry and focus bookkeeping without a display. They prove nothing about
Premiere - that needs a real host test.
"""

from __future__ import annotations

import unittest

from window_control import PaletteWindowController


class FakeWindowAdapter:
    def __init__(self, *, focus_after: int = 0, handle: int = 4242):
        self.shown = 0
        self.raised = 0
        self.activated = 0
        self.focus_requests = 0
        self.moved_to = None
        self._handle = handle
        self._focus_after = focus_after

    def show(self):
        self.shown += 1

    def raise_window(self):
        self.raised += 1

    def activate(self):
        self.activated += 1

    def handle(self):
        return self._handle

    def move(self, x, y):
        self.moved_to = (x, y)

    def width(self):
        return 700

    def height_hint(self):
        return 320

    def focus_input(self):
        self.focus_requests += 1

    def has_input_focus(self):
        return self.focus_requests > self._focus_after


class FakeScheduler:
    def __init__(self):
        self.pending = []
        self.cancelled = []
        self.next_id = 1

    def after(self, delay_ms, callback):
        job_id = self.next_id
        self.next_id += 1
        self.pending.append((job_id, callback))
        return job_id

    def after_cancel(self, job_id):
        self.cancelled.append(job_id)
        self.pending = [(i, c) for i, c in self.pending if i != job_id]

    def drain(self, limit=10):
        for _ in range(limit):
            if not self.pending:
                return
            _job_id, callback = self.pending.pop(0)
            callback()


class FakeNative:
    def __init__(self, foreground=9999):
        self.foreground = foreground
        self.activated = []

    def foreground_handle(self):
        return self.foreground

    def activate_handle(self, handle):
        self.activated.append(handle)


class FocusAcquisitionTests(unittest.TestCase):
    def _controller(self, **kwargs):
        self.adapter = FakeWindowAdapter(**kwargs)
        self.scheduler = FakeScheduler()
        self.native = FakeNative()
        controller = PaletteWindowController(self.adapter, self.scheduler, self.native)
        controller.is_open = True
        return controller

    def test_first_attempt_that_wins_focus_schedules_no_retry(self):
        controller = self._controller(focus_after=0)
        controller.begin_focus_attempts(max_attempts=3)
        self.assertEqual(self.adapter.focus_requests, 1)
        self.assertEqual(self.scheduler.pending, [])

    def test_it_retries_until_focus_lands(self):
        controller = self._controller(focus_after=2)
        controller.begin_focus_attempts(max_attempts=3)
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 3)
        self.assertEqual(self.scheduler.pending, [])

    def test_it_gives_up_after_max_attempts(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=2)
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 3)  # initial + 2 retries
        self.assertEqual(self.scheduler.pending, [])

    def test_a_closed_palette_stops_retrying(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=3)
        controller.is_open = False
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 1)

    def test_each_attempt_shows_raises_and_activates(self):
        controller = self._controller(focus_after=0)
        controller.begin_focus_attempts(max_attempts=3)
        self.assertEqual(self.adapter.shown, 1)
        self.assertEqual(self.adapter.raised, 1)
        self.assertEqual(self.native.activated, [4242])

    def test_cancel_clears_the_pending_retry(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=3)
        controller.cancel_focus_attempts()
        self.assertEqual(self.scheduler.pending, [])

    def test_on_focus_acquired_fires_once_with_the_attempt_number(self):
        self.adapter = FakeWindowAdapter(focus_after=1)
        self.scheduler = FakeScheduler()
        self.native = FakeNative()
        seen = []
        controller = PaletteWindowController(
            self.adapter, self.scheduler, self.native, on_focus_acquired=seen.append,
        )
        controller.is_open = True
        controller.begin_focus_attempts(max_attempts=3)
        self.scheduler.drain()
        self.assertEqual(seen, [1])


class PreviousFocusTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeWindowAdapter()
        self.scheduler = FakeScheduler()
        self.native = FakeNative(foreground=9999)
        self.controller = PaletteWindowController(self.adapter, self.scheduler, self.native)

    def test_remember_then_restore_reactivates_the_previous_window(self):
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [9999])

    def test_it_never_remembers_its_own_window(self):
        self.native.foreground = self.adapter.handle()
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [])

    def test_restore_is_idempotent(self):
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [9999])


class AnchorTests(unittest.TestCase):
    def test_anchor_offsets_by_the_screen_origin(self):
        adapter = FakeWindowAdapter()
        controller = PaletteWindowController(adapter, FakeScheduler(), FakeNative())

        def chooser(**kwargs):
            return 100, 200

        controller.anchor_to_pointer(
            pointer=(500, 500),
            available=(1920, 0, 1920, 1080),  # x, y, width, height
            position_chooser=chooser,
        )
        self.assertEqual(adapter.moved_to, (2020, 200))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'window_control'`.

- [ ] **Step 3: Create `companion/window_control.py`**

```python
"""Native window behaviour for the palette, isolated behind an adapter.

Premiere does not hand focus over politely: opening the palette needs a show/raise/activate
plus a Win32 activation, retried a couple of times before the input field actually has the
caret. That retry logic is the single most fragile thing in the UI, so it lives here, gets
unit-tested against a fake, and talks to the window only through WindowAdapter -- which is
what lets a QQuickWindow replace a QWidget later without touching any of this.
"""

from __future__ import annotations

from typing import Callable, Protocol

# Delay before each retry, indexed by attempt number and clamped to the last entry.
RETRY_DELAYS_MS = (25, 75)


class WindowAdapter(Protocol):
    def show(self) -> None: ...
    def raise_window(self) -> None: ...
    def activate(self) -> None: ...
    def handle(self) -> int | None: ...
    def move(self, x: int, y: int) -> None: ...
    def width(self) -> int: ...
    def height_hint(self) -> int: ...
    def focus_input(self) -> None: ...
    def has_input_focus(self) -> bool: ...


class WidgetWindowAdapter:
    """WindowAdapter over the current QtWidgets palette."""

    def __init__(self, window, focus_widget):
        self._window = window
        self._focus_widget = focus_widget
        self._handle: int | None = None

    def show(self):
        self._window.show()

    def raise_window(self):
        self._window.raise_()

    def activate(self):
        self._window.activateWindow()

    def handle(self):
        if self._handle:
            return self._handle
        try:
            self._handle = int(self._window.winId())
        except Exception:
            return None
        return self._handle

    def move(self, x, y):
        self._window.move(x, y)

    def width(self):
        return self._window.width()

    def height_hint(self):
        return self._window.sizeHint().height()

    def focus_input(self):
        from PySide6 import QtCore

        self._focus_widget.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)

    def has_input_focus(self):
        return self._focus_widget.hasFocus()


class PaletteWindowController:
    def __init__(self, adapter: WindowAdapter, scheduler, native,
                 on_focus_acquired: Callable[[int], None] | None = None):
        self._adapter = adapter
        self._scheduler = scheduler
        self._native = native
        self._on_focus_acquired = on_focus_acquired
        self._focus_job = None
        self._previous_handle: int | None = None
        self._focus_reported = False
        self.is_open = False

    def handle(self) -> int | None:
        return self._adapter.handle()

    # --- previous-focus bookkeeping -------------------------------------------------

    def remember_previous_focus(self) -> None:
        previous = self._native.foreground_handle()
        if previous and previous != self._adapter.handle():
            self._previous_handle = previous

    def restore_previous_focus(self) -> None:
        previous = self._previous_handle
        self._previous_handle = None
        if previous and previous != self._adapter.handle():
            self._native.activate_handle(previous)

    # --- focus acquisition ----------------------------------------------------------

    def begin_focus_attempts(self, max_attempts: int) -> None:
        self._focus_reported = False
        self._attempt_focus(0, max_attempts)

    def cancel_focus_attempts(self) -> None:
        if self._focus_job is None:
            return
        try:
            self._scheduler.after_cancel(self._focus_job)
        except Exception:
            pass
        self._focus_job = None

    def _attempt_focus(self, attempt: int, max_attempts: int) -> None:
        self._focus_job = None
        if not self.is_open:
            return
        try:
            self._adapter.show()
            self._adapter.raise_window()
            self._adapter.activate()
            self._native.activate_handle(self._adapter.handle())
            self._adapter.focus_input()
        except Exception:
            pass

        if self._adapter.has_input_focus():
            self.cancel_focus_attempts()
            if not self._focus_reported:
                self._focus_reported = True
                if self._on_focus_acquired is not None:
                    self._on_focus_acquired(attempt)
            return

        if attempt < max_attempts:
            delay = RETRY_DELAYS_MS[min(attempt, len(RETRY_DELAYS_MS) - 1)]
            self._focus_job = self._scheduler.after(
                delay, lambda: self._attempt_focus(attempt + 1, max_attempts),
            )

    # --- placement ------------------------------------------------------------------

    def anchor_to_pointer(self, pointer, available, position_chooser) -> None:
        pointer_x, pointer_y = pointer
        available_x, available_y, available_w, available_h = available
        x, y = position_chooser(
            pointer_x=pointer_x,
            pointer_y=pointer_y,
            window_width=self._adapter.width(),
            window_height=self._adapter.height_hint(),
            screen_width=available_w,
            screen_height=available_h,
        )
        self._adapter.move(available_x + x, available_y + y)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all eleven tests across `FocusAcquisitionTests`, `PreviousFocusTests` and `AnchorTests`.

- [ ] **Step 5: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/window_control.py companion/tests/test_window_control.py
git commit -m "feat(window): extract the palette's native focus and placement logic"
```

---

## Task 9: Bind the palette to `PaletteWindowController`

**Files:**
- Modify: `companion/app.py` — `QtEffectPalette.__init__`, `_window_hwnd`, `_remember_previous_focus`, `_activate_window_native`, `_force_focus_attempt`, `_cancel_focus_attempts`, `_restore_previous_focus`, `_anchor_window_to_pointer`, `show`, `hide`

**Interfaces:**
- Consumes: `PaletteWindowController`, `WidgetWindowAdapter` (Task 8).
- Produces: `QtEffectPalette.window_controller: PaletteWindowController`. `self._native_hwnd`, `self._previous_foreground_hwnd`, `self._focus_attempt_job` and `self._focus_reported` are removed.

- [ ] **Step 1: Construct the controller**

At the end of `_build`, replacing the `self._native_hwnd = self._window_hwnd()` line:

```python
self.window_controller = PaletteWindowController(
    WidgetWindowAdapter(self.window, self.entry),
    self.root,
    _NativeWindowCalls(),
    on_focus_acquired=self._report_focus_acquired,
)
```

Add near the top of `app.py`, next to the other native helpers:

```python
class _NativeWindowCalls:
    """Adapts the module-level Win32 helpers to what PaletteWindowController expects."""

    def foreground_handle(self):
        return foreground_window_handle_native()

    def activate_handle(self, handle):
        activate_window_handle_native(handle)
```

Add the import:

```python
from window_control import PaletteWindowController, WidgetWindowAdapter
```

Delete `self._previous_foreground_hwnd`, `self._focus_attempt_job`, `self._native_hwnd` and `self._focus_reported` from `__init__`.

- [ ] **Step 2: Move the telemetry out of the retry loop**

The `beta_report` calls that lived inside `_force_focus_attempt` become the callback:

```python
    def _report_focus_acquired(self, attempt: int):
        elapsed_ms = None
        if self._open_requested_at is not None:
            elapsed_ms = round((time.perf_counter() - self._open_requested_at) * 1000.0, 2)
        beta_report.write_event("palette_focus_acquired", {
            "attempt": attempt, "elapsed_ms": elapsed_ms,
        })
        if self._open_requested_at is not None:
            beta_report.write_event("palette_open_latency", {
                "elapsed_ms": elapsed_ms, "focus_attempt": attempt,
            })
```

- [ ] **Step 3: Replace the extracted methods with delegations**

```python
    def _window_hwnd(self) -> int | None:
        return self.window_controller.handle()

    def _remember_previous_focus(self):
        self.window_controller.remember_previous_focus()

    def _restore_previous_focus(self):
        self.window_controller.restore_previous_focus()

    def _cancel_focus_attempts(self):
        self.window_controller.cancel_focus_attempts()

    def _force_focus_attempt(self, attempt: int = 0, max_attempts: int = OPEN_FOCUS_ATTEMPTS):
        self.window_controller.is_open = self.is_open
        self.window_controller.begin_focus_attempts(max_attempts)

    def _anchor_window_to_pointer(self):
        screen = self.app.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
        pointer = QtGui.QCursor.pos()
        self.window_controller.anchor_to_pointer(
            pointer=(pointer.x(), pointer.y()),
            available=(available.x(), available.y(), available.width(), available.height()),
            position_chooser=choose_window_position_near_pointer,
        )
```

Delete `_activate_window_native` and repoint its one caller (inside the old `_force_focus_attempt`, now gone).

In `show()` and `hide()`, set `self.window_controller.is_open = self.is_open` immediately after each assignment to `self.is_open`, so the retry loop sees the current state.

- [ ] **Step 4: Confirm no stale references remain**

Run: `grep -n "_native_hwnd\|_previous_foreground_hwnd\|_focus_attempt_job\|_focus_reported\|_activate_window_native" companion/app.py`
Expected: no matches.

- [ ] **Step 5: Run the tests**

Run: `python companion/tests/run_tests.py`
Expected: PASS.

- [ ] **Step 6: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 7: Smoke-test the real app**

Run: `cd companion && python app.py`

Check: Ctrl+Space opens the palette with the caret already in the search field; the palette appears near the pointer and stays on screen at the edges; Escape closes it and returns focus to whatever was focused before.

- [ ] **Step 8: Commit**

```bash
git add companion/app.py
git commit -m "refactor(palette): drive native focus and placement through the controller"
```

- [ ] **Step 9: Request the host test — do not skip this**

This task changed the code path that gets the palette focused over Premiere. The off-host suite cannot prove it. Stop and ask the user to verify, in a real Premiere session:

1. Premiere is foreground and playing a sequence; press the palette hotkey. The palette must appear **focused**, with the caret in the search field, on the first press.
2. Repeat ten times in a row — the retry loop is timing-dependent and an intermittent failure is the failure mode to watch for.
3. Apply an effect. After it completes, focus must return to Premiere.
4. With two monitors, open the palette with the pointer on the second monitor. It must appear on that monitor, fully on screen.

Report the Premiere version with the result. Only after the user confirms may this be described as working; record the evidence in `TECHNICAL_PLAN.md` per `CLAUDE.md`.

---

## Task 10: Connection state, status text and footer hint on the view-model

Completes the spec's `PaletteViewModel` property list. After this, the palette widgets read every
string and state they display from the view-model rather than computing it.

**Files:**
- Modify: `companion/palette_view_models.py`
- Modify: `companion/app.py` — `AppQueryServices`, `_render_view_model`, `_update_connection_indicator`, `_on_loader_snapshot_ready`, `_build`
- Test: `companion/tests/test_view_models.py`

**Interfaces:**
- Consumes: `PaletteViewModel` (Task 4), `AppQueryServices` (Task 5).
- Produces:
  - `QueryServices` gains `translate(key: str, **kwargs) -> str`
  - `PaletteViewModel` gains:
    - property `statusText: str` (signal `statusTextChanged`)
    - property `connectionState: str` (signal `connectionStateChanged`)
    - property `footerHint: str` (signal `footerHintChanged`)
    - `set_connection_state(state: str) -> None`

- [ ] **Step 1: Write the failing test**

Append to `companion/tests/test_view_models.py`. First add `translate` to the existing fake — inside `FakeQueryServices`, add:

```python
    def translate(self, key, **kwargs):
        if key == "status_results_count":
            return f"{kwargs['visible']}/{kwargs['total']}"
        if key == "status_no_results":
            return "no results"
        if key == "footer_hint":
            return "Enter to apply"
        return key
```

Then add the test case:

```python
class PaletteViewModelStatusTests(unittest.TestCase):
    def setUp(self):
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)

    def test_results_state_reports_the_visible_and_total_counts(self):
        self.vm.set_query("gaussian")
        self.assertEqual(self.vm.statusText, "2/2")

    def test_message_state_reports_no_results(self):
        self.vm.set_query("nothing matches this")
        self.assertEqual(self.vm.statusText, "no results")

    def test_idle_state_has_no_status_text(self):
        self.services.recent = ()
        self.vm.set_query("")
        self.assertEqual(self.vm.statusText, "")

    def test_footer_hint_comes_from_the_translator(self):
        self.assertEqual(self.vm.footerHint, "Enter to apply")

    def test_connection_state_defaults_to_offline(self):
        self.assertEqual(self.vm.connectionState, "offline")

    def test_set_connection_state_emits_once_per_change(self):
        seen = []
        self.vm.connectionStateChanged.connect(lambda: seen.append(self.vm.connectionState))
        self.vm.set_connection_state("connected")
        self.vm.set_connection_state("connected")
        self.vm.set_connection_state("offline")
        self.assertEqual(seen, ["connected", "offline"])

    def test_status_text_changed_fires_on_transition(self):
        seen = []
        self.vm.statusTextChanged.connect(lambda: seen.append(self.vm.statusText))
        self.vm.set_query("gaussian")
        self.vm.set_query("nothing matches this")
        self.assertEqual(seen, ["2/2", "no results"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python companion/tests/run_tests.py`
Expected: FAIL with `AttributeError: 'PaletteViewModel' object has no attribute 'statusText'`.

- [ ] **Step 3: Extend the protocol and the view-model**

In `companion/palette_view_models.py`, add to the `QueryServices` protocol:

```python
    def translate(self, key: str, **kwargs) -> str: ...
```

Add to `PaletteViewModel.__init__`, after `self._view_state = "idle"`:

```python
        self._status_text = ""
        self._connection_state = "offline"
```

Add the three signals next to the existing ones:

```python
    statusTextChanged = QtCore.Signal()
    connectionStateChanged = QtCore.Signal()
    footerHintChanged = QtCore.Signal()
```

Add the properties:

```python
    @QtCore.Property(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @QtCore.Property(str, notify=connectionStateChanged)
    def connectionState(self) -> str:
        return self._connection_state

    @QtCore.Property(str, notify=footerHintChanged)
    def footerHint(self) -> str:
        return self._services.translate("footer_hint")

    @QtCore.Slot(str)
    def set_connection_state(self, state: str) -> None:
        if state != self._connection_state:
            self._connection_state = state
            self.connectionStateChanged.emit()

    def _set_status_text(self, value: str) -> None:
        if value != self._status_text:
            self._status_text = value
            self.statusTextChanged.emit()
```

In `_recompute`, replace the three `_set_view_state` call sites so each also sets the status text:

```python
        if rows:
            self._set_selected_index(0)
            self._set_status_text(self._services.translate(
                "status_results_count",
                visible=self._result_set.visible_count,
                total=self._result_set.total_count,
            ))
            self._set_view_state("results")
        else:
            self._set_selected_index(-1)
            if not query:
                self._set_status_text("")
                self._set_view_state("idle")
            else:
                self._set_status_text(self._services.translate("status_no_results"))
                self._set_view_state("message")
```

- [ ] **Step 4: Implement `translate` on `AppQueryServices`**

In `companion/app.py`, add to `AppQueryServices`:

```python
    def translate(self, key: str, **kwargs) -> str:
        return tr(key, **kwargs)
```

- [ ] **Step 5: Bind the widgets to the new properties**

In `_render_view_model`, delete the three `self.status_label.setText(...)` calls and the `result_set` lookup, replacing them with a single line at the end of the method:

```python
        self.status_label.setText(self.view_model.statusText)
```

The method then reads:

```python
    def _render_view_model(self):
        state = self.view_model.viewState
        if state == "idle":
            self._cancel_render_chunk()
            self._row_widgets = []
            self.results_list.clear()
            self._set_idle_state()
        elif state == "message":
            self._set_message_state()
        else:
            self._populate_results()
            self._set_results_state()
        self.status_label.setText(self.view_model.statusText)
        self._resize_to_content()
```

Change `_update_connection_indicator` to read from the view-model:

```python
    def _update_connection_indicator(self):
        tokens = get_connection_state_tokens(self.view_model.connectionState)
        self.conn_dot.setStyleSheet(
            f"background: {tokens['fill']}; border: 1px solid {tokens['outline']}; border-radius: 5px;"
        )
```

In `_on_loader_snapshot_ready`, push the snapshot's state into the view-model before the existing refresh call:

```python
        self.view_model.set_connection_state(snapshot.connection_state)
```

In `_build`, set the initial state right after the view-model exists, so the first paint is correct:

```python
        self.view_model.set_connection_state(self.loader.snapshot.connection_state)
```

and change the footer label to `self.help_label = QtWidgets.QLabel(self.view_model.footerHint)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python companion/tests/run_tests.py`
Expected: PASS, including all seven `PaletteViewModelStatusTests`.

- [ ] **Step 7: Verify the whole suite**

Run: `npm run validate`
Expected: PASS.

- [ ] **Step 8: Smoke-test the real app**

Run: `cd companion && python app.py`

Check: the footer hint reads as before; the result count in the footer matches the list; searching for nonsense shows the "no results" message; the connection dot is the same color it was before this task (grey/offline with no plugin connected, and it changes when the plugin connects).

- [ ] **Step 9: Commit**

```bash
git add companion/palette_view_models.py companion/app.py companion/tests/test_view_models.py
git commit -m "refactor(palette): move status, connection and footer text onto the view-model"
```

---

## After this plan

Phase 1 of the spec is complete: all palette state and logic live in tested, headless objects, and the native window layer sits behind `WindowAdapter`. The follow-up plan (spec phases 2–4) can then be written against real interfaces:

- `Theme.qml` singleton with the design and motion tokens
- `Palette.qml` bound to `PaletteViewModel` / `ResultsModel` / `ApplyController`
- `QuickWindowAdapter` implementing `WindowAdapter` for `QQuickWindow` — the only new code the native layer needs
- The PyInstaller QML packaging changes, verified with a real packaged build
- The secondary windows, one at a time
- Deleting the widget scaffolding
