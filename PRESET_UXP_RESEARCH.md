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
| Recover easing from the source file instead of the host | `.prfpset` fields 4–7 carry speed/influence per keyframe | Decoded in 0.38.0; the curve is reconstructed from the file and baked into sampled keys, so the missing host surface stops being the limiting factor |
| Approximate scalar easing | Sample a cubic curve into official Linear helper Keyframes | Implemented as an opt-in 0.21.0 experiment; Point/Position excluded; host comparison pending |
| Per-frame scalar baking | Use official sequence frame duration and add one sampled Keyframe per intervening frame | OpenCurve confirms the strategy independently; 0.22.0 Transform Scale host test pending |
| Manual-preset A/B oracle | Sample manual clip A through `getValueAtTime()`, replay exact samples on clip B | Implemented for Transform Scale Height in 0.23.0; host comparison pending |
| Complete Transform A/B oracle | Copy all supported static values and independently sample every animated number/Point parameter | Implemented in 0.24.0; real-preset host test pending |
| Typed values | number, string, boolean, `PointF`, or `Color` | Documented; individual control-type mapping pending |
| Read `.prfpset` | UXP filesystem `request` plus internal dependency-free XML tree parser | Browser `DOMParser` absent in UXP 9.3; corrected 0.25.1 host retest pending |

Official references:

