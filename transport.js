"use strict";

// Localhost transport (TECHNICAL_PLAN.md stage 5). Connects this plugin to the Python companion so
// every Premiere operation the product needs arrives as one execution-adapter.js action. This file
// only ever calls out over WebSocket; it never listens, matching the only network capability
// official UXP documentation describes (fetch/XHR/WebSocket as a client). The Python companion is
// therefore the server, and this plugin is the client that reconnects to it.
//
// The port and token below are fixed at build time on purpose: UXP's manifest network permission
// requires an exact pre-declared domain, so there is no way to negotiate a port at runtime without
// user configuration - and a fixed, unconfigured port is exactly what "no setup" requires. The token
// is a shared constant baked into this plugin bundle, not a real secret: anything shipped to the
// user's machine can be read by anything else running on that same machine with any code-execution
// capability of its own. Its purpose is to keep this channel from accidentally cross-talking with an
// unrelated local service that happens to speak a similar JSON-over-WebSocket protocol, not to
// defend against a co-located attacker - localhost-only Windows process isolation is what actually
// keeps other machines out; nothing here is a substitute for that.
const TRANSPORT_PORT = 58756;
const TRANSPORT_TOKEN = "fxpalette-uxp-transport-v1-2eaf1cf6a94b4a5b8f0e3b7c9a5d6e21";
const TRANSPORT_URL = `ws://localhost:${TRANSPORT_PORT}`;

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000, 10000, 20000, 30000];
const WEBSOCKET_OPEN = 1;

let socket = null;
let stopped = true;
let reconnectAttempt = 0;
let reconnectTimer = null;
let handshakeAcknowledged = false;
let handlersRef = null;
let executionAdapterRef = null;

// Read-only view of the connection, surfaced through diagnostics; it never drives the connection.
const state = {
  status: "disconnected", // disconnected | connecting | connected | error
  lastError: null,
  connectedSince: null,
  lastMessageAt: null
};

function setStatus(status, extra) {
  state.status = status;
  if (extra && extra.lastError !== undefined) state.lastError = extra.lastError;
  if (extra && extra.connectedSince !== undefined) state.connectedSince = extra.connectedSince;
}

function errorMessage(error) {
  return error && error.message ? error.message : String(error);
}

function scheduleReconnect() {
  // stop() must be final: the close event of the socket it just closed still fires afterwards,
  // and without this guard that event would schedule a fresh connection from a destroyed plugin.
  if (stopped || reconnectTimer) return;
  const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)];
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, delay);
}

function sendOn(ws, payload) {
  if (!ws) return false;
  // readyState is only consulted when the runtime exposes it - not every WebSocket implementation
  // does, and a missing property must not silently disable the transport.
  if (typeof ws.readyState === "number" && ws.readyState !== WEBSOCKET_OPEN) return false;
  try {
    ws.send(JSON.stringify(payload));
    return true;
  } catch (error) {
    // The socket closed between the check and the send; its close listener drives reconnection.
    return false;
  }
}

async function handleIncoming(ws, raw) {
  state.lastMessageAt = new Date().toISOString();
  let message;
  try {
    message = JSON.parse(raw);
  } catch (error) {
    return; // Not our protocol; ignore rather than crash the connection over a stray frame.
  }

  if (!handshakeAcknowledged) {
    if (message && message.type === "hello-ack" && message.ok === true) {
      handshakeAcknowledged = true;
      reconnectAttempt = 0;
      setStatus("connected", { connectedSince: new Date().toISOString(), lastError: null });
    } else {
      // The server did not accept our token; do not treat any further traffic as trusted.
      try { ws.close(); } catch (error) { /* already closing */ }
    }
    return;
  }

  // Every message after the handshake is one already-shaped executionAdapter action; the response
  // is that same executionAdapter result shape, echoing requestId so the companion can correlate it.
  // The reply goes to the socket the request arrived on: if that connection dropped while the
  // handler ran, the result is discarded rather than sent down a newer connection that never asked.
  const result = await executionAdapterRef.execute(message, handlersRef);
  sendOn(ws, result);
}

function openSocket() {
  if (stopped || socket) return;
  if (typeof WebSocket === "undefined") {
    // Defensive: if this UXP runtime does not expose WebSocket, fail visibly in diagnostics rather
    // than silently never connecting.
    setStatus("error", { lastError: "WebSocket is not available in this UXP runtime." });
    return;
  }
  setStatus("connecting");
  handshakeAcknowledged = false;
  let ws;
  try {
    ws = new WebSocket(TRANSPORT_URL);
  } catch (error) {
    setStatus("error", { lastError: errorMessage(error) });
    scheduleReconnect();
    return;
  }
  socket = ws;

  // Every listener checks it still belongs to the current socket. A superseded socket's late
  // close/error/message events must not touch the connection that replaced it.
  ws.addEventListener("open", () => {
    if (ws !== socket) return;
    if (!sendOn(ws, { type: "hello", schemaVersion: 1, token: TRANSPORT_TOKEN })) {
      setStatus("error", { lastError: "Could not send the transport handshake." });
    }
  });

  ws.addEventListener("message", (event) => {
    if (ws !== socket) return;
    handleIncoming(ws, event.data);
  });

  ws.addEventListener("close", () => {
    if (ws !== socket) return;
    const wasConnected = handshakeAcknowledged;
    socket = null;
    handshakeAcknowledged = false;
    setStatus("disconnected", wasConnected ? { connectedSince: null } : undefined);
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    if (ws !== socket) return;
    // The close event still fires after error and is what actually drives reconnection; this only
    // records the failure for diagnostics.
    setStatus("error", { lastError: "WebSocket connection error." });
  });
}

// handlers must be the same { actionType: handlerFunction } map index.js uses for everything, so
// the transport never has a different notion of what an action does than the plugin itself.
function start(handlers, executionAdapter) {
  handlersRef = handlers;
  executionAdapterRef = executionAdapter;
  stopped = false;
  reconnectAttempt = 0;
  openSocket();
}

function stop() {
  stopped = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  const ws = socket;
  socket = null;
  handshakeAcknowledged = false;
  if (ws) {
    try { ws.close(); } catch (error) { /* best effort */ }
  }
  setStatus("disconnected", { connectedSince: null });
}

function getStatus() {
  return { ...state };
}

// Fire-and-forget progress line, sent mid-request while an executionAdapter action is still
// awaiting (handleIncoming only sends the final result once execute() resolves) - used to diagnose
// a hang inside a long multi-step action (ensureGenericProjectItem, index.js) without needing the
// UXP plugin's own DevTools console, which is harder to reach than the companion's own log output.
function sendDiagnosticLog(message) {
  if (!handshakeAcknowledged) return;
  sendOn(socket, { type: "diagnostic.log", message: String(message) });
}

module.exports = { start, stop, getStatus, sendDiagnosticLog, TRANSPORT_PORT, TRANSPORT_URL };
