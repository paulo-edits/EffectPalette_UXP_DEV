"""
Premiere Pro FX.palette
Atalho: Ctrl+Espaco - abre a paleta de efeitos
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import winreg
except ImportError:
    winreg = None

import beta_report
from models import MatchInfo, PaletteLayoutMetrics, ResultRowModel, SearchResultSet
from palette_view_models import ApplyController, PaletteViewModel
from window_control import PaletteWindowController, WidgetWindowAdapter

try:
    from pynput import keyboard
    HAS_PYNPUT = True
except Exception:
    keyboard = None
    HAS_PYNPUT = False

try:
    from rapidfuzz import fuzz as _rf_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    _rf_fuzz = None
    HAS_RAPIDFUZZ = False

FUZZY_THRESHOLD = 65

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    HAS_WATCHDOG = True
except ImportError:
    FileSystemEventHandler = object
    Observer = None
    HAS_WATCHDOG = False

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False
    print("[Aviso] pygetwindow nao instalado - usando foco nativo do Windows quando disponivel")

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    Image = ImageDraw = None
    HAS_PIL = False

try:
    import pystray
    HAS_TRAY = HAS_PIL
except Exception:
    pystray = None
    HAS_TRAY = False

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    HAS_QT = True
except Exception:
    QtCore = None
    QtGui = None
    QtWidgets = None
    HAS_QT = False

try:
    from uxp_execution_adapter import (
        PremiereUxpExecutionAdapter,
        UXP_TRANSITIONS_FILE,
        UXP_FAVORITES_FILE,
        UXP_EFFECTS_FILE,
        UXP_PROJECT_ITEMS_FILE,
        UXP_PRESETS_FILE,
        RECONSTRUCT_EASING_DEFAULT,
        TRANSPORT_PORT,
        timeout_seconds_for_effect,
    )
    HAS_UXP_ADAPTER = HAS_QT
except Exception:
    PremiereUxpExecutionAdapter = None
    UXP_TRANSITIONS_FILE = Path(__file__).resolve().parent / "data" / "uxp_video_transitions.json"
    UXP_FAVORITES_FILE = Path(__file__).resolve().parent / "data" / "uxp_favorites.json"
    UXP_EFFECTS_FILE = Path(__file__).resolve().parent / "data" / "uxp_effects.json"
    RECONSTRUCT_EASING_DEFAULT = True
    TRANSPORT_PORT = 58756

    def timeout_seconds_for_effect(effect: dict) -> float:
        # Mirrors uxp_execution_adapter's table; only reached if that module failed to import,
        # in which case every action already fails closed through _UnavailableExecutionAdapter.
        return {"generic_item": 45.0, "preset": 30.0}.get((effect or {}).get("type"), 5.0)

    UXP_PROJECT_ITEMS_FILE = Path(__file__).resolve().parent / "data" / "uxp_project_items.json"
    UXP_PRESETS_FILE = Path(__file__).resolve().parent / "data" / "uxp_presets.json"
    HAS_UXP_ADAPTER = False


APPDATA = Path(os.environ.get("APPDATA", ""))

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_app_dir()
APP_DIR = BASE_DIR
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

# Every catalog the palette lists is written here by PremiereUxpExecutionAdapter from the plugin's
# own responses (uxp_*.json). The CEP worker's premiere_*.json exports are no longer read.
DATA_DIR = UXP_EFFECTS_FILE.parent
GOOGLE_SANS_FLEX_REGULAR = FONTS_DIR / "GoogleSansFlex-Regular.ttf"
GOOGLE_SANS_FLEX_MEDIUM = FONTS_DIR / "GoogleSansFlex-Medium.ttf"
# One mark for the taskbar, Alt-Tab, every window and the tray. The .ico is the installer's
# and the exe's; the .png is Qt's and pystray's.
APP_ICON_ICO = ASSETS_DIR / "fx_palette.ico"
APP_ICON_PNG = ASSETS_DIR / "fx_palette.png"

# ─── Language / i18n ──────────────────────────────────────────────────────────

SETTINGS_FILE = APPDATA / "Adobe" / "CEP" / "extensions" / "EffectPalette" / "settings.json"
SUPPORTED_LANGUAGES = ("en", "pt")
DEFAULT_NEST_BIN = "Nested Clips"
DEFAULT_APP_PREFERENCES = {"animations": True, "reconstructEasing": True}


def _load_settings_data() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings_data(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_safe(SETTINGS_FILE, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:
        pass


def _load_language() -> str:
    lang = _load_settings_data().get("language", "en")
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def _save_language(lang: str) -> None:
    data = _load_settings_data()
    data["language"] = lang
    _save_settings_data(data)


def load_app_preferences() -> dict:
    raw = _load_settings_data().get("app", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "animations": raw.get("animations", DEFAULT_APP_PREFERENCES["animations"]) is not False,
        "reconstructEasing": raw.get("reconstructEasing", DEFAULT_APP_PREFERENCES["reconstructEasing"]) is not False,
    }


def save_app_preferences(*, animations: bool, reconstruct_easing: bool) -> None:
    data = _load_settings_data()
    raw = data.get("app", {})
    if not isinstance(raw, dict):
        raw = {}
    raw["animations"] = bool(animations)
    raw["reconstructEasing"] = bool(reconstruct_easing)
    data["app"] = raw
    _save_settings_data(data)


def load_alias_entries() -> list[dict[str, str]]:
    raw_entries = _load_settings_data().get("aliases", [])
    if not isinstance(raw_entries, list):
        return []
    entries = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("alias", "")).strip()
        target = str(entry.get("target", "")).strip()
        if alias and target:
            entries.append({"alias": alias, "target": target})
    return entries


def validate_alias_entries(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    aliases: dict[str, tuple[int, str]] = {}
    for row, entry in enumerate(entries, 1):
        alias = str(entry.get("alias", "")).strip()
        target = str(entry.get("target", "")).strip()
        if not alias and not target:
            continue
        if not alias or not target:
            errors.append(f"Row {row}: alias and target are required")
            continue
        normalized = normalize_search_text(alias)
        if normalized in aliases:
            errors.append(f"Rows {aliases[normalized][0]} and {row}: duplicate alias")
            continue
        aliases[normalized] = (row, target)

    for normalized, (row, _target) in aliases.items():
        visited = {normalized}
        current = normalized
        while current in aliases:
            next_target = normalize_search_text(aliases[current][1])
            if next_target in visited:
                errors.append(f"Row {row}: alias cycle detected")
                break
            visited.add(next_target)
            current = next_target
    return errors


def save_alias_entries(entries: list[dict]) -> None:
    cleaned = [
        {"alias": str(entry.get("alias", "")).strip(), "target": str(entry.get("target", "")).strip()}
        for entry in entries
        if str(entry.get("alias", "")).strip() or str(entry.get("target", "")).strip()
    ]
    errors = validate_alias_entries(cleaned)
    if errors:
        raise ValueError("; ".join(errors))
    data = _load_settings_data()
    data["aliases"] = cleaned
    _save_settings_data(data)


def resolve_alias_query(query: str, entries: list[dict] | None = None) -> str:
    """Resolve an exact alias, including safe alias chains, to its canonical query."""
    aliases = {
        normalize_search_text(entry.get("alias", "")): str(entry.get("target", "")).strip()
        for entry in (entries if entries is not None else load_alias_entries())
        if normalize_search_text(entry.get("alias", "")) and str(entry.get("target", "")).strip()
    }
    resolved = query.strip()
    visited: set[str] = set()
    while True:
        normalized = normalize_search_text(resolved)
        if normalized in visited or normalized not in aliases:
            return resolved
        visited.add(normalized)
        resolved = aliases[normalized]


def action_from_effect(effect: dict) -> dict | None:
    """Convert a successful result payload into the stable product action schema."""
    effect_type = str(effect.get("type", ""))
    if effect_type == "label_color":
        return {"type": "label", "labelIndex": int(effect.get("labelIndex", 0))}
    if effect_type == "label_group_action":
        return {"type": "select_label_group"}
    if effect_type == "timeline_action" and effect.get("action") == "nest":
        action = {"type": "nest"}
        if str(effect.get("nestName", "")).strip():
            action["name"] = str(effect["nestName"]).strip()
        return action
    name = str(effect.get("name", "")).strip()
    if name and effect_type not in {"history_action", "project_item"}:
        return {"type": "apply_search", "query": name}
    return None


def load_recent_actions() -> list[dict]:
    raw = _load_settings_data().get("recentActions", [])
    if not isinstance(raw, list):
        return []
    return [dict(entry) for entry in raw if isinstance(entry, dict) and isinstance(entry.get("action"), dict)][:MAX_RECENT_ACTIONS]


def record_successful_action(effect: dict, *, confirmed_by: str) -> dict | None:
    action = action_from_effect(effect)
    if action is None:
        return None
    entry = {
        "name": str(effect.get("name") or action.get("query") or action.get("type")),
        "action": action,
        "timestamp": time.time(),
        "confirmedBy": confirmed_by,
    }
    action_key = json.dumps(action, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    recent = [
        previous for previous in load_recent_actions()
        if json.dumps(previous.get("action", {}), sort_keys=True, ensure_ascii=False, separators=(",", ":")) != action_key
    ]
    data = _load_settings_data()
    data["recentActions"] = [entry, *recent][:MAX_RECENT_ACTIONS]
    _save_settings_data(data)
    return entry


def last_successful_action() -> dict | None:
    recent = load_recent_actions()
    return dict(recent[0]["action"]) if recent else None


def build_recent_action_items(limit: int = 7) -> tuple[dict, ...]:
    recent = load_recent_actions()
    if not recent:
        return ()
    items = []
    for entry in recent[:limit]:
        items.append({
            "name": str(entry.get("name", "")),
            "category": "Recentes",
            "type": "history_action",
            "action": "execute_recent",
            "productAction": dict(entry["action"]),
        })
    return tuple(items)


def startup_registry_enabled() -> bool:
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _kind = winreg.QueryValueEx(key, "FX.palette")
        return bool(value)
    except OSError:
        return False


def set_startup_registry_enabled(enabled: bool) -> bool:
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                executable = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
                winreg.SetValueEx(key, "FX.palette", 0, winreg.REG_SZ, f'"{executable}"')
            else:
                try:
                    winreg.DeleteValue(key, "FX.palette")
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


CURRENT_LANGUAGE = _load_language()


def set_language(lang: str) -> None:
    global CURRENT_LANGUAGE
    if lang not in SUPPORTED_LANGUAGES or lang == CURRENT_LANGUAGE:
        return
    CURRENT_LANGUAGE = lang
    _save_language(lang)


STRINGS: dict[str, dict[str, str]] = {
    # Footer hint
    "footer_hint": {"en": "[↑↓] navigate  [↵] apply  [esc] close", "pt": "[↑↓] navegar  [↵] aplicar  [esc] fechar"},
    # Status / apply flow
    "status_requesting_refresh": {"en": "Requesting update from Premiere...", "pt": "Solicitando atualizacao ao Premiere..."},
    "status_no_response": {"en": "Premiere did not respond", "pt": "Premiere nao respondeu"},
    "status_applied": {"en": "[Applied] {name}", "pt": "[Aplicado] {name}"},
    "status_send_failed": {"en": "Failed to send command", "pt": "Falha ao enviar comando"},
    "status_creating_track": {"en": "Creating track...", "pt": "Criando track..."},
    "status_apply_cancelled": {"en": "Application cancelled", "pt": "Aplicacao cancelada"},
    "status_no_results": {"en": "0 results", "pt": "0 resultados"},
    "status_results_count": {"en": "{visible}/{total} results", "pt": "{visible}/{total} resultados"},
    "status_shortcut_unavailable": {"en": "Nest has no keyboard shortcut in Premiere", "pt": "Nest nao possui atalho configurado no Premiere"},
    "status_premiere_window_unavailable": {"en": "Premiere window unavailable", "pt": "Janela do Premiere indisponivel"},
    "status_label_shortcut_unavailable": {"en": "Label shortcut unavailable", "pt": "Atalho de label indisponivel"},
    "label_select_group": {"en": "Select current label group", "pt": "Selecionar grupo da label atual"},
    "label_select_group_desc": {"en": "Select every timeline clip with the same label as the selected clip", "pt": "Seleciona todos os clipes da timeline com a mesma label do clipe selecionado"},
    "no_results_helper": {"en": "No results", "pt": "Nenhum resultado"},
    "action_applying": {"en": "Applying", "pt": "Aplicando"},
    "action_applying_preset": {"en": "Applying preset", "pt": "Aplicando preset"},
    "action_applying_transition": {"en": "Applying transition", "pt": "Aplicando transicao"},
    "action_inserting": {"en": "Inserting", "pt": "Inserindo"},
    "action_executing": {"en": "Executing", "pt": "Executando"},
    # Transition placement dialog
    "transition_dialog_title": {"en": "Transition position", "pt": "Posicao da transicao"},
    "transition_dialog_question": {"en": "Where do you want to apply the transition?", "pt": "Onde voce quer aplicar a transicao?"},
    "transition_dialog_auto_desc": {
        "en": "Automatic uses Premiere's current behavior: between two clips when there's a cut, or at the end of the clip when that makes sense.",
        "pt": "Automatico usa o comportamento atual do Premiere: entre dois clips quando houver corte, ou no fim do clip quando fizer sentido.",
    },
    "transition_dialog_start": {"en": "Start", "pt": "Inicio"},
    "transition_dialog_end": {"en": "End", "pt": "Fim"},
    "transition_dialog_auto": {"en": "Automatic", "pt": "Automatico"},
    # System tray menu
    "tray_open_palette": {"en": "Open palette", "pt": "Abrir paleta"},
    "tray_toggle_palette": {"en": "Show/Hide palette", "pt": "Mostrar/Ocultar paleta"},
    "tray_debug_window": {"en": "Debug window", "pt": "Janela de debug"},
    "tray_edit_hotkeys": {"en": "Edit custom shortcuts", "pt": "Editar atalhos personalizados"},
    "tray_settings": {"en": "Settings", "pt": "Configurações"},
    "tray_hotkeys_restart": {"en": "Restart FX.palette after saving shortcut changes.", "pt": "Reinicie o FX.palette depois de salvar os atalhos."},
    "tray_generate_beta_report": {"en": "Generate beta report", "pt": "Gerar relatorio beta"},
    "tray_open_report_folder": {"en": "Open reports folder", "pt": "Abrir pasta de relatorios"},
    "tray_language": {"en": "Language", "pt": "Idioma"},
    "tray_language_en": {"en": "English", "pt": "Ingles"},
    # Inline Nest configuration
    "nest_dialog_title": {"en": "Create Nest", "pt": "Criar Nest"},
    "timeline_action_nest": {"en": "Nest clips", "pt": "Aninhar clipes"},
    "repeat_last_action": {"en": "Repeat last action", "pt": "Repetir ultima acao"},
    "nest_dialog_question": {"en": "Create a nested sequence", "pt": "Criar sequencia aninhada"},
    "nest_mode_label": {"en": "Method", "pt": "Metodo"},
    "nest_mode_auto": {"en": "Automatic", "pt": "Automatico"},
    "nest_mode_premiere": {"en": "Premiere", "pt": "Premiere"},
    "nest_mode_api": {"en": "Extension API", "pt": "API da extensao"},
    "nest_mode_auto_desc": {
        "en": "Chooses Premiere for simple selections and the API when audio should be collapsed to one track.",
        "pt": "Usa o Premiere em selecoes simples e a API quando o audio deve ser unido em uma faixa.",
    },
    "nest_mode_premiere_desc": {
        "en": "Uses Premiere's native Nest command and automatically confirms its naming window.",
        "pt": "Usa o comando Nest nativo e confirma automaticamente a janela de nome.",
    },
    "nest_mode_api_desc": {
        "en": "Creates a subsequence directly and collapses the result to one video and one audio track.",
        "pt": "Cria a subsequencia diretamente e une o resultado em uma faixa de video e uma de audio.",
    },
    "nest_name_label": {"en": "Sequence name (optional)", "pt": "Nome da sequencia (opcional)"},
    "nest_name_hint": {"en": "Empty generates FXN-001, FXN-002...", "pt": "Vazio gera FXN-001, FXN-002..."},
    "nest_cancel": {"en": "Cancel", "pt": "Cancelar"},
    "nest_confirm": {"en": "Create Nest", "pt": "Criar Nest"},
    "nest_footer_hint": {"en": "[enter] create  [esc] cancel", "pt": "[enter] criar  [esc] cancelar"},
    "tray_language_pt": {"en": "Portuguese", "pt": "Portugues"},
    "tray_quit": {"en": "Quit", "pt": "Sair"},
    "tray_language_restart_title": {"en": "FX.palette", "pt": "FX.palette"},
    "tray_language_restart_body": {
        "en": "Language changed. Restart FX.palette for it to take effect.",
        "pt": "Idioma alterado. Reinicie o FX.palette para aplicar.",
    },
    # Bridge failure labels
    "bridge_error_no_selection": {"en": "No selection available", "pt": "Nenhuma selecao disponivel"},
    "bridge_error_no_sequence": {"en": "No active sequence", "pt": "Nenhuma sequencia ativa"},
    "bridge_error_not_found": {"en": "Item not found", "pt": "Item nao encontrado"},
    "bridge_error_not_inserted": {"en": "Item not inserted", "pt": "Item nao inserido"},
    "bridge_error_template_missing": {"en": "Missing template", "pt": "Template ausente"},
    "bridge_error_create_failed": {"en": "Failed to create item", "pt": "Falha ao criar item"},
    "bridge_error_not_supported": {"en": "Unsupported item", "pt": "Item nao suportado"},
    "bridge_error_command_unavailable": {"en": "Premiere command unavailable", "pt": "Comando indisponivel no Premiere"},
    "bridge_error_api_unavailable": {"en": "Premiere subsequence API unavailable", "pt": "API de subsequencia indisponivel no Premiere"},
    "bridge_error_unsafe_overlap": {"en": "Unselected clips overlap the destination track", "pt": "Ha clipes nao selecionados na faixa de destino"},
    "bridge_error_generic": {"en": "Failed to apply", "pt": "Falha ao aplicar"},
}


def tr(key: str, **kwargs) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(CURRENT_LANGUAGE) or entry.get("en") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


CATEGORY_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "Todos": {"en": "All", "pt": "Todos"},
    "Video": {"en": "Video", "pt": "Video"},
    "Audio": {"en": "Audio", "pt": "Audio"},
    "Transicoes": {"en": "Transitions", "pt": "Transicoes"},
    "Presets": {"en": "Presets", "pt": "Presets"},
    "Projeto": {"en": "Project", "pt": "Projeto"},
    "Favoritos": {"en": "Favorites", "pt": "Favoritos"},
}


def tr_category(cat: str) -> str:
    entry = CATEGORY_DISPLAY_LABELS.get(cat)
    if not entry:
        return cat
    return entry.get(CURRENT_LANGUAGE) or entry.get("en") or cat

WATCH_INTERVAL = 3.0
ENABLE_DEBUG_HOTKEY = os.environ.get("EFFECT_PALETTE_ENABLE_DEBUG_HOTKEY", "").lower() in {"1", "true", "yes", "on"}
BETA_FEEDBACK_MIN_OPEN_SECONDS = int(os.environ.get(
    "EFFECT_PALETTE_BETA_FEEDBACK_MIN_OPEN_SECONDS",
    os.environ.get("EFFECT_PALETTE_BETA_FEEDBACK_SECONDS", "300"),
))
PREMIERE_MONITOR_INTERVAL_MS = 30000
PREMIERE_PROCESS_NAMES = (
    "Adobe Premiere Pro.exe",
)
PROCESSENTRY32W_MAX_PATH = 260
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
VK_D = 0x44
VK_Q = 0x51
RESULT_LIMIT = 150
RELOAD_COALESCE_MS = 80
WIDTH_MEASURE_SAMPLE = 12
RESULTS_EXPANDED_HEIGHT = 394
OPEN_ANIMATION_MS = 140
CLOSE_ANIMATION_MS = 110
QT_INITIAL_RENDER_ROWS = 16
QT_RENDER_CHUNK_ROWS = 48
FIXED_SEARCH_WINDOW_WIDTH = 760
POINTER_WINDOW_MARGIN = 12
POINTER_VERTICAL_GAP = 18
OPEN_FOCUS_ATTEMPTS = 2
APPLY_STATUS_INITIAL_DELAY_MS = 40
APPLY_STATUS_POLL_MS = 60
# The per-effect deadlines themselves live in uxp_execution_adapter.EFFECT_TIMEOUT_SECONDS, which
# is what actually enforces them: the adapter marks a request error_timeout, and _poll_apply_status
# acts on that terminal status. This UI-side deadline is only the backstop for an adapter that never
# transitions at all (the stubs below, whose poll_status always returns None), so it deliberately
# sits just *past* the adapter's own - a UI deadline at or below it would pre-empt the real status
# the adapter was about to produce, which is exactly the bug that made both per-effect values above
# unreachable for as long as this module kept its own parallel copy of them.
APPLY_STATUS_TIMEOUT_GRACE_MS = 2000
APPLY_SUCCESS_CLOSE_DELAY_MS = 300
MAX_RECENT_ACTIONS = 20


def apply_status_timeout_ms(effect: dict) -> float:
    return timeout_seconds_for_effect(effect) * 1000.0 + APPLY_STATUS_TIMEOUT_GRACE_MS
SEARCH_FONT_SIZE = 14

WATCHED_DATA_FILES = {
    UXP_EFFECTS_FILE.name,
    UXP_PRESETS_FILE.name,
    UXP_PROJECT_ITEMS_FILE.name,
    UXP_FAVORITES_FILE.name,
}

FALLBACK_EFFECTS = [
    {"name": "Lumetri Color", "category": "Color", "type": "video"},
    {"name": "Gaussian Blur", "category": "Blur", "type": "video"},
    {"name": "Warp Stabilizer", "category": "Distort", "type": "video"},
    {"name": "Ultra Key", "category": "Keying", "type": "video"},
    {"name": "Parametric EQ", "category": "Audio", "type": "audio"},
    {"name": "Multiband Compressor", "category": "Audio", "type": "audio"},
]

GENERIC_ITEMS = [
    {"name": "Adjustment Layer", "category": "Favoritos", "type": "generic_item", "genericKey": "adjustment_layer"},
    {"name": "Bars and Tone", "category": "Favoritos", "type": "generic_item", "genericKey": "bars_and_tone"},
    {"name": "Black Video", "category": "Favoritos", "type": "generic_item", "genericKey": "black_video"},
    {"name": "Color Matte", "category": "Favoritos", "type": "generic_item", "genericKey": "color_matte"},
    {"name": "Transparent Video", "category": "Favoritos", "type": "generic_item", "genericKey": "transparent_video"},
]

TIMELINE_ACTIONS = [
    {
        "name": tr("timeline_action_nest"),
        "searchText": "Nest clips Aninhar clipes",
        "category": "Timeline",
        "type": "timeline_action",
        "action": "nest",
    },
]

BG = "#0D0C14"
BG2 = "#111019"
BORDER = "#2A2A35"
TEXT = "#E8E8F0"
TEXT_MUTED = "#88889B"
ACCENT = "#7278F0"
GREEN = "#3DD68C"
ORANGE = "#F5A623"
OFFLINE = "#7E8698"
SURFACE_ALT = "#13122B"
ROW_BORDER = "#27254D"
ICON_BG = "#2A2742"
ICON_FG = "#BFC6F3"
TYPE_BG = "#1E1E35"
TYPE_FG = "#AAA8BE"
CHIP_BG = "#17172E"
CHIP_BORDER = "#2E2C40"
REFRESH_BUTTON_BG = "#171724"
REFRESH_BUTTON_BORDER = "#2B2A3C"
REFRESH_BUTTON_HOVER_BG = "#212033"
REFRESH_BUTTON_ACTIVE_BG = "#161523"

FILTER_PALETTE = {
    "Todos": "#AEB8C7",
    "Video": "#A9E5D1",
    "Audio": "#F5C3A9",
    "Presets": "#D8C2F3",
    "Projeto": "#B7D7F6",
    "Favoritos": "#F2DA8A",
}

ITEM_TYPE_FILTER_KEYS = {
    "video": "Video",
    "audio": "Audio",
    "preset": "Presets",
    "project_item": "Projeto",
    "generic_item": "Favoritos",
    "favorite_item": "Favoritos",
    "favorite": "Favoritos",
    "timeline_action": "Todos",
}

for _generic_item in GENERIC_ITEMS:
    _generic_item["category"] = "Favoritos"

IS_WINDOWS = os.name == "nt"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\FX.palette.Application"
ERROR_ALREADY_EXISTS = 183
_single_instance_mutex_handle = None

if IS_WINDOWS:
    try:
        USER32 = ctypes.windll.user32
    except Exception:
        USER32 = None
else:
    USER32 = None
SW_RESTORE = 9


def acquire_single_instance_lock() -> bool:
    """Keep only one FX.palette process alive per Windows user session."""
    global _single_instance_mutex_handle
    if not IS_WINDOWS:
        return True
    if _single_instance_mutex_handle:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _single_instance_mutex_handle = (kernel32, handle)
        return True
    except Exception as exc:
        beta_report.log_exception("Single-instance lock failed", exc)
        return True


def release_single_instance_lock() -> None:
    global _single_instance_mutex_handle
    lock = _single_instance_mutex_handle
    _single_instance_mutex_handle = None
    if not lock:
        return
    kernel32, handle = lock
    try:
        kernel32.CloseHandle(handle)
    except Exception as exc:
        beta_report.log_exception("Single-instance lock release failed", exc)


@dataclass(frozen=True)
class DataPaths:
    data_dir: Path = DATA_DIR
    uxp_transitions_file: Path = UXP_TRANSITIONS_FILE
    uxp_favorites_file: Path = UXP_FAVORITES_FILE
    uxp_effects_file: Path = UXP_EFFECTS_FILE
    uxp_project_items_file: Path = UXP_PROJECT_ITEMS_FILE
    uxp_presets_file: Path = UXP_PRESETS_FILE


@dataclass(frozen=True)
class HotkeySpec:
    id: int
    name: str
    modifiers: int
    vk: int
    requires_premiere_focus: bool
    callback_name: str
    action: dict | None = None


@dataclass(frozen=True)
class PremiereCommandShortcut:
    vk: int
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


HOTKEY_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
}

HOTKEY_PUNCTUATION_VKS = {
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
HOTKEY_VK_PUNCTUATION = {vk: key for key, vk in HOTKEY_PUNCTUATION_VKS.items()}


def parse_hotkey_shortcut(shortcut: str) -> tuple[int, int] | None:
    parts = [part.strip().lower() for part in str(shortcut or "").split("+") if part.strip()]
    if not parts:
        return None
    modifiers = MOD_NOREPEAT
    key_name = None
    for part in parts:
        if part in HOTKEY_MODIFIER_NAMES:
            modifiers |= HOTKEY_MODIFIER_NAMES[part]
        elif key_name is None:
            key_name = part
        else:
            return None
    if not key_name:
        return None
    if len(key_name) == 1 and key_name.isalnum():
        vk = ord(key_name.upper())
    elif key_name in HOTKEY_PUNCTUATION_VKS:
        vk = HOTKEY_PUNCTUATION_VKS[key_name]
    elif key_name == "space":
        vk = VK_SPACE
    elif re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key_name):
        vk = 0x70 + int(key_name[1:]) - 1
    else:
        return None
    return modifiers, vk


def hotkey_spec_to_pynput(spec: HotkeySpec) -> str:
    parts = []
    if spec.modifiers & MOD_CONTROL:
        parts.append("<ctrl>")
    if spec.modifiers & MOD_ALT:
        parts.append("<alt>")
    if spec.modifiers & MOD_SHIFT:
        parts.append("<shift>")
    if spec.vk == VK_SPACE:
        parts.append("<space>")
    elif 0x70 <= spec.vk <= 0x87:
        parts.append(f"<f{spec.vk - 0x70 + 1}>")
    elif spec.vk in HOTKEY_VK_PUNCTUATION:
        parts.append(HOTKEY_VK_PUNCTUATION[spec.vk])
    else:
        parts.append(chr(spec.vk).lower())
    return "+".join(parts)


def load_custom_hotkey_specs(start_id: int = 100) -> list[HotkeySpec]:
    raw_entries = _load_settings_data().get("hotkeys", [])
    if not isinstance(raw_entries, list):
        return []
    specs = []
    for offset, entry in enumerate(raw_entries):
        if not isinstance(entry, dict) or entry.get("enabled", True) is False:
            continue
        parsed = parse_hotkey_shortcut(entry.get("shortcut", ""))
        action = entry.get("action")
        if parsed is None or not isinstance(action, dict):
            continue
        modifiers, vk = parsed
        specs.append(HotkeySpec(
            id=start_id + offset,
            name=str(entry.get("name") or f"custom_{offset + 1}"),
            modifiers=modifiers,
            vk=vk,
            requires_premiere_focus=bool(entry.get("requiresPremiereFocus", True)),
            callback_name="custom_action",
            action=dict(action),
        ))
    return specs


def load_custom_hotkey_entries() -> list[dict]:
    """Return editable copies of the user-defined shortcut entries."""
    raw_entries = _load_settings_data().get("hotkeys", [])
    if not isinstance(raw_entries, list):
        return []
    return [dict(entry) for entry in raw_entries if isinstance(entry, dict)]


def save_custom_hotkey_entries(entries: list[dict]) -> None:
    data = _load_settings_data()
    data["hotkeys"] = entries
    _save_settings_data(data)


def _xml_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_premiere_command_shortcut(kys_file: Path, command_name: str) -> PremiereCommandShortcut | None:
    """Read one Premiere keyboard command from a .kys shortcut file."""
    try:
        root = ET.parse(kys_file).getroot()
    except (OSError, ET.ParseError):
        return None

    for item in root.iter():
        try:
            if item.findtext("commandname", "").strip() != command_name:
                continue
            raw_virtual_key = int(item.findtext("virtualkey", "0"))
        except (AttributeError, TypeError, ValueError):
            continue

        # Premiere stores keyboard virtual keys with a high-bit marker.
        vk = raw_virtual_key & 0xFFFF
        # Premiere stores printable letter shortcuts as lowercase character
        # codes, while Windows SendInput expects the uppercase virtual-key code.
        if ord("a") <= vk <= ord("z"):
            vk = ord(chr(vk).upper())
        if vk <= 0 or vk > 0xFF:
            continue
        return PremiereCommandShortcut(
            vk=vk,
            ctrl=_xml_bool(item.findtext("modifier.ctrl")),
            alt=_xml_bool(item.findtext("modifier.alt")),
            shift=_xml_bool(item.findtext("modifier.shift")),
        )
    return None


def find_premiere_command_shortcut(command_name: str) -> tuple[PremiereCommandShortcut | None, Path | None]:
    documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    shortcuts_root = documents / "Adobe" / "Premiere Pro"
    if not shortcuts_root.exists():
        return None, None

    try:
        candidates = sorted(
            shortcuts_root.glob("*/Profile-*/Win/*.kys"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        candidates = []

    for candidate in candidates:
        shortcut = parse_premiere_command_shortcut(candidate, command_name)
        if shortcut is not None:
            return shortcut, candidate
    return None, None


def load_premiere_shortcut_conflicts() -> dict[tuple[int, int], tuple[str, ...]]:
    """Index shortcuts from the most recently used Premiere .kys profile."""
    documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    shortcuts_root = documents / "Adobe" / "Premiere Pro"
    try:
        candidates = sorted(shortcuts_root.glob("*/Profile-*/Win/*.kys"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return {}
    for candidate in candidates:
        try:
            root = ET.parse(candidate).getroot()
        except (OSError, ET.ParseError):
            continue
        conflicts: dict[tuple[int, int], list[str]] = {}
        for item in root.iter():
            command_name = (item.findtext("commandname", "") or "").strip()
            try:
                vk = int(item.findtext("virtualkey", "0")) & 0xFFFF
            except (TypeError, ValueError):
                continue
            if ord("a") <= vk <= ord("z"):
                vk = ord(chr(vk).upper())
            if not command_name or not 0 < vk <= 0xFF:
                continue
            modifiers = MOD_NOREPEAT
            if _xml_bool(item.findtext("modifier.ctrl")):
                modifiers |= MOD_CONTROL
            if _xml_bool(item.findtext("modifier.alt")):
                modifiers |= MOD_ALT
            if _xml_bool(item.findtext("modifier.shift")):
                modifiers |= MOD_SHIFT
            conflicts.setdefault((modifiers, vk), []).append(command_name)
        if conflicts:
            return {key: tuple(dict.fromkeys(names)) for key, names in conflicts.items()}
    return {}


def send_native_shortcut(shortcut: PremiereCommandShortcut) -> bool:
    if not IS_WINDOWS or USER32 is None:
        return False

    key_event_up = 0x0002
    modifier_keys = []
    if shortcut.ctrl:
        modifier_keys.append(0x11)  # VK_CONTROL
    if shortcut.alt:
        modifier_keys.append(0x12)  # VK_MENU
    if shortcut.shift:
        modifier_keys.append(0x10)  # VK_SHIFT

    pressed = []
    try:
        for vk in modifier_keys:
            USER32.keybd_event(vk, 0, 0, 0)
            pressed.append(vk)
        USER32.keybd_event(shortcut.vk, 0, 0, 0)
        pressed.append(shortcut.vk)
        USER32.keybd_event(shortcut.vk, 0, key_event_up, 0)
        pressed.pop()
        for vk in reversed(modifier_keys):
            USER32.keybd_event(vk, 0, key_event_up, 0)
            if vk in pressed:
                pressed.remove(vk)
        return True
    except Exception:
        for vk in reversed(pressed):
            try:
                USER32.keybd_event(vk, 0, key_event_up, 0)
            except Exception:
                pass
        return False


def apply_windows_11_window_effects(hwnd: int | None) -> bool:
    """Ask DWM for native dark framing, rounded corners and compositor shadow."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

        def set_attribute(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data)
            )

        set_attribute(20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attribute(33, 2)  # DWMWA_WINDOW_CORNER_PREFERENCE / ROUND
        set_attribute(34, 0x00453B3B)  # DWMWA_BORDER_COLOR (COLORREF/BGR)

        class MARGINS(ctypes.Structure):
            _fields_ = (("left", ctypes.c_int), ("right", ctypes.c_int),
                        ("top", ctypes.c_int), ("bottom", ctypes.c_int))

        margins = MARGINS(1, 1, 1, 1)
        dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        return True
    except Exception:
        return False


def native_window_process_id(hwnd: int | None) -> int | None:
    if not IS_WINDOWS or USER32 is None or not hwnd:
        return None
    try:
        process_id = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(process_id))
        return int(process_id.value) if process_id.value else None
    except Exception:
        return None


