"""UXP execution adapter (TECHNICAL_PLAN.md stage 5, companion side).

Talks to the FX.palette Premiere UXP plugin over the WebSocket protocol
`transport.js` implements: this class is the server, the plugin is the client,
and every request/response after the handshake is one `executionAdapter.execute()`
action from `execution-adapter.js`.

Public shape: `execute(effect) -> float` (a request timestamp), `poll_status`,
`is_terminal`, `is_success`, `last_response_data` and `diagnostics() -> dict`.

Port and token are copied from `transport.js` verbatim - they must stay in
sync, since the UXP manifest pre-declares this exact domain and cannot
negotiate one at runtime.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer

import beta_report

TRANSPORT_PORT = 58756
TRANSPORT_TOKEN = "fxpalette-uxp-transport-v1-2eaf1cf6a94b4a5b8f0e3b7c9a5d6e21"

REQUEST_TIMEOUT_SECONDS = 5.0
# Per-effect-type deadlines, and the single source of truth for them: app.py's own
# apply_status_timeout_ms reads this table rather than keeping a parallel copy. It used to keep
# one, and the duplication was a real bug - poll_status applied the flat REQUEST_TIMEOUT_SECONDS
# to every request and reported error_timeout (terminal) before app.py's longer per-effect
# deadline could ever be consulted, so both entries below were dead code from the day they were
# written. Every recorded timeout in the beta telemetry landed at ~5.04s, never at 30s or 45s.
#
# "generic_item": an item not already in the project imports a whole template project, moves the
# result into a bin, and deletes the leftover sequence (index.js's ensureGenericProjectItem) - a
# multi-transaction round trip measured well past the default on first use. 20s was not enough
# once template_project.prproj grew to ~80 sequences; only the first import of a given resolution
# pays this, since later calls reuse whatever was already imported.
#
# "preset": reconstructEasing (on by default) writes one keyframe per frame across an animated
# parameter's whole duration, each its own createKeyframe/position/setTemporalInterpolationMode
# call - a preset with many animated parameters over several seconds can need thousands of these.
EFFECT_TIMEOUT_SECONDS = {
    "generic_item": 45.0,
    "preset": 30.0,
}
PENDING_RETENTION_SECONDS = 60.0
CATALOG_REQUEST_ID = "uxp-adapter-video-catalog"
TRANSITION_CATALOG_REQUEST_ID = "uxp-adapter-video-transitions"
FAVORITES_CATALOG_REQUEST_ID = "uxp-adapter-favorites"
PROJECT_ITEM_CATALOG_REQUEST_ID = "uxp-adapter-project-items"
EFFECT_PRESET_CATALOG_REQUEST_ID = "uxp-adapter-effect-presets"
# The plugin can only scan FX.palette_Favorites while the bundled template project happens to be
# the one currently open in Premiere (index.js's readFavoritesCatalog) - re-requested on a timer
# instead of once per connection so a favorite curated mid-session (open the template project, add
# one, switch back) shows up without needing the plugin to reload.
FAVORITES_REFRESH_INTERVAL_MS = 5000
# Project items reflect whatever the user is actively editing right now (media imported, sequences
# created) - unlike favorites/presets/effects, which only change when the user deliberately curates
# them, this needs to stay live throughout a normal editing session.
PROJECT_ITEMS_REFRESH_INTERVAL_MS = 5000

# Written by this adapter from the plugin's own catalog and read back by EffectsLoader, so the
# palette lists exactly the transitions UXP can actually apply, each carrying its exact matchName.
UXP_TRANSITIONS_FILE = Path(__file__).resolve().parent / "data" / "uxp_video_transitions.json"
# Written by this adapter from the plugin's own FX.palette_Favorites scan - replaces the CEP
# worker's premiere_favorites.json the same way UXP_TRANSITIONS_FILE replaces the CEP transition
# export, once index.js has ever seen the template project open and reported at least one item.
UXP_FAVORITES_FILE = Path(__file__).resolve().parent / "data" / "uxp_favorites.json"
# Written from index.js's readVideoEffectCatalog/readAudioFilterFactory.getDisplayNames() response -
# replaces the CEP worker's QE-DOM-sourced premiere_effects.json (video + audio filters only; video/
# audio transitions already have their own separate UXP-native catalogs).
UXP_EFFECTS_FILE = Path(__file__).resolve().parent / "data" / "uxp_effects.json"
# Written from index.js's readProjectItemCatalog - replaces premiere_project_items.json.
UXP_PROJECT_ITEMS_FILE = Path(__file__).resolve().parent / "data" / "uxp_project_items.json"
# Written from index.js's readEffectPresetCatalog, itself sourced from whatever .prfpset the user
# already granted this plugin persistent access to (importPrfpsetCatalog's one-time picker,
# restored silently via restoreImportedPresetCatalogFromToken on every plugin load since) - replaces
# premiere_presets.json's CEP-side filesystem auto-scan, which UXP has no silent equivalent for.
UXP_PRESETS_FILE = Path(__file__).resolve().parent / "data" / "uxp_presets.json"

# On: the plugin rebuilds each animated parameter's curve from the .prfpset's own
# speed/influence fields and frame-samples it, measured indistinguishable from a manual
# application in rendered output (0.016 px worst case, CAPABILITY_MATRIX.md). Off: only the
# principal keyframes are written, so easing shape is lost - which is what a user notices
# immediately on a preset built around its curve. Product default is therefore on; the plugin's
# diagnostics panel keeps its own checkbox for isolating the two behaviours during testing.
RECONSTRUCT_EASING_DEFAULT = True

# effect["type"] values translated so far. Every other type returns error_not_supported
# immediately - callers see a clear failure instead of a hang. "transition_audio" is
# deliberately absent: the plugin exposes no audio-transition action at all.
_SUPPORTED_EFFECT_TYPES = {
    "video",
    "audio",
    "preset",
    "transition_video",
    "timeline_action",
    "project_item",
    "generic_item",
    "favorite_item",
}

# Every generic item except Color Matte now has a UXP-side template mapping (index.js's
# GENERIC_ITEM_TEMPLATES) - Color Matte has no way to set its color after creation on either CEP or
# UXP, so a pre-built template could never be recolored per use and stays unsupported here. Requesting
# it (or any future key without a template) fails closed instead of round-tripping a request the
# plugin can only reject.
_GENERIC_ITEM_KEYS_WITH_UXP_TEMPLATE = {"adjustment_layer", "bars_and_tone", "black_video", "transparent_video"}

# Vendor prefixes literally encoded in transition matchNames, longest-first so
# "Universe_Transitions" is recognized before the shorter "Universe". Extracting this is not a
# guess about wording - the prefix is either there, delimited by "_"/" "/a capital letter, or it
# is not; nothing about where the *rest* of the name's words begin is invented.
_TRANSITION_VENDOR_PREFIXES = (
    ("Universe_Transitions", "Universe"),
    ("AE_Custom_Impact", "Impact"),
    ("AE_Impact", "Impact"),
    ("Impact", "Impact"),
    ("RG_UNI", "Universe"),  # Red Giant Universe's alternate internal prefix
    ("Universe", "Universe"),
    ("ADBE", "Adobe"),
    ("BCC", "BCC"),
    ("GenArts", "GenArts"),
    ("Mettle", "Mettle"),
    ("S", "Sapphire"),
)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def build_transition_entries(match_names) -> list[dict]:
    """Palette entries straight from the plugin's own catalog: "(Vendor) rest" label + matchName.

    Two earlier versions were rejected: showing the bare matchName was unreadable, and guessing
    word boundaries inside glued names (RADIALWIPE -> "Radial Wipe") was rejected as an
    unverifiable interpretation - there is no official display-name API for transitions to check
    a guess against (unlike video/audio effects, which can verify post-insertion). Tagging the
    vendor is different: the prefix is literally encoded in the matchName, not invented, so this
    strips only that known prefix and swaps "_" for spaces - nothing about where the *remaining*
    words begin is guessed at, unlike the rejected all-caps/camelCase splitting.

    A handful of BCC entries ship the same transition twice, once glued/all-caps
    ("BCC_RADIALWIPE") and once already spelled out under a different naming convention -
    sometimes BCC's own newer form ("BCC Radial WipePrTr"), sometimes another vendor's
    ("ADBE Radial Wipe"). When a glued entry's squashed letters match another entry's spelled-out
    letters exactly (not a segmentation guess - an exact whole-string match against real catalog
    text), that spelling is borrowed for display. The vendor tag and matchName stay the glued
    entry's own, so this never claims the two are the same transition, only that the words are
    spelled the same way. Measured against the real catalog: this resolves 15 of 36 previously
    all-caps entries; the remaining 21 have no such match anywhere in the catalog and are shown
    as-is rather than guessed at.
    """
    parsed = []
    for name in match_names:
        rest = re.sub(r"^(?:AE\.|PR\.)", "", name)
        vendor = ""
        for prefix, label in _TRANSITION_VENDOR_PREFIXES:
            match = re.match(rf"^{re.escape(prefix)}(?=[ _]|[A-Z]|$)", rest, re.IGNORECASE)
            if match:
                vendor = label
                rest = rest[match.end():]
                if rest[:1] in (" ", "_"):
                    rest = rest[1:]
                break
        rest = re.sub(r"PrTr$", "", rest)  # BCC's Premiere-transition suffix, not part of the name
        rest = re.sub(r"\s+", " ", rest.replace("_", " ")).strip() or rest
        parsed.append((name, vendor, rest))

    spellings_by_squash: dict[str, str] = {}
    for _, _, rest in parsed:
        if re.search(r"[a-z]", rest) or " " in rest:  # already spelled out, not glued/all-caps
            spellings_by_squash.setdefault(_squash(rest), rest)

    parsed = [
        (name, vendor, spellings_by_squash.get(_squash(rest), rest) if rest.isupper() else rest)
        for name, vendor, rest in parsed
    ]

    label_counts: dict[str, int] = {}
    for _, vendor, rest in parsed:
        label = f"({vendor}) {rest}" if vendor else rest
        label_counts[label] = label_counts.get(label, 0) + 1

    entries = []
    for name, vendor, rest in parsed:
        label = f"({vendor}) {rest}" if vendor else rest
        if label_counts[label] > 1:
            # Two different matchNames reduced to the same vendor+rest (BCC ships some
            # transitions twice, e.g. "BCC Blur DissolvePrTr" and "BCC_BLURDISSOLVE"). Rather
            # than show identical entries, fall back to the matchName as the discriminator.
            technical = re.sub(r"^(?:AE\.|PR\.)", "", name)
            label = f"{label} [{technical}]"
        entries.append({
            "name": label,
            "category": "Transicoes > Video",
            "type": "transition_video",
            "matchName": name,
        })
    return entries


def timeout_seconds_for_effect(effect: dict) -> float:
    """How long this one effect is allowed to stay pending before it counts as timed out."""
    return EFFECT_TIMEOUT_SECONDS.get((effect or {}).get("type"), REQUEST_TIMEOUT_SECONDS)


def _error_code_to_status(code) -> str:
    if not code:
        return "error"
    return "error_" + str(code).lower()


def find_prfpset_file() -> Path | None:
    """Locate the user's "Effect Presets and Custom Items.prfpset", mirroring the stable CEP
    product's own bridge.js::findPresetFile() - same well-known layout
    (Documents/Adobe/Premiere Pro/<version>/Profile-<name>/Effect Presets and Custom Items.prfpset).
    Done here, in Python, because the companion already has unrestricted filesystem access - the
    plugin only needs the exact path handed to it (manifest.json's "fullAccess" localFileSystem
    permission lets it then read that one file directly, no native file picker required)."""
    documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    root = documents / "Adobe" / "Premiere Pro"
    if not root.exists():
        return None
    try:
        candidates = sorted(
            root.glob("*/Profile-*/Effect Presets and Custom Items.prfpset"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


class PremiereUxpExecutionAdapter(QtCore.QObject):
    """Host boundary backed by the UXP transport - drop-in for PremiereExecutionAdapter."""

    backend_name = "uxp"

    def __init__(self, parent=None, port: int = TRANSPORT_PORT):
        super().__init__(parent)
        self._port = port
        self._server = QWebSocketServer(
            "FX.palette UXP transport", QWebSocketServer.SslMode.NonSecureMode, self
        )
        self._client = None
        self._authenticated = False
        self._pending: dict[str, dict] = {}
        self._video_match_names_by_display: dict[str, str] = {}
        self._transition_match_names_by_label: dict[str, str] = {}
        self._catalog_requested = False
        self._effect_preset_retry_scheduled = False
        # Last serialized payload per catalog file, so a repeating catalog that has not changed
        # (favorites and project items are re-read every few seconds) is not rewritten to disk -
        # each rewrite also fired the palette's file watcher and a full search-index rebuild.
        self._last_written: dict[Path, str] = {}
        self._reset_connection_log_state()
        # None until the first catalog.effectPresets.read round-trip settles (including its one
        # retry) - surfaced in diagnostics() so the settings center can tell the user a preset
        # catalog needs (re-)importing, now that the diagnostics panel's own import button is gone
        # from the shipped build.
        self._preset_catalog_available: bool | None = None
        self._favorites_timer = QtCore.QTimer(self)
        self._favorites_timer.timeout.connect(self._request_favorites_catalog)
        self._project_items_timer = QtCore.QTimer(self)
        self._project_items_timer.timeout.connect(self._request_project_item_catalog)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(QHostAddress.SpecialAddress.LocalHost, port):
            raise RuntimeError(
                f"Could not start the UXP transport server on port {port}: "
                f"{self._server.errorString()}"
            )
        self._port = self._server.serverPort()  # port 0 (tests) resolves to whatever was bound

    def _reset_connection_log_state(self):
        # "Only log on change" bookkeeping; reset per connection so the first response of a new
        # connection always logs, whatever the previous one last reported.
        self._favorites_last_applicable = None
        self._favorites_last_error_code = None
        self._favorites_last_logged_count = None
        self._project_items_last_count = None

    # ---- connection lifecycle -------------------------------------------------

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        previous = self._client
        self._client = socket
        self._authenticated = False
        self._catalog_requested = False
        self._effect_preset_retry_scheduled = False
        self._reset_connection_log_state()
        socket.textMessageReceived.connect(lambda raw, ws=socket: self._on_message(raw, ws))
        socket.disconnected.connect(lambda ws=socket: self._on_disconnected(ws))
        if previous is not None:
            # A plugin reload reconnects before the old socket has finished closing. Closing it
            # here fires its disconnected signal *after* the new client was installed above, so
            # _on_disconnected must check which socket it is being told about.
            try:
                previous.close()
            except Exception:
                pass

    def _on_disconnected(self, socket=None):
        if socket is not None and socket is not self._client:
            return  # a superseded connection; the live one is unaffected
        self._client = None
        self._authenticated = False
        self._catalog_requested = False
        self._favorites_timer.stop()
        self._project_items_timer.stop()
        self._effect_preset_retry_scheduled = False
        self._reset_connection_log_state()

    def _on_message(self, raw: str, socket=None):
        if socket is not None and socket is not self._client:
            return  # late frame from a superseded connection
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(message, dict):
            return

        if not self._authenticated:
            if message.get("type") == "hello" and message.get("token") == TRANSPORT_TOKEN:
                self._authenticated = True
                print("[UXP adapter] UXP plugin connected and authenticated over ws://localhost:"
                      f"{self._port}", flush=True)
                self._send({"type": "hello-ack", "ok": True})
                self._request_catalogs()
            elif self._client is not None:
                self._client.close()
            return

        if message.get("type") == "diagnostic.log":
            print(f"[UXP plugin log] {message.get('message', '')}", flush=True)
            return

        request_id = message.get("requestId")
        if request_id == CATALOG_REQUEST_ID:
            self._handle_catalog_response(message)
            return
        if request_id == TRANSITION_CATALOG_REQUEST_ID:
            self._handle_transition_catalog_response(message)
            return
        if request_id == FAVORITES_CATALOG_REQUEST_ID:
            self._handle_favorites_catalog_response(message)
            return
        if request_id == PROJECT_ITEM_CATALOG_REQUEST_ID:
            self._handle_project_item_catalog_response(message)
            return
        if request_id == EFFECT_PRESET_CATALOG_REQUEST_ID:
            self._handle_effect_preset_catalog_response(message)
            return
        if request_id in self._pending:
            self._resolve_pending(request_id, message)

    def _send(self, payload: dict):
        if self._client is None:
            return
        self._client.sendTextMessage(json.dumps(payload))

    def _write_catalog(self, path: Path, payload: dict) -> bool:
        """Atomically write a catalog file; returns False when the content is unchanged."""
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        if self._last_written.get(path) == serialized and path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)
        self._last_written[path] = serialized
        return True

    def refresh_catalogs(self) -> bool:
        """Re-request every catalog from the connected plugin (the palette's refresh button).
        Returns False when no authenticated plugin is connected."""
        if self._client is None or not self._authenticated:
            return False
        self._catalog_requested = False
        self._request_catalogs()
        return True

    # ---- video match-name resolution -------------------------------------------

    def _request_catalogs(self):
        if self._catalog_requested:
            return
        self._catalog_requested = True
        self._send({
            "schemaVersion": 1,
            "type": "catalog.videoEffects.read",
            "requestId": CATALOG_REQUEST_ID,
            "payload": {},
        })
        self._send({
            "schemaVersion": 1,
            "type": "catalog.videoTransitions.read",
            "requestId": TRANSITION_CATALOG_REQUEST_ID,
            "payload": {},
        })
        self._request_effect_preset_catalog()
        self._request_favorites_catalog()
        self._favorites_timer.start(FAVORITES_REFRESH_INTERVAL_MS)
        self._request_project_item_catalog()
        self._project_items_timer.start(PROJECT_ITEMS_REFRESH_INTERVAL_MS)

    def _request_project_item_catalog(self):
        self._send({
            "schemaVersion": 1,
            "type": "catalog.projectItems.read",
            "requestId": PROJECT_ITEM_CATALOG_REQUEST_ID,
            "payload": {},
        })

    def _handle_project_item_catalog_response(self, message: dict):
        if not message.get("ok"):
            return
        items = (message.get("data") or {}).get("items") or []
        try:
            payload = {"schemaVersion": 1, "source": "uxp", "items": items}
            self._write_catalog(UXP_PROJECT_ITEMS_FILE, payload)
            # Repeats every PROJECT_ITEMS_REFRESH_INTERVAL_MS - only log on an actual count change.
            if len(items) != self._project_items_last_count:
                self._project_items_last_count = len(items)
                print(f"[UXP adapter] {len(items)} project items written to {UXP_PROJECT_ITEMS_FILE}", flush=True)
        except Exception as error:
            print(f"[UXP adapter] could not write the project item catalog: {error}", flush=True)

    def _request_effect_preset_catalog(self):
        prfpset_path = find_prfpset_file()
        if prfpset_path is not None:
            # readFromPath's response is shaped identically to catalog.effectPresets.read's, so
            # the same requestId/handler below covers both without any extra routing.
            self._send({
                "schemaVersion": 1,
                "type": "catalog.effectPresets.readFromPath",
                "requestId": EFFECT_PRESET_CATALOG_REQUEST_ID,
                "payload": {"path": str(prfpset_path)},
            })
            return
        # No .prfpset found automatically (non-default install, or none exists yet) - fall back
        # to asking whether something was already loaded (e.g. via the Diagnostics tab's manual
        # native-picker fallback).
        self._send({
            "schemaVersion": 1,
            "type": "catalog.effectPresets.read",
            "requestId": EFFECT_PRESET_CATALOG_REQUEST_ID,
            "payload": {},
        })

    def _request_catalogs_retry(self):
        if self._client is None or not self._authenticated or self._catalog_requested:
            return
        self._request_catalogs()

    def _request_effect_preset_catalog_retry(self):
        # QTimer.singleShot still fires even if the connection that scheduled it has since dropped;
        # _on_disconnected resets the pending flag but there is nothing to send to a closed socket.
        if self._client is None or not self._authenticated:
            return
        self._request_effect_preset_catalog()

    def _handle_effect_preset_catalog_response(self, message: dict):
        if not message.get("ok"):
            return
        data = message.get("data") or {}
        if not data.get("available"):
            # index.js's restoreImportedPresetCatalogFromToken() is fire-and-forget from
            # entrypoints.plugin.create(), started around the same time as the transport itself -
            # this response can genuinely arrive before that async restore has finished reading and
            # parsing the .prfpset file. One retry after a short delay covers that race without
            # delaying the whole transport's connection just for presets specifically.
            if not self._effect_preset_retry_scheduled:
                self._effect_preset_retry_scheduled = True
                QtCore.QTimer.singleShot(3000, self._request_effect_preset_catalog_retry)
            else:
                print("[UXP adapter] catalog.effectPresets.read: no .prfpset catalog imported in the plugin yet", flush=True)
                self._preset_catalog_available = False
            return
        self._effect_preset_retry_scheduled = False
        self._preset_catalog_available = True
        presets = data.get("presets") or []
        try:
            payload = {
                "schemaVersion": 1,
                "source": "uxp",
                "fileName": data.get("fileName"),
                "presets": [{"name": p["name"], "category": p.get("category", ""), "filterPresets": []} for p in presets if p.get("name")],
            }
            if self._write_catalog(UXP_PRESETS_FILE, payload):
                print(f"[UXP adapter] {len(payload['presets'])} presets written to {UXP_PRESETS_FILE}", flush=True)
        except Exception as error:
            print(f"[UXP adapter] could not write the preset catalog: {error}", flush=True)

    def _request_favorites_catalog(self):
        self._send({
            "schemaVersion": 1,
            "type": "catalog.favorites.read",
            "requestId": FAVORITES_CATALOG_REQUEST_ID,
            "payload": {},
        })

    def _handle_favorites_catalog_response(self, message: dict):
        # Unlike the transition/effect catalogs, this one legitimately comes back empty most of
        # the time (whenever the template project isn't the one currently open) - "applicable"
        # tells the difference between "not open right now" (keep whatever was last written) and
        # "open, but the favorites bin is empty" (write an empty list, matching what's really there).
        if not message.get("ok"):
            error = message.get("error") or {}
            if error.get("code") != self._favorites_last_error_code:
                self._favorites_last_error_code = error.get("code")
                print(f"[UXP adapter] catalog.favorites.read failed: {error}", flush=True)
            return
        self._favorites_last_error_code = None
        data = message.get("data") or {}
        applicable = bool(data.get("applicable"))
        if applicable != self._favorites_last_applicable:
            self._favorites_last_applicable = applicable
            print(
                f"[UXP adapter] catalog.favorites.read applicable={applicable} "
                f"activeProjectPath={data.get('activeProjectPath')!r} templatePath={data.get('templatePath')!r}",
                flush=True,
            )
        if not applicable:
            return
        items = data.get("items") or []
        try:
            # No timestamp in the payload: the file only changes when the favorites do, so the
            # 5-second refresh stops rewriting an identical file (and re-triggering the watcher).
            payload = {
                "version": 1,
                "sourceProjectPath": data.get("sourceProjectPath", ""),
                "items": items,
            }
            self._write_catalog(UXP_FAVORITES_FILE, payload)
            # This response repeats every FAVORITES_REFRESH_INTERVAL_MS while the template project
            # stays open - only log when the count actually changes, so the console isn't spammed.
            if len(items) != self._favorites_last_logged_count:
                self._favorites_last_logged_count = len(items)
                print(f"[UXP adapter] {len(items)} favorites written to {UXP_FAVORITES_FILE}", flush=True)
        except Exception as error:
            print(f"[UXP adapter] could not write the favorites catalog: {error}", flush=True)

    def _handle_transition_catalog_response(self, message: dict):
        # The plugin's transition catalog exposes matchNames only - no display names, and
        # VideoTransition itself exposes no readable identity after insertion, so nothing can
        # verify a name-based guess the way applyVideoEffect's verification can. Rather than
        # guess, the palette's transition list is generated straight from this catalog: each
        # entry carries its exact matchName, so picking any entry can only apply that exact
        # transition. Measured against the real host catalog: 305 match names -> 305 distinct
        # labels, no collisions.
        if not message.get("ok"):
            return
        match_names = (message.get("data") or {}).get("matchNames") or []
        if not match_names:
            return
        entries = build_transition_entries(match_names)
        self._transition_match_names_by_label = {e["name"]: e["matchName"] for e in entries}
        try:
            payload = {"schemaVersion": 1, "source": "uxp", "transitions": entries}
            if self._write_catalog(UXP_TRANSITIONS_FILE, payload):
                print(f"[UXP adapter] {len(entries)} video transitions written to {UXP_TRANSITIONS_FILE}", flush=True)
        except Exception as error:
            # Non-fatal: this session can still apply transitions from the in-memory lookup;
            # only the palette's own listing falls back to whatever it had before.
            print(f"[UXP adapter] could not write the transition catalog: {error}", flush=True)

    def _handle_catalog_response(self, message: dict):
        if not message.get("ok"):
            # Nothing re-requested this after a failure before, so a transient host error at
            # connection time left video effects unresolvable for the whole session.
            self._catalog_requested = False
            print(f"[UXP adapter] catalog.videoEffects.read failed: {message.get('error')} - retrying", flush=True)
            QtCore.QTimer.singleShot(3000, self._request_catalogs_retry)
            return
        data = message.get("data") or {}
        display_names = data.get("displayNames") or []
        match_names = data.get("matchNames") or []
        # Adobe does not document positional correspondence between these two arrays
        # (CAPABILITY_MATRIX.md); this is a same-index candidate, not a confirmed
        # mapping. execute() below verifies the actually-applied display name after
        # insertion and fails closed on a mismatch rather than trusting the guess.
        lookup: dict[str, str] = {}
        for display_name, match_name in zip(display_names, match_names):
            lookup.setdefault(display_name, match_name)
        self._video_match_names_by_display = lookup

        # Same response, second purpose: build the UXP-native effects catalog (video + audio filter
        # names only - video/audio transitions already have their own separate catalogs). Audio
        # effects apply by display name alone (AudioFilterFactory has no matchNames, confirmed
        # against the official reference), so unlike video there's no identity concern to carry
        # into the catalog entry itself.
        audio_display_names = data.get("audioDisplayNames") or []
        entries = [{"name": name, "category": "Video", "type": "video"} for name in dict.fromkeys(display_names)]
        entries += [{"name": name, "category": "Audio", "type": "audio"} for name in dict.fromkeys(audio_display_names)]
        if entries:
            try:
                payload = {"schemaVersion": 1, "source": "uxp", "effects": entries}
                if self._write_catalog(UXP_EFFECTS_FILE, payload):
                    print(f"[UXP adapter] {len(entries)} effects written to {UXP_EFFECTS_FILE}", flush=True)
            except Exception as error:
                print(f"[UXP adapter] could not write the effect catalog: {error}", flush=True)

    # ---- adapter interface -------------------------------------------------

    def execute(self, effect: dict) -> float:
        self._prune_pending()
        timestamp = time.time()
        request_id = f"{timestamp:.6f}"
        effect_type = effect.get("type")
        display_name = effect.get("name", "")

        if effect_type not in _SUPPORTED_EFFECT_TYPES:
            self._pending[request_id] = {"status": "error_not_supported"}
            return timestamp

        if self._client is None or not self._authenticated:
            self._pending[request_id] = {"status": "error_not_connected"}
            return timestamp

        requested_display_name = display_name
        if effect_type == "audio":
            action_type = "timeline.applyAudioEffect"
            payload = {"displayName": display_name}
        elif effect_type == "preset":
            # No display-name -> matchName guessing here: the plugin resolves name/category
            # directly against its own already-imported .prfpset catalog and fails closed on
            # ambiguity or a missing match, so ok:true is sufficient - no separate identity
            # check is needed the way video's same-index candidate lookup requires one.
            action_type = "timeline.applyImportedEffectPreset"
            payload = {
                "name": display_name,
                "category": effect.get("category", ""),
                "reconstructEasing": bool(effect.get("reconstructEasing", RECONSTRUCT_EASING_DEFAULT)),
            }
            requested_display_name = None
        elif effect_type == "transition_video":
            # The catalog entry carries its own matchName (see _handle_transition_catalog_response);
            # the label is never used to look one up. An entry without it is a stale CEP-sourced
            # item, which fails closed rather than being guessed at.
            match_name = effect.get("matchName") or self._transition_match_names_by_label.get(display_name)
            if not match_name:
                status = (
                    "error_catalog_not_ready"
                    if not self._transition_match_names_by_label
                    else "error_transition_not_found"
                )
                self._pending[request_id] = {"status": status}
                return timestamp
            action_type = "timeline.applyVideoTransition"
            placement = str(effect.get("transitionPlacement", "auto")).upper()
            payload = {"matchName": match_name, "position": "END" if placement == "END" else "START"}
            # VideoTransition exposes no readable identity, so there is nothing to verify against.
            requested_display_name = None
        elif effect_type == "timeline_action":
            # "nest" is the only sub-action this app.py ever builds (app.py:592) - and only when
            # nestMode resolves to "api"; "premiere" (native Ctrl+/-style keystroke) never reaches
            # execute_effect_through_adapter at all, so this adapter never needs to handle it.
            if effect.get("action") != "nest":
                self._pending[request_id] = {"status": "error_not_supported"}
                return timestamp
            nest_name = str(effect.get("nestName") or "").strip()
            if not nest_name:
                # A blank name field means "give it CEP's default codename" (FXN-001, FXN-002...),
                # not the generic UI label "Nest clips" - confirmed against host.jsx's own
                # _uniqueNestSequenceName, which falls back to _nextNestCodeName() the same way.
                nest_name = self.next_nest_codename() or display_name
            nest_name = nest_name.strip()
            if not nest_name:
                self._pending[request_id] = {"status": "error_nest_name_required"}
                return timestamp
            action_type = "timeline.createNest"
            bin_name = str(effect.get("nestBin") or "").strip() or "Nested Clips"
            payload = {"name": nest_name, "binName": bin_name}
            requested_display_name = None
        elif effect_type == "project_item":
            # There is no official API to set the Project panel's own selection
            # (ProjectItemSelection is read-only), so this resolves the target by its full bin
            # path instead of requiring the item to already be selected there - see
            # findProjectItemByTreePath in index.js.
            tree_path = str(effect.get("treePath") or "").strip()
            if not tree_path:
                self._pending[request_id] = {"status": "error_tree_path_required"}
                return timestamp
            action_type = "timeline.insertProjectItem"
            # videoTrackIndex/audioTrackIndex are deliberately omitted: the plugin auto-targets
            # the currently selected clip's track (falling back to the first available track from
            # there), matching host.jsx's _resolveInsertionTracks - sending an explicit 0 here
            # would override that and always insert on track 0 regardless of selection.
            payload = {"treePath": tree_path, "editMode": "INSERT"}
            requested_display_name = None
        elif effect_type == "generic_item":
            generic_key = str(effect.get("genericKey") or "").strip()
            if not generic_key:
                self._pending[request_id] = {"status": "error_generic_key_required"}
                return timestamp
            if generic_key not in _GENERIC_ITEM_KEYS_WITH_UXP_TEMPLATE:
                self._pending[request_id] = {"status": "error_not_supported"}
                return timestamp
            action_type = "timeline.insertProjectItem"
            payload = {"genericKey": generic_key, "editMode": "INSERT"}
            requested_display_name = None
        elif effect_type == "favorite_item":
            if not str(effect.get("name") or "").strip():
                self._pending[request_id] = {"status": "error_favorite_name_required"}
                return timestamp
            action_type = "timeline.insertProjectItem"
            payload = {
                "favorite": {
                    "name": effect.get("name", ""),
                    "favoriteType": effect.get("favoriteType", ""),
                    "mediaPath": effect.get("mediaPath", ""),
                    "sequenceID": effect.get("sequenceID", ""),
                    "sourceProjectPath": effect.get("sourceProjectPath", ""),
                },
                "editMode": "INSERT",
            }
            requested_display_name = None
        else:
            match_name = self._video_match_names_by_display.get(display_name)
            if match_name is None:
                status = (
                    "error_catalog_not_ready"
                    if not self._video_match_names_by_display
                    else "error_effect_not_found"
                )
                self._pending[request_id] = {"status": status}
                return timestamp
            action_type = "timeline.applyVideoEffect"
            payload = {"matchName": match_name}

        self._pending[request_id] = {
            "status": "pending",
            "requested_display_name": requested_display_name,
            "sent_at": timestamp,
            "action_type": action_type,
            "timeout_seconds": timeout_seconds_for_effect(effect),
        }
        print(f"[UXP adapter] -> {action_type} requestId={request_id} payload={payload}", flush=True)
        self._send({
            "schemaVersion": 1,
            "type": action_type,
            "requestId": request_id,
            "payload": payload,
        })
        return timestamp

    def check_track_availability(self, generic_key: str = "") -> float:
        """Read-only pre-flight before an insertion-family effect: does a free Timeline track
        already exist for whatever media kind(s) this item needs? Same request/poll bookkeeping as
        execute() (reuses _pending/poll_status/last_response_data), just not shaped as a user-facing
        effect - app.py calls this before insertProjectItem so it can drive the native "Add
        Tracks..." dialog first when needed, rather than after (see TECHNICAL_PLAN.md for why an
        after-the-fact undo-and-retry was tried first and abandoned).
        """
        self._prune_pending()
        timestamp = time.time()
        request_id = f"{timestamp:.6f}"
        if self._client is None or not self._authenticated:
            self._pending[request_id] = {"status": "error_not_connected"}
            return timestamp
        self._pending[request_id] = {"status": "pending", "sent_at": timestamp}
        self._send({
            "schemaVersion": 1,
            "type": "timeline.checkTrackAvailability",
            "requestId": request_id,
            "payload": {"genericKey": generic_key},
        })
        return timestamp

    def _resolve_pending(self, request_id: str, message: dict):
        entry = self._pending.get(request_id)
        if entry is None:
            return
        print(f"[UXP adapter] <- requestId={request_id} ok={message.get('ok')} data/error={message.get('data') or message.get('error')}", flush=True)
        # stdout alone is invisible in the installed (windowed) build, so a plugin-side failure
        # left no trace anywhere - the palette's own telemetry only records that it gave up
        # waiting. Record the plugin's real verdict, and how long it actually took, so a request
        # that outlives REQUEST_TIMEOUT_SECONDS is still diagnosable after the fact.
        sent_at = entry.get("sent_at")
        beta_report.write_event("adapter_response", {
            "requestId": request_id,
            "actionType": entry.get("action_type"),
            "ok": bool(message.get("ok")),
            "error": message.get("error"),
            "elapsed_ms": round((time.time() - sent_at) * 1000.0, 2) if sent_at else None,
            "timedOutBeforeResponse": entry.get("status") == "error_timeout",
        })

        if not message.get("ok"):
            error = message.get("error") or {}
            entry["status"] = _error_code_to_status(error.get("code"))
            return

        data = message.get("data") or {}
        entry["data"] = data
        # Some actions report their own post-mutation check (transitions compare the sequence's
        # transition count before/after). An explicit false means the transaction was accepted
        # but nothing actually landed, which must not be reported to the user as success.
        if data.get("verificationSucceeded") is False:
            entry["status"] = "error_not_applied"
            return

        verification = data.get("verification") or []
        requested = (entry.get("requested_display_name") or "").strip().casefold()
        if requested and verification:
            actual = (verification[0].get("displayName") or "").strip().casefold()
            # The plugin's own verification only confirms it inserted the matchName we
            # asked for, which is trivially true even if our display-name candidate
            # guessed the wrong matchName. Comparing display names here is what
            # actually catches that case instead of reporting a false success.
            if actual and actual != requested:
                entry["status"] = "error_identity_mismatch"
                return
        entry["status"] = "done"

    def _prune_pending(self):
        now = time.time()
        cutoff = now - PENDING_RETENTION_SECONDS
        stale = []
        for request_id, entry in self._pending.items():
            status = entry.get("status") or ""
            sent_at = entry.get("sent_at") or float(request_id)
            if status == "pending":
                # Still unanswered: keep it for its own deadline plus the retention window, so a
                # caller that stopped polling does not leave it in memory for the whole session.
                deadline = sent_at + (entry.get("timeout_seconds") or REQUEST_TIMEOUT_SECONDS)
                if deadline < cutoff:
                    stale.append(request_id)
            elif sent_at < cutoff:
                stale.append(request_id)
        for request_id in stale:
            del self._pending[request_id]

    def poll_status(self, timestamp) -> str | None:
        if timestamp is None:
            return None
        request_id = f"{float(timestamp):.6f}"
        entry = self._pending.get(request_id)
        if entry is None:
            return None
        timeout_seconds = entry.get("timeout_seconds") or REQUEST_TIMEOUT_SECONDS
        if entry["status"] == "pending" and (time.time() - entry["sent_at"]) > timeout_seconds:
            entry["status"] = "error_timeout"
        return None if entry["status"] == "pending" else entry["status"]

    def last_response_data(self, timestamp) -> dict | None:
        """Raw `data` payload from a completed request - e.g. an insertion's own trackFallback
        flags, which poll_status's plain status string has no room to carry."""
        if timestamp is None:
            return None
        entry = self._pending.get(f"{float(timestamp):.6f}")
        return entry.get("data") if entry else None

    @staticmethod
    def is_terminal(status) -> bool:
        return status == "done" or bool(status and str(status).startswith("error"))

    @staticmethod
    def is_success(status) -> bool:
        return status == "done"

    def diagnostics(self) -> dict:
        return {
            "backend": self.backend_name,
            "listening": self._server.isListening(),
            "port": self._port,
            "client_connected": self._client is not None,
            "authenticated": self._authenticated,
            "video_catalog_entries": len(self._video_match_names_by_display),
            "preset_catalog_available": self._preset_catalog_available,
        }

    def import_prfpset_catalog(self, timeout_ms: int = 120000) -> dict | None:
        """Open the plugin's native file picker for a .prfpset file and grant it persistent access
        - the same one-time consent cost `TECHNICAL_PLAN.md` already documents, just reachable from
        the settings center now that the diagnostics panel that used to trigger it is gone from the
        shipped build. A generous timeout since this blocks on the user actually picking a file in
        a native dialog, not a quick round trip. Returns None on any failure (not connected, no
        selection, plugin-side error) - the caller shows that as "still not imported", not a crash.
        """
        result = self._blocking_request(
            "catalog.effectPresets.importPrfpset", "adapter-import-prfpset", timeout_ms
        )
        if result is not None:
            self._request_effect_preset_catalog()
        return result

    def _blocking_request(self, action_type: str, request_id: str, timeout_ms: int) -> dict | None:
        """Send one action and block the caller (via a nested Qt event loop) for its response.

        Only for UI-callback call sites that need a synchronous-looking answer and cannot
        themselves be made async - a nested QEventLoop is the standard Qt pattern for that.
        Returns the raw ok:true response's `data`, or None on any failure (not connected,
        timeout, ok:false, malformed) so the caller always has an explicit "couldn't find out"
        case to fall back from, instead of a guess.
        """
        if self._client is None or not self._authenticated:
            return None

        outcome: dict = {}
        loop = QtCore.QEventLoop()

        def on_message(raw: str):
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                return
            if isinstance(message, dict) and message.get("requestId") == request_id:
                outcome["message"] = message
                loop.quit()

        client = self._client
        client.textMessageReceived.connect(on_message)
        client.disconnected.connect(loop.quit)  # do not sit out the full timeout on a dead socket
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        self._send({"schemaVersion": 1, "type": action_type, "requestId": request_id, "payload": {}})
        loop.exec()
        timer.stop()
        for signal, slot in ((client.textMessageReceived, on_message), (client.disconnected, loop.quit)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # client already gone (disconnected mid-wait)

        message = outcome.get("message")
        if not message or not message.get("ok"):
            return None
        return message.get("data") or {}

    def has_multi_track_audio_selection(self, timeout_ms: int = 1200) -> bool | None:
        """Does the current Timeline selection span more than one audio track?

        CEP's `resolve_nest_mode` used this to route a native Nest away from the API path,
        because native `cmd.clip.nestify` leaves multiple selected audio tracks unmerged instead
        of consolidating them onto one - a real behavior gap, not a CEP-vs-UXP one. It read this
        from `current_selection.json`, a file `bridge.js` no longer exists to keep updated under
        this adapter, so that signal went permanently stale. This asks the plugin directly instead.

        Returns None (not True/False) on any failure so the caller can fall back to its own
        default instead of silently picking a mode.
        """
        data = self._blocking_request("diagnostics.read", "adapter-selection-audio-check", timeout_ms)
        if data is None:
            return None
        items = (data.get("timelineSelection") or {}).get("items") or []
        audio_tracks = {item.get("trackIndex") for item in items if item.get("isAudio")}
        return len(audio_tracks) > 1

    def next_nest_codename(self, timeout_ms: int = 1200) -> str | None:
        """The same default Nest name CEP generated for a blank name field: "FXN-NNN", the
        existing highest such name in the project plus one, zero-padded to 3 digits
        (host.jsx `_nextNestCodeName`). UXP's `timeline.createNest` requires a non-empty name
        up front - it has no equivalent server-side fallback - so this has to be resolved before
        sending the action, using the real project's sequence list rather than a local counter
        that could silently collide with a sequence the user (or CEP, historically) already named.

        Returns None on failure (not connected, timeout) so the caller can fall back further.
        """
        data = self._blocking_request("diagnostics.read", "adapter-nest-codename", timeout_ms)
        if data is None:
            return None
        sequence_names = (data.get("project") or {}).get("sequenceNames") or []
        highest = 0
        for name in sequence_names:
            match = re.match(r"^FXN-(\d+)$", str(name or ""), re.IGNORECASE)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"FXN-{highest + 1:03d}"
