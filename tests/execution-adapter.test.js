"use strict";

const assert = require("assert");
const adapter = require("../execution-adapter.js");

async function run() {
  assert.strictEqual(adapter.normalizeAction(null).error.code, "INVALID_ACTION");
  assert.strictEqual(adapter.normalizeAction({}).error.code, "MISSING_ACTION_TYPE");
  assert.strictEqual(adapter.normalizeAction({ type: "arbitrary.execute" }).error.code, "UNSUPPORTED_ACTION");
  assert.strictEqual(adapter.normalizeAction({ type: "projectItems.setColorLabel" }).error.code, "UNSUPPORTED_ACTION");

  // Every action the companion actually sends must resolve.
  for (const type of [
    "diagnostics.read",
    "catalog.videoEffects.read",
    "catalog.videoTransitions.read",
    "catalog.favorites.read",
    "catalog.projectItems.read",
    "catalog.effectPresets.read",
    "catalog.effectPresets.readFromPath",
    "catalog.effectPresets.importPrfpset",
    "timeline.applyVideoEffect",
    "timeline.applyImportedEffectPreset",
    "timeline.applyAudioEffect",
    "timeline.applyVideoTransition",
    "timeline.createNest",
    "timeline.organizeNativeNest",
    "timeline.insertProjectItem",
    "timeline.checkTrackAvailability"
  ]) {
    assert.strictEqual(adapter.normalizeAction({ type }).ok, true, `expected ${type} to be supported`);
  }

  // Research / diagnostic probes were removed from the shipped allowlist (git history keeps them),
  // as were the two actions the companion never sent (2026-09 audit).
  for (const type of [
    "timeline.createSubsequence",
    "timeline.insertGenericItem",
    "catalog.videoEffects.resolve",
    "timeline.probeVideoEffectParameters",
    "timeline.probeStaticVideoEffectParameter",
    "timeline.probeAnimatedVideoEffectParameter",
    "timeline.captureTransformCurveReference",
    "timeline.applyTransformCurveReference",
    "catalog.effectPresets.inspectImported",
    "catalog.effectPresets.inspectBridgeCandidate",
    "catalog.effectPresets.exportBridge",
    "catalog.effectPresets.compareImportedTransform",
    "timeline.inspectSelectedVideoComponents",
    "motracker.testNest",
    "motracker.getClipInfo",
    "motracker.getFollowTargetMediaPath",
    "motracker.applyTrack"
  ]) {
    assert.strictEqual(adapter.normalizeAction({ type }).error.code, "UNSUPPORTED_ACTION", `expected ${type} to be removed`);
  }

  const action = adapter.normalizeAction({
    type: "diagnostics.read",
    requestId: "test-request",
    payload: { readOnly: true }
  });
  assert.strictEqual(action.ok, true);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(action)), action);

  const result = await adapter.execute(action.action, {
    "diagnostics.read": async () => ({ hostVersion: "test" })
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.requestId, "test-request");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(result)), result);

  // Every failure response must echo requestId. The companion routes strictly by it
  // (uxp_execution_adapter.py's _handle_text_message: `if request_id in self._pending`), so a
  // failure without one is dropped on the floor - the request stays pending and surfaces to the
  // user as a 5s "timeout" with the plugin's real error message discarded.
  const failingAction = { type: "diagnostics.read", requestId: "failure-request", payload: {} };

  const thrown = await adapter.execute(failingAction, {
    "diagnostics.read": async () => { throw new Error("host said no"); }
  });
  assert.strictEqual(thrown.ok, false);
  assert.strictEqual(thrown.error.code, "EXECUTION_FAILED");
  assert.strictEqual(thrown.requestId, "failure-request");

  const noHandler = await adapter.execute(failingAction, {});
  assert.strictEqual(noHandler.error.code, "MISSING_HANDLER");
  assert.strictEqual(noHandler.requestId, "failure-request");

  const unsupported = await adapter.execute(
    { type: "timeline.notARealAction", requestId: "failure-request", payload: {} }, {});
  assert.strictEqual(unsupported.error.code, "UNSUPPORTED_ACTION");
  assert.strictEqual(unsupported.requestId, "failure-request");

  const noType = await adapter.execute({ requestId: "failure-request" }, {});
  assert.strictEqual(noType.error.code, "MISSING_ACTION_TYPE");
  assert.strictEqual(noType.requestId, "failure-request");

  // A non-object action carries no requestId to echo; it must still be a well-formed response.
  const invalid = await adapter.execute("not an action", {});
  assert.strictEqual(invalid.error.code, "INVALID_ACTION");
  assert.strictEqual(invalid.requestId, null);

  console.log("Execution adapter tests passed.");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