def send_native_unicode_text(text: str) -> bool:
    if not text:
        return True
    if not IS_WINDOWS or USER32 is None:
        return False

    keyeventf_keyup = 0x0002
    keyeventf_unicode = 0x0004
    input_keyboard = 1

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    try:
        utf16 = text.encode("utf-16-le")
        for offset in range(0, len(utf16), 2):
            code_unit = int.from_bytes(utf16[offset:offset + 2], "little")
            events = (INPUT * 2)()
            events[0].type = input_keyboard
            events[0].ki = KEYBDINPUT(0, code_unit, keyeventf_unicode, 0, 0)
            events[1].type = input_keyboard
            events[1].ki = KEYBDINPUT(0, code_unit, keyeventf_unicode | keyeventf_keyup, 0, 0)
            if USER32.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
                return False
        return True
    except Exception:
        return False


def send_native_keyboard_text(text: str) -> bool:
    """Type text with the active Windows keyboard layout when Unicode input is rejected."""
    if not text:
        return True
    if not IS_WINDOWS or USER32 is None:
        return False
    key_event_up = 0x0002
    modifier_map = ((0x01, 0x10), (0x02, 0x11), (0x04, 0x12))
    try:
        for character in text:
            key_state = int(USER32.VkKeyScanW(character))
            if key_state == -1:
                return False
            vk = key_state & 0xFF
            modifiers = (key_state >> 8) & 0xFF
            pressed = []
            for flag, modifier_vk in modifier_map:
                if modifiers & flag:
                    USER32.keybd_event(modifier_vk, 0, 0, 0)
                    pressed.append(modifier_vk)
            USER32.keybd_event(vk, 0, 0, 0)
            USER32.keybd_event(vk, 0, key_event_up, 0)
            for modifier_vk in reversed(pressed):
                USER32.keybd_event(modifier_vk, 0, key_event_up, 0)
        return True
    except Exception:
        return False


def fill_and_confirm_native_nest_dialog(nest_name: str) -> bool:
    if nest_name:
        if not send_native_shortcut(PremiereCommandShortcut(vk=0x41, ctrl=True)):  # Ctrl+A
            return False
        if not send_native_unicode_text(nest_name):
            if not send_native_shortcut(PremiereCommandShortcut(vk=0x41, ctrl=True)):
                return False
            if not send_native_keyboard_text(nest_name):
                return False
    return send_native_shortcut(PremiereCommandShortcut(vk=0x0D))  # Enter


def schedule_native_nest_dialog_confirmation(palette, premiere_hwnd: int, nest_name: str, on_confirmed=None) -> None:
    premiere_process_id = native_window_process_id(premiere_hwnd)
    if premiere_process_id is None:
        beta_report.write_event("native_nest_dialog_autofill_unavailable", {"reason": "process_unknown"})
        return

    deadline = time.monotonic() + 3.0

    def poll():
        foreground_hwnd = foreground_window_handle_native()
        foreground_process_id = native_window_process_id(foreground_hwnd)
        if (
            foreground_hwnd
            and foreground_hwnd != premiere_hwnd
            and foreground_process_id == premiere_process_id
        ):
            confirmed = fill_and_confirm_native_nest_dialog(nest_name)
            beta_report.write_event("native_nest_dialog_autofill", {
                "confirmed": confirmed,
                "custom_name": bool(nest_name),
            })
            if confirmed and on_confirmed is not None:
                on_confirmed()
            return
        if time.monotonic() >= deadline:
            beta_report.write_event("native_nest_dialog_autofill_unavailable", {"reason": "dialog_timeout"})
            return
        palette.root.after(50, poll)

    palette.root.after(100, poll)


NATIVE_NEST_ORGANIZE_POLL_MS = 750
NATIVE_NEST_ORGANIZE_TIMEOUT_SECONDS = 20.0


def schedule_native_nest_organization(palette, baseline_guids: list[str], nest_name: str, bin_name: str) -> None:
    """After Premiere's own Nest dialog was confirmed, poll the plugin until the new sequence exists,
    then have it renamed/filed into the bin (timeline.organizeNativeNest). This is the UXP-side
    replacement for the CEP worker's watchNativeNest, which the old bridge-file watch armed."""
    adapter = getattr(palette, "execution_adapter", None)
    if adapter is None or not hasattr(adapter, "organize_native_nest"):
        beta_report.write_event("native_nest_organize_unavailable", {"reason": "no_adapter"})
        return
    deadline = time.monotonic() + NATIVE_NEST_ORGANIZE_TIMEOUT_SECONDS
    attempts = {"count": 0, "failures": 0}

    def poll():
        attempts["count"] += 1
        result = adapter.organize_native_nest(baseline_guids, nest_name, bin_name)
        if result is None:
            attempts["failures"] += 1
        elif result.get("found"):
            beta_report.write_event("native_nest_organized", {
                "attempts": attempts["count"],
                "sequence": result.get("sequence"),
                "renamed": result.get("renameTransactionSucceeded"),
                "bin": result.get("binPlacement"),
            })
            return
        if time.monotonic() >= deadline:
            beta_report.write_event("native_nest_organize_timeout", attempts)
            return
        palette.root.after(NATIVE_NEST_ORGANIZE_POLL_MS, poll)

    palette.root.after(NATIVE_NEST_ORGANIZE_POLL_MS, poll)


NATIVE_DISPATCH_MIN_DELAY_MS = 80
NATIVE_DISPATCH_FOCUS_TIMEOUT_MS = 1500


def dispatch_when_window_foreground(palette, hwnd: int, dispatch, *, action: str) -> None:
    """Send a synthesized keystroke only once `hwnd` (Premiere) is actually the foreground window.

    activate_window_handle_native() asks for focus; Windows grants it asynchronously, so a keystroke
    sent a fixed 80 ms later can land before Premiere is in front. The old bridge-file watch hid
    this: on a machine with the CEP worker still installed its acknowledgement took a few hundred
    ms, and native Nest never missed. The wait is bounded and logged so a miss is diagnosable.
    """
    started = time.monotonic()

    def poll():
        foreground = foreground_window_handle_native() == hwnd
        waited_ms = (time.monotonic() - started) * 1000.0
        if foreground or waited_ms >= NATIVE_DISPATCH_FOCUS_TIMEOUT_MS:
            beta_report.write_event("native_dispatch_focus", {
                "action": action, "foreground": foreground, "waited_ms": round(waited_ms, 1),
            })
            dispatch()
            return
        palette.root.after(30, poll)

    palette.root.after(NATIVE_DISPATCH_MIN_DELAY_MS, poll)


VK_TAB = 0x09


def fill_and_confirm_native_add_tracks_dialog(*, need_video: bool, need_audio: bool) -> bool:
    """Drive Premiere's own "Add Tracks..." dialog (opened separately via cmd.sequence.addtracks)
    to add only the track type(s) actually needed.

    Host-confirmed layout: it opens with the video Amount field focused and its default "1"
    pre-selected; two Tabs from there land on the audio Amount field, also pre-selected; Enter
    confirms from either field. Zeroing out the Amount for a track type that isn't needed is what
    keeps this from creating an extra, unused track every time.
    """
    if not need_video and not send_native_keyboard_text("0"):
        return False
    if not send_native_shortcut(PremiereCommandShortcut(vk=VK_TAB)):
        return False
    if not send_native_shortcut(PremiereCommandShortcut(vk=VK_TAB)):
        return False
    if not need_audio and not send_native_keyboard_text("0"):
        return False
    return send_native_shortcut(PremiereCommandShortcut(vk=0x0D))  # Enter


def schedule_native_add_tracks_dialog(palette, premiere_hwnd: int, *, need_video: bool, need_audio: bool, on_done) -> None:
    """Send cmd.sequence.addtracks, wait for its dialog to take the foreground, then fill and
    confirm it. Calls on_done(True) only once the dialog was actually driven; on_done(False) for
    every other case (shortcut not bound, send failed, dialog never appeared) so the caller can
    fall back to accepting whatever the original insertion already produced instead of hanging.
    """
    shortcut, _shortcut_file = find_premiere_command_shortcut("cmd.sequence.addtracks")
    if shortcut is None:
        beta_report.write_event("native_add_tracks_unavailable", {"reason": "shortcut_unbound"})
        on_done(False)
        return
    premiere_process_id = native_window_process_id(premiere_hwnd)
    if premiere_process_id is None:
        beta_report.write_event("native_add_tracks_unavailable", {"reason": "process_unknown"})
        on_done(False)
        return
    if not send_native_shortcut(shortcut):
        beta_report.write_event("native_add_tracks_unavailable", {"reason": "shortcut_send_failed"})
        on_done(False)
        return

    deadline = time.monotonic() + 3.0

    def poll():
        foreground_hwnd = foreground_window_handle_native()
        foreground_process_id = native_window_process_id(foreground_hwnd)
        if (
            foreground_hwnd
            and foreground_hwnd != premiere_hwnd
            and foreground_process_id == premiere_process_id
        ):
            confirmed = fill_and_confirm_native_add_tracks_dialog(need_video=need_video, need_audio=need_audio)
            beta_report.write_event("native_add_tracks_dialog_autofill", {
                "confirmed": confirmed,
                "need_video": need_video,
                "need_audio": need_audio,
            })
            if not confirmed:
                on_done(False)
                return
            # Sending Enter only means the keystroke was dispatched, not that Premiere has finished
            # acting on it - a real host test showed the very next insertion still landing on a
            # reused track because the new one hadn't actually been created yet by the time it ran.
            # Wait for the dialog to actually close (foreground back on Premiere's own main window)
            # before telling the caller it's safe to insert.
            settle_deadline = time.monotonic() + 2.0

            def wait_for_dialog_close():
                if foreground_window_handle_native() == premiere_hwnd:
                    on_done(True)
                    return
                if time.monotonic() >= settle_deadline:
                    beta_report.write_event("native_add_tracks_unavailable", {"reason": "dialog_close_timeout"})
                    on_done(True)
                    return
                palette.root.after(50, wait_for_dialog_close)

            palette.root.after(50, wait_for_dialog_close)
            return
        if time.monotonic() >= deadline:
            beta_report.write_event("native_add_tracks_unavailable", {"reason": "dialog_timeout"})
            on_done(False)
            return
        palette.root.after(50, poll)

    palette.root.after(100, poll)

@dataclass(frozen=True)
class IndexedItem:
    payload: dict
    normalized_name: str
    tokens: tuple[str, ...]
    item_type: str
    load_order: int


