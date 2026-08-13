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

The `catalog.videoEffects.resolve` probe confirmed that an unattached `VideoFilterComponent` has no official display-name method. The two factory arrays therefore remain separate and are not paired by position. After mutation, the handler reads the newly appended object back from `VideoComponentChain` as a `Component`; only then does it call the official `getMatchName()` and `getDisplayName()` methods and serialize the verified identity.

### Known differences

- The current CEP path supports video and audio effects; the first UXP probe is video-only.
- Preset application remains unknown because no official effect-preset catalog/application API has been identified.
- Locale/display-name behavior should be separated from match-name identity.
- On Premiere 26.3.2, `AE.ADBE Mosaic` resolved to **Mosaic (Legacy)**, not the modern Mosaic effect expected from the remembered name. Resolve and record `Component.getDisplayName()` rather than inferring identity.
- The same host exposed `AE.Impact_Mosaic_FX`; applying it and reading the inserted `Component` confirmed the modern **Mosaic** effect. This establishes a tested modern/Legacy pair without relying on array order.
- Duplicate-effect behavior and multi-clip partial failure behavior require host tests.

## Audio effect application

The CEP implementation routes audio effects through the undocumented QE DOM. The official UXP path instead resolves an exact localized display name through `AudioFilterFactory`, creates an `AudioFilterComponent` for each selected `AudioClipTrackItem`, and appends it through that clip's `AudioComponentChain` in a single project transaction. As with video, the inserted component is read back after mutation for serialized identity verification.

## Video transition application

The official UXP path validates a runtime match name from `TransitionFactory`, creates a `VideoTransition`, configures the target clip edge with `AddTransitionOptions`, and submits `VideoClipTrackItem.createAddVideoTransitionAction()` in a project transaction. Unlike inserted effect components, `VideoTransition` exposes no readable identity surface, so the proof of concept records catalog validation, transaction acceptance, transition-count change and required visual confirmation without claiming a direct post-insertion identity check.

Premiere 26.3.2 visually mapped `ADBE Additive Dissolve` and `ADBE Film Dissolve` to their **(Legacy)** variants. Adobe's Premiere 26.0 documentation confirms that modern GPU-accelerated Film Impact replacements took over several familiar transition names while historical implementations moved to Legacy. Comparison of the complete official match-name export with the read-only CEP visible-name catalog produced candidates that were then verified visually: `AE.Impact_Additive_Dissolve` → Additive Dissolve, `AE.Impact_Film_Dissolve` → Film Dissolve, and `AE.AE_Impact_Dissolve` → Cross Dissolve. This is a small evidence-backed mapping, not a general positional or inferred catalog pairing.

The read-only CEP catalog file is generated at runtime by `getEffectsList()` in `scripts/host.jsx`. That function enables the undocumented QE DOM and reads `qe.project.getVideoEffectList()`, `getAudioEffectList()`, `getVideoTransitionList()` and `getAudioTransitionList()`; `bridge.js` then serializes the returned display names to `data/premiere_effects.json`. On the tested host it contained 340 video-transition display names, including both modern and Legacy dissolve names, while official UXP reported 305 transition match names. The CEP file is therefore useful evidence but cannot be imported as an official UXP identity map.

## Nest / subsequence creation

The official UXP surface provides `Sequence.createSubsequence(ignoreTrackTargeting)` directly. The proof of concept requires an explicit current Timeline selection and passes `true`, avoiding dependence on the user's targeted tracks. It renames the returned sequence through its official ProjectItem action. Unlike effect/transition mutations, creation does not expose an Action, so atomic Undo behavior cannot be assumed and must be characterized in the host.

Host testing confirmed that `createSubsequence()` creates a project-root sequence without replacing the selected source clips. Rename and creation occupy separate Undo steps. Reproducing the product's full Nest behavior therefore requires a second stage that removes the original selection and inserts the new sequence item at the correct Timeline coordinates using official actions.

Version 0.11.0 implements that second stage as an official-only host probe. After `createSubsequence(true)`, a single transaction adds `ProjectItem.createSetNameAction()`, `SequenceEditor.createRemoveItemsAction()` and `SequenceEditor.createOverwriteItemAction()`. The earliest selected start and lowest selected video/audio track indices define the insertion point. This mirrors the observable Nest result without invoking CEP or undocumented commands, but remains pending Premiere host validation and is expected to retain two Undo levels because sequence creation cannot be added to the transaction.

Premiere 26.3.2 testing confirmed creation, replacement and placement, but also showed that ProjectItem rename alone does not change the nested Timeline instance name. Review of the stable CEP implementation identified its additional `clip.name = finalName` pass. Version 0.12.0 translates that pass to official UXP `VideoClipTrackItem.createSetNameAction()` and `AudioClipTrackItem.createSetNameAction()` calls after overwrite. This avoids private APIs, but requires a separate transaction and therefore likely introduces a third Undo level; host testing must confirm both naming and Undo behavior.

## Reference locations (read-only)

- `EffectPalette/app.py`: `PremiereExecutionAdapter`, `execute_effect_through_adapter()`.
- `EffectPalette/bridge.js`: `applyEffect(cmd)`.
- `EffectPalette/scripts/host.jsx`: `applyEffectWithSelection()`.
