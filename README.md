# FX.palette UXP proof of concept

Minimal, read-only UXP plugin for Adobe Premiere 25.6 or newer. This repository is isolated from the stable Python + CEP application and contains no CEP fallback or Python communication.

## Requirements

- Adobe Premiere 25.6 or newer.
- UXP Developer Tool (UDT) 2.2 or newer, run with administrator privileges.
- Developer Mode enabled in both UDT and Premiere.
- Node.js only for the optional local validation command; the plugin itself has no build step or dependencies.

## Validate locally

```powershell
npm run validate
```

This checks the required manifest fields, minimum host version, entrypoint/main-file consistency, absence of requested permissions, and JavaScript syntax. It does not replace a real host load.

## Load in Premiere

1. Start Premiere 25.6+.
2. In Premiere, open **Settings > Plugins**, enable **Developer Mode**, and restart Premiere if the setting changed.
3. Start UXP Developer Tool 2.2+ as administrator and enable its Developer Mode.
4. Select **Add Plugin** and choose this repository's `manifest.json`.
5. With Premiere shown under **Connected Apps**, select **Load & Watch**.
6. Confirm that UDT reports a successful load. For failures, open the UDT Logs and App Logs.
7. In Premiere, open **Window > UXP Plugins > FX.palette UXP Diagnostics > FX.palette Diagnostics**.
8. Click **Refresh diagnostics** with these states and record the results in `CAPABILITY_MATRIX.md`:
   - no project open;
   - project open with no active sequence;
   - active sequence with no selected timeline items;
   - active sequence with one or more selected items.

Manifest changes require **Unload**, followed by **Load & Watch**. JavaScript/HTML/CSS changes can be reloaded through UDT.

## Expected diagnostic output

The panel reads host name, Premiere version, UXP runtime version, active project name/GUID, active sequence name/GUID, project-panel selection, detailed timeline selection, and documented effect/transition catalogs. It also displays the complete serializable adapter result.

Version 0.11.0 reports:

- selected project-item name, type, ID and color-label index;
- selected timeline-item name, type, track index, media type and linked project item;
- counts and samples from the official video-effect, audio-effect and video-transition factories;
- effect presets as unknown because no corresponding official catalog API has been identified.

Every JSON output has an adjacent copy button. It uses Premiere UXP's official `navigator.clipboard.setContent()` API and briefly changes its label to `Copied!` or `Copy failed`. The manifest requests only the required `clipboard: readAndWrite` permission; the plugin still requests no network or filesystem access.

It also includes the first mutation probe: `projectItems.setColorLabel`. The panel can set the currently selected Project-panel items to Adobe's `VIOLET` label slot. This constant identifies a palette slot/index; its visible name and color can differ when the user customizes Premiere's Label palette. The action is allowlisted, creates Premiere `Action` objects inside `Project.lockedAccess()`, and submits them as one undoable `Project.executeTransaction()` operation. No other mutation is accepted by the adapter.

The second mutation probe is `timeline.applyVideoEffect`. It accepts an exact `matchName` present in `VideoFilterFactory.getMatchNames()`, creates one `VideoFilterComponent` per selected video clip, and appends the components to their official `VideoComponentChain` objects in one undoable transaction. After the transaction, it reads each newly appended `Component` from the chain and serializes its authoritative `getMatchName()` and `getDisplayName()` values, component counts, and identity check. Verification failure is reported separately from mutation failure because the effect may already have been applied. The diagnostic UI defaults to `PR.ADBE Gamma Correction`, used by Adobe's official Premiere UXP sample.

The third mutation probe is `timeline.applyAudioEffect`. It accepts an exact, runtime-provided display name from `AudioFilterFactory.getDisplayNames()`, selects only audio track items, creates each component with its target `AudioClipTrackItem`, and appends all components in one undoable transaction. It then reads the appended `Component` objects back from their audio chains and verifies their official display and match names. Audio display names can be localized; callers must use a value from the current runtime catalog rather than a remembered English name. Presets remain unsupported.

The fourth mutation probe is `timeline.applyVideoTransition`. It accepts an exact runtime `matchName`, filters selection to video clips, creates one `VideoTransition` per target, and applies it to the selected start or end edge in one undoable transaction. The proof of concept leaves duration and alignment at Premiere's host defaults. It verifies the transition-item count before and after; transition identity remains visual-only because the official `VideoTransition` class exposes no methods or properties.

