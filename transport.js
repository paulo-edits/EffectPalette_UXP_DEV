"use strict";

// Optional localhost transport (TECHNICAL_PLAN.md stage 5). Connects this plugin to the stable
// product's Python companion so the UXP side can eventually replace CEP for the operations
// CAPABILITY_MATRIX.md already proves have parity, per the architecture decision in
// TECHNICAL_PLAN.md. This file only ever calls out over WebSocket; it never listens, matching the
// only network capability official UXP documentation describes (fetch/XHR/WebSocket as a client).
// The Python companion is therefore the server, and this plugin is the client that reconnects to it.
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

let socket = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let handshakeAcknowledged = false;
let handlersRef = null;
let executionAdapterRef = null;
let statusListeners = [];

// Exposed for the diagnostics panel, so the transport's live state can be shown without needing a
// separate probe action - this is read-only, it never drives the connection itself.
const state = {
  status: "disconnected", // disconnected | connecting | connected | error
  lastError: null,
  connectedSince: null,
  lastMessageAt: null
};

function notifyStatus() {
  statusListeners.forEach((listener) => {
    try { listener({ ...state }); } catch (error) { /* a bad listener must not break the transport */ }
  });
}

function setStatus(status, extra) {
  state.status = status;
  if (extra && extra.lastError !== undefined) state.lastError = extra.lastError;
  if (extra && extra.connectedSince !== undefined) state.connectedSince = extra.connectedSince;
  notifyStatus();
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)];
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, delay);
}

async function handleIncoming(raw) {
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
      try { socket.close(); } catch (error) { /* already closing */ }
    }
    return;
  }

  // Every message after the handshake is one already-shaped executionAdapter action; the response
  // is that same executionAdapter result shape, echoing requestId so the companion can correlate it.
  const result = await executionAdapterRef.execute(message, handlersRef);
  try {
    socket.send(JSON.stringify(result));
  } catch (error) {
    // The socket likely closed between receiving the request and finishing the handler; the
    // close/error listener below will already be driving reconnection.
  }
}

function openSocket() {
  if (socket) return;
  if (typeof WebSocket === "undefined") {
    // Defensive: if this UXP runtime does not expose WebSocket, fail visibly in diagnostics rather
    // than silently never connecting.
    setStatus("error", { lastError: "WebSocket is not available in this UXP runtime." });
    return;
  }
  setStatus("connecting");
  handshakeAcknowledged = false;
  try {
    socket = new WebSocket(TRANSPORT_URL);
  } catch (error) {
    socket = null;
    setStatus("error", { lastError: error && error.message ? error.message : String(error) });
    scheduleReconnect();
    return;
  }

  socket.addEventListener("open", () => {
    try {
      socket.send(JSON.stringify({ type: "hello", schemaVersion: 1, token: TRANSPORT_TOKEN }));
    } catch (error) {
      setStatus("error", { lastError: error && error.message ? error.message : String(error) });
    }
  });

  socket.addEventListener("message", (event) => { handleIncoming(event.data); });

  socket.addEventListener("close", () => {
    const wasConnected = handshakeAcknowledged;
    socket = null;
    handshakeAcknowledged = false;
    setStatus("disconnected", wasConnected ? { connectedSince: null } : undefined);
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    // The close event still fires after error and is what actually drives reconnection; this only
    // records the failure for diagnostics.
    setStatus("error", { lastError: "WebSocket connection error." });
  });
}

// handlers must be the same { actionType: handlerFunction } map the diagnostics panel already uses,
// so the transport never has a different notion of what an action does than the visible UI does.
function start(handlers, executionAdapter) {
  handlersRef = handlers;
  executionAdapterRef = executionAdapter;
  openSocket();
}

function stop() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (socket) {
    try { socket.close(); } catch (error) { /* best effort */ }
    socket = null;
  }
  handshakeAcknowledged = false;
  setStatus("disconnected");
}

function onStatusChange(listener) {
  statusListeners.push(listener);
  return () => { statusListeners = statusListeners.filter((entry) => entry !== listener); };
}

function getStatus() {
  return { ...state };
}

module.exports = { start, stop, onStatusChange, getStatus, TRANSPORT_PORT, TRANSPORT_URL };