def choose_window_position_near_pointer(
    *,
    pointer_x: int,
    pointer_y: int,
    window_width: int,
    window_height: int,
    screen_width: int,
    screen_height: int,
    margin: int = POINTER_WINDOW_MARGIN,
    vertical_gap: int = POINTER_VERTICAL_GAP,
) -> tuple[int, int]:
    min_x = margin
    max_x = max(margin, screen_width - window_width - margin)
    x = max(min_x, min(pointer_x - (window_width // 2), max_x))

    preferred_y = pointer_y - window_height - vertical_gap
    below_y = pointer_y + vertical_gap
    min_y = margin
    max_y = max(margin, screen_height - window_height - margin)
    if preferred_y >= min_y:
        y = preferred_y
    else:
        y = min(below_y, max_y)
    y = max(min_y, min(y, max_y))
    return x, y


@dataclass(frozen=True)
class LoaderSnapshot:
    effects: tuple[dict, ...]
    presets: tuple[dict, ...]
    project_items: tuple[dict, ...]
    favorite_items: tuple[dict, ...]
    generic_items: tuple[dict, ...]
    all_items: tuple[dict, ...]
    indexed_items: tuple[IndexedItem, ...]
    exact_name_map: dict[str, tuple[int, ...]]
    prefix_map: dict[str, tuple[int, ...]]
    token_prefix_map: dict[str, tuple[int, ...]]
    trigram_map: dict[str, tuple[int, ...]]
    source: str
    mtimes: dict[str, float]
    connection_state: str
    load_issues: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.effects)

    @property
    def preset_count(self) -> int:
        return len(self.presets)

    @property
    def project_item_count(self) -> int:
        return len(self.project_items)

    @property
    def favorite_item_count(self) -> int:
        return len(self.favorite_items)

    @property
    def generic_item_count(self) -> int:
        return len(self.generic_items)


SLASH_COMMAND_CATEGORIES: dict[str, str | None] = {
    "video": "Video", "vid": "Video", "v": "Video",
    "audio": "Audio", "aud": "Audio", "a": "Audio",
    "trans": "Transicoes", "transition": "Transicoes", "transitions": "Transicoes",
    "transicao": "Transicoes", "transicoes": "Transicoes", "t": "Transicoes",
    "pset": "Presets", "preset": "Presets", "presets": "Presets", "p": "Presets",
    "proj": "Projeto", "project": "Projeto", "projeto": "Projeto",
    "fav": "Favoritos", "favorite": "Favoritos", "favorites": "Favoritos",
    "favorito": "Favoritos", "favoritos": "Favoritos", "f": "Favoritos",
    "all": None, "todos": None, "clear": None,
}


def parse_slash_command(query: str) -> tuple[str, str | None, bool]:
    """Detects a leading "/command" token in the raw search text.

    Returns (effective_query, category, matched). `matched` is False when no
    recognized command is present, in which case `category` should be
    ignored entirely (the caller must leave the active category filter as-is,
    so plain typing never overrides a pill click or an earlier command).
    """
    stripped = query.lstrip()
    if not stripped.startswith("/"):
        return query, None, False

    parts = stripped[1:].split(None, 1)
    if not parts:
        return query, None, False

    word = parts[0].lower()
    if word not in SLASH_COMMAND_CATEGORIES:
        return query, None, False

    remainder = parts[1] if len(parts) > 1 else ""
    return remainder, SLASH_COMMAND_CATEGORIES[word], True


LABEL_COMMAND_WORDS = {"l", "label", "labels", "lbl", "etiqueta", "etiquetas", "rotulo", "rotulos"}
# Label names and colors are user-configurable. They are loaded from the same
# Premiere profile that owns the active keyboard-shortcut preset.
LABEL_COLOR_COUNT = 16
DEFAULT_LABEL_NAMES = (
    "Violet", "Iris", "Caribbean", "Lavender", "Cerulean", "Forest", "Rose", "Mango",
    "Purple", "Blue", "Teal", "Magenta", "Tan", "Green", "Brown", "Yellow",
)
_label_preferences_cache: tuple[Path | None, int | None, tuple[dict, ...]] | None = None


def premiere_label_color_to_hex(raw_value) -> str:
    try:
        packed = int(str(raw_value).strip()) & 0xFFFFFF
    except (TypeError, ValueError):
        return "#808080"
    red = packed & 0xFF
    green = (packed >> 8) & 0xFF
    blue = (packed >> 16) & 0xFF
    return f"#{red:02X}{green:02X}{blue:02X}"


def load_premiere_label_preferences() -> tuple[dict, ...]:
    global _label_preferences_cache
    if _label_preferences_cache:
        prefs_file = _label_preferences_cache[0]
    else:
        _shortcut, shortcut_file = find_premiere_command_shortcut("cmd.edit.label.0")
        prefs_file = shortcut_file.parent.parent / "Adobe Premiere Pro Prefs" if shortcut_file else None
    try:
        mtime_ns = prefs_file.stat().st_mtime_ns if prefs_file and prefs_file.exists() else None
    except OSError:
        mtime_ns = None
    if _label_preferences_cache and _label_preferences_cache[:2] == (prefs_file, mtime_ns):
        return _label_preferences_cache[2]

    names = list(DEFAULT_LABEL_NAMES)
    colors = ["#808080"] * LABEL_COLOR_COUNT
    if prefs_file and mtime_ns is not None:
        try:
            root = ET.parse(prefs_file).getroot()
            values = {element.tag: (element.text or "") for element in root.iter()}
            for index in range(LABEL_COLOR_COUNT):
                names[index] = values.get(f"BE.Prefs.LabelNames.{index}", names[index]) or names[index]
                colors[index] = premiere_label_color_to_hex(values.get(f"BE.Prefs.LabelColors.{index}"))
        except (OSError, ET.ParseError):
            pass

    items = tuple({
        "name": names[index], "category": "Label", "type": "label_color",
        "labelIndex": index, "labelColor": colors[index],
    } for index in range(LABEL_COLOR_COUNT))
    _label_preferences_cache = (prefs_file, mtime_ns, items)
    return items


def parse_label_command(query: str) -> str | None:
    """Return the optional color filter after a label command or alias."""
    stripped = query.strip()
    if not stripped:
        return None
    command_text = stripped[1:] if stripped.startswith("/") else stripped
    parts = command_text.split(None, 1)
    command_word = normalize_search_text(parts[0]) if parts else ""
    if command_word not in LABEL_COMMAND_WORDS:
        return None
    return parts[1] if len(parts) > 1 else ""


def build_label_color_items(filter_text: str) -> list[dict]:
    items = [{
        "name": tr("label_select_group"),
        "searchText": "Select current label group Selecionar grupo da label atual",
        "category": "Label",
        "type": "label_group_action",
        "action": "select_label_group",
    }]
    items.extend(dict(item) for item in load_premiere_label_preferences())
    if filter_text:
        normalized = normalize_search_text(filter_text)
        items = [item for item in items if normalized in normalize_search_text(item.get("searchText") or item["name"])]
    return items


def resolve_action_query_items(loader, query: str, *, limit: int = RESULT_LIMIT, alias_entries: list[dict] | None = None) -> tuple[dict, ...]:
    """Resolve aliases and product commands to the same items used by search actions."""
    resolved = resolve_alias_query(query, alias_entries)
    label_filter = parse_label_command(resolved)
    if label_filter is not None:
        return tuple(build_label_color_items(label_filter)[:limit])
    search_query, _category, _matched = parse_slash_command(resolved)
    return tuple(loader.search(search_query, limit=limit).items)


def build_alias_target_catalog(loader) -> list[dict[str, str]]:
    """Build stable, user-selectable alias targets from the product action catalog."""
    targets = [
        {"name": "Nest", "category": "FX.palette", "query": "Nest", "kind": "actions"},
        {"name": tr("label_select_group"), "category": "Labels", "query": "L", "kind": "labels"},
    ]
    targets.extend({
        "name": str(label["name"]), "category": "Labels", "query": f'L {label["name"]}', "kind": "labels",
    } for label in load_premiere_label_preferences())
    kind_by_type = {
        "video": "video_effects",
        "audio": "audio_effects",
        "transition_video": "transitions",
        "transition_audio": "transitions",
        "preset": "presets",
        "favorite_item": "favorites",
        "generic_item": "generic_items",
    }
    for item in loader.snapshot.all_items:
        name = str(item.get("name", "")).strip()
        item_type = str(item.get("type", ""))
        if not name or item_type in {"project_item", "label_color", "label_group_action", "timeline_action"}:
            continue
        targets.append({
            "name": name,
            "category": str(item.get("category") or item_type or "FX.palette"),
            "query": name,
            "kind": kind_by_type.get(item_type, "actions"),
        })
    unique: dict[str, dict[str, str]] = {}
    for target in targets:
        unique.setdefault(normalize_search_text(target["query"]), target)
    return sorted(unique.values(), key=lambda item: (normalize_search_text(item["category"]), normalize_search_text(item["name"])))


def normalize_search_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_value.lower()).strip()


def tokenize_search_text(value: str) -> tuple[str, ...]:
    normalized = normalize_search_text(value)
    if not normalized:
        return ()
    return tuple(token for token in re.split(r"[^a-z0-9]+", normalized) if token)


def iter_prefixes(value: str, max_len: int = 4):
    normalized = normalize_search_text(value)
    if not normalized:
        return
    for length in range(1, min(len(normalized), max_len) + 1):
        yield normalized[:length]


def make_trigrams(value: str) -> tuple[str, ...]:
    normalized = normalize_search_text(value)
    if len(normalized) < 3:
        return ()
    return tuple(normalized[idx:idx + 3] for idx in range(len(normalized) - 2))


def freeze_id_lists(mapping: dict[str, list[int]]) -> dict[str, tuple[int, ...]]:
    return {key: tuple(values) for key, values in mapping.items()}


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[idx:idx + 2], 16) for idx in (0, 2, 4))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def blend_colors(start: str, end: str, progress: float) -> str:
    start_rgb = hex_to_rgb(start)
    end_rgb = hex_to_rgb(end)
    rgb = tuple(int(start_rgb[idx] + ((end_rgb[idx] - start_rgb[idx]) * progress)) for idx in range(3))
    return rgb_to_hex(rgb)


def filter_key_for_item_type(item_type: str) -> str:
    if item_type in FILTER_PALETTE:
        return item_type
    return ITEM_TYPE_FILTER_KEYS.get(item_type, "Todos")


def get_filter_palette_color(filter_key: str) -> str:
    return FILTER_PALETTE.get(filter_key, FILTER_PALETTE["Todos"])


def get_pill_visual_tokens(filter_key: str, *, active: bool) -> dict[str, str]:
    pastel = get_filter_palette_color(filter_key)
    if active:
        return {
            "bg": pastel,
            "fg": BG,
            "border": blend_colors(pastel, "#FFFFFF", 0.18),
        }
    return {
        "bg": CHIP_BG,
        "fg": TEXT_MUTED,
        "border": blend_colors(CHIP_BORDER, pastel, 0.26),
    }


def get_row_visual_tokens(accent_kind: str, *, selected: bool, hovered: bool, accent_color: str | None = None) -> dict[str, str]:
    filter_key = filter_key_for_item_type(accent_kind)
    pastel = accent_color or get_filter_palette_color(filter_key)
    if selected:
        bg = blend_colors(SURFACE_ALT, pastel, 0.22)
        border = blend_colors(ROW_BORDER, pastel, 0.75)
        badge_bg = blend_colors(TYPE_BG, pastel, 0.42)
        icon_fg = blend_colors(ICON_FG, pastel, 0.50)
        icon_bg = blend_colors(ICON_BG, pastel, 0.35)
        subtitle_fg = blend_colors(TEXT_MUTED, "#FFFFFF", 0.42)
    elif hovered:
        bg = blend_colors(SURFACE_ALT, pastel, 0.16)
        border = blend_colors(ROW_BORDER, pastel, 0.54)
        badge_bg = blend_colors(TYPE_BG, pastel, 0.32)
        icon_fg = blend_colors(ICON_FG, pastel, 0.34)
        icon_bg = blend_colors(ICON_BG, pastel, 0.25)
        subtitle_fg = blend_colors(TEXT_MUTED, "#FFFFFF", 0.28)
    else:
        bg = blend_colors(SURFACE_ALT, pastel, 0.10)
        border = blend_colors(ROW_BORDER, pastel, 0.34)
        badge_bg = blend_colors(TYPE_BG, pastel, 0.24)
        icon_fg = blend_colors(ICON_FG, pastel, 0.20)
        icon_bg = blend_colors(ICON_BG, pastel, 0.15)
        subtitle_fg = TEXT_MUTED
    return {
        "bg": bg,
        "accent": pastel,
        "icon_fg": icon_fg,
        "icon_bg": icon_bg,
        "border": border,
        "subtitle_fg": subtitle_fg,
        "type_bg": badge_bg,
        "type_fg": blend_colors(TYPE_FG, pastel, 0.65),
        "title_fg": TEXT,
    }


def derive_connection_state(*, source: str, load_issues: tuple[str, ...]) -> str:
    if source == "fallback":
        if load_issues:
            return "problem"
        return "offline"
    if load_issues:
        return "problem"
    return "connected"


def get_connection_state_tokens(state: str) -> dict[str, str]:
    if state == "connected":
        color = GREEN
    elif state == "problem":
        color = ORANGE
    else:
        color = OFFLINE
    return {
        "fill": color,
        "outline": blend_colors(BORDER, color, 0.45),
    }


def get_icon_glyph(icon_kind: str, *, ascii_only: bool = False) -> str:
    glyphs = {
        "effect": "✦",
        "preset": "✎",
        "project": "▣",
        "favorite": "★",
    }
    fallback = {
        "effect": "FX",
        "preset": "PR",
        "project": "PJ",
        "favorite": "*",
        "action": "N",
    }
    if ascii_only:
        return fallback.get(icon_kind, "•")
    return glyphs.get(icon_kind, fallback.get(icon_kind, "•"))


def get_reload_icon_glyph() -> str:
    return "\u21bb"


def ease_out_expo(progress: float) -> float:
    if progress <= 0:
        return 0.0
    if progress >= 1:
        return 1.0
    return 1 - pow(2, -10 * progress)


def ease_in_expo(progress: float) -> float:
    if progress <= 0:
        return 0.0
    if progress >= 1:
        return 1.0
    return pow(2, 10 * progress - 10)


