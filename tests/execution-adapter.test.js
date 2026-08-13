"use strict";

const assert = require("assert");
const adapter = require("../execution-adapter.js");

async function run() {
  assert.strictEqual(adapter.normalizeAction(null).error.code, "INVALID_ACTION");
  assert.strictEqual(adapter.normalizeAction({}).error.code, "MISSING_ACTION_TYPE");
  assert.strictEqual(adapter.normalizeAction({ type: "arbitrary.execute" }).error.code, "UNSUPPORTED_ACTION");
  assert.strictEqual(adapter.normalizeAction({ type: "projectItems.setColorLabel" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.applyVideoEffect" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.probeVideoEffectParameters" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.probeStaticVideoEffectParameter" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.probeAnimatedVideoEffectParameter" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.captureTransformCurveReference" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.applyTransformCurveReference" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "catalog.effectPresets.importPrfpset" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "catalog.effectPresets.inspectImported" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.applyImportedTransformPreset" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.applyAudioEffect" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.applyVideoTransition" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.createSubsequence" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.createNest" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.insertProjectItem" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "timeline.insertGenericItem" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "catalog.videoEffects.resolve" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "catalog.videoEffects.read" }).ok, true);
  assert.strictEqual(adapter.normalizeAction({ type: "catalog.videoTransitions.read" }).ok, true);

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
