"use strict";

// Off-host test of transport.js against a fake WebSocket. It covers the client-side contract the
// companion relies on (handshake, request/response echo, reconnection) and the two lifecycle bugs
// fixed in the 2026-09 audit: a superseded socket's late events touching the live connection, and
// stop() being undone by the close event of the socket it had just closed.

const assert = require("assert");

const instances = [];

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.closed = false;
    this._listeners = {};
    instances.push(this);
  }
  addEventListener(type, listener) {
    (this._listeners[type] = this._listeners[type] || []).push(listener);
  }
  _emit(type, event) {
    (this._listeners[type] || []).forEach((listener) => listener(event || {}));
  }
  send(data) {
    if (this.readyState !== 1) throw new Error("socket is not open");
    this.sent.push(JSON.parse(data));
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.readyState = 3;
    this._emit("close");
  }
  // Server-side helpers.
  serverOpen() { this.readyState = 1; this._emit("open"); }
  serverSend(payload) { this._emit("message", { data: JSON.stringify(payload) }); }
  serverDrop() { this.close(); }
}

global.WebSocket = FakeWebSocket;

const transport = require("../transport.js");
const executionAdapter = require("../execution-adapter.js");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function handshake(ws) {
  ws.serverOpen();
  assert.deepStrictEqual(ws.sent[0], { type: "hello", schemaVersion: 1, token: ws.sent[0].token });
  assert.strictEqual(typeof ws.sent[0].token, "string");
  ws.serverSend({ type: "hello-ack", ok: true });
  assert.strictEqual(transport.getStatus().status, "connected");
}

async function run() {
  let resolveSlowHandler;
  const handlers = {
    "diagnostics.read": async () => ({ hostVersion: "fake" }),
    "timeline.applyVideoEffect": () => new Promise((resolve) => { resolveSlowHandler = resolve; })
  };

  // 1. Connect, handshake, request/response echo.
  transport.start(handlers, executionAdapter);
  assert.strictEqual(instances.length, 1);
  assert.strictEqual(transport.getStatus().status, "connecting");
  const ws1 = instances[0];
  assert.strictEqual(ws1.url, transport.TRANSPORT_URL);
  handshake(ws1);

  ws1.serverSend({ schemaVersion: 1, type: "diagnostics.read", requestId: "r1", payload: {} });
  await flush();
  const reply = ws1.sent[1];
  assert.strictEqual(reply.ok, true);
  assert.strictEqual(reply.requestId, "r1");
  assert.deepStrictEqual(reply.data, { hostVersion: "fake" });

  // Failures are answered too, with the requestId the companion routes by.
  ws1.serverSend({ schemaVersion: 1, type: "timeline.notReal", requestId: "r2", payload: {} });
  await flush();
  assert.strictEqual(ws1.sent[2].ok, false);
  assert.strictEqual(ws1.sent[2].requestId, "r2");
  assert.strictEqual(ws1.sent[2].error.code, "UNSUPPORTED_ACTION");

  // Diagnostic log lines go out only on an acknowledged connection.
  transport.sendDiagnosticLog("progress");
  assert.deepStrictEqual(ws1.sent[3], { type: "diagnostic.log", message: "progress" });

  // 2. A dropped connection reconnects (first delay 500ms) and a superseded socket's late events
  //    do not disturb the replacement.
  ws1.serverSend({ schemaVersion: 1, type: "timeline.applyVideoEffect", requestId: "slow", payload: {} });
  await flush();
  ws1.serverDrop();
  assert.strictEqual(transport.getStatus().status, "disconnected");
  await sleep(650);
  assert.strictEqual(instances.length, 2, "expected one reconnection attempt");
  const ws2 = instances[1];
  handshake(ws2);

  ws1._emit("close");           // late duplicate close from the dead socket
  ws1._emit("error");
  ws1.serverSend({ schemaVersion: 1, type: "diagnostics.read", requestId: "stale", payload: {} });
  await flush();
  assert.strictEqual(transport.getStatus().status, "connected", "stale close must not reset the live socket");
  await sleep(650);
  assert.strictEqual(instances.length, 2, "stale close must not schedule another reconnect");
  assert.strictEqual(ws2.sent.length, 1, "a stale socket's request must not be answered on the new one");

  // The slow handler that started on ws1 resolves after ws1 died: its result must be dropped, not
  // sent down ws2, which never issued that request.
  resolveSlowHandler({ applied: true });
  await flush();
  assert.strictEqual(ws2.sent.length, 1);

  // 3. A server that does not acknowledge the token gets closed and is not trusted.
  ws2.serverDrop();
  await sleep(650);
  assert.strictEqual(instances.length, 3);
  const ws3 = instances[2];
  ws3.serverOpen();
  ws3.serverSend({ schemaVersion: 1, type: "diagnostics.read", requestId: "untrusted", payload: {} });
  await flush();
  assert.strictEqual(ws3.closed, true, "traffic before hello-ack must close the socket");
  assert.strictEqual(ws3.sent.length, 1, "only the hello may have been sent");

  // 4. stop() is final: the close event it triggers must not schedule a reconnection.
  //    (ws3 never completed a handshake, so this is the second back-off step: 1000ms.)
  await sleep(1200);
  assert.strictEqual(instances.length, 4);
  const ws4 = instances[3];
  handshake(ws4);
  transport.stop();
  assert.strictEqual(ws4.closed, true);
  assert.strictEqual(transport.getStatus().status, "disconnected");
  await sleep(1200);
  assert.strictEqual(instances.length, 4, "stop() must not be followed by a reconnection");
  transport.sendDiagnosticLog("after stop");   // must be a no-op, not a throw

  // 5. start() after stop() connects again.
  transport.start(handlers, executionAdapter);
  assert.strictEqual(instances.length, 5);
  transport.stop();

  console.log("Transport tests passed.");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