- [Component](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/component)
- [ComponentParam](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/componentparam)
- [PointKeyframe](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/pointkeyframe)
- [Premiere constants, including InterpolationMode](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [Filesystem operations and permissions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/)

## Decoded `.prfpset` keyframe record

Adobe does not document this format; the mapping below was derived on 2026-08-21 by comparing a
two-key handcrafted preset (`Slide IN UP`) against a per-frame baked preset produced by a
third-party easing tool, plus an official Adobe sample `.prfpset`. Each entry in `<Keyframes>` is a
semicolon-terminated record of 14 comma-separated fields:

| Field | Meaning |
| --- | --- |
| 0 | Keyframe time in ticks (absolute; presets may start at a large offset) |
| 1 | Value (`x:y` for Point parameters, scalar otherwise) |
| 2–3 | Not yet identified; observed as `5,0` and `0,0` |
| 4 | Incoming temporal speed, signed |
| 5 | Incoming temporal influence, as a fraction where `0.16666…` is Premiere's default and `0.33333…` is Easy Ease |
| 6 | Outgoing temporal speed, signed |
| 7 | Outgoing temporal influence |
| 8 | Interpolation code; observed as `5` on every record inspected so far |
| 9 | Interpolation code that tracks the motion path: `4` on straight fixtures, `2` on a deliberately curved one, so it most likely selects the spatial interpolation type rather than the temporal one |
| 10–11 | Incoming spatial tangent, as an offset from this keyframe's own value |
| 12–13 | Outgoing spatial tangent, same convention |

Only fields 0–7 are common to every record. Point parameters serialize all 14 fields; scalar
parameters stop at field 7, since interpolation codes and spatial tangents do not apply to them.
Code must therefore treat fields 8–13 as absent rather than as zero on scalar records.

A separate, component-level flag matters for application rather than curve math: Premiere serializes
`<Intrinsic>true</Intrinsic>` on the `VideoFilterComponent` of fixed effects that exist on every
clip and cannot be duplicated, such as Motion and Opacity, confirmed in
`PRESET TEST MOTION - OPACITY - TIME REMAP`. A preset targeting one of these must locate the clip's
existing component and write to it, rather than create and append a new one through
`VideoFilterFactory`, which does not produce these components in the first place. Reading this flag
directly is preferable to a maintained list of known intrinsic match names, since it generalizes to
Time Remapping and any other fixed effect without further evidence being required to add it.
Applying this fixture confirmed intrinsic targeting for Motion and Opacity: both components verified
with `intrinsic: true`, and the all-intrinsic preset correctly skipped the insertion transaction
entirely, dropping to one Undo step. The user separately found that manually applying this preset
does not create Time Remapping keyframes in Premiere 26.3.2 at all, a host limitation that leaves
that specific effect's applicability unverified independent of this repository's correctness.

That fixture also exposed a real timing bug, distinct from the intrinsic-targeting question above.
Every animated parameter had been anchored at its own first keyframe - correct only when a preset
has exactly one, true of every fixture used before it. Here Opacity's fade starts 0.667 s after
Position's move, per the shared `FilterPreset.AnchorInPoint` both filters were captured against, and
the prior code silently started them together. `AnchorInPoint` is now parsed and used as the shared
origin, with each parameter's own stagger layered on top of it; the clip-length check was corrected
to use each parameter's true end relative to the clip rather than its own internal span. Host retest
returned `startOffsetSeconds` of exactly `0.6666666666666666` for Opacity and `0` for Position,
`longestAnimationSeconds` of `1.5833333333333333`, and the user confirmed in Effect Controls that the
fade now visibly starts after Position has already moved. The fix is byte-identical for every
fixture validated earlier, since each had one animated parameter whose first key already equalled
its filter's `AnchorInPoint` - which is exactly why the bug went undetected until a preset with two
animated parameters was tested.

Two independent checks support this mapping. The recurring `0.16666666666666666` equals 1/6, the
documented After Effects default influence. And in `Slide IN UP` the outgoing spatial tangent is
exactly 1/6 of the value delta while the outgoing temporal influence on the same keyframe is
`0.3148…`, so fields 10–13 cannot be the same quantity as fields 4–7: the former describe the
motion path, the latter the timing along it.

Temporal speed and influence convert to cubic controls through the same relations Adobe's own
After Effects exporters use:

```
x1 = outgoingInfluence(firstKey)
y1 = x1 * outgoingSpeed(firstKey) / averageSpeed
x2 = 1 - incomingInfluence(secondKey)
y2 = 1 - (1 - x2) * incomingSpeed(secondKey) / averageSpeed
```

Speed keeps its sign, and `averageSpeed` is signed for scalars — the value delta over the duration.
For Point parameters it is instead the unsigned Euclidean distance over the duration, because speed
there is measured along the path. Zero influence with zero speed degenerates to a linear segment.

The sign is what carries overshoot, and getting it wrong is silent. A keyframe reached while
travelling opposite to the segment's overall direction has passed its own value and is returning:
`PRESET TESTE OVERSHOOT` arrives at 125 with speed `-107.69` after peaking at 167. Signed division
yields `y2 = 5.3077`, placing the control point far above 1. Taking magnitudes yields `-3.3077`,
which folds the same curve below the start value instead. Both produce plausible-looking output, so
this must be verified against a fixture whose speeds are non-zero rather than assumed.

Because the temporal curve yields distance travelled rather than the spatial cubic's own parameter,
the motion path is traversed by arc length. For collinear default handles this reproduces linear
interpolation exactly, so straight moves are unaffected and only genuinely curved paths bend.

This decoding replaces the hand-tuned heuristic used through version 0.37.0, which read the
incoming influence where the outgoing one was required and scaled it through magic constants.

Premiere 26.3.2 measured it against the manual host oracle on `Slide IN UP`: RMS 0.0000083 and
maximum 0.0000152 normalized Point error across 52 samples, against 0.047095 and 0.097424 for the
heuristic. The host returned exactly the predicted controls `(0.3148, 0, 0, 1)`, so the mapping is
confirmed rather than fitted. Values are normalized by frame height — the serialized
`1.5277777910232544` is the 1650 px the host reports — leaving a residual of 0.009 px RMS and
0.016 px maximum.

A recreated overshoot fixture then exercised the speed-ratio term that `Slide IN UP` leaves at zero.
`PRESET TESTE OVERSHOOT` measured RMS 0.0000045 and maximum 0.0000076 Scale points across 61
samples, and the host returned the predicted `y2 = 5.30769287109375` digit for digit along with a
peak of 167.14112854 against a predicted 167.143.

Both fixtures were predicted before being measured, which is what distinguishes this decoding from
the heuristic it replaced: a model fitted to observations explains them by construction, whereas
these predictions could have failed and did not.

A third fixture then exercised the spatial handles. `TESTE KEYFRAMES CURVA` animates `AE.ADBE Motion`
Position corner to corner along an S-curve 13.8% longer than its chord, with Easy Ease at both ends
and zero speeds, which isolates the path question from the temporal one. It measured RMS 0.0000408
and maximum 0.0000809 normalized error across 61 samples.

That fixture also settled how the two halves couple. Feeding eased progress straight into the
spatial cubic as its parameter is a plausible alternative that agrees with arc-length traversal on
every straight path, so neither earlier fixture could tell them apart. Here they diverge by up to
0.12 normalized, roughly 210 px, and the measurement selects arc length by a wide margin.

Applying `Slide IN UP` through the executor and comparing its velocity graph against a manually
applied copy confirmed the decoding end to end, which the read-only oracle alone does not: the two
graphs are the same curve where the superseded heuristic produced a visibly different one.

The one visible difference there is inherent to baking. Per-frame linear keyframes have a staircase
derivative, which cannot reach a smooth curve's instantaneous peak, so the host's velocity graph
peaks 0.59% lower on the reconstruction — matching the shortfall predicted from the frame rate.
Rendered position is unaffected. Raising the sample rate above the frame rate would not help, since
keyframes and rendering both land on frame boundaries; only sub-frame sampling such as motion blur
could see the difference, and then only by the same tiny margin.

The residual on that fixture is about six times larger than this repository's arc-length table can
account for at 256 chords, so most of it originates elsewhere — plausibly Premiere's own path
parameterization, though that was not isolated. The table now uses 1024 chords, which drops its own
contribution to roughly 1e-6 and removes it from future attribution.

## Audio filter presets

Audio filters serialize differently from video ones in three ways, discovered from
`Áudio Estourado + Posterize` and the isolated `PRESET TEST + DISTORTION` fixtures on 2026-08-22:

1. **Nesting.** `VideoFilterComponent` nests its common payload one level deep
   (`VideoFilterComponent > Component`). `AudioFilterComponent` nests it two levels deep
   (`AudioFilterComponent > AudioComponent > Component`); resolving only the outer level silently
   returns an empty component, which is exactly the bug the first inspection surfaced.
2. **Identity.** The `FilterMatchName` on an audio filter is a Premiere-internal GUID
   (`e0b23f05-...`), not a stable public identifier. Consulting this project's CEP reference
   (`EffectPalette`, read-only per `TECHNICAL_PLAN.md`) showed that even the stable product resolves
   audio effects by **display name** through the legacy QE DOM (`qe.project.getAudioEffectByName`),
   never by this GUID; the official UXP surface already used by this project's `timeline.applyAudioEffect`
   probe agrees, resolving by display name through `AudioFilterFactory`.
3. **Channel-variant duplication.** One logical audio effect application serializes as one
   `AudioFilterComponent` **per channel configuration** the instance could run under - mono, stereo,
   5.1, observed as three "Distortion" entries with the same GUID, differing `ChannelConfigData`
   channel counts, and (when the user's edit was reflected) identical parameter values shared across
   the variants Premiere treated as linked. The CEP reference has a dedicated function,
   `_cleanupDuplicateAudioComponents`, solely to detect and remove these after `addAudioEffect`,
   confirming this is inherent Premiere behavior rather than a preset-authoring artifact. Applying
   all three would not reconstruct the preset correctly; they must collapse to one.

The official `AudioFilterFactory.createComponentByDisplayName(displayName, item)` differs from
`VideoFilterFactory.createComponent(matchName)` by taking the **target item**, making it
channel-aware from the destination clip. The already host-tested `timeline.applyAudioEffect` probe
confirms this produces exactly one component per clip (`appended: componentCountAfter ===
componentCountsBefore + 1`), so reconstruction does not need to replicate Premiere's channel-variant
duplication - it needs to collapse the `.prfpset`'s variants back to one before creating anything.

`dedupeAudioFilterVariants` first tried keeping whichever parameter set was shared by the most
variants, on the theory that Premiere propagates an edited value across every channel configuration
it treats as linked. Applying `PRESET TEST + DISTORTION` to its actual stereo source clip refuted
this: the majority-shared mono/5.1 values (52%/42%/56%/-60dB) were wrong, and a manual drag of the
same preset onto the same clip produced the minority stereo variant instead (0%/0%/0%/-120dB,
matching Adobe's built-in "Maximum Pain" Distortion preset).

The obvious correct rule - match the variant whose channel count equals the target clip's actual
channel count - turned out not to be implementable: the official UXP reference lists no channel-count
accessor on `AudioClipTrackItem`, and a developer-forum thread confirms `getAudioChannelMapping()`
from ExtendScript has no UXP equivalent. This is a documented API gap, not an unexplored option.

The current rule instead keeps whichever variant is **first in the `.prfpset` file's `FilterPreset`
order** for a given identity. A retest with `verification` extended to read parameter values back
from the host (not just component identity) confirmed this reproduces the manual "Maximum Pain"
result exactly - Positive/Negative/Time Smoothing and dB Range all `0` - without needing a screenshot
this time. This remains this project's best current explanation from one confirmed data point, not a
verified general rule; a preset where the first-listed variant is not the correct one would defeat
it, and there is no positive signal yet that would detect that case rather than silently applying the
wrong values.

The two host tests above jointly confirm what was previously unverified: `createComponentByDisplayName`
behaves the same inside preset reconstruction as in the isolated single-effect probe it was proven
in, and `ComponentParam.createKeyframe()` / `createSetValueAction()` - previously proven only on video
components - work identically on audio ones. A preset mixing video and audio filters is still
explicitly rejected rather than guessed at, since reconstructing both halves onto what may not even
be the same TrackItem remains unresolved.

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
5. **Compound-filter probe:** reconstruct a small real preset containing multiple effects and static/animated parameters on one and multiple selected clips. Implemented in version 0.37.0 as `timeline.applyImportedEffectPreset` for one selected clip; multi-clip application remains untested.
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
