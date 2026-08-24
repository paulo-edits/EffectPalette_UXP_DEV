"use strict";

const ACTION_SCHEMA_VERSION = 1;
const SUPPORTED_ACTIONS = Object.freeze([
  "diagnostics.read",
  "catalog.videoEffects.resolve",
  "catalog.videoEffects.read",
  "catalog.videoTransitions.read",
  "timeline.applyVideoEffect",
  "timeline.probeVideoEffectParameters",
  "timeline.probeStaticVideoEffectParameter",
  "timeline.probeAnimatedVideoEffectParameter",
  "timeline.captureTransformCurveReference",
  "timeline.applyTransformCurveReference",
  "catalog.effectPresets.importPrfpset",
  "catalog.effectPresets.inspectImported",
  "catalog.effectPresets.inspectBridgeCandidate",
  "catalog.effectPresets.exportBridge",
  "catalog.effectPresets.compareImportedTransform",
  "timeline.inspectSelectedVideoComponents",
  "timeline.applyImportedEffectPreset",
  "timeline.applyAudioEffect",
  "timeline.applyVideoTransition",
  "timeline.createSubsequence",
  "timeline.createNest",
  "timeline.insertProjectItem",
  "timeline.insertGenericItem"
]);

function failure(code, message, actionType) {
  return {
    ok: false,
    schemaVersion: ACTION_SCHEMA_VERSION,
    actionType: actionType || null,
    error: { code, message }
  };
}

function normalizeAction(action) {
  if (!action || typeof action !== "object" || Array.isArray(action)) {
    return failure("INVALID_ACTION", "Action must be a serializable object.");
  }

  const type = typeof action.type === "string" ? action.type.trim() : "";
  if (!type) {
    return failure("MISSING_ACTION_TYPE", "Action type is required.");
  }

  if (SUPPORTED_ACTIONS.indexOf(type) === -1) {
    return failure("UNSUPPORTED_ACTION", "Action is not implemented in this proof of concept.", type);
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
    return failure("MISSING_HANDLER", "No execution handler is registered.", normalized.action.type);
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
    return failure("EXECUTION_FAILED", error && error.message ? error.message : String(error), normalized.action.type);
  }
}

module.exports = { ACTION_SCHEMA_VERSION, SUPPORTED_ACTIONS, normalizeAction, execute };
