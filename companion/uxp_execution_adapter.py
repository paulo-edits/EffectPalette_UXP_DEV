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
import time

from PySide6 import QtCore
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer

TRANSPORT_PORT = 58756
TRANSPORT_TOKEN = "fxpalette-uxp-transport-v1-2eaf1cf6a94b4a5b8f0e3b7c9a5d6e21"

REQUEST_TIMEOUT_SECONDS = 5.0
PENDING_RETENTION_SECONDS = 60.0
CATALOG_REQUEST_ID = "uxp-adapter-video-catalog"

# On: the plugin rebuilds each animated parameter's curve from the .prfpset's own
# speed/influence fields and frame-samples it, measured indistinguishable from a manual
# application in rendered output (0.016 px worst case, CAPABILITY_MATRIX.md). Off: only the
# principal keyframes are written, so easing shape is lost - which is what a user notices
# immediately on a preset built around its curve. Product default is therefore on; the plugin's
# diagnostics panel keeps its own checkbox for isolating the two behaviours during testing.
RECONSTRUCT_EASING_DEFAULT = True

# effect["type"] values this first slice translates. Every other type returns
# error_not_supported immediately - callers see a clear failure instead of a hang.
_SUPPORTED_EFFECT_TYPES = {"video", "audio", "preset"}


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
                self._request_video_catalog()
            elif self._client is not None:
                self._client.close()
            return

        request_id = message.get("requestId")
        if request_id == CATALOG_REQUEST_ID:
            self._handle_catalog_response(message)
            return
        if request_id in self._pending:
            self._resolve_pending(request_id, message)

    def _send(self, payload: dict):
        if self._client is None:
            return
        self._client.sendTextMessage(json.dumps(payload))

    # ---- video match-name resolution -------------------------------------------

    def _request_video_catalog(self):
        if self._catalog_requested:
            return
        self._catalog_requested = True
        self._send({
            "schemaVersion": 1,
            "type": "catalog.videoEffects.read",
            "requestId": CATALOG_REQUEST_ID,
            "payload": {},
        })

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