class EffectsLoader:
    def __init__(self, paths: DataPaths | None = None):
        self.paths = paths or DataPaths()
        self._lock = threading.Lock()
        self._reload_lock = threading.Lock()
        self._reload_inflight = False
        self._reload_pending = False
        self._reload_force = False
        self._snapshot = self._build_snapshot(force_reload=True)

    @property
    def snapshot(self) -> LoaderSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def source(self) -> str:
        return self.snapshot.source

    @property
    def count(self) -> int:
        return self.snapshot.count

    @property
    def preset_count(self) -> int:
        return self.snapshot.preset_count

    @property
    def project_item_count(self) -> int:
        return self.snapshot.project_item_count

    @property
    def favorite_item_count(self) -> int:
        return self.snapshot.favorite_item_count

    @property
    def generic_item_count(self) -> int:
        return self.snapshot.generic_item_count

    def current_mtimes(self) -> dict[str, float]:
        return {
            "uxp_transitions": self._safe_mtime(self.paths.uxp_transitions_file),
            "uxp_favorites": self._safe_mtime(self.paths.uxp_favorites_file),
            "uxp_effects": self._safe_mtime(self.paths.uxp_effects_file),
            "uxp_project_items": self._safe_mtime(self.paths.uxp_project_items_file),
            "uxp_presets": self._safe_mtime(self.paths.uxp_presets_file),
        }

    def needs_reload(self) -> bool:
        return self.current_mtimes() != self.snapshot.mtimes

    def request_refresh(self, root, on_ready, *, force: bool = False):
        with self._reload_lock:
            if self._reload_inflight:
                self._reload_pending = True
                self._reload_force = self._reload_force or force
                return
            if not force and not self.needs_reload():
                return
            self._reload_inflight = True
            self._reload_force = False

        def worker(force_reload: bool):
            snapshot = self._build_snapshot(force_reload=force_reload)

            def publish():
                try:
                    if self._publish_snapshot(snapshot):
                        on_ready(snapshot)
                finally:
                    self._finish_refresh(root, on_ready)

            root.after(0, publish)

        threading.Thread(target=worker, args=(force,), daemon=True).start()

    def _finish_refresh(self, root, on_ready):
        with self._reload_lock:
            pending = self._reload_pending
            force = self._reload_force
            self._reload_inflight = False
            self._reload_pending = False
            self._reload_force = False

        if pending:
            self.request_refresh(root, on_ready, force=force)

    def _publish_snapshot(self, snapshot: LoaderSnapshot) -> bool:
        with self._lock:
            if (
                snapshot.mtimes == self._snapshot.mtimes
                and snapshot.all_items == self._snapshot.all_items
                and snapshot.source == self._snapshot.source
                and snapshot.connection_state == self._snapshot.connection_state
                and snapshot.load_issues == self._snapshot.load_issues
            ):
                return False
            self._snapshot = snapshot
            return True

    def _build_snapshot(self, *, force_reload: bool = False) -> LoaderSnapshot:
        previous = self.snapshot if hasattr(self, "_snapshot") else None
        mtimes = self.current_mtimes()
        if not force_reload and previous and mtimes == previous.mtimes:
            return previous

        effects, source, effect_issues = self._load_effects()
        load_issues = list(effect_issues)
        if source == "fallback":
            presets = ()
            project_items = ()
            favorite_items = ()
            mtimes = {key: 0.0 for key in mtimes}
        else:
            presets, preset_issues = self._load_presets()
            project_items, project_item_issues = self._load_project_items()
            favorite_items, favorite_issues = self._load_favorites()
            load_issues.extend(preset_issues)
            load_issues.extend(project_item_issues)
            load_issues.extend(favorite_issues)

        generic_items = tuple(dict(item) for item in GENERIC_ITEMS)
        timeline_actions = tuple(dict(item) for item in TIMELINE_ACTIONS)
        all_items = effects + presets + project_items + favorite_items + generic_items + timeline_actions
        indexed_items, exact_name_map, prefix_map, token_prefix_map, trigram_map = self._build_indexes(all_items)

        return LoaderSnapshot(
            effects=effects,
            presets=presets,
            project_items=project_items,
            favorite_items=favorite_items,
            generic_items=generic_items,
            all_items=all_items,
            indexed_items=indexed_items,
            exact_name_map=exact_name_map,
            prefix_map=prefix_map,
            token_prefix_map=token_prefix_map,
            trigram_map=trigram_map,
            source=source,
            mtimes=mtimes,
            connection_state=derive_connection_state(source=source, load_issues=tuple(load_issues)),
            load_issues=tuple(load_issues),
        )

    def _load_effects(self) -> tuple[tuple[dict, ...], str, tuple[str, ...]]:
        """Video/audio effects plus video transitions, straight from the plugin's own catalogs.

        Both files are written by PremiereUxpExecutionAdapter on every plugin connection. Until the
        plugin has connected at least once on this machine, neither exists and the palette shows the
        small built-in fallback list - previously this also required the *CEP* worker's
        premiere_effects.json to exist, so a fresh install without the old CEP product never left
        fallback mode no matter what the UXP plugin reported.
        """
        issues: list[str] = []
        effects: list[dict] = []
        if self.paths.uxp_effects_file.exists():
            try:
                with open(self.paths.uxp_effects_file, encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                effects = [
                    {"name": item["name"], "category": item.get("category", ""), "type": item.get("type", "video")}
                    for item in data.get("effects", [])
                    if item.get("name") and item.get("type") in {"video", "audio"}
                ]
            except Exception as exc:
                print(f"[Efeitos] Erro ao ler catalogo UXP: {exc}")
                issues.append(f"effects:{exc}")

        transitions: list[dict] = []
        if self.paths.uxp_transitions_file.exists():
            try:
                with open(self.paths.uxp_transitions_file, encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                # Each entry carries its exact matchName, so picking it can only apply that one
                # transition - there is no display-name guessing anywhere on this path.
                transitions = [
                    {
                        "name": item["name"],
                        "category": item.get("category", "Transicoes > Video"),
                        "type": "transition_video",
                        "matchName": item["matchName"],
                    }
                    for item in data.get("transitions", [])
                    if item.get("name") and item.get("matchName")
                ]
            except Exception as exc:
                print(f"[Transicoes] Erro ao ler catalogo UXP: {exc}")
                issues.append(f"transitions:{exc}")

        if not effects and not transitions:
            fallback = tuple(dict(item, type=item.get("type", "video")) for item in FALLBACK_EFFECTS)
            return fallback, "fallback", tuple(issues)
        print(f"[Efeitos] {len(effects)} efeitos e {len(transitions)} transicoes do catalogo UXP")
        return tuple(effects + transitions), "uxp", tuple(issues)

    def _load_presets(self) -> tuple[tuple[dict, ...], tuple[str, ...]]:
        source_file = self.paths.uxp_presets_file
        if not source_file.exists():
            return (), ()
        try:
            with open(source_file, encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            presets = []
            for preset in data.get("presets", []):
                presets.append({
                    "name": preset["name"],
                    "category": preset.get("category", "Presets"),
                    "type": "preset",
                    "filterPresets": preset.get("filterPresets", []),
                })
            print(f"[Presets] {len(presets)} carregados")
            return tuple(presets), ()
        except Exception as exc:
            print(f"[Presets] Erro ao ler: {exc}")
            return (), (f"presets:{exc}",)

    def _load_project_items(self) -> tuple[tuple[dict, ...], tuple[str, ...]]:
        source_file = self.paths.uxp_project_items_file
        if not source_file.exists():
            return (), ()
        try:
            with open(source_file, encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            items = []
            for item in data.get("items", []):
                items.append({
                    "name": item["name"],
                    "category": item.get("category", "Projeto"),
                    "type": "project_item",
                    "nodeId": item.get("nodeId", ""),
                    "itemType": item.get("itemType", ""),
                    "isSequence": item.get("isSequence", False),
                    "treePath": item.get("treePath", ""),
                    "mediaPath": item.get("mediaPath", ""),
                })
            print(f"[Projeto] {len(items)} itens carregados")
            return tuple(items), ()
        except Exception as exc:
            print(f"[Projeto] Erro ao ler: {exc}")
            return (), (f"project_items:{exc}",)

    def _load_favorites(self) -> tuple[tuple[dict, ...], tuple[str, ...]]:
        # Written by PremiereUxpExecutionAdapter whenever the bundled template project is open.
        source_file = self.paths.uxp_favorites_file
        if not source_file.exists():
            return (), ()
        try:
            with open(source_file, encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            items = []
            for item in data.get("items", []):
                items.append({
                    "name": item["name"],
                    "category": item.get("category", "Favoritos"),
                    "type": "favorite_item",
                    "favoriteType": item.get("favoriteType", ""),
                    "sourceProjectPath": item.get("sourceProjectPath", ""),
                    "sourceTreePath": item.get("sourceTreePath", ""),
                    "mediaPath": item.get("mediaPath", ""),
                    "sequenceID": item.get("sequenceID", ""),
                    "isSequence": item.get("isSequence", False),
                    "itemType": item.get("itemType", ""),
                })
            print(f"[Favoritos] {len(items)} itens carregados")
            return tuple(items), ()
        except Exception as exc:
            print(f"[Favoritos] Erro ao ler: {exc}")
            return (), (f"favorites:{exc}",)

    def _build_indexes(self, items: tuple[dict, ...]):
        indexed_items: list[IndexedItem] = []
        exact_name_map: dict[str, list[int]] = {}
        prefix_map: dict[str, list[int]] = {}
        token_prefix_map: dict[str, list[int]] = {}
        trigram_map: dict[str, list[int]] = {}

        for idx, item in enumerate(items):
            search_text = item.get("searchText") or item.get("name", "")
            normalized_name = normalize_search_text(search_text)
            tokens = tokenize_search_text(search_text)
            indexed = IndexedItem(
                payload=item,
                normalized_name=normalized_name,
                tokens=tokens,
                item_type=item.get("type", ""),
                load_order=idx,
            )
            indexed_items.append(indexed)
            exact_name_map.setdefault(normalized_name, []).append(idx)
            for prefix in iter_prefixes(normalized_name):
                prefix_map.setdefault(prefix, []).append(idx)
            for token in tokens:
                for prefix in iter_prefixes(token):
                    token_prefix_map.setdefault(prefix, []).append(idx)
            for trigram in set(make_trigrams(normalized_name)):
                trigram_map.setdefault(trigram, []).append(idx)

        return (
            tuple(indexed_items),
            freeze_id_lists(exact_name_map),
            freeze_id_lists(prefix_map),
            freeze_id_lists(token_prefix_map),
            freeze_id_lists(trigram_map),
        )

    def search(self, query: str, *, type_filters: set[str] | None = None, limit: int = RESULT_LIMIT) -> SearchResultSet:
        snapshot = self.snapshot
        query = resolve_alias_query(query)
        normalized_query = normalize_search_text(query)
        if not normalized_query:
            return SearchResultSet(items=(), match_infos=(), total_count=0, visible_count=0, query="")

        allowed_types = set(type_filters) if type_filters else None
        prefix_key = normalized_query[:4]
        score_map: dict[int, tuple[float, tuple[tuple[int, int], ...]]] = {}

        def is_allowed(idx: int) -> bool:
            if allowed_types is None:
                return True
            return snapshot.indexed_items[idx].item_type in allowed_types

        def record(idx: int, score: float, ranges: tuple[tuple[int, int], ...]):
            if idx not in score_map:
                score_map[idx] = (score, ranges)

        # Tier 1 — exact name (1000)
        for idx in snapshot.exact_name_map.get(normalized_query, ()):
            if is_allowed(idx):
                name = snapshot.indexed_items[idx].normalized_name
                record(idx, 1000.0, ((0, len(name)),))

        # Tier 2 — full-name prefix (900)
        for idx in snapshot.prefix_map.get(prefix_key, ()):
            if not is_allowed(idx):
                continue
            if snapshot.indexed_items[idx].normalized_name.startswith(normalized_query):
                record(idx, 900.0, ((0, len(normalized_query)),))

        # Tier 3 — token prefix (800)
        for idx in snapshot.token_prefix_map.get(prefix_key, ()):
            if not is_allowed(idx):
                continue
            item = snapshot.indexed_items[idx]
            for token in item.tokens:
                if token.startswith(normalized_query):
                    pos = item.normalized_name.find(token)
                    ranges = ((pos, pos + len(normalized_query)),) if pos >= 0 else ()
                    record(idx, 800.0, ranges)
                    break

        # Tier 4 — substring / trigram (700)
        if len(normalized_query) >= 3:
            trigram_groups = []
            for trigram in set(make_trigrams(normalized_query)):
                ids = snapshot.trigram_map.get(trigram)
                if not ids:
                    trigram_groups = []
                    break
                trigram_groups.append(set(ids))
            contains_candidates = list(set.intersection(*trigram_groups)) if trigram_groups else []
        else:
            contains_candidates = list(range(len(snapshot.indexed_items)))

        for idx in contains_candidates:
            if not is_allowed(idx):
                continue
            item = snapshot.indexed_items[idx]
            pos = item.normalized_name.find(normalized_query)
            if pos >= 0:
                record(idx, 700.0, ((pos, pos + len(normalized_query)),))

        # Tier 5 — fuzzy (0–699), only when rapidfuzz is available
        if HAS_RAPIDFUZZ and len(normalized_query) >= 2:
            for idx in range(len(snapshot.indexed_items)):
                if idx in score_map or not is_allowed(idx):
                    continue
                item = snapshot.indexed_items[idx]
                raw_score = _rf_fuzz.WRatio(normalized_query, item.normalized_name)
                if raw_score < FUZZY_THRESHOLD:
                    continue
                try:
                    alignment = _rf_fuzz.partial_ratio_alignment(normalized_query, item.normalized_name)
                    ranges = ((alignment.dest_start, alignment.dest_end),)
                except Exception:
                    ranges = ()
                record(idx, raw_score * (699.0 / 100.0), ranges)

        def sort_key(idx: int):
            item = snapshot.indexed_items[idx]
            return (-score_map[idx][0], len(item.normalized_name), item.load_order)

        ranked_ids = sorted(score_map.keys(), key=sort_key)
        visible_ids = ranked_ids[:limit]
        visible_match_infos = tuple(
            MatchInfo(score=score_map[idx][0], ranges=score_map[idx][1])
            for idx in visible_ids
        )
        return SearchResultSet(
            items=tuple(snapshot.indexed_items[idx].payload for idx in visible_ids),
            match_infos=visible_match_infos,
            total_count=len(ranked_ids),
            visible_count=len(visible_ids),
            query=normalized_query,
        )

    @staticmethod
    def _safe_mtime(file_path: Path) -> float:
        try:
            return file_path.stat().st_mtime
        except Exception:
            return 0.0


class _UnavailableExecutionAdapter:
    """Returned when the UXP transport server cannot start (e.g. its port is already bound).

    Keeps the companion usable - search palette, catalogs and global shortcuts all still work -
    while every Premiere action fails closed with a clear status, instead of silently falling
    back to the CEP bridge (removed as a runtime path once UXP reached parity, see STATUS.md).
    """

    backend_name = "unavailable"

    def __init__(self, reason: str = ""):
        self._reason = reason

    def execute(self, effect: dict) -> float:
        return time.time()

    def poll_status(self, timestamp: float | None) -> str | None:
        return "error_not_connected"

    @staticmethod
    def is_terminal(status: str | None) -> bool:
        return bool(status and str(status).startswith("error"))

    @staticmethod
    def is_success(status: str | None) -> bool:
        return False

    def diagnostics(self) -> dict:
        return {"backend": self.backend_name, "reason": self._reason}


def watched_data_directories(paths: DataPaths) -> list[Path]:
    """Directories the file watcher must observe: just the companion's own data folder."""
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return [paths.data_dir]


def create_execution_adapter():
    """The UXP transport (TECHNICAL_PLAN.md stage 5) is the only Premiere execution backend.

    The CEP bridge fallback was removed once UXP reached parity for everything the product uses
    (STATUS.md). If the UXP adapter cannot start, return a stub that fails every action closed
    rather than silently degrading to a backend nothing is listening on.
    """
    # QWebSocketServer needs a running Qt event loop to ever fire a signal, so this
    # only applies when the Qt UI actually created a QApplication.
    qt_app_running = HAS_QT and QtWidgets.QApplication.instance() is not None
    if not (HAS_UXP_ADAPTER and qt_app_running):
        return _UnavailableExecutionAdapter(
            "UXP adapter unavailable (requires the Qt UI and companion/uxp_execution_adapter.py)"
        )
    try:
        return PremiereUxpExecutionAdapter()
    except Exception as error:
        print(f"[Aviso] Falha ao iniciar o adapter UXP: {error}")
        return _UnavailableExecutionAdapter(str(error))


def collect_diagnostics(palette) -> list[dict]:
    """Return UI-independent, actionable health checks for the settings center."""
    snapshot = palette.loader.snapshot
    premiere_running = premiere_is_running()
    catalog_healthy = snapshot.source != "fallback" and snapshot.count > 0 and not snapshot.load_issues
    _nest_shortcut, kys_file = find_premiere_command_shortcut("cmd.clip.nestify")
    listener = getattr(palette, "hotkey_listener", None)
    registered = len(getattr(listener, "_registered_hotkey_ids", ())) if listener is not None else 0
    configured = len(getattr(listener, "_specs", ())) if listener is not None else 0
    adapter = palette.execution_adapter.diagnostics()
    executor_healthy = adapter.get("backend") == "uxp"

    catalog_details = f"{snapshot.count} items · {snapshot.source}"
    if snapshot.load_issues:
        catalog_details += f" · {len(snapshot.load_issues)} issue(s)"
    hotkey_details = f"{registered}/{configured} registered" if configured else f"{registered} registered"
    return [
        {
            "key": "premiere",
            "name": "Premiere Pro",
            "healthy": premiere_running,
            "details": "Process detected" if premiere_running else "Process not detected",
            "recommendation": "Ready" if premiere_running else "Start Premiere Pro, then refresh",
            "action": "start_premiere" if not premiere_running else "",
        },
        {
            "key": "catalog",
            "name": "Catalog",
            "healthy": catalog_healthy,
            "details": catalog_details,
            "recommendation": "Ready" if catalog_healthy else "Refresh the catalog and inspect load issues",
            "action": "refresh_catalog" if not catalog_healthy else "open_data_folder",
        },
        {
            "key": "uxp_transport",
            "name": "UXP plugin",
            "healthy": bool(adapter.get("authenticated")),
            "details": (
                f"connected on port {adapter.get('port')}" if adapter.get("authenticated")
                else "listening, waiting for the plugin" if adapter.get("listening")
                else str(adapter.get("reason") or "transport not started")
            ),
            "recommendation": "Ready" if adapter.get("authenticated") else "Load the FX.palette UXP plugin in Premiere (UDT: Load & Watch)",
            "action": "",
        },
        {
            "key": "shortcut_profile",
            "name": "Shortcut profile",
            "healthy": kys_file is not None,
            "details": str(kys_file or "—"),
            "recommendation": "Ready" if kys_file else "Open Premiere and save a keyboard shortcut profile",
            "action": "open_shortcut_folder",
        },
        {
            "key": "global_shortcuts",
            "name": "Global shortcuts",
            "healthy": registered > 0 and (configured == 0 or registered == configured),
            "details": hotkey_details,
            "recommendation": "Ready" if registered > 0 and (configured == 0 or registered == configured) else "Review assignments and reload shortcuts",
            "action": "edit_shortcuts",
        },
        {
            "key": "executor",
            "name": "Premiere executor",
            "healthy": executor_healthy,
            "details": str(adapter.get("backend", "unknown")).upper(),
            "recommendation": "Ready" if executor_healthy else "The UXP plugin must be loaded in Premiere for actions to run",
            "action": "open_data_folder",
        },
        {
            "key": "preset_catalog",
            "name": "Preset catalog",
            # Only the UXP backend needs an explicit import - CEP scans .prfpset from disk itself.
            # None means "not yet known" (still waiting on the plugin's own restore-from-token
            # round trip), treated as healthy rather than flashing a false alarm on every startup.
            "healthy": adapter.get("backend") != "uxp" or adapter.get("preset_catalog_available") is not False,
            "details": "Imported" if adapter.get("preset_catalog_available") else "Not imported",
            "recommendation": "Ready" if adapter.get("preset_catalog_available") is not False else "Pick a .prfpset file to enable preset apply",
            "action": "import_preset_catalog" if (adapter.get("backend") == "uxp" and adapter.get("preset_catalog_available") is False) else "",
        },
    ]


def execute_effect_through_adapter(palette, effect: dict) -> float:
    # The palette's own Settings > General checkbox is the only place this is user-facing;
    # EffectsLoader never puts "reconstructEasing" on a preset's own dict, so this is the sole
    # point deciding it before the adapter's own RECONSTRUCT_EASING_DEFAULT fallback would apply.
    if effect.get("type") == "preset" and "reconstructEasing" not in effect:
        effect = dict(effect, reconstructEasing=getattr(palette, "reconstruct_easing_enabled", RECONSTRUCT_EASING_DEFAULT))
    adapter = getattr(palette, "execution_adapter", None) or _UnavailableExecutionAdapter("no adapter on palette")
    return adapter.execute(effect)


def track_adapter_action_success(palette, effect: dict, timestamp: float) -> None:
    """Record asynchronous adapter actions only after the backend reports success."""
    root = getattr(palette, "root", None)
    adapter = getattr(palette, "execution_adapter", None)
    if root is None or not hasattr(root, "after") or adapter is None:
        return
    deadline = time.monotonic() + (apply_status_timeout_ms(effect) / 1000.0)

    def poll():
        status = adapter.poll_status(timestamp)
        if adapter.is_success(status):
            record_successful_action(effect, confirmed_by=adapter.backend_name)
            return
        if adapter.is_terminal(status) or time.monotonic() >= deadline:
            return
        root.after(APPLY_STATUS_POLL_MS, poll)

    root.after(APPLY_STATUS_INITIAL_DELAY_MS, poll)


def execute_configured_hotkey_action(palette, action: dict) -> bool:
    """Execute one configured action while preserving Premiere's selection/focus."""
    action_type = str(action.get("type", "")).strip().lower()
    premiere_hwnd = foreground_window_handle_native()
    # When invoked from a visible recent-action row, the palette itself owns
    # focus. Preserve the Premiere handle captured when the palette opened.
    if premiere_hwnd and premiere_is_focused():
        palette.window_controller.previous_handle = premiere_hwnd

    if action_type == "open_search":
        query = str(action.get("query", ""))
        palette.show()

        def fill_search():
            if hasattr(palette, "_set_search_text"):
                palette._set_search_text(query)
            else:
                palette.entry.setText(query)
            palette._refresh_list()

        palette.root.after(0, fill_search)
        return True

    if action_type == "repeat_last_action":
        previous = last_successful_action()
        return execute_configured_hotkey_action(palette, previous) if previous else False

    if action_type == "nest":
        effect = dict(next((item for item in TIMELINE_ACTIONS if item.get("action") == "nest"), {}))
        if not effect:
            return False
        effect.update({
            "nestMode": resolve_nest_mode("auto", getattr(palette, "execution_adapter", None)),
            "nestName": str(action.get("name", "")).strip(),
            "nestBin": DEFAULT_NEST_BIN,
        })
    elif action_type == "label":
        try:
            label_index = int(action.get("labelIndex"))
        except (TypeError, ValueError):
            return False
        if not 0 <= label_index < LABEL_COLOR_COUNT:
            return False
        effect = dict(load_premiere_label_preferences()[label_index])
    elif action_type == "select_label_group":
        effect = {
            "name": tr("label_select_group"),
            "type": "label_group_action",
            "action": "select_label_group",
        }
    elif action_type == "apply_search":
        query = str(action.get("query", "")).strip()
        if not query:
            return False
        items = resolve_action_query_items(palette.loader, query, limit=1)
        if not items:
            beta_report.write_event("custom_hotkey_action_failed", {"type": action_type, "reason": "not_found", "query": query})
            return False
        effect = dict(items[0])
        if effect.get("action") == "nest":
            effect.update({
                "nestMode": resolve_nest_mode("auto", getattr(palette, "execution_adapter", None)),
                "nestName": "",
                "nestBin": DEFAULT_NEST_BIN,
            })
        if effect.get("type") in {"transition_video", "transition_audio"}:
            effect["transitionPlacement"] = "auto"
    else:
        return False

    if effect.get("type") in {"label_color", "label_group_action"}:
        return bool(palette._execute_label_action(effect))
    if effect.get("type") == "timeline_action" and effect.get("action") == "nest":
        if effect.get("nestMode") == "premiere":
            return bool(palette._execute_timeline_action(effect))
        timestamp = execute_effect_through_adapter(palette, effect)
        track_adapter_action_success(palette, effect, timestamp)
        return True
    timestamp = execute_effect_through_adapter(palette, effect)
    track_adapter_action_success(palette, effect, timestamp)
    return True


def write_safe(file_path: Path, content: str):
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(file_path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def resolve_nest_mode(requested_mode: str, adapter=None) -> str:
    """"auto" -> "api" (the plugin's own createNest) or "premiere" (native Ctrl+/-style keystroke).

    Native Nest leaves a selection that spans more than one audio track unmerged, so the plugin is
    asked whether the current selection does; only a single-audio-track selection (or a failed
    check) takes the native route, and only when the user's profile actually binds
    cmd.clip.nestify - otherwise there is no keystroke to send.
    """
    if requested_mode in {"premiere", "api"}:
        return requested_mode
    if adapter is not None and hasattr(adapter, "has_multi_track_audio_selection"):
        if adapter.has_multi_track_audio_selection() is True:
            return "api"
    shortcut, _shortcut_file = find_premiere_command_shortcut("cmd.clip.nestify")
    return "premiere" if shortcut is not None else "api"


def format_apply_failure(status: str | None) -> str:
    if not status:
        return tr("status_no_response")
    keys = {
        "error_no_selection": "bridge_error_no_selection",
        "error_no_sequence": "bridge_error_no_sequence",
        "error_not_found": "bridge_error_not_found",
        "error_not_inserted": "bridge_error_not_inserted",
        "error_template_missing": "bridge_error_template_missing",
        "error_create_failed": "bridge_error_create_failed",
        "error_not_supported": "bridge_error_not_supported",
        "error_command_unavailable": "bridge_error_command_unavailable",
        "error_api_unavailable": "bridge_error_api_unavailable",
        "error_unsafe_overlap": "bridge_error_unsafe_overlap",
    }
    return tr(keys.get(status, "bridge_error_generic"))


def _premiere_process_via_toolhelp() -> bool | None:
    if not IS_WINDOWS:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * PROCESSENTRY32W_MAX_PATH),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None

        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        found = False
        has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        target_names = {name.lower() for name in PREMIERE_PROCESS_NAMES}

        while has_entry:
            if str(entry.szExeFile).lower() in target_names:
                found = True
                break
            has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))

        kernel32.CloseHandle(snapshot)
        return found
    except Exception as exc:
        beta_report.log_exception("Premiere Toolhelp process check failed", exc)
        return None


def _premiere_process_via_tasklist() -> bool | None:
    if not IS_WINDOWS:
        return None

    try:
        startupinfo = None
        creationflags = 0
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Adobe Premiere Pro.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except Exception as exc:
        beta_report.log_exception("Premiere tasklist process check failed", exc)
        return None

    if result.returncode != 0:
        beta_report.write_event("premiere_tasklist_process_check_failed", {
            "returncode": result.returncode,
            "stderr": (result.stderr or "").strip()[:500],
            "stdout": (result.stdout or "").strip()[:500],
        })
        return None

    output = (result.stdout or "").lower()
    for process_name in PREMIERE_PROCESS_NAMES:
        if process_name.lower() in output:
            return True

    return False


def premiere_process_is_running() -> bool | None:
    """Checks the Premiere process directly. Returns None if checks cannot run."""
    toolhelp_running = _premiere_process_via_toolhelp()
    if toolhelp_running is not None:
        return toolhelp_running

    return _premiere_process_via_tasklist()


def premiere_is_running() -> bool:
    """Best-effort check used only for closed beta feedback prompts."""
    process_running = premiere_process_is_running()
    if process_running is not None:
        return process_running

    if not HAS_PYGETWINDOW:
        return True

    try:
        titles = gw.getAllTitles()
    except Exception:
        return True

    for title in titles:
        title_text = str(title or "")
        if "Adobe Premiere" in title_text or "Premiere Pro" in title_text:
            return True
    return False


def foreground_window_title_native() -> str | None:
    if not IS_WINDOWS or USER32 is None:
        return None
    try:
        hwnd = USER32.GetForegroundWindow()
        if not hwnd:
            return None
        length = USER32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        USER32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return None


def foreground_window_handle_native() -> int | None:
    if not IS_WINDOWS or USER32 is None:
        return None
    try:
        hwnd = USER32.GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception:
        return None


def window_is_minimized_native(hwnd: int | None) -> bool:
    if not hwnd or USER32 is None:
        return False
    try:
        return bool(USER32.IsIconic(hwnd))
    except Exception:
        return False


class _NativeWindowCalls:
    """Adapts the module-level Win32 helpers to what PaletteWindowController expects."""

    def foreground_handle(self):
        return foreground_window_handle_native()

    def activate_handle(self, handle):
        activate_window_handle_native(handle)


def activate_window_handle_native(hwnd: int | None):
    if not hwnd or USER32 is None:
        return
    try:
        if window_is_minimized_native(hwnd):
            USER32.ShowWindow(hwnd, SW_RESTORE)
        USER32.BringWindowToTop(hwnd)
        USER32.SetForegroundWindow(hwnd)
        USER32.SetActiveWindow(hwnd)
        USER32.SetFocus(hwnd)
    except Exception:
        pass


def premiere_is_focused() -> bool:
    title = foreground_window_title_native()
    if title is not None:
        return "Adobe Premiere" in title
    if not HAS_PYGETWINDOW:
        return False
    try:
        active = gw.getActiveWindow()
        if active is None:
            return False
        return "Adobe Premiere" in active.title
    except Exception:
        return False


class DataFilesChangeHandler(FileSystemEventHandler):
    def __init__(self, palette):
        self.palette = palette

    def on_any_event(self, event):
        if getattr(event, "is_directory", False):
            return
        for raw_path in (getattr(event, "src_path", ""), getattr(event, "dest_path", "")):
            if raw_path and Path(raw_path).name in WATCHED_DATA_FILES:
                self.palette.schedule_data_refresh()
                return


# Palette category chip -> item types it shows.
CATEGORY_TYPE_FILTERS = {
    "Video": {"video"},
    "Audio": {"audio"},
    "Transicoes": {"transition_video", "transition_audio"},
    "Presets": {"preset"},
    "Projeto": {"project_item"},
    "Favoritos": {"generic_item", "favorite_item"},
}


# Result-row presentation model, shared by every place that renders a palette row.
def build_result_row_model(effect: dict) -> ResultRowModel:
    item_type = effect.get("type", "video")
    is_favorite = item_type in {"generic_item", "favorite_item"}
    if item_type == "preset":
        type_label = "Preset"
        icon_kind = "preset"
        subtitle = effect.get("category", "Presets")
    elif item_type in {"transition_video", "transition_audio"}:
        type_label = "Transition"
        icon_kind = "effect"
        subtitle = effect.get("category", "Transicoes")
    elif item_type == "project_item":
        type_label = "Project"
        icon_kind = "project"
        subtitle = effect.get("treePath") or effect.get("category", "Projeto")
    elif item_type == "label_color":
        type_label = "Label"
        icon_kind = "effect"
        subtitle = effect.get("labelColor", "")
    elif item_type == "label_group_action":
        type_label = "Action"
        icon_kind = "action"
        subtitle = tr("label_select_group_desc")
    elif item_type == "timeline_action":
        type_label = "Action"
        icon_kind = "action"
        subtitle = effect.get("category", "Timeline")
    elif item_type == "history_action":
        type_label = "Recent"
        icon_kind = "action"
        subtitle = effect.get("category", "Recentes")
    elif is_favorite:
        type_label = "Favorite"
        icon_kind = "favorite"
        subtitle = effect.get("sourceTreePath") or effect.get("category", "Favoritos")
    else:
        type_label = "Effect"
        icon_kind = "effect"
        subtitle = effect.get("category", "Effects")
    return ResultRowModel(
        payload=effect,
        title=effect.get("name", ""),
        subtitle=subtitle,
        type_label=type_label,
        icon_kind=icon_kind,
        is_favorite=is_favorite,
        accent_kind=filter_key_for_item_type(item_type),
        accent_color=effect.get("labelColor") if item_type == "label_color" else None,
    )


class QtRootAdapter(QtCore.QObject):
    _schedule_requested = QtCore.Signal(int, int)
    _post_requested = QtCore.Signal(object)

    def __init__(self, app: QtWidgets.QApplication):
        super().__init__()
        self.app = app
        self._next_job_id = 1
        self._callbacks: dict[int, Callable] = {}
        self._timers: dict[int, QtCore.QTimer] = {}
        self._destroyed = False
        self._schedule_requested.connect(self._start_timer, QtCore.Qt.ConnectionType.QueuedConnection)
        self._post_requested.connect(self._run_post, QtCore.Qt.ConnectionType.QueuedConnection)

    def after(self, delay_ms: int, callback=None, *args):
        if callback is None:
            return None
        job_id = self._next_job_id
        self._next_job_id += 1
        self._callbacks[job_id] = lambda: callback(*args)
        self._schedule_requested.emit(max(0, int(delay_ms)), job_id)
        return job_id

    def post(self, callback=None, *args):
        if callback is None or self._destroyed:
            return
        self._post_requested.emit(lambda: callback(*args))

    def after_cancel(self, job_id):
        if job_id is None:
            return
        self._callbacks.pop(job_id, None)
        timer = self._timers.pop(job_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    @QtCore.Slot(int, int)
    def _start_timer(self, delay_ms: int, job_id: int):
        if job_id not in self._callbacks or self._destroyed:
            return
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)

        def fire():
            self._timers.pop(job_id, None)
            callback = self._callbacks.pop(job_id, None)
            timer.deleteLater()
            if callback is not None and not self._destroyed:
                callback()

        timer.timeout.connect(fire)
        self._timers[job_id] = timer
        timer.start(delay_ms)

    @QtCore.Slot(object)
    def _run_post(self, callback):
        if not self._destroyed:
            callback()

    def winfo_exists(self):
        return not self._destroyed

    def update(self):
        self.app.processEvents()

    def mainloop(self):
        return self.app.exec()

    def destroy(self):
        self._destroyed = True
        for job_id in list(self._callbacks):
            self.after_cancel(job_id)
        self.app.quit()


class QtResultRowWidget(QtWidgets.QFrame):
    def __init__(self, model: ResultRowModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.setObjectName("resultRow")
        self.setFixedHeight(PaletteLayoutMetrics().row_height)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 10, 6)
        layout.setSpacing(10)

        self.icon = QtWidgets.QLabel(get_icon_glyph(model.icon_kind))
        self.icon.setObjectName("rowIcon")
        self.icon.setFixedWidth(20)
        self.icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon)

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.title = QtWidgets.QLabel(model.title)
        self.title.setObjectName("rowTitle")
        self.title.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.subtitle = QtWidgets.QLabel(model.subtitle)
        self.subtitle.setObjectName("rowSubtitle")
        self.subtitle.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        text_layout.addWidget(self.title)
        text_layout.addWidget(self.subtitle)
        layout.addLayout(text_layout, 1)

        self.badge = QtWidgets.QLabel(model.type_label)
        self.badge.setObjectName("rowBadge")
        self.badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.badge)
        self.apply_state(selected=False)
        self._fade_animation = None

    def animate_in(self, delay_ms: int = 0):
        effect = QtWidgets.QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        animation = QtCore.QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        def finish():
            self.setGraphicsEffect(None)
            self._fade_animation = None

        if delay_ms:
            group = QtCore.QSequentialAnimationGroup(self)
            group.addPause(delay_ms)
            group.addAnimation(animation)
            group.finished.connect(finish)
            self._fade_animation = group
            group.start()
        else:
            animation.finished.connect(finish)
            self._fade_animation = animation
            animation.start()

    def animate_selection(self):
        effect = QtWidgets.QGraphicsOpacityEffect(self)
        effect.setOpacity(0.86)
        self.setGraphicsEffect(effect)
        animation = QtCore.QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(110)
        animation.setStartValue(0.86)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self.setGraphicsEffect(None))
        animation.start(QtCore.QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def apply_state(self, *, selected: bool):
        tokens = get_row_visual_tokens(self.model.accent_kind, selected=selected, hovered=False, accent_color=self.model.accent_color)
        self.setStyleSheet(
            f"""
            QFrame#resultRow {{
                background: {tokens["bg"]};
                border: 1px solid {tokens["border"]};
                border-radius: 12px;
            }}
            QLabel#rowTitle {{
                color: {tokens["title_fg"]};
                font-weight: 700;
                background: transparent;
            }}
            QLabel#rowSubtitle {{
                color: {tokens["subtitle_fg"]};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#rowIcon {{
                color: {tokens["icon_fg"]};
                background: transparent;
                font-size: 14px;
            }}
            QLabel#rowBadge {{
                color: {tokens["type_fg"]};
                background: {tokens["type_bg"]};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }}
            """
        )


class QtHotkeyCatalogEditor(QtWidgets.QDialog):
    """Premiere-style searchable catalog of every shortcut-capable action."""

    def __init__(self, palette):
        super().__init__(palette.window)
        self.palette = palette
        self.catalog: list[dict] = []
        self.excluded_keys: set[str] = set()
        self.assignments: dict[str, dict] = {}
        self.item_by_key: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._selected_key = None
        self.setWindowTitle("Atalhos do FX.palette" if CURRENT_LANGUAGE == "pt" else "FX.palette shortcuts")
        self.setModal(False)
        self.resize(900, 620)
        self.setMinimumSize(720, 460)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        self._build_catalog()
        self._load_assignments()
        self._build_ui()
        self._populate()

    def _text(self, pt: str, en: str) -> str:
        return pt if CURRENT_LANGUAGE == "pt" else en

    @staticmethod
    def _action_key(action: dict) -> str:
        return json.dumps(action, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _append_action(self, name: str, category: str, action: dict, description: str = ""):
        key = self._action_key(action)
        if any(item["key"] == key for item in self.catalog):
            return
        self.catalog.append({"key": key, "name": name, "category": category, "action": action, "description": description})

    def _build_catalog(self):
        system_category = "FX.palette"
        self._append_action(self._text("Abrir FX.palette", "Open FX.palette"), system_category, {"type": "open_search", "query": ""})
        self._append_action(self._text("Criar sequência aninhada", "Create nested sequence"), system_category, {"type": "nest"})
        self._append_action(tr("label_select_group"), self._text("Labels", "Labels"), {"type": "select_label_group"})
        for label in load_premiere_label_preferences():
            self._append_action(str(label["name"]), self._text("Labels", "Labels"),
                                {"type": "label", "labelIndex": int(label["labelIndex"])})
        for alias in load_alias_entries():
            self._append_action(
                alias["alias"], self._text("Aliases", "Aliases"),
                {"type": "apply_search", "query": alias["alias"]},
                self._text(f'Alvo: {alias["target"]}', f'Target: {alias["target"]}'),
            )

        type_categories = {
            "video": self._text("Efeitos de vídeo", "Video effects"),
            "audio": self._text("Efeitos de áudio", "Audio effects"),
            "transition_video": self._text("Transições", "Transitions"),
            "transition_audio": self._text("Transições", "Transitions"),
            "preset": self._text("Presets", "Presets"),
            "favorite_item": self._text("Favoritos", "Favorites"),
            "generic_item": system_category,
        }
        for item in self.palette.loader.snapshot.all_items:
            item_type = str(item.get("type", ""))
            if item_type == "project_item" and item.get("name"):
                self.excluded_keys.add(self._action_key({"type": "apply_search", "query": str(item["name"])}))
                continue
            if item_type not in type_categories or not item.get("name"):
                continue
            name = str(item["name"])
            self._append_action(name, type_categories[item_type], {"type": "apply_search", "query": name}, str(item.get("category", "")))
        self.catalog.sort(key=lambda item: (normalize_search_text(item["category"]), normalize_search_text(item["name"])))

    def _load_assignments(self):
        for entry in load_custom_hotkey_entries():
            action = entry.get("action")
            if not isinstance(action, dict):
                continue
            key = self._action_key(action)
            if key in self.excluded_keys and not any(item["key"] == key for item in self.catalog):
                continue
            self.assignments[key] = {
                "name": str(entry.get("name") or ""),
                "shortcut": str(entry.get("shortcut") or ""),
                "enabled": entry.get("enabled", True) is not False,
                "requiresPremiereFocus": bool(entry.get("requiresPremiereFocus", True)),
                "action": dict(action),
            }
            if not any(item["key"] == key for item in self.catalog):
                self.catalog.append({"key": key, "name": str(entry.get("name") or key),
                                     "category": self._text("Personalizados", "Custom"), "action": dict(action), "description": ""})

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)
        title = QtWidgets.QLabel(self._text("Atalhos de teclado", "Keyboard shortcuts"))
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)
        subtitle = QtWidgets.QLabel(self._text(
            "Pesquise uma ação, selecione-a e pressione a combinação desejada. F13–F24 são ideais para Stream Deck.",
            "Find an action, select it, and press the desired combination. F13–F24 are ideal for Stream Deck.",
        ))
        subtitle.setStyleSheet("color: #a8a8b3;")
        root.addWidget(subtitle)

        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(self._text("Pesquisar comandos…", "Search commands…"))
        self.search.setClearButtonEnabled(True)
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItem(self._text("Todas as categorias", "All categories"), "")
        for category in sorted({item["category"] for item in self.catalog}, key=normalize_search_text):
            self.filter_combo.addItem(category, category)
        self.filter_combo.addItem(self._text("Somente atribuídos", "Assigned only"), "__assigned__")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.filter_combo)
        root.addLayout(filters)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels((self._text("Comando", "Command"), self._text("Categoria", "Category"), self._text("Atalho", "Shortcut")))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.tree, 1)

        assignment = QtWidgets.QFrame()
        assignment.setObjectName("assignmentPanel")
        assignment_layout = QtWidgets.QVBoxLayout(assignment)
        assignment_layout.setContentsMargins(12, 10, 12, 10)
        assignment_layout.setSpacing(8)
        self.selected_label = QtWidgets.QLabel(self._text("Selecione um comando", "Select a command"))
        self.selected_label.setObjectName("selectedCommand")
        self.selected_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        assignment_layout.addWidget(self.selected_label)
        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        shortcut_caption = QtWidgets.QLabel(self._text("Atalho", "Shortcut"))
        shortcut_caption.setObjectName("shortcutCaption")
        self.sequence_edit = QtWidgets.QKeySequenceEdit()
        self.sequence_edit.setClearButtonEnabled(True)
        self.sequence_edit.setEnabled(False)
        self.sequence_edit.setMinimumWidth(230)
        self.enabled_check = QtWidgets.QCheckBox(self._text("Ativo", "Enabled"))
        self.enabled_check.setEnabled(False)
        clear_button = QtWidgets.QPushButton(self._text("Remover atalho", "Clear shortcut"))
        clear_button.clicked.connect(self._clear_selected)
        controls_layout.addWidget(shortcut_caption)
        controls_layout.addWidget(self.sequence_edit, 1)
        controls_layout.addWidget(self.enabled_check)
        controls_layout.addStretch(1)
        controls_layout.addWidget(clear_button)
        assignment_layout.addLayout(controls_layout)
        root.addWidget(assignment)

        footer = QtWidgets.QHBoxLayout()
        count = QtWidgets.QLabel(self._text(f"{len(self.catalog)} comandos disponíveis", f"{len(self.catalog)} commands available"))
        count.setStyleSheet("color: #898994;")
        footer.addWidget(count)
        footer.addStretch(1)
        cancel = QtWidgets.QPushButton(self._text("Cancelar", "Cancel"))
        cancel.clicked.connect(self.close)
        save = QtWidgets.QPushButton(self._text("Salvar", "Save"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self.search.textChanged.connect(self._apply_filter)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.tree.currentItemChanged.connect(self._select_item)
        self.sequence_edit.keySequenceChanged.connect(self._sequence_changed)
        self.enabled_check.toggled.connect(self._enabled_changed)
        self.setStyleSheet("""
            QDialog, QWidget { background: #17171b; color: #f1f1f4; }
            QLineEdit, QComboBox, QKeySequenceEdit { background: #24242b; border: 1px solid #3b3b45; border-radius: 6px; padding: 7px; }
            QLineEdit:focus, QComboBox:focus, QKeySequenceEdit:focus { border-color: #5b6bf8; }
            QTreeWidget { background: #1d1d22; alternate-background-color: #212127; border: 1px solid #33333c; border-radius: 7px; }
            QTreeWidget::item { padding: 7px 5px; }
            QTreeWidget::item:selected { background: #3541a5; }
            QHeaderView::section { background: #292930; color: #c8c8d0; padding: 7px; border: 0; border-right: 1px solid #3b3b45; }
            QFrame#assignmentPanel { background: #202026; border: 1px solid #373740; border-radius: 7px; }
            QFrame#assignmentPanel QLabel, QFrame#assignmentPanel QCheckBox { background: transparent; border: 0; }
            QLabel#selectedCommand { color: #f1f1f4; font-weight: 650; font-size: 13px; }
            QLabel#shortcutCaption { color: #a8a8b3; }
            QPushButton { background: #2c2c34; border: 1px solid #44444f; border-radius: 6px; padding: 7px 12px; }
            QPushButton:hover { background: #393943; }
        """)

    def _populate(self):
        self.tree.clear()
        self.item_by_key.clear()
        for catalog_item in self.catalog:
            shortcut = self.assignments.get(catalog_item["key"], {}).get("shortcut", "")
            tree_item = QtWidgets.QTreeWidgetItem((catalog_item["name"], catalog_item["category"], shortcut))
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, catalog_item["key"])
            if shortcut:
                tree_item.setForeground(2, QtGui.QColor("#78dc9b"))
            self.tree.addTopLevelItem(tree_item)
            self.item_by_key[catalog_item["key"]] = tree_item
        self._apply_filter()

    def _apply_filter(self):
        query = normalize_search_text(self.search.text())
        category = self.filter_combo.currentData()
        for item in self.catalog:
            haystack = normalize_search_text(f'{item["name"]} {item["category"]} {item["description"]}')
            matches_query = not query or all(token in haystack for token in query.split())
            matches_category = not category or (category == "__assigned__" and item["key"] in self.assignments) or item["category"] == category
            self.item_by_key[item["key"]].setHidden(not (matches_query and matches_category))

    def _select_item(self, current, _previous):
        self._selected_key = current.data(0, QtCore.Qt.ItemDataRole.UserRole) if current else None
        self.sequence_edit.blockSignals(True)
        self.enabled_check.blockSignals(True)
        if self._selected_key:
            catalog_item = next(item for item in self.catalog if item["key"] == self._selected_key)
            assignment = self.assignments.get(self._selected_key, {})
            self.selected_label.setText(catalog_item["name"])
            self.selected_label.setToolTip(catalog_item["name"])
            self.sequence_edit.setKeySequence(QtGui.QKeySequence(str(assignment.get("shortcut", ""))))
            self.enabled_check.setChecked(assignment.get("enabled", True))
            self.sequence_edit.setEnabled(True)
            self.enabled_check.setEnabled(True)
        else:
            self.selected_label.setText(self._text("Selecione um comando", "Select a command"))
            self.selected_label.setToolTip("")
            self.sequence_edit.clear()
            self.sequence_edit.setEnabled(False)
            self.enabled_check.setEnabled(False)
        self.sequence_edit.blockSignals(False)
        self.enabled_check.blockSignals(False)

    def _sequence_changed(self, sequence):
        if not self._selected_key:
            return
        shortcut = sequence.toString(QtGui.QKeySequence.SequenceFormat.PortableText)
        catalog_item = next(item for item in self.catalog if item["key"] == self._selected_key)
        if shortcut:
            self.assignments[self._selected_key] = {
                "name": catalog_item["name"], "shortcut": shortcut, "enabled": self.enabled_check.isChecked(),
                "requiresPremiereFocus": True, "action": dict(catalog_item["action"]),
            }
        else:
            self.assignments.pop(self._selected_key, None)
        tree_item = self.item_by_key[self._selected_key]
        tree_item.setText(2, shortcut)
        tree_item.setForeground(2, QtGui.QColor("#78dc9b") if shortcut else QtGui.QColor("#f1f1f4"))

    def _enabled_changed(self, checked: bool):
        if self._selected_key in self.assignments:
            self.assignments[self._selected_key]["enabled"] = checked

    def _clear_selected(self):
        if self._selected_key:
            self.sequence_edit.clear()

    def _save(self):
        entries = []
        used = {
            parse_hotkey_shortcut("Ctrl+Space"): self._text("atalho principal", "main shortcut"),
            parse_hotkey_shortcut("Ctrl+Q"): self._text("atalho de depuração", "debug shortcut"),
        }
        errors = []
        for assignment in self.assignments.values():
            shortcut = assignment.get("shortcut", "")
            parsed = parse_hotkey_shortcut(shortcut)
            if parsed is None:
                errors.append(self._text(f'{assignment["name"]}: combinação inválida.', f'{assignment["name"]}: invalid combination.'))
                continue
            if parsed in used:
                errors.append(self._text(f'{assignment["name"]}: combinação já usada por {used[parsed]}.', f'{assignment["name"]}: combination already used by {used[parsed]}.'))
                continue
            used[parsed] = assignment["name"]
            entries.append(dict(assignment))
        if errors:
            QtWidgets.QMessageBox.warning(self, self._text("Verifique os atalhos", "Check shortcuts"), "\n".join(errors))
            return
        premiere_shortcuts = load_premiere_shortcut_conflicts()
        premiere_collisions = []
        for assignment in entries:
            if not assignment.get("enabled", True):
                continue
            command_names = premiere_shortcuts.get(parse_hotkey_shortcut(assignment.get("shortcut", "")), ())
            if command_names:
                premiere_collisions.append(
                    f'{assignment["name"]} ({assignment["shortcut"]}) → {", ".join(command_names[:2])}'
                )
        if premiere_collisions:
            visible = premiere_collisions[:8]
            if len(premiere_collisions) > len(visible):
                visible.append(self._text(
                    f"…e mais {len(premiere_collisions) - len(visible)} conflito(s).",
                    f"…and {len(premiere_collisions) - len(visible)} more conflict(s).",
                ))
            message = self._text(
                "Estas combinações já estão atribuídas no perfil de teclado do Premiere:\n\n",
                "These combinations are already assigned in Premiere's keyboard profile:\n\n",
            ) + "\n".join(visible) + self._text(
                "\n\nAs duas ações podem disputar o mesmo atalho. Deseja salvar mesmo assim?",
                "\n\nBoth actions may compete for the same shortcut. Save anyway?",
            )
            answer = QtWidgets.QMessageBox.question(
                self,
                self._text("Conflito com o Premiere", "Premiere shortcut conflict"),
                message,
                QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Save:
                return
        save_custom_hotkey_entries(entries)
        listener = getattr(self.palette, "hotkey_listener", None)
        failures = listener.reload() if listener is not None else ()
        if failures:
            QtWidgets.QMessageBox.warning(self, "FX.palette", self._text(
                "Os atalhos foram salvos e recarregados, mas estas combinações não puderam ser registradas:\n",
                "Shortcuts were saved and reloaded, but these combinations could not be registered:\n",
            ) + "\n".join(failures))
        else:
            QtWidgets.QMessageBox.information(self, "FX.palette", self._text(
                "Atalhos salvos e ativados.",
                "Shortcuts saved and activated.",
            ))
        self.accept()


class QtAliasTargetPicker(QtWidgets.QDialog):
    def __init__(self, palette, current_query: str = "", parent=None):
        super().__init__(parent or palette.window)
        self.selected_query = ""
        self.catalog = build_alias_target_catalog(palette.loader)
        self.setWindowTitle("Selecionar alvo do alias" if CURRENT_LANGUAGE == "pt" else "Select alias target")
        self.resize(680, 520)
        self.setMinimumSize(520, 380)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Pesquisar efeitos, presets e ações…" if CURRENT_LANGUAGE == "pt" else "Search effects, presets, and actions…")
        self.search.textChanged.connect(self._populate)
        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.kind_filter = QtWidgets.QComboBox()
        filter_options = (
            ("Todos", "All", "all"),
            ("Ações do FX.palette", "FX.palette actions", "actions"),
            ("Labels", "Labels", "labels"),
            ("Efeitos de vídeo", "Video effects", "video_effects"),
            ("Efeitos de áudio", "Audio effects", "audio_effects"),
            ("Transições", "Transitions", "transitions"),
            ("Presets", "Presets", "presets"),
            ("Favoritos", "Favorites", "favorites"),
            ("Itens genéricos", "Generic items", "generic_items"),
        )
        for pt, en, key in filter_options:
            self.kind_filter.addItem(pt if CURRENT_LANGUAGE == "pt" else en, key)
        self.kind_filter.currentIndexChanged.connect(self._populate)
        search_row.addWidget(self.kind_filter)
        layout.addLayout(search_row)
        self.results = QtWidgets.QTreeWidget()
        self.results.setHeaderLabels(("Nome" if CURRENT_LANGUAGE == "pt" else "Name", "Categoria" if CURRENT_LANGUAGE == "pt" else "Category"))
        self.results.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.results.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.results.itemDoubleClicked.connect(lambda *_args: self._accept_selection())
        layout.addWidget(self.results, 1)
        self.result_count = QtWidgets.QLabel()
        layout.addWidget(self.result_count)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._populate()
        if current_query:
            for index in range(self.results.topLevelItemCount()):
                item = self.results.topLevelItem(index)
                if normalize_search_text(item.data(0, QtCore.Qt.ItemDataRole.UserRole)) == normalize_search_text(current_query):
                    self.results.setCurrentItem(item)
                    self.results.scrollToItem(item)
                    break

    def _populate(self):
        needle = normalize_search_text(self.search.text())
        selected_kind = str(self.kind_filter.currentData() or "all")
        self.results.clear()
        for target in self.catalog:
            if selected_kind != "all" and target.get("kind") != selected_kind:
                continue
            haystack = normalize_search_text(f'{target["name"]} {target["category"]}')
            if needle and needle not in haystack:
                continue
            item = QtWidgets.QTreeWidgetItem((target["name"], target["category"]))
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, target["query"])
            self.results.addTopLevelItem(item)
        count = self.results.topLevelItemCount()
        self.result_count.setText(f"{count} resultado(s)" if CURRENT_LANGUAGE == "pt" else f"{count} result(s)")
        if self.results.topLevelItemCount():
            self.results.setCurrentItem(self.results.topLevelItem(0))

    def _accept_selection(self):
        item = self.results.currentItem()
        if item is None:
            return
        self.selected_query = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        self.accept()


class QtSettingsCenter(QtWidgets.QDialog):
    def __init__(self, palette):
        super().__init__(palette.window)
        self.palette = palette
        self.setWindowTitle("Configurações do FX.palette" if CURRENT_LANGUAGE == "pt" else "FX.palette Settings")
        self.resize(720, 500)
        self.setMinimumSize(620, 420)
        self.setModal(False)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        self._build()
        self._refresh_diagnostics()

    def _text(self, pt: str, en: str) -> str:
        return pt if CURRENT_LANGUAGE == "pt" else en

    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        title = QtWidgets.QLabel(self._text("Configurações", "Settings"))
        title.setObjectName("settingsTitle")
        root.addWidget(title)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)

        general = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(general)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(14)
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.addItem("Português", "pt")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(CURRENT_LANGUAGE)))
        form.addRow(self._text("Idioma", "Language"), self.language_combo)
        self.animations_check = QtWidgets.QCheckBox(self._text("Usar animações da interface", "Use interface animations"))
        self.animations_check.setChecked(load_app_preferences()["animations"])
        form.addRow("", self.animations_check)
        self.reconstruct_easing_check = QtWidgets.QCheckBox(self._text(
            "Recriar curva de easing dos presets (mais fiel, mais lento)",
            "Reconstruct preset easing curves (more faithful, slower)"))
        self.reconstruct_easing_check.setChecked(load_app_preferences()["reconstructEasing"])
        form.addRow("", self.reconstruct_easing_check)
        self.startup_check = QtWidgets.QCheckBox(self._text("Iniciar com o Windows", "Start with Windows"))
        self.startup_check.setChecked(startup_registry_enabled())
        form.addRow("", self.startup_check)
        form.addRow(QtWidgets.QLabel(self._text(
            "O idioma é aplicado completamente na próxima inicialização.",
            "Language changes are fully applied on the next launch.",
        )))
        self.tabs.addTab(general, self._text("Geral", "General"))

        shortcuts = QtWidgets.QWidget()
        shortcut_layout = QtWidgets.QVBoxLayout(shortcuts)
        shortcut_layout.setContentsMargins(18, 18, 18, 18)
        shortcut_layout.addWidget(QtWidgets.QLabel(self._text(
            "Pesquise todas as ações e atribua combinações globais, incluindo F13–F24 para Stream Deck.",
            "Search every action and assign global combinations, including F13–F24 for Stream Deck.",
        )))
        edit_shortcuts = QtWidgets.QPushButton(self._text("Abrir editor de atalhos", "Open shortcut editor"))
        edit_shortcuts.clicked.connect(self.palette.show_hotkey_editor)
        shortcut_layout.addWidget(edit_shortcuts)
        shortcut_layout.addStretch(1)
        self.tabs.addTab(shortcuts, self._text("Atalhos", "Shortcuts"))

        aliases = QtWidgets.QWidget()
        self.aliases_tab = aliases
        aliases_layout = QtWidgets.QVBoxLayout(aliases)
        aliases_layout.setContentsMargins(18, 18, 18, 18)
        aliases_layout.addWidget(QtWidgets.QLabel(self._text(
            "Crie nomes curtos para efeitos, presets, Labels e ações. O alvo usa a mesma consulta da busca.",
            "Create short names for effects, presets, Labels, and actions. The target uses the same search query.",
        )))
        self.aliases_table = QtWidgets.QTableWidget(0, 2)
        self.aliases_table.setHorizontalHeaderLabels((self._text("Alias", "Alias"), self._text("Alvo", "Target")))
        self.aliases_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.aliases_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.aliases_table.verticalHeader().setVisible(False)
        self.aliases_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.aliases_table.itemDoubleClicked.connect(lambda item: self._select_alias_target(item.row()) if item.column() == 1 else None)
        for entry in load_alias_entries():
            self._append_alias_row(entry["alias"], entry["target"])
        aliases_layout.addWidget(self.aliases_table, 1)
        alias_buttons = QtWidgets.QHBoxLayout()
        add_alias = QtWidgets.QPushButton(self._text("Adicionar alias", "Add alias"))
        choose_target = QtWidgets.QPushButton(self._text("Selecionar alvo", "Select target"))
        remove_alias = QtWidgets.QPushButton(self._text("Remover selecionado", "Remove selected"))
        add_alias.clicked.connect(lambda: self._append_alias_row("", ""))
        choose_target.clicked.connect(self._select_current_alias_target)
        remove_alias.clicked.connect(self._remove_selected_aliases)
        alias_buttons.addWidget(add_alias)
        alias_buttons.addWidget(choose_target)
        alias_buttons.addWidget(remove_alias)
        alias_buttons.addStretch(1)
        aliases_layout.addLayout(alias_buttons)
        self.tabs.addTab(aliases, self._text("Aliases", "Aliases"))

        diagnostics = QtWidgets.QWidget()
        diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics)
        diagnostics_layout.setContentsMargins(18, 18, 18, 18)
        self.diagnostics_tree = QtWidgets.QTreeWidget()
        self.diagnostics_tree.setHeaderLabels((self._text("Componente", "Component"), self._text("Estado", "Status"), self._text("Detalhes", "Details"), self._text("Próximo passo", "Next step")))
        self.diagnostics_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.diagnostics_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.diagnostics_tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.diagnostics_tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.diagnostics_tree.itemSelectionChanged.connect(self._update_diagnostic_action)
        diagnostics_layout.addWidget(self.diagnostics_tree, 1)
        diagnostic_actions = QtWidgets.QHBoxLayout()
        self.diagnostic_action = QtWidgets.QPushButton(self._text("Executar ação recomendada", "Run recommended action"))
        self.diagnostic_action.setEnabled(False)
        self.diagnostic_action.clicked.connect(self._run_diagnostic_action)
        diagnostic_actions.addWidget(self.diagnostic_action)
        diagnostic_actions.addStretch(1)
        refresh = QtWidgets.QPushButton(self._text("Atualizar diagnóstico", "Refresh diagnostics"))
        refresh.clicked.connect(self._refresh_diagnostics)
        diagnostic_actions.addWidget(refresh)
        diagnostics_layout.addLayout(diagnostic_actions)
        self.tabs.addTab(diagnostics, self._text("Diagnóstico", "Diagnostics"))

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = QtWidgets.QPushButton(self._text("Cancelar", "Cancel"))
        cancel.clicked.connect(self.close)
        save = QtWidgets.QPushButton(self._text("Salvar", "Save"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)
        self.setStyleSheet("""
            QDialog, QWidget { background: #17171b; color: #f1f1f4; }
            QLabel#settingsTitle { font-size: 20px; font-weight: 700; padding-bottom: 8px; }
            QTabWidget::pane { border: 1px solid #383841; border-radius: 8px; background: #1d1d22; }
            QTabBar::tab { background: #24242b; padding: 9px 16px; margin-right: 3px; border-radius: 6px; }
            QTabBar::tab:selected { background: #3541a5; }
            QComboBox { background: #27272e; border: 1px solid #41414b; border-radius: 6px; padding: 7px; min-width: 240px; }
            QTreeWidget { background: #202026; border: 1px solid #383841; border-radius: 6px; }
            QTableWidget { background: #202026; border: 1px solid #383841; border-radius: 6px; gridline-color: #383841; }
            QTreeWidget::item { padding: 6px; }
            QHeaderView::section { background: #292930; padding: 7px; border: 0; }
            QPushButton { background: #2c2c34; border: 1px solid #44444f; border-radius: 6px; padding: 8px 13px; }
            QPushButton:hover { background: #393943; }
        """)

    def _append_alias_row(self, alias: str, target: str):
        row = self.aliases_table.rowCount()
        self.aliases_table.insertRow(row)
        self.aliases_table.setItem(row, 0, QtWidgets.QTableWidgetItem(alias))
        target_item = QtWidgets.QTableWidgetItem(target)
        target_item.setFlags(target_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.aliases_table.setItem(row, 1, target_item)
        if not alias:
            self.aliases_table.setCurrentCell(row, 0)
            self.aliases_table.editItem(self.aliases_table.item(row, 0))

    def _select_current_alias_target(self):
        row = self.aliases_table.currentRow()
        if row < 0:
            return
        self._select_alias_target(row)

    def _select_alias_target(self, row: int):
        target_item = self.aliases_table.item(row, 1)
        picker = QtAliasTargetPicker(self.palette, target_item.text() if target_item else "", self)
        if picker.exec() == QtWidgets.QDialog.DialogCode.Accepted and picker.selected_query:
            if target_item is None:
                target_item = QtWidgets.QTableWidgetItem()
                target_item.setFlags(target_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.aliases_table.setItem(row, 1, target_item)
            target_item.setText(picker.selected_query)

    def _remove_selected_aliases(self):
        rows = sorted({index.row() for index in self.aliases_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.aliases_table.removeRow(row)

    def _alias_entries(self) -> list[dict]:
        entries = []
        for row in range(self.aliases_table.rowCount()):
            alias_item = self.aliases_table.item(row, 0)
            target_item = self.aliases_table.item(row, 1)
            alias = alias_item.text().strip() if alias_item else ""
            target = target_item.text().strip() if target_item else ""
            if alias or target:
                entries.append({"alias": alias, "target": target})
        return entries

    def _diagnostic_row(self, diagnostic: dict):
        healthy = diagnostic["healthy"]
        status = self._text("OK", "OK") if healthy else self._text("Atenção", "Attention")
        recommendation = diagnostic["recommendation"]
        translations = {
            "Ready": "Pronto",
            "Start Premiere Pro, then refresh": "Inicie o Premiere Pro e atualize",
            "Refresh the catalog and inspect load issues": "Atualize o catálogo e verifique os erros",
            "Load the FX.palette UXP plugin in Premiere (UDT: Load & Watch)": "Carregue o plugin UXP FX.palette no Premiere (UDT: Load & Watch)",
            "Open Premiere and save a keyboard shortcut profile": "Abra o Premiere e salve um perfil de atalhos",
            "Review assignments and reload shortcuts": "Revise as atribuições e recarregue os atalhos",
            "The UXP plugin must be loaded in Premiere for actions to run": "O plugin UXP precisa estar carregado no Premiere para executar ações",
        }
        if CURRENT_LANGUAGE == "pt":
            recommendation = translations.get(recommendation, recommendation)
        item = QtWidgets.QTreeWidgetItem((diagnostic["name"], status, diagnostic["details"], recommendation))
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, diagnostic.get("action", ""))
        item.setForeground(1, QtGui.QColor("#78dc9b" if healthy else "#f5a623"))
        self.diagnostics_tree.addTopLevelItem(item)

    def _refresh_diagnostics(self):
        self.diagnostics_tree.clear()
        names_pt = {"Catalog": "Catálogo", "Shortcut profile": "Perfil de atalhos", "Global shortcuts": "Atalhos globais", "Premiere executor": "Executor do Premiere", "Preset catalog": "Catálogo de presets"}
        for diagnostic in collect_diagnostics(self.palette):
            if CURRENT_LANGUAGE == "pt":
                diagnostic = dict(diagnostic, name=names_pt.get(diagnostic["name"], diagnostic["name"]))
            self._diagnostic_row(diagnostic)
        self._update_diagnostic_action()

    def _update_diagnostic_action(self):
        item = self.diagnostics_tree.currentItem()
        action = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item is not None else ""
        self.diagnostic_action.setEnabled(bool(action))

    def _open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _run_diagnostic_action(self):
        item = self.diagnostics_tree.currentItem()
        action = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item is not None else ""
        if action == "refresh_catalog":
            self.palette.loader.request_refresh(self.palette.root, lambda _snapshot: self._refresh_diagnostics(), force=True)
        elif action == "edit_shortcuts":
            self.palette.show_hotkey_editor()
        elif action == "open_shortcut_folder":
            self._open_folder(Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "Adobe" / "Premiere Pro")
        elif action == "open_data_folder":
            self._open_folder(DATA_DIR)
        elif action == "start_premiere":
            QtWidgets.QMessageBox.information(self, "FX.palette", self._text(
                "Inicie o Adobe Premiere Pro e clique em Atualizar diagnóstico.",
                "Start Adobe Premiere Pro, then click Refresh diagnostics.",
            ))
        elif action == "import_preset_catalog":
            adapter = getattr(self.palette, "execution_adapter", None)
            if adapter is None or not hasattr(adapter, "import_prfpset_catalog"):
                return
            self.diagnostic_action.setEnabled(False)
            self.diagnostic_action.setText(self._text("Aguardando seleção do arquivo...", "Waiting for file selection..."))
            self.palette.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            result = adapter.import_prfpset_catalog()
            self.diagnostic_action.setText(self._text("Executar ação recomendada", "Run recommended action"))
            if result is None:
                QtWidgets.QMessageBox.warning(self, "FX.palette", self._text(
                    "Nenhum arquivo .prfpset foi importado.",
                    "No .prfpset file was imported.",
                ))
            else:
                QtWidgets.QMessageBox.information(self, "FX.palette", self._text(
                    "Catálogo de presets importado com sucesso.",
                    "Preset catalog imported successfully.",
                ))
            self._refresh_diagnostics()

    def _save(self):
        alias_entries = self._alias_entries()
        alias_errors = validate_alias_entries(alias_entries)
        if not alias_errors:
            for row, entry in enumerate(alias_entries, 1):
                if not resolve_action_query_items(self.palette.loader, entry["target"], limit=1, alias_entries=alias_entries):
                    alias_errors.append(f'Row {row}: target not found ({entry["target"]})')
        if alias_errors:
            translated = []
            for error in alias_errors:
                translated.append(error if CURRENT_LANGUAGE != "pt" else error.replace("Row", "Linha").replace("alias and target are required", "alias e alvo são obrigatórios").replace("duplicate alias", "alias duplicado").replace("alias cycle detected", "ciclo de aliases detectado").replace("target not found", "alvo não encontrado"))
            QtWidgets.QMessageBox.warning(self, self._text("Verifique os aliases", "Check aliases"), "\n".join(translated))
            self.tabs.setCurrentWidget(self.aliases_tab)
            return
        previous_language = CURRENT_LANGUAGE
        selected_language = str(self.language_combo.currentData())
        if selected_language != previous_language:
            set_language(selected_language)
        animations = self.animations_check.isChecked()
        reconstruct_easing = self.reconstruct_easing_check.isChecked()
        save_app_preferences(animations=animations, reconstruct_easing=reconstruct_easing)
        save_alias_entries(alias_entries)
        self.palette.animations_enabled = animations
        self.palette.reconstruct_easing_enabled = reconstruct_easing
        startup_ok = set_startup_registry_enabled(self.startup_check.isChecked())
        if not startup_ok and IS_WINDOWS:
            QtWidgets.QMessageBox.warning(self, "FX.palette", self._text(
                "As preferências foram salvas, mas não foi possível alterar a inicialização com o Windows.",
                "Preferences were saved, but Windows startup could not be changed.",
            ))
        elif selected_language != previous_language:
            QtWidgets.QMessageBox.information(self, "FX.palette", self._text(
                "Configurações salvas. Reinicie o FX.palette para aplicar o idioma em toda a interface.",
                "Settings saved. Restart FX.palette to apply the language throughout the interface.",
            ))
        self.accept()


class QtPaletteWindow(QtWidgets.QWidget):
    def __init__(self, palette):
        super().__init__(None)
        self.palette = palette
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def keyPressEvent(self, event):
        key = event.key()
        if key == QtCore.Qt.Key.Key_Escape:
            if getattr(self.palette, "_nest_inline_panel", None) is not None:
                self.palette._cancel_nest_options()
            else:
                self.palette.hide()
            return

        if key == QtCore.Qt.Key.Key_Down:
            self.palette._move_selection(1)
            return
        if key == QtCore.Qt.Key.Key_Up:
            self.palette._move_selection(-1)
            return
        super().keyPressEvent(event)


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

    def translate(self, key: str, **kwargs) -> str:
        return tr(key, **kwargs)


class QtEffectPalette:
    CATEGORY_TYPE_FILTERS = CATEGORY_TYPE_FILTERS

    def __init__(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        if APP_ICON_PNG.exists():
            self.app.setWindowIcon(QtGui.QIcon(str(APP_ICON_PNG)))
        self.root = QtRootAdapter(self.app)
        self.loader = EffectsLoader()
        self.view_model = PaletteViewModel(AppQueryServices(self.loader))
        self.execution_adapter = create_execution_adapter()
        self.apply_controller = ApplyController(self.execution_adapter, self.root)
        self.apply_controller.stateChanged.connect(self._on_apply_state_changed)
        self.animations_enabled = load_app_preferences()["animations"]
        self.reconstruct_easing_enabled = load_app_preferences()["reconstructEasing"]
        self.is_open = False
        self._search_job = None
        self._data_refresh_job = None
        self._watch_job = None
        self._data_observer = None
        self._premiere_monitor_job = None
        self._premiere_seen = False
        self._premiere_seen_since = None
        self._feedback_prompt_shown = False
        self._row_widgets: list[QtResultRowWidget] = []
        self._render_chunk_job = None
        self._render_generation = 0
        self._opacity_animation = None
        self._geometry_animation = None
        self._qt_middle_height = 0
        self.window_controller = None  # built in _build(), once the window exists
        self._open_requested_at = None
        self._apply_poll_job = None
        self._apply_close_job = None
        self._apply_started_at = None
        self._exiting = False
        self._build()
        self._start_file_watcher()
        self._start_premiere_monitor()

    def _build(self):
        self._load_qt_fonts()
        self.ui_font_family = self._choose_qt_font_family()
        self.window = QtPaletteWindow(self)
        self.window.setObjectName("paletteWindow")
        self.window.setFixedWidth(FIXED_SEARCH_WINDOW_WIDTH)

        root_layout = QtWidgets.QVBoxLayout(self.window)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.top_card = QtWidgets.QFrame()
        self.top_card.setObjectName("topCard")
        top_layout = QtWidgets.QVBoxLayout(self.top_card)
        top_layout.setContentsMargins(8, 8, 8, 8)
        top_layout.setSpacing(0)
        root_layout.addWidget(self.top_card)

        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(14, 0, 14, 0)
        search_row.setSpacing(8)
        self.prompt = QtWidgets.QLabel(">")
        self.prompt.setObjectName("prompt")
        self.entry = QtWidgets.QLineEdit()
        self.entry.setObjectName("searchEntry")
        self.entry.setFrame(False)
        self.entry.textChanged.connect(self._on_search_change)
        self.entry.returnPressed.connect(self._apply_selected)
        self.refresh_btn = QtWidgets.QPushButton(get_reload_icon_glyph())
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.clicked.connect(self._manual_refresh)
        search_row.addWidget(self.prompt)
        search_row.addWidget(self.entry, 1)
        search_row.addWidget(self.refresh_btn)
        top_layout.addLayout(search_row)

        divider = QtWidgets.QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        top_layout.addWidget(divider)

        filters_row = QtWidgets.QHBoxLayout()
        filters_row.setContentsMargins(14, 7, 14, 6)
        filters_row.setSpacing(8)
        self.category_buttons: dict[str, QtWidgets.QPushButton] = {}
        for category in ["Todos", "Video", "Audio", "Presets", "Projeto", "Favoritos"]:
            button = QtWidgets.QPushButton(tr_category(category))
            button.setObjectName("categoryButton")
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, c=category: self._on_category_click(c))
            self.category_buttons[category] = button
            filters_row.addWidget(button)
        filters_row.addStretch(1)
        self.conn_dot = QtWidgets.QLabel()
        self.conn_dot.setObjectName("connectionDot")
        self.conn_dot.setFixedSize(10, 10)
        filters_row.addWidget(self.conn_dot)
        top_layout.addLayout(filters_row)

        self.body_card = QtWidgets.QFrame()
        self.body_card.setObjectName("bodyCard")
        body_layout = QtWidgets.QVBoxLayout(self.body_card)
        self.body_layout = body_layout
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(0)
        root_layout.addWidget(self.body_card)

        self.empty_label = QtWidgets.QLabel(tr("no_results_helper"))
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.results_list = QtWidgets.QListWidget()
        self.results_list.setObjectName("resultsList")
        self.results_list.setUniformItemSizes(False)
        self.results_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.currentRowChanged.connect(self._sync_row_selection)
        self.results_list.itemDoubleClicked.connect(lambda _item: self._apply_selected())
        self.results_list.itemActivated.connect(lambda _item: self._apply_selected())
        body_layout.addWidget(self.empty_label)
        body_layout.addWidget(self.results_list)

        self.footer = QtWidgets.QFrame()
        self.footer.setObjectName("footer")
        footer_layout = QtWidgets.QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(16, 9, 16, 9)
        self.help_label = QtWidgets.QLabel(self.view_model.footerHint)
        self.help_label.setObjectName("helpLabel")
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.apply_progress = QtWidgets.QProgressBar()
        self.apply_progress.setObjectName("applyProgress")
        self.apply_progress.setRange(0, 0)
        self.apply_progress.setTextVisible(False)
        self.apply_progress.setFixedSize(72, 4)
        self.apply_progress.hide()
        footer_layout.addWidget(self.help_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.apply_progress)
        footer_layout.addWidget(self.status_label)
        body_layout.addWidget(self.footer)

        self._apply_styles()
        self._update_category_buttons()
        self.view_model.set_connection_state(self.loader.snapshot.connection_state)
        self._update_connection_indicator()
        self._set_idle_state()
        self.window.layout().activate()
        self._resize_to_content()
        self._idle_window_height = self.window.height()
        self.window_controller = PaletteWindowController(
            WidgetWindowAdapter(self.window, self.entry),
            self.root,
            _NativeWindowCalls(),
            on_focus_acquired=self._report_focus_acquired,
        )
        self.window.hide()

    def _load_qt_fonts(self):
        for font_path in (GOOGLE_SANS_FLEX_REGULAR, GOOGLE_SANS_FLEX_MEDIUM):
            if font_path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(font_path))

    def _choose_qt_font_family(self) -> str:
        families = set(QtGui.QFontDatabase.families())
        return "Google Sans Flex" if "Google Sans Flex" in families else "Segoe UI"

    def _apply_styles(self):
        self.window.setStyleSheet(
            f"""
            QWidget {{
                font-family: "{self.ui_font_family}";
                color: {TEXT};
            }}
            QFrame#topCard {{
                background: {BG2};
                border: 1px solid {BORDER};
                border-radius: 16px;
            }}
            QFrame#bodyCard {{
                background: {BG};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QFrame#divider {{
                background: {BORDER};
                border: 0;
            }}
            QLabel#prompt {{
                color: {ACCENT};
                font-size: 15px;
            }}
            QLineEdit#searchEntry {{
                background: {BG2};
                color: {TEXT};
                selection-background-color: {ACCENT};
                border: 0;
                padding: 13px 0;
                font-size: {SEARCH_FONT_SIZE}px;
            }}
            QPushButton#refreshButton {{
                color: {TEXT_MUTED};
                background: {REFRESH_BUTTON_BG};
                border: 1px solid {REFRESH_BUTTON_BORDER};
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton#refreshButton:hover {{
                color: {ACCENT};
                background: {REFRESH_BUTTON_HOVER_BG};
                border-color: {blend_colors(REFRESH_BUTTON_BORDER, ACCENT, 0.42)};
            }}
            QListWidget#resultsList {{
                background: {BG};
                border: 0;
                outline: 0;
                padding: 6px;
            }}
            QListWidget#resultsList::item {{
                border: 0;
                padding: 0;
                margin: 0 0 4px 0;
            }}
            QLabel#emptyLabel {{
                color: {TEXT_MUTED};
                background: {BG};
                padding: 24px;
            }}
            QFrame#footer {{
                background: {BG};
                border-top: 1px solid {ROW_BORDER};
            }}
            QLabel#helpLabel {{
                color: {TEXT_MUTED};
                font-size: 11px;
            }}
            QLabel#statusLabel {{
                color: {ACCENT};
                font-size: 11px;
                font-weight: 700;
            }}
            QProgressBar#applyProgress {{
                background: {ROW_BORDER};
                border: 0;
                border-radius: 2px;
            }}
            QProgressBar#applyProgress::chunk {{
                background: {ACCENT};
                border-radius: 2px;
            }}
            QScrollBar:vertical {{
                background: {BG};
                width: 7px;
            }}
            QScrollBar::handle:vertical {{
                background: {TEXT_MUTED};
                border-radius: 3px;
                min-height: 32px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        )

    def _style_category_button(self, button: QtWidgets.QPushButton, category: str, active: bool):
        tokens = get_pill_visual_tokens(category, active=active)
        button.setStyleSheet(
            f"""
            QPushButton#categoryButton {{
                background: {tokens["bg"]};
                color: {tokens["fg"]};
                border: 1px solid {tokens["border"]};
                border-radius: 6px;
                padding: 5px 13px;
                min-width: 42px;
                font-size: 11px;
                font-weight: 700;
            }}
            """
        )

    def _update_category_buttons(self):
        for category, button in self.category_buttons.items():
            active_category = self.view_model.activeCategory
            active = (category == "Todos" and active_category is None) or category == active_category
            self._style_category_button(button, category, active)

    def _update_connection_indicator(self):
        tokens = get_connection_state_tokens(self.view_model.connectionState)
        self.conn_dot.setStyleSheet(
            f"background: {tokens['fill']}; border: 1px solid {tokens['outline']}; border-radius: 5px;"
        )

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
            self._set_idle_state()
        elif state == "message":
            self._set_message_state()
        else:
            self._populate_results()
            self._set_results_state()
        self.status_label.setText(self.view_model.statusText)
        self._resize_to_content()

    def _cancel_render_chunk(self):
        self._render_generation += 1
        if self._render_chunk_job is not None:
            self.root.after_cancel(self._render_chunk_job)
            self._render_chunk_job = None

    def _set_idle_state(self):
        self._qt_middle_height = 0
        self.results_list.setFixedHeight(0)
        self.results_list.hide()
        self.empty_label.setFixedHeight(0)
        self.empty_label.hide()
        self.body_card.show()

    def _set_message_state(self):
        self._qt_middle_height = 84
        self.results_list.setFixedHeight(0)
        self.results_list.hide()
        self.empty_label.setFixedHeight(84)
        self.empty_label.show()
        self.body_card.show()

    def _set_results_state(self):
        self._qt_middle_height = RESULTS_EXPANDED_HEIGHT
        self.empty_label.setFixedHeight(0)
        self.empty_label.hide()
        self.results_list.setFixedHeight(RESULTS_EXPANDED_HEIGHT)
        self.results_list.show()
        self.body_card.show()

    def _populate_results(self):
        self._cancel_render_chunk()
        self.results_list.setUpdatesEnabled(False)
        self.results_list.clear()
        self._row_widgets = []
        self.results_list.setUpdatesEnabled(True)
        generation = self._render_generation
        self._append_result_rows(0, QT_INITIAL_RENDER_ROWS, generation)

    def _append_result_rows(self, start: int, count: int, generation: int):
        if generation != self._render_generation:
            return
        end = min(self.view_model.results.rowCount(), start + count)
        self.results_list.setUpdatesEnabled(False)
        try:
            for index in range(start, end):
                model = self.view_model.results.row_at(index)
                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.ItemDataRole.UserRole, model.payload)
                item.setSizeHint(QtCore.QSize(FIXED_SEARCH_WINDOW_WIDTH - 34, PaletteLayoutMetrics().row_height + 4))
                self.results_list.addItem(item)
                row_widget = QtResultRowWidget(model)
                self.results_list.setItemWidget(item, row_widget)
                self._row_widgets.append(row_widget)
                if self.animations_enabled and start < 12:
                    row_widget.animate_in(min(90, len(self._row_widgets) * 10))
            if start == 0 and self._row_widgets:
                self.results_list.setCurrentRow(0)
                self._sync_row_selection(0)
        finally:
            self.results_list.setUpdatesEnabled(True)
        if end < self.view_model.results.rowCount():
            self._render_chunk_job = self.root.after(
                1,
                lambda next_start=end, gen=generation: self._append_result_rows(next_start, QT_RENDER_CHUNK_ROWS, gen),
            )
        else:
            self._render_chunk_job = None

    def _sync_row_selection(self, selected_row: int):
        self.view_model.set_selected_index(selected_row)
        for index, row in enumerate(self._row_widgets):
            row.apply_state(selected=index == selected_row)
            if index == selected_row and row._fade_animation is None:
                row.animate_selection()

    def _move_selection(self, direction: int):
        self.view_model.move_selection(direction)
        self.results_list.setCurrentRow(self.view_model.selectedIndex)

    def _selected_payload(self):
        return self.view_model.selected_payload()

    def _apply_action_label(self, effect: dict) -> str:
        effect_type = effect.get("type")
        if effect_type in {"project_item", "generic_item", "favorite_item"}:
            return tr("action_inserting")
        if effect_type in {"transition_video", "transition_audio"}:
            return tr("action_applying_transition")
        if effect_type == "preset":
            return tr("action_applying_preset")
        if effect_type == "timeline_action":
            return tr("action_executing")
        return tr("action_applying")

    def _on_apply_state_changed(self):
        """Widgets follow the controller; the controller never touches widgets."""
        state = self.apply_controller.state
        locked = state in {"busy", "success"}
        self.apply_progress.setVisible(state == "busy")
        self.entry.setEnabled(not locked)
        self.refresh_btn.setEnabled(not locked)
        for button in self.category_buttons.values():
            button.setEnabled(not locked)

    def _set_apply_busy(self, busy: bool, label: str = ""):
        if not busy:
            self.apply_controller.reset()
        if label:
            self.status_label.setText(label)

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
                "name": effect_name,
                "status": status,
                "elapsed_ms": elapsed_ms,
            })
            self._apply_close_job = self.root.after(
                APPLY_SUCCESS_CLOSE_DELAY_MS,
                self._finish_successful_apply,
            )
            return

        self.status_label.setText(format_apply_failure(status))
        beta_report.write_event("apply_failed", {
            "name": effect_name,
            "status": status,
            "elapsed_ms": elapsed_ms,
        })
        self.entry.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)

    def _finish_successful_apply(self):
        self._apply_close_job = None
        self._set_apply_busy(False)
        self.hide()

    def _poll_apply_status(self):
        self._apply_poll_job = None
        if not self.apply_controller.busy:
            return
        status = self.execution_adapter.poll_status(self.apply_controller.command_timestamp)
        if status and status != self.apply_controller.lastStatus:
            self.apply_controller.set_last_status(status)
            beta_report.write_event("apply_status_changed", {
                "name": self.apply_controller.activeEffect.get("name", ""),
                "status": status,
            })
        if status and self.execution_adapter.is_terminal(status):
            self._complete_apply(status)
            return
        if self._apply_started_at is not None:
            elapsed_ms = (time.perf_counter() - self._apply_started_at) * 1000.0
            if elapsed_ms >= apply_status_timeout_ms(self.apply_controller.activeEffect):
                effect_name = self.apply_controller.activeEffect.get("name", "")
                # reset() re-enables the widgets through _on_apply_state_changed.
                self.apply_controller.reset()
                self.apply_progress.hide()
                self.status_label.setText(tr("status_no_response"))
                beta_report.write_event("apply_timeout", {
                    "name": effect_name,
                    "elapsed_ms": round(elapsed_ms, 2),
                })
                self.entry.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
                return
        self._apply_poll_job = self.root.after(APPLY_STATUS_POLL_MS, self._poll_apply_status)

    def _begin_apply(self, effect: dict):
        action = self._apply_action_label(effect)
        name = effect.get("name", "")
        self.apply_controller.begin(effect)
        self._set_apply_busy(True, f"{action}: {name}")
        self.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if effect.get("type") in {"project_item", "generic_item", "favorite_item"}:
            self._begin_apply_with_track_check(effect)
            return
        self._dispatch_apply(effect)

    def _begin_apply_with_track_check(self, effect: dict):
        """Pre-flight a free Timeline track; drive Premiere's own "Add Tracks..." dialog first if needed."""
        adapter = getattr(self, "execution_adapter", None)
        if adapter is None or not hasattr(adapter, "check_track_availability"):
            self._dispatch_apply(effect)
            return
        generic_key = str(effect.get("genericKey") or "")
        check_timestamp = adapter.check_track_availability(generic_key)
        deadline = time.monotonic() + 2.0

        def poll():
            status = adapter.poll_status(check_timestamp)
            if status is None:
                if time.monotonic() >= deadline:
                    self._dispatch_apply(effect)
                    return
                self.root.after(APPLY_STATUS_POLL_MS, poll)
                return
            data = adapter.last_response_data(check_timestamp) or {}
            need_video = bool(data.get("needsVideo")) and not bool(data.get("videoAvailable", True))
            need_audio = bool(data.get("needsAudio")) and not bool(data.get("audioAvailable", True))
            premiere_hwnd = self.window_controller.previous_handle
            if (need_video or need_audio) and premiere_hwnd:
                self.status_label.setText(tr("status_creating_track"))
                # The palette still holds OS focus here - force it onto Premiere first so the
                # native shortcut actually reaches it, with a short delay for Windows to
                # complete the switch before the shortcut is sent.
                activate_window_handle_native(premiere_hwnd)
                self.root.after(80, lambda: schedule_native_add_tracks_dialog(
                    self, premiere_hwnd, need_video=need_video, need_audio=need_audio,
                    on_done=lambda _confirmed: self._dispatch_apply(effect),
                ))
            else:
                self._dispatch_apply(effect)

        self.root.after(APPLY_STATUS_POLL_MS, poll)

    def _dispatch_apply(self, effect: dict):
        name = effect.get("name", "")
        try:
            command_timestamp = execute_effect_through_adapter(self, effect)
        except Exception as exc:
            # reset() re-enables the widgets through _on_apply_state_changed.
            self.apply_controller.reset()
            self.apply_progress.hide()
            self.status_label.setText(tr("status_send_failed"))
            beta_report.log_exception("Apply command failed", exc)
            return
        self.apply_controller.set_command_timestamp(command_timestamp)
        self._apply_started_at = time.perf_counter()
        beta_report.write_event("apply_started", {
            "name": name,
            "type": effect.get("type", ""),
            "timestamp": command_timestamp,
        })
        self._apply_poll_job = self.root.after(
            APPLY_STATUS_INITIAL_DELAY_MS,
            self._poll_apply_status,
        )

    def _show_nest_options(self, effect: dict) -> None:
        self._close_nest_options(restore=False)
        self._pending_nest_effect = dict(effect)
        self.entry.setEnabled(False)
        self.results_list.hide()
        self.empty_label.hide()

        panel = QtWidgets.QFrame()
        panel.setObjectName("nestInlinePanel")
        panel.setFixedHeight(170)
        panel.setStyleSheet(
            f"""
            QFrame#nestInlinePanel {{ background: {BG}; border: 0; }}
            QLabel#nestHeading {{ color: {TEXT}; font-size: 15px; font-weight: 700; }}
            QLabel#nestFieldLabel {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 700; }}
            QLineEdit#nestName {{
                background: {BG2}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 7px; padding: 8px 10px; selection-background-color: {ACCENT};
            }}
            QPushButton {{
                background: {BG2}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 7px; padding: 7px 13px;
            }}
            QPushButton#nestConfirm {{
                background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 700;
            }}
            """
        )
        self._nest_inline_panel = panel
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(7)

        heading = QtWidgets.QLabel(tr("nest_dialog_question"))
        heading.setObjectName("nestHeading")
        layout.addWidget(heading)

        name_label = QtWidgets.QLabel(tr("nest_name_label"))
        name_label.setObjectName("nestFieldLabel")
        layout.addWidget(name_label)
        name_entry = QtWidgets.QLineEdit()
        name_entry.setObjectName("nestName")
        name_entry.setPlaceholderText(tr("nest_name_hint"))
        name_entry.returnPressed.connect(self._confirm_nest_options)
        self._nest_inline_name_entry = name_entry
        layout.addWidget(name_entry)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QtWidgets.QPushButton(tr("nest_cancel"))
        confirm_button = QtWidgets.QPushButton(tr("nest_confirm"))
        confirm_button.setObjectName("nestConfirm")
        cancel_button.clicked.connect(self._cancel_nest_options)
        confirm_button.clicked.connect(self._confirm_nest_options)
        actions.addWidget(cancel_button)
        actions.addWidget(confirm_button)
        layout.addLayout(actions)

        footer_index = self.body_layout.indexOf(self.footer)
        self.body_layout.insertWidget(footer_index, panel)
        self._qt_middle_height = 170
        self.body_card.show()
        self.help_label.setText(tr("nest_footer_hint"))
        self.status_label.setText("")
        self._resize_to_content()
        name_entry.setFocus()

    def _close_nest_options(self, *, restore: bool) -> None:
        panel = getattr(self, "_nest_inline_panel", None)
        if panel is not None:
            self.body_layout.removeWidget(panel)
            panel.deleteLater()
        self._nest_inline_panel = None
        self._pending_nest_effect = None
        self.entry.setEnabled(True)
        self.help_label.setText(tr("footer_hint"))
        if restore and self.is_open:
            self._refresh_list()
            self.entry.setFocus()

    def _cancel_nest_options(self) -> None:
        self._close_nest_options(restore=True)
        self.status_label.setText(tr("status_apply_cancelled"))

    def _confirm_nest_options(self) -> None:
        effect = getattr(self, "_pending_nest_effect", None)
        if not effect:
            return
        effect = dict(effect)
        effect.update({
            "nestMode": resolve_nest_mode("auto", self.execution_adapter),
            "nestName": self._nest_inline_name_entry.text().strip(),
            "nestBin": DEFAULT_NEST_BIN,
        })
        self._close_nest_options(restore=True)
        beta_report.write_event("nest_mode_resolved", {
            "requested": "auto",
            "resolved": effect["nestMode"],
        })
        if self._execute_timeline_action(effect):
            return
        self._begin_apply(effect)

    def _execute_timeline_action(self, effect: dict) -> bool:
        if effect.get("action") != "nest":
            return False
        if effect.get("nestMode") != "premiere":
            return False
        shortcut, shortcut_file = find_premiere_command_shortcut("cmd.clip.nestify")
        if shortcut is None:
            self.status_label.setText(tr("status_shortcut_unavailable"))
            return True
        premiere_hwnd = self.window_controller.previous_handle
        if not premiere_hwnd:
            self.status_label.setText(tr("status_premiere_window_unavailable"))
            return True

        adapter = self.execution_adapter
        nest_name = str(effect.get("nestName", "")).strip()
        if not nest_name and hasattr(adapter, "next_nest_codename"):
            # Blank name = CEP's FXN-NNN codename, typed into Premiere's own dialog, exactly like
            # the API path resolves it before timeline.createNest.
            nest_name = adapter.next_nest_codename() or ""
        bin_name = str(effect.get("nestBin") or DEFAULT_NEST_BIN)
        baseline_guids = adapter.snapshot_sequence_guids() if hasattr(adapter, "snapshot_sequence_guids") else None

        beta_report.write_event("timeline_action_started", {
            "action": "nest",
            "shortcut_vk": shortcut.vk,
            "shortcut_ctrl": shortcut.ctrl,
            "shortcut_alt": shortcut.alt,
            "shortcut_shift": shortcut.shift,
            "shortcut_file": str(shortcut_file or ""),
            "nest_name": nest_name,
            "baseline_sequences": None if baseline_guids is None else len(baseline_guids),
        })
        self.hide()

        def on_dialog_confirmed():
            if baseline_guids is None:
                beta_report.write_event("native_nest_organize_unavailable", {"reason": "no_baseline"})
                return
            schedule_native_nest_organization(self, baseline_guids, nest_name, bin_name)

        def focus_then_send():
            activate_window_handle_native(premiere_hwnd)

            def dispatch():
                sent = send_native_shortcut(shortcut)
                beta_report.write_event("timeline_action_dispatched", {"action": "nest", "sent": sent})
                if sent:
                    record_successful_action(effect, confirmed_by="native_dispatch")
                    schedule_native_nest_dialog_confirmation(self, premiere_hwnd, nest_name, on_confirmed=on_dialog_confirmed)

            dispatch_when_window_foreground(self, premiere_hwnd, dispatch, action="nest")

        self.root.after(CLOSE_ANIMATION_MS + 40, focus_then_send)
        return True

    def _execute_label_action(self, effect: dict) -> bool:
        effect_type = effect.get("type")
        if effect_type not in {"label_color", "label_group_action"}:
            return False
        label_index = int(effect.get("labelIndex", 0)) if effect_type == "label_color" else None
        command_name = f"cmd.edit.label.{label_index}" if label_index is not None else "cmd.edit.labelgroup"
        shortcut, shortcut_file = find_premiere_command_shortcut(command_name)
        if shortcut is None:
            self.status_label.setText(tr("status_label_shortcut_unavailable"))
            return True
        premiere_hwnd = self.window_controller.previous_handle
        if not premiere_hwnd:
            self.status_label.setText(tr("status_premiere_window_unavailable"))
            return True

        beta_report.write_event("timeline_action_started", {
            "action": "set_label",
            "label_index": label_index,
            "shortcut_vk": shortcut.vk,
            "shortcut_file": str(shortcut_file or ""),
        })
        self.hide()

        def focus_then_send():
            activate_window_handle_native(premiere_hwnd)
            def dispatch():
                sent = send_native_shortcut(shortcut)
                beta_report.write_event("timeline_action_dispatched", {"action": "set_label", "label_index": label_index, "sent": sent})
                if sent:
                    record_successful_action(effect, confirmed_by="native_dispatch")
            dispatch_when_window_foreground(self, premiere_hwnd, dispatch, action="set_label")

        self.root.after(CLOSE_ANIMATION_MS + 40, focus_then_send)
        return True

    def _apply_selected(self):
        if self.apply_controller.state in {"busy", "success"}:
            return
        effect = self._selected_payload()
        if not effect:
            return
        if effect.get("type") == "history_action" and effect.get("action") == "repeat_last_action":
            if execute_configured_hotkey_action(self, {"type": "repeat_last_action"}):
                self.hide()
            return
        if effect.get("type") == "history_action" and effect.get("action") == "execute_recent":
            action = effect.get("productAction")
            if isinstance(action, dict) and execute_configured_hotkey_action(self, action):
                self.hide()
            return
        if effect.get("type") == "timeline_action" and effect.get("action") == "nest":
            self._show_nest_options(effect)
            return
        if self._execute_label_action(effect):
            return
        self._begin_apply(effect)

    def _manual_refresh(self):
        if self.apply_controller.state in {"busy", "success"}:
            return
        refresh = getattr(self.execution_adapter, "refresh_catalogs", None)
        if callable(refresh):
            refresh()
        self.status_label.setText(tr("status_requesting_refresh"))
        self.loader.request_refresh(self.root, self._on_loader_snapshot_ready, force=True)

    def _on_loader_snapshot_ready(self, snapshot: LoaderSnapshot):
        print(f"[Watcher] Lista atualizada - {snapshot.count} efeitos")
        self.view_model.set_connection_state(snapshot.connection_state)
        self._update_connection_indicator()
        if self.is_open:
            self._refresh_list()

    def schedule_data_refresh(self):
        if self._data_refresh_job is not None:
            self.root.after_cancel(self._data_refresh_job)
        self._data_refresh_job = self.root.after(RELOAD_COALESCE_MS, self._consume_data_refresh)

    def _consume_data_refresh(self):
        self._data_refresh_job = None
        self.loader.request_refresh(self.root, self._on_loader_snapshot_ready)

    def _start_file_watcher(self):
        self.loader.paths.data_dir.mkdir(parents=True, exist_ok=True)
        if HAS_WATCHDOG:
            handler = DataFilesChangeHandler(self)
            self._data_observer = Observer()
            for directory in watched_data_directories(self.loader.paths):
                self._data_observer.schedule(handler, str(directory), recursive=False)
            self._data_observer.start()
            return

        def watch():
            if self.loader.needs_reload():
                self.loader.request_refresh(self.root, self._on_loader_snapshot_ready)
            self._watch_job = self.root.after(int(WATCH_INTERVAL * 1000), watch)

        self._watch_job = self.root.after(int(WATCH_INTERVAL * 1000), watch)

    def _start_premiere_monitor(self):
        self._premiere_monitor_job = self.root.after(5000, self._monitor_premiere_shutdown)

    def _monitor_premiere_shutdown(self):
        self._premiere_monitor_job = None
        try:
            running = premiere_is_running()
            now = time.time()
            if running:
                if not self._premiere_seen:
                    self._premiere_seen_since = now
                    beta_report.write_event("premiere_detected")
                self._premiere_seen = True
            elif self._premiere_seen and not self._feedback_prompt_shown:
                open_seconds = now - (self._premiere_seen_since or now)
                self._premiere_seen = False
                self._premiere_seen_since = None
                if open_seconds >= BETA_FEEDBACK_MIN_OPEN_SECONDS:
                    self._feedback_prompt_shown = True
                    self._show_beta_feedback_dialog()
        except Exception as exc:
            beta_report.log_exception("Premiere monitor failed", exc)
        finally:
            if self.root.winfo_exists():
                self._premiere_monitor_job = self.root.after(PREMIERE_MONITOR_INTERVAL_MS, self._monitor_premiere_shutdown)

    def _show_beta_feedback_dialog(self):
        result = QtWidgets.QMessageBox.question(
            self.window,
            "FX.palette - Feedback da beta",
            "O Premiere parece ter sido fechado. Gerar um relatorio beta agora?",
        )
        if result != QtWidgets.QMessageBox.StandardButton.Yes:
            beta_report.write_event("feedback_prompt_skipped")
            return
        try:
            report_path = beta_report.build_report(None, DATA_DIR, APP_DIR, reason="feedback_after_premiere_closed")
            self.show_message("Relatorio salvo", "Envie este arquivo ao Paulo:\n" + str(report_path))
        except Exception as exc:
            beta_report.log_exception("Failed to save beta feedback", exc)
            self.show_message("Erro ao gerar relatorio", "Nao consegui salvar o relatorio beta.", error=True)

    def _anchor_window_to_pointer(self):
        screen = self.app.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
        pointer = QtGui.QCursor.pos()
        self.window_controller.anchor_to_pointer(
            pointer=(pointer.x(), pointer.y()),
            available=(available.x(), available.y(), available.width(), available.height()),
            position_chooser=choose_window_position_near_pointer,
        )

    def _resize_to_content(self):
        self.window.setMinimumSize(FIXED_SEARCH_WINDOW_WIDTH, 0)
        self.window.setMaximumSize(FIXED_SEARCH_WINDOW_WIDTH, 16777215)
        self.window.layout().activate()
        body_margins = self.body_card.layout().contentsMargins()
        body_extra = body_margins.top() + body_margins.bottom() + 2
        target_height = (
            self.top_card.sizeHint().height()
            + self.footer.sizeHint().height()
            + self._qt_middle_height
            + body_extra
        )
        if self.is_open and self.window.isVisible() and self.window.height() != target_height:
            self._animate_window_height(target_height)
        else:
            self.window.setFixedSize(FIXED_SEARCH_WINDOW_WIDTH, target_height)

    def _animate_window_height(self, target_height: int):
        if not self.animations_enabled:
            self.window.setFixedSize(FIXED_SEARCH_WINDOW_WIDTH, target_height)
            return
        if self._geometry_animation is not None:
            self._geometry_animation.stop()
        start = self.window.geometry()
        end = QtCore.QRect(start.x(), start.y(), FIXED_SEARCH_WINDOW_WIDTH, target_height)
        self.window.setMinimumSize(FIXED_SEARCH_WINDOW_WIDTH, 0)
        self.window.setMaximumSize(FIXED_SEARCH_WINDOW_WIDTH, 16777215)
        animation = QtCore.QPropertyAnimation(self.window, b"geometry", self.window)
        animation.setDuration(190)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        def finish():
            self.window.setFixedSize(FIXED_SEARCH_WINDOW_WIDTH, target_height)
            self._geometry_animation = None

        animation.finished.connect(finish)
        animation.start()
        self._geometry_animation = animation

    def _window_hwnd(self) -> int | None:
        return self.window_controller.handle()

    def _report_focus_acquired(self, attempt: int):
        elapsed_ms = None
        if self._open_requested_at is not None:
            elapsed_ms = round((time.perf_counter() - self._open_requested_at) * 1000.0, 2)
        beta_report.write_event("palette_focus_acquired", {
            "attempt": attempt,
            "elapsed_ms": elapsed_ms,
        })
        if self._open_requested_at is not None:
            beta_report.write_event("palette_open_latency", {
                "elapsed_ms": elapsed_ms,
                "focus_attempt": attempt,
            })

    def _remember_previous_focus(self):
        self.window_controller.remember_previous_focus()

    def _restore_previous_focus(self):
        self.window_controller.restore_previous_focus()

    def _cancel_focus_attempts(self):
        self.window_controller.cancel_focus_attempts()

    def _force_focus_attempt(self, attempt: int = 0, max_attempts: int = OPEN_FOCUS_ATTEMPTS):
        self.window_controller.is_open = self.is_open
        self.window_controller.begin_focus_attempts(max_attempts)

    def show(self, invoked_at: float | None = None):
        if self.is_open:
            self.window.raise_()
            self.window.activateWindow()
            self._force_focus_attempt()
            return
        self._open_requested_at = invoked_at or time.perf_counter()
        beta_report.write_event("palette_open_requested")
        self._remember_previous_focus()
        self.is_open = True
        self.window_controller.is_open = True
        self.entry.blockSignals(True)
        self.entry.clear()
        self.entry.blockSignals(False)
        # Resets the category and the result set in one go; _refresh_list() below recomputes.
        self.view_model.select_category("Todos")
        self._update_category_buttons()
        self._cancel_render_chunk()
        self.results_list.clear()
        self.status_label.setText("")
        self._set_idle_state()
        self.window.setFixedSize(FIXED_SEARCH_WINDOW_WIDTH, self._idle_window_height)
        self._anchor_window_to_pointer()
        self.window.setWindowOpacity(0.92)
        self.window.show()
        self._refresh_list()
        apply_windows_11_window_effects(self._window_hwnd())
        target_geometry = self.window.geometry()
        self.window.setGeometry(target_geometry.translated(0, 10))
        self._force_focus_attempt()
        self._animate_window_opacity(1.0, OPEN_ANIMATION_MS, ease_out_expo)
        self._animate_window_geometry(target_geometry, OPEN_ANIMATION_MS, opening=True)

    def hide(self):
        if self.apply_controller.busy:
            return
        if not self.is_open:
            return
        if getattr(self, "_nest_inline_panel", None) is not None:
            self._close_nest_options(restore=False)
        self.is_open = False
        # The retry loop reads is_open from the controller; a stale True would keep it going.
        self.window_controller.is_open = False
        self._cancel_focus_attempts()

        def finish():
            self.window.hide()
            self.window.setWindowOpacity(1.0)
            self._restore_previous_focus()

        self._animate_window_opacity(0.0, CLOSE_ANIMATION_MS, ease_in_expo, finish)
        self._animate_window_geometry(self.window.geometry().translated(0, 7), CLOSE_ANIMATION_MS, opening=False)

    def _animate_window_geometry(self, target: QtCore.QRect, duration_ms: int, *, opening: bool):
        if not self.animations_enabled:
            self.window.setGeometry(target)
            return
        if self._geometry_animation is not None:
            self._geometry_animation.stop()
        animation = QtCore.QPropertyAnimation(self.window, b"geometry", self.window)
        animation.setDuration(duration_ms)
        animation.setStartValue(self.window.geometry())
        animation.setEndValue(target)
        animation.setEasingCurve(
            QtCore.QEasingCurve.Type.OutCubic if opening else QtCore.QEasingCurve.Type.InCubic
        )
        animation.finished.connect(lambda: setattr(self, "_geometry_animation", None))
        animation.start()
        self._geometry_animation = animation

    def _animate_window_opacity(self, target: float, duration_ms: int, easing, on_complete=None):
        if not self.animations_enabled:
            self.window.setWindowOpacity(target)
            if on_complete is not None:
                on_complete()
            return
        start = self.window.windowOpacity()
        animation = QtCore.QVariantAnimation(self.window)
        animation.setDuration(duration_ms)
        animation.setStartValue(start)
        animation.setEndValue(target)
        animation.valueChanged.connect(lambda value: self.window.setWindowOpacity(float(value)))
        if on_complete is not None:
            animation.finished.connect(on_complete)
        animation.finished.connect(animation.deleteLater)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutExpo if easing is ease_out_expo else QtCore.QEasingCurve.Type.InExpo)
        animation.start(QtCore.QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        self._opacity_animation = animation

    def toggle(self, invoked_at: float | None = None):
        self.hide() if self.is_open else self.show(invoked_at=invoked_at)

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        self.root.after(0, self.root.destroy)

    def show_hotkey_editor(self):
        editor = getattr(self, "_hotkey_editor", None)
        if editor is None or not editor.isVisible():
            editor = QtHotkeyCatalogEditor(self)
            editor.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            editor.destroyed.connect(lambda: setattr(self, "_hotkey_editor", None))
            self._hotkey_editor = editor
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def show_settings_center(self):
        center = getattr(self, "_settings_center", None)
        if center is None or not center.isVisible():
            center = QtSettingsCenter(self)
            center.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            center.destroyed.connect(lambda: setattr(self, "_settings_center", None))
            self._settings_center = center
        center.show()
        center.raise_()
        center.activateWindow()

    def show_message(self, title: str, text: str, *, error: bool = False):
        box = QtWidgets.QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical if error else QtWidgets.QMessageBox.Icon.Information)
        box.exec()

    def shutdown(self):
        beta_report.write_event("session_shutdown")
        if self.window_controller is not None:
            self.window_controller.cancel_focus_attempts()
        for job_name in (
            "_watch_job",
            "_data_refresh_job",
            "_search_job",
            "_premiere_monitor_job",
            "_render_chunk_job",
            "_apply_poll_job",
            "_apply_close_job",
        ):
            job = getattr(self, job_name, None)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, job_name, None)
        if self._data_observer is not None:
            try:
                self._data_observer.stop()
                self._data_observer.join(timeout=1.0)
            except Exception:
                pass
            self._data_observer = None

    def run(self):
        self.root.mainloop()


