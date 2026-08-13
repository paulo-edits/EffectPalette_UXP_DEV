# CEP to UXP comparison notes

This document records product-behavior evidence from the read-only `Effect-Palette_DEV` checkout and the corresponding official UXP path. It does not authorize CEP fallbacks in this repository.

## Video effect application

### Existing CEP behavior

- Python sends a serializable dictionary through `PremiereExecutionAdapter`.
- `bridge.js` routes the effect name/type and a previously captured selection snapshot to `applyEffectWithSelection()`.
- `host.jsx` enables the undocumented QE DOM, normalizes the display name, resolves a QE effect, then re-identifies each selected clip using track index and approximate start ticks.
- Application is confirmed by comparing standard-DOM component snapshots because QE calls can fail ambiguously.

### Official UXP path

- `VideoFilterFactory.getMatchNames()` supplies stable runtime match names.
- `Sequence.getSelection()` supplies the actual current `TrackItemSelection`; no persisted selection JSON or tick-based clip lookup is needed. Selected-item media `Guid` values are compared with video-track media `Guid` values to exclude audio items.
- Each selected `VideoClipTrackItem` supplies its `VideoComponentChain` directly.
- `VideoFilterFactory.createComponent(matchName)` creates the component.
- `VideoComponentChain.createAppendComponentAction()` plus `Project.lockedAccess()` and `Project.executeTransaction()` creates one undoable operation.

### Boundary mapping

Provisional UXP request:

```json
{
  "schemaVersion": 1,
  "type": "timeline.applyVideoEffect",
  "requestId": "caller-generated-id",
  "payload": {
    "matchName": "AE.ADBE Mosaic"
  }
}
```

The production adapter should resolve product search results to exact UXP match names before dispatch. Display-name normalization belongs in catalog/search preparation, not in the host mutation handler.

### Known differences

- The current CEP path supports video and audio effects; the first UXP probe is video-only.
- Preset application remains unknown because no official effect-preset catalog/application API has been identified.
- Locale/display-name behavior should be separated from match-name identity.
- Duplicate-effect behavior and multi-clip partial failure behavior require host tests.

## Reference locations (read-only)

- `EffectPalette/app.py`: `PremiereExecutionAdapter`, `execute_effect_through_adapter()`.
- `EffectPalette/bridge.js`: `applyEffect(cmd)`.
- `EffectPalette/scripts/host.jsx`: `applyEffectWithSelection()`.
