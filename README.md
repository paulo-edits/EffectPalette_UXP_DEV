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

Version 0.5.0 reports:

- selected project-item name, type, ID and color-label index;
- selected timeline-item name, type, track index, media type and linked project item;
- counts and samples from the official video-effect, audio-effect and video-transition factories;
- effect presets as unknown because no corresponding official catalog API has been identified.

It also includes the first mutation probe: `projectItems.setColorLabel`. The panel can set the currently selected Project-panel items to Adobe's `VIOLET` label slot. This constant identifies a palette slot/index; its visible name and color can differ when the user customizes Premiere's Label palette. The action is allowlisted, creates Premiere `Action` objects inside `Project.lockedAccess()`, and submits them as one undoable `Project.executeTransaction()` operation. No other mutation is accepted by the adapter.

The second mutation probe is `timeline.applyVideoEffect`. It accepts an exact `matchName` present in `VideoFilterFactory.getMatchNames()`, creates one `VideoFilterComponent` per selected video clip, reports the component's authoritative `getDisplayName()`, and appends the components to their official `VideoComponentChain` objects in one undoable transaction. The diagnostic UI defaults to `PR.ADBE Gamma Correction`, used by Adobe's official Premiere UXP sample. Audio effects and presets are not handled by this action.

Do not infer effect identity from a remembered match name. In Premiere 26.3.2, the documented example `AE.ADBE Mosaic` resolved to the component displayed as **Mosaic (Legacy)**. The production catalog must persist both the runtime match name and the display name resolved from a created component or another officially supported mapping; it must not assume positional correspondence between separately returned arrays.

The explicit `catalog.videoEffects.resolve` action now probes this limitation without modifying a project. It creates one unattached `VideoFilterComponent`, reports whether the runtime exposes a display-name method, and returns only concise samples from the two separate catalogs. Premiere 26.3.2 returned no such method, consistently with Adobe's documentation that `VideoFilterComponent` has no methods or properties. `Component.getDisplayName()` applies to a component already read from a clip's `VideoComponentChain`; obtaining it requires a timeline mutation first. Because Adobe does not document positional correspondence between `getDisplayNames()` and `getMatchNames()`, this proof of concept does not zip those arrays or claim a direct mapping.

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
- [UDT plugin workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
