# UXP capability matrix

## Evidence legend

- **Locally validated**: static manifest/code checks ran outside Premiere.
- **Documented**: supported by linked official Adobe documentation, but not exercised in Premiere for this repository.
- **Tested in Premiere**: requires a dated record with exact Premiere/UXP versions and reproducible evidence.
- **Unknown**: no sufficient official API or test evidence has been established yet.

No row is currently marked **Tested in Premiere**. Local validation is not host validation.

| Operation | UXP status | CEP baseline | Native-shortcut fallback | Limitations / evidence | Tested Premiere versions |
| --- | --- | --- | --- | --- | --- |
| Load minimal panel | Locally validated; documented | Available in stable product | Not applicable | Manifest v5, `premierepro`, minimum 25.6 and panel entrypoint follow the official manifest/entrypoint docs. Must still be loaded via UDT. | None |
| Read Premiere and UXP versions | Documented; locally syntax-validated | Available | Not applicable | Uses official `require("uxp")` `host` and `versions`. | None |
| Read active project | Documented; locally syntax-validated | Available | Not applicable | `premiere.Project.getActiveProject()` is documented since 25.6. Empty-project behavior must be tested. | None |
| Read active sequence | Documented; locally syntax-validated | Available | Not applicable | `project.getActiveSequence()` is documented since 25.6. No-active-sequence behavior must be tested. | None |
| Read timeline selection | Documented; locally syntax-validated | Available | Not applicable | `sequence.getSelection()` and `selection.getTrackItems()` are documented since 25.6. Current panel records count only. | None |
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
| — | — | — | — | — | — | No host test executed yet. |

Local environment observation on 2026-08-13: Adobe Premiere 26.3.2 is installed, but UXP Developer Tool was not detected and Premiere was not running. This is environment discovery only, not plugin test evidence.

## Official documentation consulted

- [Premiere UXP overview](https://developer.adobe.com/premiere-pro/uxp/)
- [Build the first plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/)
- [Manifest reference](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
- [Entrypoints](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/entrypoints/)
- [Premiere DOM API](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/)
- [Project class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project)
- [Sequence class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence)
- [TrackItemSelection class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/trackitemselection)
- [ProjectUtils class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectutils)
- [SequenceEditor class](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor)
- [Constants](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/)
- [UDT workflows](https://developer.adobe.com/premiere-pro/uxp/plugins/tutorials/udt-deep-dive/plugin-workflows)