class QtDebugWindow:
    """Tray-reachable debug window: the companion's own log, live diagnostics, catalog refresh."""

    def __init__(self, palette: "QtEffectPalette"):
        self.palette = palette
        self.root = palette.root
        self.win = None
        self.is_open = False
        self._poll_job = None
        self._mode = "log"  # log | diagnostics

    def _build(self):
        self.win = QtWidgets.QWidget()
        self.win.setWindowTitle("FX.palette - Debug")
        self.win.setWindowFlags(self.win.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.win.resize(640, 380)
        layout = QtWidgets.QVBoxLayout(self.win)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        self.status_lbl = QtWidgets.QLabel("")
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        actions = QtWidgets.QHBoxLayout()
        for label, callback in (
            ("Log", self._show_log),
            ("Diagnostico", self._show_diagnostics),
            ("Atualizar catalogos", self._refresh_catalogs),
            ("Limpar log", self._clear_log),
            ("Gerar relatorio beta", self._create_beta_report),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            actions.addWidget(button)
        layout.addWidget(self.status_lbl)
        layout.addWidget(self.text, 1)
        layout.addLayout(actions)
        self.win.setStyleSheet(
            f"QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; }}"
            f"QPlainTextEdit {{ background: {BG2}; color: {TEXT_MUTED}; border: 1px solid {BORDER}; }}"
            f"QPushButton {{ background: {BG2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 10px; }}"
        )

    def _poll(self):
        if not self.is_open:
            return
        if self._mode == "log":
            self._render_log()
        self._poll_job = self.root.after(1000, self._poll)

    def _render_log(self):
        log_file = beta_report.APP_LOG_FILE
        try:
            if log_file.exists():
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                self.text.setPlainText("\n".join(lines[-200:]))
                self.status_lbl.setText(f"{len(lines)} linhas - {log_file}")
            else:
                self.text.setPlainText("Nenhum log gravado ainda.")
                self.status_lbl.setText(str(log_file))
        except Exception as exc:
            self.status_lbl.setText(f"erro: {exc}")

    def _show_log(self):
        self._mode = "log"
        self._render_log()

    def _show_diagnostics(self):
        self._mode = "diagnostics"
        try:
            rows = collect_diagnostics(self.palette)
            adapter = self.palette.execution_adapter.diagnostics()
        except Exception as exc:
            self.status_lbl.setText(f"erro: {exc}")
            return
        lines = [
            f"[{'OK' if row['healthy'] else '!!'}] {row['name']}: {row['details']} -> {row['recommendation']}"
            for row in rows
        ]
        lines += ["", "Adapter:", json.dumps(adapter, indent=2, ensure_ascii=False, default=str)]
        self.text.setPlainText("\n".join(lines))
        self.status_lbl.setText("diagnostico")

    def _refresh_catalogs(self):
        refresh = getattr(self.palette.execution_adapter, "refresh_catalogs", None)
        if callable(refresh) and refresh():
            self.status_lbl.setText("catalogos solicitados ao plugin")
        else:
            self.status_lbl.setText("plugin UXP nao conectado - recarregando apenas os arquivos locais")
        self.palette.loader.request_refresh(self.root, self.palette._on_loader_snapshot_ready, force=True)

    def _clear_log(self):
        try:
            if beta_report.APP_LOG_FILE.exists():
                beta_report.APP_LOG_FILE.write_text("", encoding="utf-8")
            self._show_log()
            self.status_lbl.setText("log limpo")
        except Exception as exc:
            self.status_lbl.setText(f"erro: {exc}")

    def _create_beta_report(self):
        try:
            report_path = beta_report.build_report(None, DATA_DIR, APP_DIR, reason="manual_debug_window")
            self.status_lbl.setText("relatorio beta salvo")
            QtWidgets.QMessageBox.information(self.win, "Relatorio beta salvo", "Envie este arquivo ao Paulo:\n" + str(report_path))
        except Exception as exc:
            beta_report.log_exception("Failed to create manual beta report", exc)
            self.status_lbl.setText(f"erro: {exc}")

    def show(self):
        if self.is_open:
            self.win.raise_()
            self.win.activateWindow()
            return
        self.is_open = True
        self._build()
        self.win.show()
        self._poll()

    def hide(self):
        if not self.is_open:
            return
        self.is_open = False
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self.win is not None:
            self.win.close()
            self.win = None

    def toggle(self):
        self.hide() if self.is_open else self.show()


class SystemTrayController:
    def __init__(self, palette: "QtEffectPalette", debug: QtDebugWindow):
        self.palette = palette
        self.debug = debug
        self.icon = None
        self._thread = None
        self.available = HAS_TRAY

    def _make_icon_image(self):
        if APP_ICON_PNG.exists():
            return Image.open(APP_ICON_PNG).convert("RGBA")
        return self._draw_fallback_icon_image()

    def _draw_fallback_icon_image(self):
        image = Image.new("RGBA", (64, 64), (15, 15, 17, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(26, 26, 31, 255), outline=(91, 107, 248, 255), width=3)
        draw.rectangle((20, 16, 44, 24), fill=(61, 214, 140, 255))
        draw.rectangle((20, 30, 44, 38), fill=(91, 107, 248, 255))
        draw.rectangle((20, 44, 36, 50), fill=(245, 166, 35, 255))
        return image

    def _run_on_ui(self, callback):
        try:
            if self.palette.root and self.palette.root.winfo_exists():
                self.palette.root.after(0, callback)
        except Exception as exc:
            beta_report.log_exception("System tray callback failed", exc)

    def _show_palette(self, icon=None, item=None):
        self._run_on_ui(self.palette.show)

    def _toggle_palette(self, icon=None, item=None):
        self._run_on_ui(self.palette.toggle)

    def _show_debug(self, icon=None, item=None):
        self._run_on_ui(self.debug.show)

    def _show_settings(self, icon=None, item=None):
        editor = getattr(self.palette, "show_settings_center", None)
        if callable(editor):
            self._run_on_ui(editor)
        else:
            self._edit_hotkeys(icon, item)

    def _edit_hotkeys(self, icon=None, item=None):
        def open_settings():
            editor = getattr(self.palette, "show_hotkey_editor", None)
            if callable(editor):
                editor()
                return
            data = _load_settings_data()
            if not isinstance(data.get("hotkeys"), list):
                data["hotkeys"] = []
                _save_settings_data(data)
            try:
                if IS_WINDOWS:
                    os.startfile(str(SETTINGS_FILE))
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(SETTINGS_FILE)])
                if self.icon is not None:
                    self.icon.notify(tr("tray_hotkeys_restart"), "FX.palette")
            except Exception as exc:
                beta_report.log_exception("Failed to open shortcut settings", exc)
        self._run_on_ui(open_settings)

    def _set_language(self, lang: str):
        if lang == CURRENT_LANGUAGE:
            return
        set_language(lang)
        if self.icon is not None:
            try:
                self.icon.notify(tr("tray_language_restart_body"), tr("tray_language_restart_title"))
            except Exception:
                pass

    def _generate_beta_report(self, icon=None, item=None):
        def create_report():
            try:
                report_path = beta_report.build_report(None, DATA_DIR, APP_DIR, reason="manual_system_tray")
                beta_report.write_event("tray_report_created", {"zip_path": str(report_path)})
                self.palette.show_message("Relatorio beta salvo", "Envie este arquivo ao Paulo:\n" + str(report_path))
            except Exception as exc:
                beta_report.log_exception("Failed to create tray beta report", exc)
                self.palette.show_message(
                    "Erro ao gerar relatorio",
                    "Nao consegui gerar o relatorio beta. Tente pela janela de debug.",
                    error=True,
                )

        self._run_on_ui(create_report)

    def _open_report_folder(self, icon=None, item=None):
        def open_folder():
            try:
                report_dir = beta_report.ensure_report_dir()
                if IS_WINDOWS:
                    os.startfile(str(report_dir))
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(report_dir)])
            except Exception as exc:
                beta_report.log_exception("Failed to open beta report folder", exc)

        self._run_on_ui(open_folder)

    def _quit(self, icon=None, item=None):
        def shutdown():
            beta_report.write_event("tray_quit_requested")
            try:
                self.stop()
            finally:
                self.palette.root.destroy()

        self._run_on_ui(shutdown)

    def start(self):
        if not self.available:
            beta_report.log_app("System tray unavailable: pystray/Pillow not installed", "WARN")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem(tr("tray_open_palette"), self._show_palette, default=True),
                pystray.MenuItem(tr("tray_toggle_palette"), self._toggle_palette),
                pystray.MenuItem(tr("tray_settings"), self._show_settings),
                pystray.MenuItem(tr("tray_debug_window"), self._show_debug),
                pystray.MenuItem(tr("tray_edit_hotkeys"), self._edit_hotkeys),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    tr("tray_language"),
                    pystray.Menu(
                        pystray.MenuItem(
                            tr("tray_language_en"),
                            lambda: self._set_language("en"),
                            checked=lambda item: CURRENT_LANGUAGE == "en",
                            radio=True,
                        ),
                        pystray.MenuItem(
                            tr("tray_language_pt"),
                            lambda: self._set_language("pt"),
                            checked=lambda item: CURRENT_LANGUAGE == "pt",
                            radio=True,
                        ),
                    ),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(tr("tray_generate_beta_report"), self._generate_beta_report),
                pystray.MenuItem(tr("tray_open_report_folder"), self._open_report_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(tr("tray_quit"), self._quit),
            )
            self.icon = pystray.Icon("FX.palette", self._make_icon_image(), "FX.palette", menu)
            self._thread = threading.Thread(target=self.icon.run, daemon=True)
            self._thread.start()
            beta_report.write_event("system_tray_started")
        except Exception as exc:
            self.available = False
            beta_report.log_exception("Failed to start system tray", exc)

    def stop(self):
        if self.icon is None:
            return
        try:
            self.icon.stop()
        except Exception:
            pass
        self.icon = None


