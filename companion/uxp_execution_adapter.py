"""UXP execution adapter (TECHNICAL_PLAN.md stage 5, companion side).

Talks to the EffectPalette_UXP Premiere plugin over the WebSocket protocol
`transport.js` already implements and host-tested: this class is the server,
the plugin is the client, and every request/response after the handshake is
one `executionAdapter.execute()` action from `execution-adapter.js`.

Same public shape as `PremiereExecutionAdapter` from the stable product
(`execute(effect) -> float`, `diagnostics() -> dict`), plus `poll_status`,
`is_terminal` and `is_success`, so callers that currently poll the CEP
bridge file can instead poll through whichever adapter is active.

Port and token are copied from `transport.js` verbatim - they must stay in
sync, since the UXP manifest pre-declares this exact domain and cannot
negotiate one at runtime.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer

TRANSPORT_PORT = 58756
TRANSPORT_TOKEN = "fxpalette-uxp-transport-v1-2eaf1cf6a94b4a5b8f0e3b7c9a5d6e21"

REQUEST_TIMEOUT_SECONDS = 5.0
PENDING_RETENTION_SECONDS = 60.0
CATALOG_REQUEST_ID = "uxp-adapter-video-catalog"
TRANSITION_CATALOG_REQUEST_ID = "uxp-adapter-video-transitions"

# Written by this adapter from the plugin's own catalog and read back by EffectsLoader, so the
# palette lists exactly the transitions UXP can actually apply, each carrying its exact matchName.
UXP_TRANSITIONS_FILE = Path(__file__).resolve().parent / "data" / "uxp_video_transitions.json"

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
_SUPPORTED_EFFECT_TYPES = {"video", "audio", "preset", "transition_video", "timeline_action", "project_item"}

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


def _error_code_to_status(code) -> str:
    if not code:
        return "error"
    return "error_" + str(code).lower()


class PremiereUxpExecutionAdapter(QtCore.QObject):
    """Host boundary backed by the UXP transport - drop-in for PremiereExecutionAdapter."""

    backend_name = "uxp"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QWebSocketServer(
            "FX.palette UXP transport", QWebSocketServer.SslMode.NonSecureMode, self
        )
        self._client = None
        self._authenticated = False
        self._pending: dict[str, dict] = {}
        self._video_match_names_by_display: dict[str, str] = {}
        self._transition_match_names_by_label: dict[str, str] = {}
        self._catalog_requested = False
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(QHostAddress.SpecialAddress.LocalHost, TRANSPORT_PORT):
            raise RuntimeError(
                f"Could not start the UXP transport server on port {TRANSPORT_PORT}: "
                f"{self._server.errorString()}"
            )

    # ---- connection lifecycle -------------------------------------------------

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = socket
        self._authenticated = False
        socket.textMessageReceived.connect(self._on_message)
        socket.disconnected.connect(self._on_disconnected)

    def _on_disconnected(self):
        self._client = None
        self._authenticated = False
        self._catalog_requested = False

    def _on_message(self, raw: str):
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
                      f"{TRANSPORT_PORT}", flush=True)
                self._send({"type": "hello-ack", "ok": True})
                self._request_catalogs()
            elif self._client is not None:
                self._client.close()
            return

        request_id = message.get("requestId")
        if request_id == CATALOG_REQUEST_ID:
            self._handle_catalog_response(message)
            return
        if request_id == TRANSITION_CATALOG_REQUEST_ID:
            self._handle_transition_catalog_response(message)
            return
        if request_id in self._pending:
            self._resolve_pending(request_id, message)

    def _send(self, payload: dict):
        if self._client is None:
            return
        self._client.sendTextMessage(json.dumps(payload))

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
            UXP_TRANSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"schemaVersion": 1, "source": "uxp", "transitions": entries}
            tmp = UXP_TRANSITIONS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(UXP_TRANSITIONS_FILE)
            print(f"[UXP adapter] {len(entries)} video transitions written to {UXP_TRANSITIONS_FILE}", flush=True)
        except Exception as error:
            # Non-fatal: this session can still apply transitions from the in-memory lookup;
            # only the palette's own listing falls back to whatever it had before.
            print(f"[UXP adapter] could not write the transition catalog: {error}", flush=True)

    def _handle_catalog_response(self, message: dict):
        if not message.get("ok"):
            self._catalog_requested = False
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
        }
        print(f"[UXP adapter] -> {action_type} requestId={request_id} payload={payload}", flush=True)
        self._send({
            "schemaVersion": 1,
            "type": action_type,
            "requestId": request_id,
            "payload": payload,
        })
        return timestamp

    def _resolve_pending(self, request_id: str, message: dict):
        entry = self._pending.get(request_id)
        if entry is None:
            return
        print(f"[UXP adapter] <- requestId={request_id} ok={message.get('ok')} data/error={message.get('data') or message.get('error')}", flush=True)

        if not message.get("ok"):
            error = message.get("error") or {}
            entry["status"] = _error_code_to_status(error.get("code"))
            return

        data = message.get("data") or {}
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
        cutoff = time.time() - PENDING_RETENTION_SECONDS
        stale = [
            request_id
            for request_id, entry in self._pending.items()
            if entry.get("status") == "done" or (entry.get("status") or "").startswith("error")
            if float(request_id) < cutoff
        ]
        for request_id in stale:
            del self._pending[request_id]

    def poll_status(self, timestamp) -> str | None:
        if timestamp is None:
            return None
        request_id = f"{float(timestamp):.6f}"
        entry = self._pending.get(request_id)
        if entry is None:
            return None
        if entry["status"] == "pending" and (time.time() - entry["sent_at"]) > REQUEST_TIMEOUT_SECONDS:
            entry["status"] = "error_timeout"
        return None if entry["status"] == "pending" else entry["status"]

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
            "port": TRANSPORT_PORT,
            "client_connected": self._client is not None,
            "authenticated": self._authenticated,
            "video_catalog_entries": len(self._video_match_names_by_display),
        }

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

        self._client.textMessageReceived.connect(on_message)
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        self._send({"schemaVersion": 1, "type": action_type, "requestId": request_id, "payload": {}})
        loop.exec()
        timer.stop()
        try:
            self._client.textMessageReceived.disconnect(on_message)
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
