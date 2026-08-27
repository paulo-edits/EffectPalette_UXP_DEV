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
    "timeline.createSubsequence",
    "timeline.createNest",
    "timeline.insertProjectItem",
    "timeline.insertGenericItem",
    "timeline.checkTrackAvailability",
    "motracker.getClipInfo",
    "motracker.getFollowTargetMediaPath",
    "motracker.testNest",
    "motracker.applyTrack"
  ]) {
    assert.strictEqual(adapter.normalizeAction({ type }).ok, true, `expected ${type} to be supported`);
  }

  // Research / diagnostic probes were removed from the shipped allowlist (git history keeps them).
  for (const type of [
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
    "timeline.inspectSelectedVideoComponents"
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

  console.log("Execution adapter tests passed.");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