# --- Global hotkey listener ------------------------------------------------------------

class HotkeyListener:
    def __init__(self, palette: "QtEffectPalette", debug: QtDebugWindow):
        self.palette = palette
        self.debug = debug
        self._specs = self._build_specs()
        self._specs_by_id = {spec.id: spec for spec in self._specs}
        self._thread = None
        self._thread_id = None
        self._ready_event = threading.Event()
        self._registered_hotkey_ids: set[int] = set()
        self._fallback_hotkeys = None
        self._stopping = False
        self._native_backend_unavailable = False
        self._reload_lock = threading.Lock()
        self._registration_failures: list[str] = []

    def _build_specs(self) -> list[HotkeySpec]:
        specs = [
            HotkeySpec(
                id=1,
                name="toggle_palette",
                modifiers=MOD_CONTROL | MOD_NOREPEAT,
                vk=VK_SPACE,
                requires_premiere_focus=True,
                callback_name="toggle_palette",
            ),
            HotkeySpec(
                id=3,
                name="quit",
                modifiers=MOD_CONTROL | MOD_NOREPEAT,
                vk=VK_Q,
                requires_premiere_focus=False,
                callback_name="quit",
            ),
        ]
        specs.extend(load_custom_hotkey_specs())
        if ENABLE_DEBUG_HOTKEY:
            specs.insert(
                1,
                HotkeySpec(
                    id=2,
                    name="toggle_debug",
                    modifiers=MOD_CONTROL | MOD_NOREPEAT,
                    vk=VK_D,
                    requires_premiere_focus=False,
                    callback_name="toggle_debug",
                ),
            )
        return specs

    def _dispatch_hotkey(self, spec: HotkeySpec, *, triggered_at: float | None = None, focus_verified: bool = False):
        beta_report.write_event("hotkey_triggered", {"name": spec.name})
        if spec.requires_premiere_focus and not focus_verified and not premiere_is_focused():
            beta_report.write_event("hotkey_ignored_focus", {"name": spec.name})
            return

        if spec.callback_name == "toggle_palette":
            try:
                self.palette.toggle(invoked_at=triggered_at)
            except TypeError:
                self.palette.toggle()
        elif spec.callback_name == "toggle_debug":
            self.debug.toggle()
        elif spec.callback_name == "quit":
            print("[App] Encerrando via Ctrl+Q...")
            self.palette.request_exit()
        elif spec.callback_name == "custom_action" and spec.action:
            try:
                executed = execute_configured_hotkey_action(self.palette, spec.action)
                beta_report.write_event("custom_hotkey_action", {"name": spec.name, "executed": executed})
            except Exception as exc:
                beta_report.log_exception("Custom hotkey action failed", exc)

    def _schedule_dispatch(self, spec: HotkeySpec, *, native_focus_checked: bool = False):
        try:
            if self.palette.root and self.palette.root.winfo_exists():
                triggered_at = time.perf_counter()
                if spec.requires_premiere_focus and native_focus_checked and not premiere_is_focused():
                    beta_report.write_event("hotkey_ignored_focus", {"name": spec.name})
                    return
                callback = lambda spec=spec, started=triggered_at, checked=native_focus_checked: self._dispatch_hotkey(
                    spec,
                    triggered_at=started,
                    focus_verified=checked,
                )
                if hasattr(self.palette.root, "post"):
                    self.palette.root.post(callback)
                else:
                    self.palette.root.after(0, callback)
        except Exception as exc:
            beta_report.log_exception("Hotkey dispatch scheduling failed", exc)

    def _hotkey_error_text(self, error_code: int) -> str:
        if not error_code:
            return "erro desconhecido"
        try:
            return f"{error_code}: {ctypes.WinError(error_code)}"
        except Exception:
            return str(error_code)

    def _register_native_hotkey(self, user32, spec: HotkeySpec) -> bool:
        if user32.RegisterHotKey(None, spec.id, spec.modifiers, spec.vk):
            self._registered_hotkey_ids.add(spec.id)
            beta_report.write_event("hotkey_registered", {"name": spec.name})
            return True

        error_code = ctypes.get_last_error()
        error_text = self._hotkey_error_text(error_code)
        self._registration_failures.append(spec.name)
        beta_report.write_event("hotkey_register_failed", {"name": spec.name, "error": error_text})
        if spec.name == "toggle_palette":
            print(f"[Hotkey] Ctrl+Espaco nao registrado: {error_text}")
        else:
            print(f"[Hotkey] {spec.name} nao registrado: {error_text}")
        return False

    def _run_native_hotkey_loop(self):
        user32 = None
        try:
            self._native_backend_unavailable = False
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user32.PeekMessageW.restype = wintypes.BOOL
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD

            self._thread_id = int(kernel32.GetCurrentThreadId())
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

            for spec in self._specs:
                self._register_native_hotkey(user32, spec)

            self._ready_event.set()
            if 1 in self._registered_hotkey_ids:
                print("[Hotkey] Ctrl+Espaco ativo via RegisterHotKey")
            beta_report.write_event("hotkey_backend_selected", {"backend": "native_windows"})

            while not self._stopping:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    beta_report.write_event("hotkey_message_loop_failed", {"error": self._hotkey_error_text(ctypes.get_last_error())})
                    break
                if msg.message == WM_HOTKEY:
                    spec = self._specs_by_id.get(int(msg.wParam))
                    if spec is not None:
                        self._schedule_dispatch(spec, native_focus_checked=True)
        except Exception as exc:
            self._native_backend_unavailable = True
            beta_report.log_exception("Native hotkey thread failed", exc)
            self._ready_event.set()
        finally:
            if user32 is not None:
                for hotkey_id in list(self._registered_hotkey_ids):
                    try:
                        user32.UnregisterHotKey(None, hotkey_id)
                    except Exception:
                        pass
                self._registered_hotkey_ids.clear()
            self._thread_id = None

    def _start_native_backend(self):
        self._ready_event.clear()
        self._stopping = False
        self._thread = threading.Thread(target=self._run_native_hotkey_loop, name="NativeHotkeyListener", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=1.0)
        if self._native_backend_unavailable:
            self._thread.join(timeout=1.0)
            self._thread = None
            self._start_fallback_backend()

    def _fallback_bindings(self):
        bindings = {
            "<ctrl>+<space>": lambda: self._schedule_dispatch(self._specs_by_id[1]),
            "<ctrl>+q": lambda: self._schedule_dispatch(self._specs_by_id[3]),
        }
        if ENABLE_DEBUG_HOTKEY and 2 in self._specs_by_id:
            bindings["<ctrl>+d"] = lambda: self._schedule_dispatch(self._specs_by_id[2])
        for spec in self._specs:
            if spec.callback_name == "custom_action":
                bindings[hotkey_spec_to_pynput(spec)] = lambda spec=spec: self._schedule_dispatch(spec)
        return bindings

    def _start_fallback_backend(self):
        if not HAS_PYNPUT:
            print("[Aviso] pynput nao disponivel - hotkeys globais desativadas")
            return
        try:
            beta_report.write_event("hotkey_backend_selected", {"backend": "pynput_fallback"})
            self._fallback_hotkeys = keyboard.GlobalHotKeys(self._fallback_bindings())
            self._fallback_hotkeys.start()
            print("[Hotkey] fallback pynput ativo")
        except Exception as exc:
            beta_report.log_exception("pynput fallback hotkey backend failed", exc)

    def start(self):
        self._stopping = False
        self._registration_failures.clear()
        if IS_WINDOWS:
            self._start_native_backend()
            return
        self._start_fallback_backend()

    def reload(self) -> tuple[str, ...]:
        """Replace registered shortcuts from settings without restarting the app."""
        with self._reload_lock:
            self.stop()
            self._specs = self._build_specs()
            self._specs_by_id = {spec.id: spec for spec in self._specs}
            self._native_backend_unavailable = False
            self.start()
            failures = tuple(self._registration_failures)
            beta_report.write_event("hotkeys_reloaded", {
                "count": len(self._specs),
                "failures": list(failures),
            })
            return failures

    def stop(self):
        self._stopping = True
        if self._thread is not None:
            thread_id = self._thread_id
            if thread_id is not None:
                try:
                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                    user32.PostThreadMessageW.restype = wintypes.BOOL
                    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                except Exception as exc:
                    beta_report.log_exception("Failed to stop native hotkey thread", exc)
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._fallback_hotkeys is not None:
            try:
                self._fallback_hotkeys.stop()
            except Exception:
                pass
            self._fallback_hotkeys = None


