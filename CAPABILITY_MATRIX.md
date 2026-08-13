# UXP capability matrix

## Evidence legend

- **Locally validated**: static manifest/code checks ran outside Premiere.
- **Documented**: supported by linked official Adobe documentation, but not exercised in Premiere for this repository.
- **Tested in Premiere**: requires a dated record with exact Premiere/UXP versions and reproducible evidence.
- **Unknown**: no sufficient official API or test evidence has been established yet.

Rows marked **Tested in Premiere** are backed by the manual session recorded below. Local validation alone is not host validation.

| Operation | UXP status | CEP baseline | Native-shortcut fallback | Limitations / evidence | Tested Premiere versions |
| --- | --- | --- | --- | --- | --- |
| Load minimal panel | Tested in Premiere | Available in stable product | Not applicable | Loaded and opened successfully through UDT. Logs supplied contain no message identified with `com.pauloedits.effectpalette.uxp.poc`. | 26.3.2 |
| Read Premiere and UXP versions | Tested in Premiere | Available | Not applicable | Host and runtime fields populated in Test A. Exact UXP runtime string was not supplied for the written test record. | 26.3.2 |
| Read active project | Tested in Premiere | Available | Not applicable | Empty-project state, project name and project GUID succeeded. Native `Project.guid` is converted with the officially documented `Guid.toString()`. | 26.3.2 |
| Read active sequence | Tested in Premiere | Available | Not applicable | No-sequence state, active-sequence name and sequence GUID succeeded. Native `Sequence.guid` is converted with `Guid.toString()`. | 26.3.2 |
| Read timeline selection | Tested in Premiere | Available | Not applicable | Empty, single and multiple selections returned expected counts and serialized name, type, track index, media type and linked project item. | 26.3.2 |
| Read project-panel selection | Tested in Premiere | Available | Not applicable | Empty, single and multiple selections returned expected count, name, type, ID and color-label index through official selection APIs. | 26.3.2 |
| Read video effect catalog | Tested in Premiere; full independent-array export pending retest | Available | None selected | Official display-name and match-name factories returned 829 entries each. A complete serializable export now supports offline native/third-party candidate analysis, while explicitly refusing positional pairing. | 26.3.2 |
| Resolve video effect display-name/match-name pairs | Tested in Premiere: unsupported before insertion; partial post-insertion mapping | CEP normalizes display names from QE catalog | None selected | `VideoFilterComponent` has no readable identity before insertion and the arrays have no documented positional mapping. The official catalog exposed 32 Impact identifiers. Post-insertion inspection confirmed modern `AE.Impact_Mosaic_FX` → Mosaic and historical `AE.ADBE Mosaic` → Mosaic (Legacy). | 26.3.2 |
| Inventory all UXP-visible video effects | Locally generated from host evidence; confirmation incomplete | QE catalog provides independent visible names | None selected | All 829 UXP entries are present; 691 display names also occur in CEP. Eight pairs are confirmed post-insertion across six provider families, while 821 same-index observations remain runtime candidates because Adobe does not document positional pairing. | 26.3.2 snapshot |
| Read audio effect catalog | Tested in Premiere | Available | None selected | Official audio-effect display-name factory returned a non-empty catalog; probe exposes count and samples only. | 26.3.2 |
| Read preset catalog | Unknown | Available | None selected | No official effect-preset catalog API was identified in the current Premiere UXP reference. Sequence/export presets are separate concepts. | None |
| Read video transition catalog | Tested in Premiere | Available | None selected | Complete official export returned 305 match names, including 75 Impact identifiers. The read-only QE-derived CEP file contained 340 visible video-transition names, so the catalogs are not assumed equivalent or positionally aligned. Focused visual tests established three modern dissolve mappings. | 26.3.2 |
| Operate without visible panel | Documented concept; not implemented or tested | CEP worker can run headless | Not applicable | Adobe documents invisible plugins using `hostUIContext.hideFromMenu`; lifecycle persistence and future authenticated transport must be proven in Premiere. | None |
| Apply video effect | Tested in Premiere | Available through undocumented QE DOM | Existing stable product may use native routing | No selection and audio-only selection failed closed; one/multiple video clips succeeded with one Undo. Post-application inspection verified both historical `AE.ADBE Mosaic` → Mosaic (Legacy) and modern `AE.Impact_Mosaic_FX` → Mosaic. | 26.3.2 |
| Apply audio effect | Tested in Premiere | Available through undocumented QE DOM | Existing stable product may use native routing | Exact runtime display-name validation, audio-only selection filtering, one/multiple clip application, post-insertion identity verification and a single Undo all succeeded. Empty/audio-free selections failed closed. Display names may be localized. | 26.3.2 |
| Apply preset | Unknown | Available | Existing stable product has routing | Mutation intentionally deferred. | None |
| Apply transition | Tested in Premiere; partial identity mapping | Available | Existing stable product has routing | Empty/audio-only selections failed closed; start/end and multi-clip application succeeded with one Undo. Historical `ADBE` identifiers produced Legacy variants. Visual tests confirmed modern mappings for Additive Dissolve, Film Dissolve and Cross Dissolve through Impact identifiers. UXP still exposes no general display-name or readable `VideoTransition` identity API. | 26.3.2 |
| Read project-item label | Tested in Premiere | Available | Existing stable product has routing | Project selection probe returned `getColorLabelIndex()` values in Test F. Mapping every numeric index to localized display names remains out of scope. | 26.3.2 |
| Set project-item label | Tested in Premiere | Available | Existing stable product has routing | Single and multiple selections succeeded and one Undo restored all prior labels. `VIOLET` identifies Adobe's default palette slot/index; customized user palettes can show a different name or color for that slot. | 26.3.2 |
| Select Label group | Unknown | Available | Existing stable product has native routing | No official end-to-end UXP operation has yet been established. | None |
| Create subsequence from selection | Tested in Premiere | Available | Existing stable product has native routing | Single, multiple and cross-track selections created a new sequence at the project root while leaving source Timeline clips intact. This is subsequence creation, not full Nest replacement. A second Undo removed the creation after the rename Undo. | 26.3.2 |
| Replace selection with Nest | Implemented; host test pending | Available | Existing stable product has native routing | Official-only probe creates a subsequence, then combines rename, original-selection removal and overwrite insertion in one transaction. Placement uses earliest selected time and lowest selected video/audio track indices. Expected Undo is replacement/rename first, creation second; mixed A/V and cross-track behavior still require host verification. | None |
| Name Nest/subsequence | Tested in Premiere | Available | Existing stable product has routing | Renaming the returned sequence ProjectItem updated the visible name correctly. The first Undo reverted only the rename; creation remained until a second Undo. | 26.3.2 |
| Insert project item into sequence | Documented API surface, not implemented | Available | Existing stable product has routing | `SequenceEditor.createInsertProjectItemAction()` is documented since 25.6; requires transaction and host test. | None |
| Insert generic item | Unknown | Available | Existing stable product has routing | Product-specific generic item mapping has not been investigated. | None |
| Serializable execution boundary | Locally validated design; diagnostics only | Existing Python adapter boundary | Not applicable | Allowlisted `diagnostics.read`; no transport, network, Python or arbitrary execution. | None |
| Copy serialized diagnostic output | Tested in Premiere | Browser clipboard path available | Not applicable | Official `navigator.clipboard.setContent()` copied complete diagnostics and action JSON successfully with the required manifest permission. Output-specific buttons provide feedback; no clipboard read is performed. | 26.3.2 |

