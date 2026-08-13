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
| Read active project | Tested in Premiere; GUID fix pending retest | Available | Not applicable | Empty-project state and project name succeeded. `Project.guid` is a native `Guid`; direct JSON serialization produced `{}`. Code now calls the officially documented `Guid.toString()`. | 26.3.2 |
| Read active sequence | Tested in Premiere; GUID fix pending retest | Available | Not applicable | No-sequence and active-sequence states succeeded. `Sequence.guid` showed the same native-object serialization issue and now uses `Guid.toString()`. | 26.3.2 |
| Read timeline selection | Tested in Premiere | Available | Not applicable | Empty selection and one/multiple selected timeline items returned the expected counts. Current panel records count only. | 26.3.2 |
| Read project-panel selection | Documented, not implemented | Available | Not applicable | Official `ProjectUtils.getSelection(project)` exists since 25.6; deferred to read-only discovery stage. | None |
| Read effect catalog | Unknown | Available | None selected | No catalog API has yet been confirmed and tested for the product's requirements. | None |
| Read preset catalog | Unknown | Available | None selected | No catalog API has yet been confirmed and tested for the product's requirements. | None |
| Read transition catalog | Unknown | Available | None selected | No catalog API has yet been confirmed and tested for the product's requirements. | None |
| Apply video/audio effect | Unknown | Available | Existing stable product may use native routing | Mutation intentionally deferred; requires documented catalog lookup, component action and undoable transaction evidence. | None |
| Apply preset | Unknown | Available | Existing stable product has routing | Mutation intentionally deferred. | None |
| Apply transition | Documented API surface, not implemented | Available | Existing stable product has routing | Track-item transition actions are documented, but catalog lookup and real behavior remain untested. | None |
| Read/set project-item label | Documented API surface, not implemented | Available | Existing stable product has routing | `ProjectItemColorLabel` constants are documented; complete product behavior and label-group semantics remain untested. | None |
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
| 2026-08-13 | 26.3.2 | Not supplied | Not supplied (2.2+ required) | Windows | A: no project; B: project/no sequence; C: active sequence/no selection; D: selected clips | A and D succeeded. B and C succeeded except native `Guid` values serialized as `{}`. Logs contained no error attributable to the FX.palette plugin ID. GUID conversion fix requires a focused B/C retest. |

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
- [SequenceEditor class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor)
- [Constants](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [UDT workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
