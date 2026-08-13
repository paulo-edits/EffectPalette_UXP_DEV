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
| Read video effect catalog | Tested in Premiere | Available | None selected | Official display-name and match-name factories returned a non-empty catalog; probe exposes counts and samples only. | 26.3.2 |
| Read audio effect catalog | Tested in Premiere | Available | None selected | Official audio-effect display-name factory returned a non-empty catalog; probe exposes count and samples only. | 26.3.2 |
| Read preset catalog | Unknown | Available | None selected | No official effect-preset catalog API was identified in the current Premiere UXP reference. Sequence/export presets are separate concepts. | None |
| Read video transition catalog | Tested in Premiere | Available | None selected | Official video-transition factory returned a non-empty match-name catalog. Audio-transition catalog coverage remains unknown. | 26.3.2 |
| Operate without visible panel | Documented concept; not implemented or tested | CEP worker can run headless | Not applicable | Adobe documents invisible plugins using `hostUIContext.hideFromMenu`; lifecycle persistence and future authenticated transport must be proven in Premiere. | None |
| Apply video effect | Documented; implemented, pending host test | Available through undocumented QE DOM | Existing stable product may use native routing | Exact runtime-catalog match name; one component per selected video clip; official component-chain append actions grouped in one locked, undoable transaction. | None |
| Apply audio effect | Documented factory surface; not implemented | Available through undocumented QE DOM | Existing stable product may use native routing | `AudioFilterFactory` creates a component using display name plus an audio clip, but the complete official append/transaction path still requires investigation. | None |
| Apply preset | Unknown | Available | Existing stable product has routing | Mutation intentionally deferred. | None |
| Apply transition | Documented API surface, not implemented | Available | Existing stable product has routing | Track-item transition actions are documented, but catalog lookup and real behavior remain untested. | None |
| Read project-item label | Tested in Premiere | Available | Existing stable product has routing | Project selection probe returned `getColorLabelIndex()` values in Test F. Mapping every numeric index to localized display names remains out of scope. | 26.3.2 |
| Set project-item label | Tested in Premiere | Available | Existing stable product has routing | Single and multiple selections succeeded and one Undo restored all prior labels. `VIOLET` identifies Adobe's default palette slot/index; customized user palettes can show a different name or color for that slot. | 26.3.2 |
| Select Label group | Unknown | Available | Existing stable product has native routing | No official end-to-end UXP operation has yet been established. | None |
| Create Nest/subsequence | Partially documented, not implemented | Available | Existing stable product has native routing | `Sequence.createSubsequence()` is documented since 25.6; selection rules, naming and `Nested Sequences` organization remain untested. | None |
| Name Nest | Unknown | Available | Existing stable product has routing | Must verify official rename action and resulting project-item/sequence behavior. | None |
| Insert project item into sequence | Documented API surface, not implemented | Available | Existing stable product has routing | `SequenceEditor.createInsertProjectItemAction()` is documented since 25.6; requires transaction and host test. | None |
| Insert generic item | Unknown | Available | Existing stable product has routing | Product-specific generic item mapping has not been investigated. | None |
| Serializable execution boundary | Locally validated design; diagnostics only | Existing Python adapter boundary | Not applicable | Allowlisted `diagnostics.read`; no transport, network, Python or arbitrary execution. | None |

## Host test record

Add one entry per test session; do not overwrite earlier evidence.

| Date | Premiere version | UXP runtime | UDT version | OS | Project fixture | Result/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | A: no project; B: project/no sequence; C: active sequence/no selection; D: selected clips | A–D succeeded. Initial B/C run exposed native `Guid` values serializing as `{}`; after applying `Guid.toString()`, focused B/C retest displayed both identifiers as strings. Logs contained no error attributable to the FX.palette plugin ID. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | E: official catalogs; F: project-panel selection; G: detailed timeline selection; H: empty selections | E–H succeeded. Video-effect, audio-effect and video-transition catalogs were populated; project and timeline selection details matched the active Premiere state. Diagnostic details were intentionally displayed as serialized JSON. |
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | I: mutation without selection; J: one selected project item plus Undo; K: multiple selected items plus single Undo | I correctly failed closed without mutation. J and K changed the selected items' label slot and Undo restored previous values as one transaction. A customized Label palette displayed a user-defined name/color rather than Adobe's default Violet appearance, confirming slot-index semantics. |

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
- [AudioFilterFactory class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/audiofilterfactory)
- [TransitionFactory class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transitionfactory)
- [Constants](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [UDT workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