def create_palette() -> "QtEffectPalette":
    return QtEffectPalette()


def create_debug_window(palette) -> QtDebugWindow:
    return QtDebugWindow(palette)


# --- Entry point -----------------------------------------------------------------------

def main():
    if not HAS_QT:
        print("[App] PySide6 nao encontrado - instale as dependencias: pip install -r companion/requirements.txt")
        sys.exit(2)
    if not acquire_single_instance_lock():
        print("[App] FX.palette ja esta em execucao")
        return

    print("=" * 52)
    print("  Premiere Pro FX.palette")
    print("  Atalho     : Ctrl+Espaco")
    print(f"  Debug      : {'Ctrl+D' if ENABLE_DEBUG_HOTKEY else 'menu da system tray'}")
    print("  Encerrar   : Ctrl+Q")
    print(f"  Catalogos  : {DATA_DIR}")
    print(f"  Transporte : ws://localhost:{TRANSPORT_PORT}")
    print("=" * 52)

    beta_report.start_session(APP_DIR, DATA_DIR)

    palette = create_palette()
    debug   = create_debug_window(palette)
    tray    = SystemTrayController(palette, debug)
    hotkey  = HotkeyListener(palette, debug)
    palette.hotkey_listener = hotkey
    hotkey.start()
    tray.start()

    try:
        palette.run()
    except KeyboardInterrupt:
        print("\n[App] Encerrando...")
        beta_report.write_event("keyboard_interrupt")
    except Exception as exc:
        beta_report.log_exception("Unhandled app exception", exc)
        raise
    finally:
        tray.stop()
        palette.shutdown()
        hotkey.stop()
        release_single_instance_lock()


if __name__ == "__main__":
    main()
