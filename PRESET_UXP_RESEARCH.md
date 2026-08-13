# Effect preset research: CEP baseline and official UXP path

## Conclusion

The current Premiere UXP API (25.6 through the 26.3 reference reviewed on 2026-08-13) exposes no native effect-preset catalog, preset object, or `applyPreset` operation. This does **not** make preset support impossible. The official API exposes the lower-level operations required to reconstruct a preset: create an effect component, enumerate its parameters, set static values, enable time variation, add typed keyframes, and set interpolation.

The proposed UXP implementation therefore remains official-only. It will parse the user's Premiere `.prfpset` data as an external serialization format, translate it into a versioned internal request, and execute the result using only documented UXP component and parameter actions. It will not call QE, ExtendScript, CEP, menu-command IDs, or keyboard shortcuts.

## What the stable CEP product actually does

The read-only `Effect-Palette_DEV` implementation was inspected end to end:

1. `bridge.js` locates `Documents/Adobe/Premiere Pro/<version>/<profile>/Effect Presets and Custom Items.prfpset`.
2. It parses the XML, follows `ObjectID` references, traverses the `Presets` bin, and generates `data/premiere_presets.json`.
3. Every preset contains one or more filter records with effect identity, parameter index/type/value and optional keyframes.
4. `scripts/host.jsx::applyPresetWithSelection()` does not invoke a native Premiere preset object. It reconstructs the preset: QE adds each effect, the standard DOM finds the new component, and `_applyPresetParams()` writes static values or clip-relative keyframes.
5. CEP passes the native interpolation number stored in the `.prfpset` to `setInterpolationTypeAtKey()`. Its current comments and README already acknowledge that easing fidelity is not complete.

The catalog snapshot examined contains 1,833 presets, 2,560 video-filter instances, 209 audio-filter instances and 1,610 animated presets. A preset may contain up to 15 filters and 267 parameters. This rules out a special-case implementation aimed at only one effect or one value type.

## Documented official UXP building blocks

| Need | Official surface | Evidence status |
| --- | --- | --- |
| Create video/audio effects | `VideoFilterFactory` / `AudioFilterFactory` and component-chain append/insert actions | Already tested in Premiere 26.3.2 in this PoC |
| Enumerate parameters | `Component.getParamCount()` and `Component.getParam(index)` | Implemented in the 0.17.0 probe; Premiere host test pending |
| Read parameter identity | `ComponentParam.displayName` | Documented; names are localized and must not be the primary key |
| Set a static value | `ComponentParam.createKeyframe(value)` and `createSetValueAction(keyframe, safeForPlayback)` | Tested with Gamma 10 → 20 in Premiere 26.3.2 |
| Enable animation | `createSetTimeVaryingAction(true)` | Tested on a normal clip and Adjustment Layer in Premiere 26.3.2 |
| Add a keyframe | set the typed Keyframe `position`, then `createAddKeyframeAction()` | Tested with exact tick/value readback in Premiere 26.3.2 |
| Set interpolation mode | `Keyframe.setTemporalInterpolationMode()` before `createAddKeyframeAction()` | Hold=4 and Bezier=5 tested with exact readback in Premiere 26.3.2 |
| Preserve exact Bezier easing | No official tangent/influence/velocity/handle surface exists on `Keyframe` | Unsupported by the reviewed official API; do not claim curve fidelity |
| Typed values | number, string, boolean, `PointF`, or `Color` | Documented; individual control-type mapping pending |
| Read `.prfpset` | UXP filesystem with `localFileSystem: "request"` and a persistent user-granted file/folder token | Documented; permission deliberately not added until the parser probe is implemented |

Official references:

- [Component](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/component)
- [ComponentParam](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/componentparam)
- [PointKeyframe](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/pointkeyframe)
- [Premiere constants, including InterpolationMode](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [Filesystem operations and permissions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/)

## Proposed serializable boundary

Parsing and execution must be separate. A future catalog service may run in the Python companion, in UXP after a user grants file access, or offline during catalog generation; the Premiere mutation handler receives the same normalized request either way.

```json
{
  "schemaVersion": 1,
  "type": "timeline.applyEffectPreset",
  "requestId": "caller-generated-id",
  "payload": {
    "presetId": "stable-catalog-id",
    "filters": [
      {
        "mediaType": "video",
        "matchName": "AE.Impact_Mosaic_FX",
        "parameters": [
          {
            "index": 1,
            "valueType": "number",
            "staticValue": 12,
            "keyframes": []
          }
        ]
      }
    ]
  }
}
```

The production schema must preserve the source preset's parameter index, control type, value, keyframes, relative timing and interpolation metadata. It must reject unknown types instead of guessing.

## Required proof sequence

1. **Parameter-surface probe:** append one known effect, enumerate every official `ComponentParam`, and serialize index, localized display name, keyframe support and readable start value/type.
2. **Static-value probe:** apply one number/boolean/point/color value by parameter index and verify it by reading the value back.
3. **Animated-value probe:** enable time variation, add two clip-relative keyframes, read their tick positions/values back, and characterize one Undo.
4. **Interpolation probe:** test every documented `Constants.InterpolationMode` and derive an evidence-based mapping from `.prfpset` codes. Never pass legacy numeric codes through unverified.
5. **Compound-filter probe:** reconstruct a small real preset containing multiple effects and static/animated parameters on one and multiple selected clips.
6. **Catalog-access probe:** use `localFileSystem: "request"`, let the user select the Premiere profile or `.prfpset`, persist the access token, and parse without requiring unrestricted filesystem access.
7. **Regression matrix:** cover video/audio, adjustment layers, stills, multiple clips, missing third-party effects, localized parameter names, partial failure and Undo behavior.

## Known risks and fail-closed rules

- `.prfpset` parameter indices are promising because CEP already records and uses them, but equivalence with UXP `getParam(index)` is not documented and must be tested.
- Display names are localized and are diagnostic metadata only; filter identity uses exact runtime IDs/match names.
- The `.prfpset` interpolation numbers are not assumed to equal UXP enum values.
- UXP can preserve the documented interpolation mode, but not exact Bezier handle/easing values. Preset results must disclose this fidelity limit rather than synthesize undocumented data.
- Some third-party parameters may expose unsupported or unusual control types. Unknown conversions must produce a structured unsupported-parameter result.
- It is not yet proven that component insertion and all parameter actions can be composed into one UXP transaction. If actions require the component to be inserted first, Undo atomicity may differ from the desired product behavior.
- Missing effects, parameter-count mismatches or incompatible `createKeyframe()` values must abort before mutation whenever possible; partial application must never be reported as full success.

## Decision

Proceed with an official UXP reconstruction proof, not with a CEP fallback and not with an undocumented native-preset claim. The first implementation deliverable should be the parameter-surface/static-value probe. Full preset application remains unproven until the host tests above pass.