The fifth mutation probe is `timeline.createSubsequence`. It requires an explicit Timeline selection and calls official `Sequence.createSubsequence(true)` so track targeting is ignored. The returned sequence's project item is renamed through an undoable `ProjectItem.createSetNameAction()` transaction, and the result serializes sequence counts, generated/requested names, GUID, project-item ID and parent bin. Creation itself is explicitly reported with unknown Undo behavior because `createSubsequence()` returns a `Sequence` directly rather than an `Action`; test only in a disposable project.

Premiere 26.3.2 host tests established that this API leaves the original Timeline selection intact and creates the new subsequence at the project root. It is not equivalent to Premiere's complete Nest command. Undo is two-stage: the first Undo reverts the separate rename transaction and the second removes the created subsequence. Full selection replacement requires a separate official remove/insert design.

The sixth mutation probe is `timeline.createNest`. It builds on the characterized subsequence behavior and uses only official UXP APIs: `Sequence.createSubsequence(true)` creates the nested sequence, then one `Project.executeTransaction()` combines the ProjectItem rename, removal of the original selection and overwrite insertion of the new sequence item. Placement uses the earliest selected start time, the lowest selected video-track index and the lowest selected audio-track index. Because subsequence creation itself is not an Action, the expected Undo model remains two-stage: one Undo for replacement/rename and another for creation. This probe is implemented but must be verified in Premiere before it is considered supported.

Premiere 26.3.2 host testing confirmed the complete replacement behavior but showed that renaming only the ProjectItem does not rename the inserted Timeline instances. The CEP reference performs those as separate operations. Version 0.12.0 follows the same model through official UXP actions: after insertion it resolves the nested video/audio TrackItems by project-item ID and start ticks, then applies each TrackItem's official `createSetNameAction()`. The serialized result includes instance counts and before/after names. Because the instances exist only after overwrite, this adds a third expected Undo level pending host characterization.

Host retesting confirmed that version 0.12.0 applies the requested name to the Project-panel item and the inserted Timeline instances. Serialized instance verification and all three Undo levels behaved as expected in Premiere 26.3.2.

Version 0.13.0 adds the `timeline.insertProjectItem` probe. It requires exactly one Project-panel item, reads the active-sequence playhead and executes either official `SequenceEditor.createInsertProjectItemAction()` or `createOverwriteItemAction()` with explicit zero-based video/audio track indices. The result verifies instances by ProjectItem ID and exact start ticks and reports the edit mode, requested tracks, before/after counts and Undoability. Host testing remains required.

Version 0.14.0 adds `timeline.insertGenericItem` for duration-matched synthetic assets. It reads the span from selected video clips, overwrites the selected Project item at the earliest start, resolves the resulting video TrackItem by ProjectItem ID/start ticks and sets its end to the latest selected end with official `createSetEndAction()`. Insertion and duration are separate transactions because the TrackItem does not exist until insertion completes; two Undo levels are therefore expected. This probe does not yet create/import generic assets or choose a free destination track automatically.

Version 0.15.0 adds an atomic-duration experiment. It casts the selected item to `ClipProjectItem`, snapshots its video Out Point, temporarily sets the Out Point to the selected span duration, performs the overwrite and restores the source Out Point inside one compound transaction. If Premiere snapshots the temporary duration during insertion, one Undo removes the complete result. If not, the existing verified post-insertion TrackItem action runs as a two-Undo fallback. JSON distinguishes both strategies and verifies that the source Out Point was restored.

Premiere 26.3.2 exposed the cast object without the documented `createSetOutPointAction()` method. Version 0.15.1 therefore feature-detects both Out-Point action forms, prefers `createSetOutPointAction()`, tries `createSetInOutPointsAction()` when that is the available official method, and safely executes the proven insertion plus TrackItem-duration fallback if neither can be used.

Version 0.15.2 extends the probe to the original object returned by Project-panel selection as well as the cast ClipProjectItem. The result serializes `atomicActionSurface` for both representations plus the selected action source/method. This distinguishes a cast-wrapper limitation from a host-wide absence before ruling out the official pre-trim strategy.

The 0.15.2 host result showed both Out-Point methods on the cast surface, but the preferred `createSetOutPointAction()` failed inside Premiere and immediately caused fallback. Version 0.15.3 tries each advertised method independently, including `createSetInOutPointsAction()`, and reports `atomicAttemptErrors` so a broken wrapper cannot prevent the alternate official action from being tested.

Premiere 26.3.2 testing established that the combined In/Out action executes but does not affect the duration captured by an overwrite action constructed in the same compound transaction: the inserted Adjustment Layer still arrived with its original one-second duration. The source Out Point was restored correctly, and the post-insertion TrackItem fallback produced the exact selected span. Consequently, the clean official implementation proven here requires two Undo transactions. A single-Undo implementation would require a different official API behavior or a native/undocumented route, neither of which is used in this repository.

