"use strict";

const ACTION_SCHEMA_VERSION = 1;
const SUPPORTED_ACTIONS = Object.freeze([
  "diagnostics.read",
  "catalog.videoEffects.read",
  "catalog.videoTransitions.read",
  "catalog.favorites.read",
  "catalog.projectItems.read",
  "timeline.applyVideoEffect",
  "catalog.effectPresets.read",
  "catalog.effectPresets.readFromPath",
  "catalog.effectPresets.importPrfpset",
  "timeline.applyImportedEffectPreset",
  "timeline.applyAudioEffect",
  "timeline.applyVideoTransition",
  "timeline.createNest",
  "timeline.insertProjectItem",
  "timeline.checkTrackAvailability"
]);

// requestId is echoed on failures for the same reason it is on successes: the companion
// correlates every response by it alone (uxp_execution_adapter.py routes with
// `if request_id in self._pending`), so a failure without one is dropped as an unknown frame -
// leaving the request pending until it surfaces as a generic 5s timeout, with the real error
// message lost. null only where the caller genuinely had no action object to read one from.
function failure(code, message, actionType, requestId) {
  return {
    ok: false,
    schemaVersion: ACTION_SCHEMA_VERSION,
    actionType: actionType || null,
    requestId: typeof requestId === "string" ? requestId : null,
    error: { code, message }
  };
}

function readRequestId(action) {
  return action && typeof action === "object" && typeof action.requestId === "string"
    ? action.requestId
    : null;
}

function normalizeAction(action) {
  if (!action || typeof action !== "object" || Array.isArray(action)) {
    return failure("INVALID_ACTION", "Action must be a serializable object.");
  }

  const requestId = readRequestId(action);
  const type = typeof action.type === "string" ? action.type.trim() : "";
  if (!type) {
    return failure("MISSING_ACTION_TYPE", "Action type is required.", null, requestId);
  }

  if (SUPPORTED_ACTIONS.indexOf(type) === -1) {
    return failure("UNSUPPORTED_ACTION", "Action is not supported by this plugin.", type, requestId);
  }

  return {
    ok: true,
    action: {
      schemaVersion: ACTION_SCHEMA_VERSION,
      type,
      requestId: typeof action.requestId === "string" ? action.requestId : null,
      payload: action.payload && typeof action.payload === "object" && !Array.isArray(action.payload)
        ? action.payload
        : {}
    }
  };
}

async function execute(action, handlers) {
  const normalized = normalizeAction(action);
  if (!normalized.ok) return normalized;

  const handler = handlers && handlers[normalized.action.type];
  if (typeof handler !== "function") {
    return failure("MISSING_HANDLER", "No execution handler is registered.", normalized.action.type, normalized.action.requestId);
  }

  try {
    const data = await handler(normalized.action);
    return {
      ok: true,
      schemaVersion: ACTION_SCHEMA_VERSION,
      actionType: normalized.action.type,
      requestId: normalized.action.requestId,
      data
    };
  } catch (error) {
    return failure("EXECUTION_FAILED", error && error.message ? error.message : String(error), normalized.action.type, normalized.action.requestId);
  }
}

module.exports = { ACTION_SCHEMA_VERSION, SUPPORTED_ACTIONS, normalizeAction, execute };