## Host test record

Add one entry per test session; do not overwrite earlier evidence.

| Date | Premiere version | UXP runtime | UDT version | OS | Project fixture | Result/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | A: no project; B: project/no sequence; C: active sequence/no selection; D: selected clips | A–D succeeded. Initial B/C run exposed native `Guid` values serializing as `{}`; after applying `Guid.toString()`, focused B/C retest displayed both identifiers as strings. Logs contained no error attributable to the FX.palette plugin ID. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | E: official catalogs; F: project-panel selection; G: detailed timeline selection; H: empty selections | E–H succeeded. Video-effect, audio-effect and video-transition catalogs were populated; project and timeline selection details matched the active Premiere state. Diagnostic details were intentionally displayed as serialized JSON. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | I: mutation without selection; J: one selected project item plus Undo; K: multiple selected items plus single Undo | I correctly failed closed without mutation. J and K changed the selected items' label slot and Undo restored previous values as one transaction. A customized Label palette displayed a user-defined name/color rather than Adobe's default Violet appearance, confirming slot-index semantics. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | L: no timeline selection; M: one video clip plus Undo; N: multiple video clips plus single Undo; O: audio-only selection | L and O failed closed without mutation. M and N appended the requested video component and Undo removed it from all affected clips as one transaction. `AE.ADBE Mosaic` displayed as Mosaic (Legacy), exposing a catalog-identity limitation rather than an application failure. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | P: resolve every video-effect identity without timeline mutation | The catalog was populated, but every attempted `displayName` was `null`. This matches the official `VideoFilterComponent` surface, which has no methods or properties. The diagnostic was subsequently reduced to a concise capability probe. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | Q: concise effect-identity capability probe | Probe reported `unsupported-by-official-api`, with 829 match names and 829 display names in 2 ms. Equal array lengths were observed but are not treated as evidence of positional correspondence because the official API does not document that guarantee. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | R: apply `AE.ADBE Mosaic`, read appended component, then Undo | Serialized evidence confirmed one affected clip, component count `2 -> 3`, insertion index `2`, `appended: true`, verified match name `AE.ADBE Mosaic`, display name `Mosaic (Legacy)`, `identityMatchesRequest: true`, `verificationSucceeded: true`, and one undoable transaction. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | S: no audio selection; T: one audio clip plus Undo; U: multiple audio clips plus single Undo; V: video-only selection | S and V failed closed without mutation. T and U applied `Automatic Click Remover`, verified the appended component identities, and a single Undo restored every affected clip. Clipboard controls and full-panel scrolling also succeeded in this host session. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | W: no selection; X: one clip/start; Y: one clip/end; Z: multiple clips/single Undo; AA: audio-only | W and AA failed closed. X–Z succeeded visually and were undoable. Serialized Z evidence reported four affected clips and transition count `0 -> 4`. The requested `ADBE Additive Dissolve` appeared as Additive Dissolve (Legacy), establishing an unresolved transition-identity limitation. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | AB: modern Additive Dissolve; AC: modern Film Dissolve; AD: modern Cross Dissolve | Full UXP export returned 305 match names, 75 containing `Impact`. Visual application plus Undo confirmed `AE.Impact_Additive_Dissolve` → Additive Dissolve, `AE.Impact_Film_Dissolve` → Film Dissolve, and `AE.AE_Impact_Dissolve` → Cross Dissolve, all without the Legacy suffix. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | AE: modern Mosaic video effect plus Undo | Focused catalog probe returned 32 Impact video-effect identifiers. Applying `AE.Impact_Mosaic_FX` succeeded; post-insertion `Component` inspection verified match name `AE.Impact_Mosaic_FX`, display name `Mosaic`, and identity match without the Legacy suffix. Undo restored the clip. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | AF–AK: representative pair validation for Adobe, Film Impact, BCC, Sapphire, Universe and VR/Mettle | All six effects applied, returned `verificationSucceeded: true` and `identityMatchesRequest: true`, matched expected display names, and were undone between tests. This confirms the six individual pairs, not every same-provider candidate. |
| 2026-08-13 | 26.3.2 | `uxp-9.3.0-local` | Not supplied (2.2+ required) | Windows | AL: empty selection; AM: one clip; AN: multiple video/audio clips; AO: clips across tracks | AL failed closed. AM–AO created a correctly named subsequence at the project root and preserved all original Timeline clips. First Undo reverted the rename; second Undo removed the created subsequence. The operation therefore proves subsequence creation but not full Nest replacement or atomic Undo. |