Version 0.16.0 adds the `headlessSetVioletLabel` command entrypoint. It appears in Premiere's Window > UXP Plugins menu and invokes the same allowlisted serializable Label action without reading or writing panel DOM. Closing the diagnostics panel before invoking it proves that discrete UXP operations do not require a visible panel. This does not yet add communication with Python.

Host testing confirmed that the command changes selected Project-item Labels with the diagnostics panel closed and does not reopen it. Review of the stable CEP Timeline-Label workaround found that its preferred path uses Premiere's internal `cmd.sequence.edit.label.<index>` command; UXP does not currently document an equivalent executor or TrackItem Label API, so that behavior is deliberately not emulated here.

In Premiere 26.3.2, `ADBE Additive Dissolve` and `ADBE Film Dissolve` visibly produced their **(Legacy)** variants. Adobe documents that Premiere 26.0 replaced several familiar transitions with modern GPU-accelerated versions originating from Film Impact while preserving the old implementations in the Legacy folder. Full official catalog export plus visual host tests established these modern runtime mappings: `AE.Impact_Additive_Dissolve` → Additive Dissolve, `AE.Impact_Film_Dissolve` → Film Dissolve, and `AE.AE_Impact_Dissolve` → Cross Dissolve. The first is now the diagnostic default. These are tested runtime mappings, not a general display-name API; other transitions still require explicit evidence.

Do not infer effect identity from a remembered match name. In Premiere 26.3.2, the documented example `AE.ADBE Mosaic` resolved to the component displayed as **Mosaic (Legacy)**. The production catalog must persist both the runtime match name and the display name resolved from a created component or another officially supported mapping; it must not assume positional correspondence between separately returned arrays.

Focused Film Impact research found 32 official video-effect match names containing `Impact`. Host application plus post-insertion component inspection confirmed `AE.Impact_Mosaic_FX` → **Mosaic**, while the historical `AE.ADBE Mosaic` → **Mosaic (Legacy)**. This is an evidence-backed pair for Mosaic only; other Impact identifiers remain candidates until verified.

The explicit `catalog.videoEffects.resolve` action now probes this limitation without modifying a project. It creates one unattached `VideoFilterComponent`, reports whether the runtime exposes a display-name method, returns concise samples from the two separate catalogs, and includes the subset of official match names containing `Impact` for focused Film Impact migration research. Premiere 26.3.2 returned no display-name method, consistently with Adobe's documentation that `VideoFilterComponent` has no methods or properties. `Component.getDisplayName()` applies to a component already read from a clip's `VideoComponentChain`; obtaining it requires a timeline mutation first. Because Adobe does not document positional correspondence between `getDisplayNames()` and `getMatchNames()`, this proof of concept does not zip those arrays or claim a direct mapping.

The separate `catalog.videoEffects.read` action exports the complete match-name and display-name arrays as independent evidence for offline analysis of native and third-party effects. The export deliberately includes `positionalPairingAssumed: false`; candidate mappings must be confirmed through post-insertion `Component` inspection before becoming trusted product data.

The generated `EFFECT_IDENTITY_MATRIX.json` inventories all 829 effects exposed by the tested runtime, including Adobe, Film Impact, BCC, Sapphire and Universe. Eight individual pairs have been confirmed post-insertion across six provider families; the remaining same-index observations stay candidates. Its companion `EFFECT_IDENTITY_MATRIX.md` defines the evidence levels. The matrix is a development artifact and is not loaded by the plugin; the CEP snapshot never becomes a runtime dependency or fallback.

The visible panel is a proof-of-concept diagnostic surface, not a production dependency. The future operational plugin must work without requiring this panel to remain open.

No status should be promoted to **Tested in Premiere** until the exact Premiere version and evidence are recorded in the capability matrix.

## Official references

- [Build the first Premiere UXP plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/)
- [UXP manifest](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
- [UXP entrypoints](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/entrypoints/)
- [Premiere DOM API](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/)
- [Project](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project)
- [Sequence](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence)
- [TrackItemSelection](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/trackitemselection)
- [Premiere 26.0 effects and transitions reorganization](https://helpx.adobe.com/premiere/desktop/add-video-effects/effects-and-transitions-library/effects-and-transitions-reorganization.html)
- [Premiere 26.0 effects and transitions changes](https://helpx.adobe.com/premiere/desktop/add-video-effects/effects-and-transitions-library/list-of-effects-and-transitions.html)
- [UDT plugin workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
