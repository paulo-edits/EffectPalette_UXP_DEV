"""Off-host tests for companion/uxp_execution_adapter.py.

The server is exercised end to end against an in-process QWebSocket client standing in for the
plugin: handshake, catalog pulls, request/response correlation, per-effect deadlines and the
reconnect/supersede lifecycle. Nothing here touches Premiere.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtWebSockets import QWebSocket

import uxp_execution_adapter as ux


def pump(ms: int) -> None:
    """Run the Qt event loop for a while (signals only fire while it runs)."""
    deadline = QtCore.QDeadlineTimer(ms)
    while not deadline.hasExpired():
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


def wait_until(predicate, timeout_ms: int = 3000) -> bool:
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while not deadline.hasExpired():
        if predicate():
            return True
        pump(20)
    return predicate()


class FakePlugin:
    """The plugin side of the protocol, as transport.js speaks it."""

    def __init__(self, port: int, token: str = ux.TRANSPORT_TOKEN):
        self.socket = QWebSocket()
        self.received: list[dict] = []
        self.acknowledged = False
        self.connected = False
        self.disconnected = False
        self.token = token
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_message)
        self.socket.open(QtCore.QUrl(f"ws://localhost:{port}"))

    def _on_connected(self):
        self.connected = True
        self.send({"type": "hello", "schemaVersion": 1, "token": self.token})

    def _on_disconnected(self):
        self.disconnected = True

    def _on_message(self, raw: str):
        message = json.loads(raw)
        if message.get("type") == "hello-ack":
            self.acknowledged = bool(message.get("ok"))
            return
        self.received.append(message)

    def send(self, payload: dict):
        self.socket.sendTextMessage(json.dumps(payload))

    def reply(self, request: dict, *, ok: bool = True, data: dict | None = None, error: dict | None = None):
        response = {"ok": ok, "schemaVersion": 1, "actionType": request["type"], "requestId": request["requestId"]}
        if ok:
            response["data"] = data or {}
        else:
            response["error"] = error or {"code": "EXECUTION_FAILED", "message": "boom"}
        self.send(response)

    def requests_of(self, action_type: str) -> list[dict]:
        return [m for m in self.received if m.get("type") == action_type]

    def close(self):
        self.socket.close()


class PureHelperTests(unittest.TestCase):
    def test_transition_entries_tag_vendor_and_keep_exact_match_name(self):
        entries = ux.build_transition_entries(["ADBE Cross Dissolve", "BCC_RADIALWIPE", "BCC Radial WipePrTr", "Zoom"])
        by_match = {e["matchName"]: e for e in entries}
        self.assertEqual(by_match["ADBE Cross Dissolve"]["name"], "(Adobe) Cross Dissolve")
        # The glued all-caps BCC entry borrows the spelled-out form, but keeps its own matchName.
        self.assertEqual(by_match["BCC_RADIALWIPE"]["name"], "(BCC) Radial Wipe [BCC_RADIALWIPE]")
        self.assertEqual(by_match["BCC Radial WipePrTr"]["name"], "(BCC) Radial Wipe [BCC Radial WipePrTr]")
        self.assertEqual(by_match["Zoom"]["name"], "Zoom")
        self.assertTrue(all(e["type"] == "transition_video" for e in entries))
        self.assertEqual(len({e["name"] for e in entries}), len(entries), "labels must stay unique")

    def test_timeouts_come_from_one_table(self):
        self.assertEqual(ux.timeout_seconds_for_effect({"type": "preset"}), ux.EFFECT_TIMEOUT_SECONDS["preset"])
        self.assertEqual(ux.timeout_seconds_for_effect({"type": "generic_item"}), ux.EFFECT_TIMEOUT_SECONDS["generic_item"])
        self.assertEqual(ux.timeout_seconds_for_effect({"type": "video"}), ux.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(ux.timeout_seconds_for_effect(None), ux.REQUEST_TIMEOUT_SECONDS)

    def test_error_code_to_status(self):
        self.assertEqual(ux._error_code_to_status("NO_SELECTION"), "error_no_selection")
        self.assertEqual(ux._error_code_to_status(None), "error")

    def test_find_prfpset_prefers_newest_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Documents" / "Adobe" / "Premiere Pro"
            old = root / "25.0" / "Profile-Paulo" / "Effect Presets and Custom Items.prfpset"
            new = root / "26.0" / "Profile-Paulo" / "Effect Presets and Custom Items.prfpset"
            for path in (old, new):
                path.parent.mkdir(parents=True)
                path.write_text("x")
            os.utime(old, (1, 1))
            previous = os.environ.get("USERPROFILE")
            os.environ["USERPROFILE"] = tmp
            try:
                self.assertEqual(ux.find_prfpset_file(), new)
            finally:
                if previous is None:
                    del os.environ["USERPROFILE"]
                else:
                    os.environ["USERPROFILE"] = previous


class AdapterServerTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self._saved = {}
        for name in ("UXP_TRANSITIONS_FILE", "UXP_FAVORITES_FILE", "UXP_EFFECTS_FILE", "UXP_PROJECT_ITEMS_FILE", "UXP_PRESETS_FILE"):
            self._saved[name] = getattr(ux, name)
            setattr(ux, name, data / self._saved[name].name)
        self.adapter = ux.PremiereUxpExecutionAdapter(port=0)  # port 0: let the OS pick a free one
        self.port = self.adapter._server.serverPort()
        self.plugins: list[FakePlugin] = []

    def tearDown(self):
        for plugin in self.plugins:
            plugin.close()
        pump(50)
        self.adapter._server.close()
        self.adapter.deleteLater()
        pump(50)
        for name, value in self._saved.items():
            setattr(ux, name, value)
        self.tmp.cleanup()

    def connect_plugin(self, **kwargs) -> FakePlugin:
        plugin = FakePlugin(self.port, **kwargs)
        self.plugins.append(plugin)
        self.assertTrue(wait_until(lambda: plugin.acknowledged or plugin.disconnected), "plugin never got an answer")
        return plugin

    def test_handshake_then_catalog_pulls(self):
        plugin = self.connect_plugin()
        self.assertTrue(plugin.acknowledged)
        self.assertTrue(wait_until(lambda: len(plugin.received) >= 5))
        types = {m["type"] for m in plugin.received}
        self.assertEqual(types, {
            "catalog.videoEffects.read", "catalog.videoTransitions.read", "catalog.favorites.read",
            "catalog.projectItems.read", "catalog.effectPresets.read", "catalog.effectPresets.readFromPath",
        } & types)
        self.assertIn("catalog.videoEffects.read", types)
        self.assertIn("catalog.videoTransitions.read", types)
        self.assertTrue(self.adapter.diagnostics()["authenticated"])

    def test_wrong_token_is_rejected(self):
        plugin = self.connect_plugin(token="not-the-token")
        self.assertFalse(plugin.acknowledged)
        self.assertTrue(wait_until(lambda: plugin.disconnected))
        self.assertFalse(self.adapter.diagnostics()["authenticated"])

    def test_catalog_files_written_once_per_change(self):
        plugin = self.connect_plugin()
        self.assertTrue(wait_until(lambda: plugin.requests_of("catalog.projectItems.read")))
        request = plugin.requests_of("catalog.projectItems.read")[0]
        items = [{"name": "Clip A", "treePath": "/Clip A"}]
        plugin.reply(request, data={"items": items})
        self.assertTrue(wait_until(lambda: ux.UXP_PROJECT_ITEMS_FILE.exists()))
        first_mtime = ux.UXP_PROJECT_ITEMS_FILE.stat().st_mtime_ns
        written = json.loads(ux.UXP_PROJECT_ITEMS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(written["items"], items)

        # The same catalog again (what the 5-second timer produces) must not rewrite the file.
        plugin.reply(request, data={"items": items})
        pump(150)
        self.assertEqual(ux.UXP_PROJECT_ITEMS_FILE.stat().st_mtime_ns, first_mtime)

        # A real change does.
        plugin.reply(request, data={"items": items + [{"name": "Clip B", "treePath": "/Clip B"}]})
        self.assertTrue(wait_until(lambda: ux.UXP_PROJECT_ITEMS_FILE.stat().st_mtime_ns != first_mtime))

    def test_execute_routes_result_by_request_id_and_maps_errors(self):
        plugin = self.connect_plugin()
        self.assertTrue(wait_until(lambda: plugin.requests_of("catalog.videoEffects.read")))
        plugin.reply(plugin.requests_of("catalog.videoEffects.read")[0], data={
            "displayNames": ["Gaussian Blur"], "matchNames": ["ADBE Gaussian Blur 2"], "audioDisplayNames": ["Reverb"],
        })
        self.assertTrue(wait_until(lambda: self.adapter._video_match_names_by_display))

        timestamp = self.adapter.execute({"type": "video", "name": "Gaussian Blur"})
        self.assertIsNone(self.adapter.poll_status(timestamp))
        self.assertTrue(wait_until(lambda: plugin.requests_of("timeline.applyVideoEffect")))
        request = plugin.requests_of("timeline.applyVideoEffect")[0]
        self.assertEqual(request["payload"], {"matchName": "ADBE Gaussian Blur 2"})

        # A plugin-side failure must surface with its own code, not as a timeout.
        plugin.reply(request, ok=False, error={"code": "NO_SELECTION", "message": "Select a clip first."})
        self.assertTrue(wait_until(lambda: self.adapter.poll_status(timestamp) == "error_no_selection"))
        self.assertTrue(self.adapter.is_terminal("error_no_selection"))
        self.assertFalse(self.adapter.is_success("error_no_selection"))

        # Identity check: the applied display name must match what was asked for.
        second = self.adapter.execute({"type": "video", "name": "Gaussian Blur"})
        self.assertTrue(wait_until(lambda: len(plugin.requests_of("timeline.applyVideoEffect")) == 2))
        plugin.reply(plugin.requests_of("timeline.applyVideoEffect")[1], data={"verification": [{"displayName": "Something Else"}]})
        self.assertTrue(wait_until(lambda: self.adapter.poll_status(second) == "error_identity_mismatch"))

        third = self.adapter.execute({"type": "video", "name": "Gaussian Blur"})
        self.assertTrue(wait_until(lambda: len(plugin.requests_of("timeline.applyVideoEffect")) == 3))
        plugin.reply(plugin.requests_of("timeline.applyVideoEffect")[2], data={"verification": [{"displayName": "Gaussian Blur"}]})
        self.assertTrue(wait_until(lambda: self.adapter.poll_status(third) == "done"))
        self.assertEqual(self.adapter.last_response_data(third), {"verification": [{"displayName": "Gaussian Blur"}]})

    def test_fail_closed_statuses_without_a_round_trip(self):
        self.assertEqual(self.adapter.poll_status(self.adapter.execute({"type": "video", "name": "x"})), "error_not_connected")
        self.assertEqual(self.adapter.poll_status(self.adapter.execute({"type": "transition_audio", "name": "x"})), "error_not_supported")
        plugin = self.connect_plugin()
        self.assertTrue(plugin.acknowledged)
        self.assertEqual(self.adapter.poll_status(self.adapter.execute({"type": "video", "name": "Unknown"})), "error_catalog_not_ready")
        self.assertEqual(self.adapter.poll_status(self.adapter.execute({"type": "generic_item", "genericKey": "color_matte"})), "error_not_supported")
        self.assertEqual(self.adapter.poll_status(self.adapter.execute({"type": "project_item", "name": "x"})), "error_tree_path_required")

    def test_per_effect_deadline_and_pruning(self):
        plugin = self.connect_plugin()
        self.assertTrue(plugin.acknowledged)
        timestamp = self.adapter.execute({"type": "preset", "name": "Zoom In", "category": "Presets"})
        entry = self.adapter._pending[f"{timestamp:.6f}"]
        self.assertEqual(entry["timeout_seconds"], ux.EFFECT_TIMEOUT_SECONDS["preset"])
        # Not timed out at the flat 5s...
        entry["sent_at"] = time.time() - (ux.REQUEST_TIMEOUT_SECONDS + 1)
        self.assertIsNone(self.adapter.poll_status(timestamp))
        # ...but timed out past its own deadline.
        entry["sent_at"] = time.time() - (ux.EFFECT_TIMEOUT_SECONDS["preset"] + 1)
        self.assertEqual(self.adapter.poll_status(timestamp), "error_timeout")

        # A request nobody polled (still "pending") is dropped once deadline + retention has passed.
        stale = self.adapter.execute({"type": "preset", "name": "Zoom Out", "category": "Presets"})
        self.adapter._pending[f"{stale:.6f}"]["sent_at"] = time.time() - 1000
        self.adapter._prune_pending()
        self.assertNotIn(f"{stale:.6f}", self.adapter._pending)
        self.assertIn(f"{timestamp:.6f}", self.adapter._pending, "recent entries survive pruning")

    def test_reconnect_supersedes_without_wiping_the_live_client(self):
        first = self.connect_plugin()
        self.assertTrue(first.acknowledged)
        second = self.connect_plugin()
        self.assertTrue(second.acknowledged)
        # The first socket is closed by the server; its disconnect must not reset the second.
        self.assertTrue(wait_until(lambda: first.disconnected))
        pump(100)
        self.assertTrue(self.adapter.diagnostics()["authenticated"])
        self.assertTrue(self.adapter.diagnostics()["client_connected"])
        self.assertTrue(wait_until(lambda: second.requests_of("catalog.videoEffects.read")), "new client gets its own catalog pull")

        # Dropping the live client really disconnects.
        second.close()
        self.assertTrue(wait_until(lambda: not self.adapter.diagnostics()["client_connected"]))
        self.assertFalse(self.adapter.refresh_catalogs())

    def test_refresh_catalogs_re_requests_everything(self):
        plugin = self.connect_plugin()
        self.assertTrue(wait_until(lambda: plugin.requests_of("catalog.videoEffects.read")))
        self.assertTrue(self.adapter.refresh_catalogs())
        self.assertTrue(wait_until(lambda: len(plugin.requests_of("catalog.videoEffects.read")) == 2))
        self.assertTrue(wait_until(lambda: len(plugin.requests_of("catalog.videoTransitions.read")) == 2))

    def test_blocking_request_returns_immediately_when_client_drops(self):
        plugin = self.connect_plugin()
        self.assertTrue(plugin.acknowledged)
        QtCore.QTimer.singleShot(50, plugin.close)
        started = time.monotonic()
        self.assertIsNone(self.adapter.next_nest_codename(timeout_ms=5000))
        self.assertLess(time.monotonic() - started, 3.0, "must not wait out the whole timeout")

    def test_next_nest_codename_counts_existing_sequences(self):
        plugin = self.connect_plugin()
        self.assertTrue(plugin.acknowledged)

        def answer():
            for request in plugin.requests_of("diagnostics.read"):
                if request["requestId"] == "adapter-nest-codename":
                    plugin.reply(request, data={"project": {"sequenceNames": ["FXN-002", "Other", "fxn-007"]}})
                    return True
            return False

        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: answer() and timer.stop())
        timer.start(20)
        self.assertEqual(self.adapter.next_nest_codename(timeout_ms=3000), "FXN-008")


if __name__ == "__main__":
    unittest.main()