Initial local environment discovery found Adobe Premiere 26.3.2. The subsequent manual host tests above supersede the earlier untested state.

## Official documentation consulted

- [Premiere UXP overview](https://developer.adobe.com/premiere-pro/uxp/)
- [Build the first plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/)
- [Manifest reference](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
- [Entrypoints](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/entrypoints/)
- [Premiere DOM API](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/)
- [Project class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project)
- [Sequence class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence)
- [Guid class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/guid)
- [TrackItemSelection class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/trackitemselection)
- [ProjectUtils class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectutils)
- [ProjectItem class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectitem)
- [SequenceEditor class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor)
- [VideoFilterFactory class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videofilterfactory)
- [VideoComponentChain class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videocomponentchain)
- [VideoFilterComponent class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videofiltercomponent)
- [Component class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/component)
- [AudioFilterFactory class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/audiofilterfactory)
- [AudioComponentChain class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/audiocomponentchain)
- [AudioClipTrackItem class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/audiocliptrackitem)
- [TransitionFactory class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transitionfactory)
- [VideoTransition class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videotransition)
- [AddTransitionOptions class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/addtransitionoptions)
- [Constants](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [UDT workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
- [Premiere UXP clipboard recipe](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/clipboard/)
- [Premiere 26.0 effects and transitions reorganization](https://helpx.adobe.com/premiere/desktop/add-video-effects/effects-and-transitions-library/effects-and-transitions-reorganization.html)
- [Premiere 26.0 effects and transitions changes](https://helpx.adobe.com/premiere/desktop/add-video-effects/effects-and-transitions-library/list-of-effects-and-transitions.html)
