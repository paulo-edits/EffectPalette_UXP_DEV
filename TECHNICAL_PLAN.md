# Technical plan

## Scope and invariants

This proof of concept establishes actual Premiere UXP capability boundaries without changing the stable Python + CEP product. It uses only official UXP and Premiere DOM APIs. It does not contain CEP, ExtendScript, native shortcuts, arbitrary script execution, or unreviewed filesystem permissions. As of stage 5, it does contain one deliberate, narrow exception to the earlier "no network, no Python communication" boundary: an outbound-only WebSocket client to a fixed, pre-declared `ws://localhost:58756`, per the user's explicit direction to build toward replacing CEP rather than stay a permanently isolated PoC. It still contains no listener - official UXP documentation describes no capability for a plugin to accept inbound connections, so the companion process is necessarily the server and this plugin the client, per `PRESET_UXP_RESEARCH.md`/this section's own research.

The production architecture must not require a visible Premiere panel. A panel may remain available for diagnostics and settings, while the operational plugin context should load automatically and run invisibly. This lifecycle requirement was proven first by the 0.16.0 headless command entrypoint, and again by stage 5's `entrypoints.plugin.create()` hook, which the official reference confirms fires automatically on plugin load independent of any panel or command being invoked by the user.

`paulo-edits/Effect-Palette_DEV` may be consulted as a read-only implementation reference for existing CEP behavior and serialized action semantics. No file, commit, branch or remote in the stable repositories may be changed or published without explicit user authorization. CEP implementation details are evidence of product behavior, not permission to introduce CEP fallbacks here.

## Architecture boundary

The provisional boundary is `execution-adapter.js`:

```json
{
  "schemaVersion": 1,
  "type": "diagnostics.read",
  "requestId": "caller-generated-id",
  "payload": {}
}
```

Responses are plain serializable objects with `ok`, `schemaVersion`, `actionType`, `requestId`, and either `data` or `error`. The allowlist now contains 24 actions (`execution-adapter.js`'s `SUPPORTED_ACTIONS`), matching every mutation and read this proof of concept has host-tested. Unknown actions fail closed.

`transport.js` (stage 5) is the first implementation of the "future optional localhost transport" this section originally deferred. It connects outbound to `ws://localhost:58756`, sends a token in an initial handshake message, and dispatches every message after that through `executionAdapter.execute(message, handlers)` with the identical handler map the diagnostics panel's own buttons use - a network caller can therefore never reach a code path the visible UI could not already reach. The token is a fixed constant baked into the plugin bundle, not a real secret: anything shipped to the user's machine is readable by anything else with code-execution capability on that same machine. Its purpose is avoiding accidental cross-talk with an unrelated local service, not defending against a co-located attacker; localhost-only Windows process isolation is what actually keeps other machines out, and this token is not a substitute for that.

## Delivery stages

1. **Bootstrap and diagnostics** — loadable Manifest v5 panel; host, UXP, project, sequence and timeline-selection reads.
2. **Read-only discovery** — inspect project/timeline selection and documented video-effect, audio-effect and video-transition catalogs. Keep effect presets unknown until an official catalog API is identified.
3. **Safe mutation experiments** — start with the allowlisted `projectItems.setColorLabel` operation, creating actions inside `Project.lockedAccess()` and adding them to one undoable `Project.executeTransaction()`. Record exact setup, version, undo behavior and outcome before adding another mutation.
4. **Architecture decision** — compare verified UXP coverage with existing CEP and native-shortcut behavior. Define which operations can migrate and which remain outside UXP.
5. **Optional transport design** — only after capability boundaries are known: localhost-only, token-authenticated, schema-validated, no arbitrary commands.

## Validation gates

- `npm run validate` passes.
- UDT 2.2+ accepts `manifest.json` and reports a successful load against Premiere 25.6+.
- Panel opens from **Window > UXP Plugins**.
- Diagnostics work across empty/project/sequence/selection states without uncaught errors.
- Read-only catalog and selection probes work with the diagnostics panel visible; later lifecycle tests must prove that production execution does not depend on panel visibility.
- UDT and Premiere App Logs contain no plugin errors during the test.
- The matrix distinguishes local validation, official documentation and actual Premiere evidence.

Packaging, communication with Python and product UI redesign are outside this stage.

## Stage 4: architecture decision

Compares every row of `CAPABILITY_MATRIX.md` (68 operations, all evidence dated 2026-08-13 through
2026-08-23) against the stable product's CEP baseline and native-shortcut fallback, per this stage's
brief. It maps directly onto `EffectPalette/ARCHITECTURE.md`'s own Phase 2 exit criterion ("record
missing API capabilities and required fallbacks") and feeds Phase 3 (prefer UXP, retain CEP only
where UXP has no parity).

### A — Full parity, ready to prefer UXP over CEP

Every read/diagnostic operation, plus: set project-item label, apply video/audio effect, apply video
transition, create subsequence, replace selection with Nest (and name it), insert project item,
insert generic item, operate without a visible panel, and the unified preset executor
(`timeline.applyImportedEffectPreset`) for its proven cases — static values across all observed
control types, animated Point/scalar easing (measured near float32 precision against a manual host
oracle), overshoot, curved motion paths, intrinsic fixed effects (Motion/Opacity), multiple selected
clips, and slider/toggle-only audio effects (Hard Limiter, and by the same evidence any of the 74
effects in the user's real catalog with no `ArbVideoComponentParam`, per the 2026-08-23 catalog
audit). All undoable as one or two transactions, matching or narrowing the CEP baseline's own Undo
behavior.

### B — Parity with a disclosed fidelity or scope caveat

Usable in production, but the caveat must reach the user, not stay implicit:

- **Preset easing reconstruction** — the `reconstructEasing` toggle (`TECH_DECISIONS.md`) must ship
  as a real setting, not stay a diagnostic checkbox. Off preserves exact principal keyframes with no
  synthesized easing shape; on frame-samples the curve and is measured indistinguishable in rendered
  output from a manual application (0.016 px worst case), with a 0.59% velocity-graph-only shortfall
  that never affects render.
- **Effects with a graphical/curve UI** (Lumetri Color, Sapphire Glow, Magic Bullet Looks, Text,
  Shape, Neon Wipe, Mocha Pro, Impact Wiggle, Sapphire Shake, BCC Camera Shake — the 10 of 84 distinct
  effects in the user's real catalog carrying `ArbVideoComponentParam`) — preset reconstruction
  correctly fails closed with a named-parameter error before any mutation, rather than silently
  applying an incomplete or corrupted result. This is a confirmed platform boundary (no official
  `ComponentParam` surface accepts opaque data), not a gap this project can close.
  `timeline.applyVideoEffect`/`timeline.applyAudioEffect` (adding the bare effect, not a preset) are
  unaffected and stay in bucket A.
- **Third-party parameters with an unrecognized control type** (e.g. Magic Bullet Looks `controlType
  9`) fail closed with the offending filter/parameter/value named, rather than being guessed at.
- **A preset mixing video and audio filters** is rejected outright rather than reconstructed
  partially, since the two halves may not target the same TrackItem and that has not been resolved.

### C — No UXP path; native-shortcut fallback stays authoritative

Only two operations in the entire matrix have no official UXP surface at all:

- **Set Timeline-item label** — no UXP Label API on video/audio TrackItems. The stable product's
  existing fallback (Python reads `cmd.edit.label.<index>` from the active `.kys` and sends the
  configured Windows shortcut) remains correct and should sit behind the future execution adapter
  without becoming a UXP-side implementation.
- **Select Label group** — same absence, same fallback shape (`cmd.edit.labelgroup`).

Both are narrow, already have a proven fallback, and do not block preferring UXP for everything else.

### D — Deliberately not part of the production path

The native preset-assist research track (window capture, OCR/UI-Automation result gating, guarded
pointer movement) and the preset alias/bridge export were both explicitly rejected as final workflows
by product decision, retained only as research evidence. `EFFECT_IDENTITY_MATRIX.*` is a development
artifact, never loaded by the plugin.

### Recommendation

UXP has parity or better for everything except Timeline-item Label operations, which is a narrow,
already-solved fallback rather than a reason to delay adoption elsewhere. Phase 3 of
`EffectPalette/ARCHITECTURE.md` (hybrid release: prefer UXP, retain CEP only for operations without
parity) is supportable today for every operation this proof of concept has exercised, conditioned on
shipping the bucket-B caveats as visible product behavior (a real `reconstructEasing` setting, and
user-facing messaging when a preset is rejected for an opaque parameter) rather than leaving them as
this repository's diagnostic-only defaults. Actual CEP retirement remains a separate decision outside
this proof of concept's scope.

## Stage 5: transport implementation

The user's stated goal changed this stage from "design" to "build": they want an eventual real
migration off CEP, not an indefinitely isolated proof of concept, with one hard constraint - it must
work with no user-visible configuration. That constraint, combined with UXP's documented network
permission model (an exact domain must be pre-declared in the manifest; there is no runtime
negotiation), rules out any form of port discovery and settles the design on a **fixed** port baked
into both sides at build time. The same domain-permission model, combined with the absence of any
documented UXP capability for accepting inbound connections, settles the direction: the Python
companion must be the WebSocket server, and this plugin the client that connects out and reconnects
with backoff if the companion is not running yet.

`transport.js` implements this: `entrypoints.plugin.create()` (confirmed via the official reference
to fire on plugin load, independent of any panel or command) opens a `WebSocket` to
`ws://localhost:58756`, sends a `{type:"hello", token}` handshake, and after the companion
acknowledges it, dispatches every subsequent message through `executionAdapter.execute()` with the
same 24-action handler map (`ACTION_HANDLERS` in `index.js`) the diagnostics panel's own buttons use.
A network caller therefore has exactly the same reach as the visible UI, nothing more. The
diagnostics panel gained a read-only "Companion transport" section showing live connection status,
for debugging this on the host - it does not control the connection beyond a manual reconnect button.

No reference Python-side server has been written yet. Per the user's explicit instruction, the stable
`Effect-Palette`/`EffectPalette`/`Effect-Palette_DEV` repositories are read-only backups and must
never be edited directly; at most, files from them may be copied into this repository. A companion
server implementation, when built, belongs in this repository first and is the user's own work (or a
future authorized session's) to carry into the real product.

The manifest's `requiredPermissions.network.domains` entry and the exact port are implemented and
host-tested. `scripts/reference_transport_server.py` (throwaway test tooling, not a product
implementation) stands in for the future companion: on connect it completes the token handshake,
sends a `diagnostics.read` probe, then a `timeline.applyVideoEffect` mutation. Reloading the plugin
in Premiere 26.3.2 on 2026-08-23 produced four independent connect/handshake/probe/mutate cycles, each
returning real host data (project, sequence, live timeline selection, full 829/102/305-entry catalogs)
and, for the mutation, `verificationSucceeded: true` with `identityMatchesRequest: true` - Gamma
Correction was actually added to the selected clip's component chain, not merely acknowledged in
JSON. This confirms `entrypoints.plugin.create()` opens the socket automatically on plugin load, the
handshake works, and a network caller reaches the exact same `executionAdapter.execute()` path as the
diagnostics panel's own buttons, for both reads and mutations. A real product-side companion server is
still unwritten and is the user's (or a future session's) work, per the read-only-CEP-repos
constraint.

## Stage 6: the real companion, first vertical slice

The user confirmed the goal is to actually bring the stable product's Python companion onto this
transport, not keep proving isolated capabilities. `companion/` in this repository is a copy (never
an edit) of `EffectPalette/app.py`, `beta_report.py`, `requirements.txt`, `assets/` and one example
data template, taken read-only from the stable repository per the user's standing instruction.

`PremiereExecutionAdapter` (`backend_name = "cep"`) already documented itself as "Host boundary
shared by shortcuts, macros and future UXP execution" and is read from exactly two call sites
(`EffectPalette.__init__`, `QtEffectPalette.__init__`), both now going through a new
`create_execution_adapter()` factory that prefers `companion/uxp_execution_adapter.py`'s
`PremiereUxpExecutionAdapter` (`backend_name = "uxp"`) whenever a Qt event loop is running, falling
back to the CEP adapter otherwise. `PremiereExecutionAdapter` itself is unmodified and untouched
apart from gaining `poll_status`/`is_terminal`/`is_success` wrappers around its existing free
functions, so the swap point is polymorphic rather than a rewrite.

`PremiereUxpExecutionAdapter` embeds a `QWebSocketServer` (`PySide6.QtWebSockets` - already covered
by the existing `PySide6>=6.7.0` dependency, no new package) bound to the exact
`ws://localhost:58756` / token `transport.js` already has baked in, so the plugin connects to the
real companion with zero additional configuration. This first slice translates `effect["type"] in
{"video", "audio"}`: audio passes `effect["name"]` straight through as `timeline.applyAudioEffect`'s
`displayName` (no lookup needed - `AudioFilterFactory.createComponentByDisplayName` takes the display
name directly); video requires resolving a display name to a `matchName`, built from
`catalog.videoEffects.read`'s two parallel arrays. Since Adobe does not document positional
correspondence between those arrays (`CAPABILITY_MATRIX.md`), the adapter treats a same-index pairing
as a candidate only, and compares the *actually inserted* component's display name (from the plugin's
own post-insertion `verification`) against what was requested, failing closed with
`error_identity_mismatch` on a wrong guess rather than reporting a false success.

Host-tested end to end in Premiere 26.3.2 on 2026-08-23, through the real companion's real floating
palette and real global hotkey (`Ctrl+Espaço`), not a throwaway script:

- **Video**: applying "Gaussian Blur" produced `identityMatchesRequest: true`, the clip's component
  count going from 2 to 3, and the plugin's own post-insertion `displayName` reading back
  "Gaussian Blur" - confirmed in Effect Controls by the user.
- **Audio**: applying "Lowpass" (twice) and "Hard Limiter" produced the same verified-identity result
  on the real selected clip.
- **Failure paths** (isolated adapter test, real code, fake plugin peer): a request before any plugin
  is connected resolves immediately to `error_not_connected`; an unsupported `effect["type"]` resolves
  to `error_not_supported` without touching the network; a display name absent from the cached catalog
  resolves to `error_effect_not_found`; a name that resolves locally but the plugin itself rejects
  resolves to the mapped `error_execution_failed`. None of these hang or wait for the 5s timeout.

Proving the CEP bridge was not involved required more than "the file wasn't created," since the CEP
extension (`Type=Custom`, `AutoVisible=false`, `StartOn: ApplicationActivate`) auto-loads without
appearing in Window > Extensions - a real gap in the first proposed check, caught by the user. The
standing proof instead layers four independent facts: `send_command()` (the only code path that
writes the CEP bridge file) has exactly one call site, inside the CEP adapter class that
`create_execution_adapter()` did not choose; the connected-plugin log line is only reachable after a
token handshake only this plugin knows; `data/current_selection.json`, which `bridge.js` rewrites
every 300ms whenever it is actually running, sat unchanged for the entire test window; and the
returned clip name/component counts are live Premiere state the Python adapter has no other way to
have produced.

### Second slice: presets

`effect["type"] == "preset"` now translates to `timeline.applyImportedEffectPreset` with the
preset's `name`/`category` passed straight through - no display-name guessing is involved, since the
plugin resolves against its own parsed `.prfpset` catalog and already fails closed on an ambiguous or
missing name, so `ok: true` needs no separate identity check the way video's same-index candidate
lookup does.

That catalog previously lived only in memory and vanished on every plugin reload, which would have
meant re-picking the 42.8MB `.prfpset` by hand each session - the opposite of the zero-configuration
requirement. `importPrfpsetCatalog` now also stores a `localFileSystem.createPersistentToken(file)`
in `localStorage`, and `entrypoints.plugin.create()` calls `restoreImportedPresetCatalogFromToken()`
to re-read it on load. Restoration is best-effort by design: a moved file, revoked permission or
absent token clears the stored token and leaves the catalog unset, which is exactly the pre-existing
"Import a .prfpset catalog first." failure rather than a new error path. Host-confirmed on 2026-08-23
- after an explicit UDT unload/reload, `catalog.effectPresets.inspectImported` resolved a real preset
(`TESTE PRESET - FINAL`, full filter chain) with no file picker shown.

One real defect was found here by the user, not by the tests: the first implementation hardcoded
`reconstructEasing: false`, which silently discarded the easing shape this project spent most of its
effort decoding. Applying `Slide IN UP` wrote 2 principal keyframes (`sampleStrategy:
principal-keys-only`) instead of the 52 frame-sampled ones (`frame-sampled-approximation`, 60fps
detected) - values and timing correct, curve visibly wrong. The default is now
`RECONSTRUCT_EASING_DEFAULT = True`, still overridable per action if this later becomes a real
product setting per Stage 4 bucket B. The user confirmed visually that the curve now matches a manual
application. Both applications are host evidence from the real companion, real palette, real hotkey.

Deferred, not blocking: transition/nest/project-item/generic-item/favorite-item translation
(same adapter pattern, one action type at a time); generic-item creation from scratch and favorite-item
import (CEP relies on undocumented `qe.project` calls with no known UXP equivalent); Timeline-clip
label and label-group selection (native-keystroke fallback stands per Stage 4 bucket C, though CEP's
own `app.executeCommand("cmd.sequence.edit.label."+index)` suggests a documented UXP command-execution
equivalent may be worth checking before accepting that as final); a single combined installer.

### Third slice: video transitions, and the limits of guessing an identity

`effect["type"] == "transition_video"` maps to `timeline.applyVideoTransition`. This slice took far
longer than presets because, unlike effects, **there is no way to verify a transition's identity**:
`VideoTransition` exposes no methods or properties at all (`CAPABILITY_MATRIX.md`), so nothing plays
the role `verification.displayName` plays for `timeline.applyVideoEffect` - a wrong guess here cannot
be caught after the fact.

The CEP catalog's 340 display names could not be reused for this reason. Measured against the real
host catalog: a vendor-aware textual match against UXP's 305 `matchNames` resolved only 105 of 340
unambiguously, and even those are unverifiable by construction - Adobe documents no correspondence
between a CEP-parsed name and a UXP `matchName`. An earlier attempt at showing the CEP names anyway
had already shown why guessing here is dangerous, not just incomplete: a naive (non-vendor-aware)
match put BCC's "Checker Wipe" onto Adobe's `ADBE Checker Wipe`, a different vendor's transition
entirely, with no way to detect the error afterward.

Three designs were tried and rejected by the user before landing on the current one - each rejection
is preserved here because it rules out a design a future session might otherwise re-attempt:

1. **Guessed word-splitting of UXP's own matchNames** (`RADIALWIPE` -> "Radial Wipe" via a
   dictionary/vocabulary built from the catalog's own already-spaced words). Produced 305 unique,
   fully readable labels, but the user rejected it outright as still unclear which real transition
   some entries were - an interpretation of the identifier is not the identifier.
2. **The bare matchName** (`transition_video` label = matchName minus its `AE.`/`PR.` prefix, no
   reformatting at all). Technically the most honest option, but the user found it illegible for
   BCC's `_ALLCAPS` and Sapphire's `camelCase` families.
3. **Vendor as a category breadcrumb** (`Transicoes > Video > BCC`, plain core name). Matched how
   presets already show their source pack, but the user wanted the vendor visible on the entry
   itself, not implied by a subtitle.

The shipped design: `(Vendor) rest-of-name`, where the vendor tag is extracted from a prefix that is
*literally encoded* in the matchName (`BCC`, `AE_Impact`, `S_` for Sapphire, etc.) - not a guess about
wording, since the prefix is either present, delimited by `_`/space/a capital letter, or it is not.
36 entries remained fully glued/all-caps after this (`BCC_RADIALWIPE`, all BCC). Of those, 15 have an
exact-string match elsewhere in the *same real catalog* - e.g. `BCC_RADIALWIPE` squashes to identical
letters as the already-legible `ADBE Radial Wipe` - and borrow that spelling for display while keeping
their own vendor tag and matchName, since this is an exact whole-string match against real catalog
text, not a segmentation guess. The user explicitly declined guessing for the remaining 21
(`BCC_SWISHGLOW`, `BCC_MLJUMPCUT`, etc., no match anywhere in the catalog): shown as-is, all-caps,
rather than invented. Host-confirmed measurement: 305/305 unique labels, 0 collisions, 15/36
previously-illegible entries fixed with real evidence, 21 left honestly unreadable.

Applying a transition also required its own success check: `applyVideoTransitionToSelection`
reports `verificationSucceeded` from a before/after transition count on the sequence rather than a
per-item check (nothing else is available), so `_resolve_pending` now treats an explicit
`verificationSucceeded: false` as `error_not_applied` for every action type, not just transitions -
a transaction the plugin accepted but that measurably changed nothing must not read as success.

Host-confirmed end to end through the real companion's real palette and hotkey: applying a video
transition by search landed the correct transition on the selected clip, confirmed by the user in
Premiere - the readability work above happened only after that mutation was already proven correct,
so the several label redesigns changed only what the user reads in the search list, never what
`timeline.applyVideoTransition` was actually sent.

### Fourth slice: Nest, and a signal CEP had that this migration silently dropped

`effect["type"] == "timeline_action"` with `action == "nest"` maps to `timeline.createNest`. Unlike
the earlier slices, this is not the only action reachable for this effect - `nestMode == "premiere"`
(a native `cmd.clip.nestify` keystroke) never reaches `execute_effect_through_adapter`/this adapter
at all; only `nestMode == "api"` does, exactly as under CEP.

`resolve_nest_mode("auto")` decided between those two modes by reading `data/current_selection.json`
- a file only `bridge.js` ever kept updated. Nothing in this migration replaced that writer, so under
the UXP-backed companion the file is permanently stale, `has_audio`/`has_video` are always false, and
`resolve_nest_mode` always falls through to whichever mode `cmd.clip.nestify`'s shortcut lookup picks
- in practice always "premiere". The consequence is real, not cosmetic: CEP's `nestSelectionApi` path
existed specifically because native Nest leaves a selection spanning multiple audio tracks unmerged
instead of consolidating them onto one track, and losing that routing silently reintroduces the bug
it was written to avoid. The user found this by describing the actual Premiere behavior
("as camadas de áudio continuam lá, não ficam em uma camada de áudio unificada"), not by reading code.

The fix asks the plugin directly instead of trusting a file nothing writes to anymore.
`describeTrackItem`/`readDiagnostics` (`index.js`) now classify each selected item with `isAudio`,
computed the same way every apply-effect/transition handler already does (compare against the
sequence's own audio-track media types) - exposed as a read instead of staying a side effect only
mutations could see. `PremiereUxpExecutionAdapter.has_multi_track_audio_selection()` asks for it via
a `diagnostics.read` round trip, and - because `resolve_nest_mode` is called synchronously from a UI
callback that cannot itself be made async - blocks the caller on a nested `QEventLoop` for up to
1.2s, returning `None` (not a guessed True/False) on any failure so `resolve_nest_mode` falls back to
the old file-based heuristic rather than picking a mode on no information. Host-confirmed: a real
selection with audio on two different tracks (`trackIndex` 0 and 1) resolved to `api`, applied via
`timeline.createNest`, and the created Nest's `audioTrackIndex` in the response was `0` for both -
consolidated onto one track, not left split.

A second, unrelated regression surfaced from the very same host test: applying a Nest with a blank
name field produced the generic UI label "Nest clips" as the literal sequence name. Checked against
the real host.jsx (`_uniqueNestSequenceName`/`_nextNestCodeName`, read-only reference): CEP's actual
behavior for a blank name was never "use whatever text was on the button" - it generates
`"FXN-" + zero-padded(highest existing FXN-NNN in the project + 1)`, scanning the real project's
sequence names for collisions. `readDiagnostics` now also returns `project.sequenceNames` for exactly
this; `PremiereUxpExecutionAdapter.next_nest_codename()` reuses the same blocking-round-trip helper
(factored out as `_blocking_request` once a second caller needed the identical pattern) to compute the
same `FXN-NNN` scheme against real project state, never a local counter that could drift from it. Unit
logic verified with 6 cases (case-insensitivity, partial/embedded names correctly rejected, multi-digit
numbers, `None`/empty entries); the `FXN-001` base case (no prior `FXN-*` sequences) is also
host-confirmed - accidentally, when a stray test connection let the real plugin answer a test script
instead of its intended fake peer, creating a real (immediately deleted) Nest in the user's project.
That interference is itself a caution worth recording: an isolated test server on the same port a real,
auto-reconnecting plugin instance is also trying to reach can get a live answer instead of its
fake one, with a real side effect - future sessions should assume that risk rather than treat a
"standalone" test script as isolated by construction.

### Bin placement, added after the slice above shipped

The user tested the slice above and found a second gap: CEP always filed a newly created Nest into
a project bin (`_organizeNestSequenceObject`/`DEFAULT_NEST_BIN`, `host.jsx`, renamed by the user
from "Nested Sequences" to "Nested Clips" - `DEFAULT_NEST_BIN` and every payload default now match).
`timeline.createNest` had no equivalent, so a created Nest just landed wherever Premiere itself put
a new subsequence. `FolderItem.createBinAction`/`createMoveItemAction` (Premiere UXP API, `Project.
getRootItem()`) are genuinely new territory for this project - no prior action had touched project-
panel bin structure - and getting them working took two host-confirmed wrong turns before a
third attempt matched the one working reference found:

1. **"Requires locked access"** - the `Action` objects (`createBinAction`, `createMoveItemAction`)
   were constructed *before* entering `project.lockedAccess(() => {...})`, only added to the
   transaction inside it. Every other working transaction in this codebase constructs the action
   itself inside the locked callback, not just the `executeTransaction()` call - this one broke
   that pattern by accident and Premiere's own error named exactly why.
2. **Silent no-op** - fixed #1, but `moveTransactionSucceeded: true` came back and nothing moved.
   The official API reference documents `createMoveItemAction(item, newParent)`'s parameters but
   not which object it must be called on; by analogy with `createRemoveItemAction` ("removes the
   given item from *this* folder") the item's own current parent bin seemed like the right target
   - it built without error and reported success while doing nothing, which is a worse failure
   mode than an exception because nothing catches it. Adobe's own official
   `AdobeDocs/uxp-premiere-pro-samples` reference panel (`sample-panels/premiere-api/src/
   projectPanel.ts`, `moveItem()`) resolved the ambiguity: it calls `createMoveItemAction` on the
   project's `rootItem` unconditionally, not the item's current parent, and re-casts the
   destination with `FolderItem.cast()` before passing it. Matching that exactly fixed it -
   host-confirmed by the user both in the transaction log (`binCreated: true,
   moveTransactionSucceeded: true`) and visually in the Project panel.

Recorded because both wrong turns were plausible from the documentation alone and neither was
caught by `ok: true` - a lesson for any future action built on `FolderItem`: verify visually before
trusting a `Succeeded` flag from an API this thinly documented, and check the official samples repo
for a working call site before inferring one from parameter names.

### Fifth slice: project-item insertion, and no selection API to target it

`effect["type"] == "project_item"` maps to `timeline.insertProjectItem`. This slice had its own
identity problem before it had a behavior one: the action's original implementation required the
target item to already be selected in the Project panel (`ProjectUtils.getSelection`), but there is
no official UXP API to *set* that selection - `ProjectItemSelection` only exposes `getItems()`,
confirmed by reading its full class reference. A search-then-apply palette has no other way to make
"the item the user picked" also be "the item Premiere's own panel has selected," so `nodeId` (the
field the CEP-era catalog carries) turned out useless too: it is an ExtendScript-only value with no
UXP counterpart. The fix instead resolves the item by its full bin path (`treePath`, e.g.
`\Project.prproj\FX.palette_Assets\Adjustment Layer_1920x1080`) - real, human-visible names, not an
opaque ID - walking the same `findDirectChildBin` the Nest bin-placement work already proved out
(`findProjectItemByTreePath`). `insertSelectedProjectItem` now accepts an optional `treePath` and
only falls back to the old selection-based lookup when it's absent, so the diagnostics panel's own
manual probe (select in the panel, click apply) still works unchanged.

Host-testing this exposed a second, larger gap: the user found the insertion behavior didn't match
the real product. Reading `host.jsx`'s `_insertResolvedProjectItem` (read-only reference) in full
showed three things this slice's first pass had skipped past:

1. **Track targeting follows the current Timeline selection**, not a hardcoded track 0 -
   `_resolveInsertionTracks` takes the track of the first selected item of each kind.
2. **An occupied track is avoided** - `_findAvailableVideoTrackAtTicks`/`_findAvailableAudioTrackAtTicks`
   scan forward from that track for the first one with nothing already at the insertion point (or,
   for the case below, nothing overlapping the whole span).
3. **Certain items stretch to cover the current video selection** - `_projectItemShouldSpanSelection`
   matches project-item names against `/adjustment layer|bars and tone|black video|color matte|
   transparent video|universal counting leader/i` (host.jsx's own regex, ported verbatim); when it
   matches and one or more video clips are selected, `_selectionVideoSpan` computes their combined
   [earliest start, latest end), the item is inserted at that start instead of the playhead, and its
   end is stretched to match afterward.

`resolveInsertionTracks`, `findAvailableVideoTrackAtTicks`/`findAvailableVideoTrackInRange`/
`findAvailableAudioTrackAtTicks`, `projectItemShouldSpanSelection`, and `selectionVideoSpan` port all
three using only documented UXP calls (`Sequence.getSelection`, `TrackItem.getStartTime`/
`getEndTime`, `VideoClipTrackItem.createSetEndAction` for the post-insert stretch). Host-confirmed:
a real 3-clip video selection produced `spanSelection: {startTicks, endTicks, sourceItemCount: 3}`
and `spanTrimSucceeded: true`, landing the Adjustment Layer exactly across the selection on the
first free track above it.

**What CEP can do here that UXP genuinely cannot**: when every existing track is occupied, host.jsx
creates a new one via `qe.project.addTracks()` - the legacy QE DOM this project has deliberately
never used elsewhere. Confirmed by exhaustive search this time, not assumption: every plausibly
relevant class (`Sequence`, `SequenceEditor`, `VideoTrack`, `AudioTrack`, `SequenceSettings`,
`Application`) and the complete official changelog from the 25.2.0 public beta through 26.3.0 - track
*renaming* was added along the way, track *creation* never was. The fallback
(`findAvailableVideoTrackAtTicks` et al.) now returns the *last* existing track instead of host.jsx's
own fallback (the original starting track), since reusing the starting track risks silently
overlapping the very clip the user just selected; the response's new `trackFallback: {video, audio}`
booleans report whenever this path was taken instead of hiding it. This is a confirmed platform
boundary, not a gap to keep chasing - the same category as the Timeline-item Label finding in Stage 4.

Still deferred: generic-item creation from scratch (needs the same missing track-creation capability
for some kinds, plus `qe.project.newBlackVideo`-style calls for others); favorite-item import;
Timeline-clip label and label-group selection. `isSequence` project items (inserting a whole
sequence as a nested item, distinct from Nest) are not specially handled by this slice either -
host.jsx branches on it explicitly and this port does not yet.

## Parity assessment (2026-08-24)

Requested by the user after five slices: how close is this to the stable CEP product today, and
how should future UXP releases be watched for capabilities that close the remaining gaps.

### Per capability, against `UXP_HANDOFF.md`'s own list of CEP's working capabilities

| Capability | Status | Notes |
| --- | --- | --- |
| Video/audio effect apply | ✅ Full parity, host-tested | Identity-verified both directions (video: same-index candidate + post-insert display-name check; audio: exact `displayName` match, no guessing needed) |
| Preset apply | ✅ Full parity, host-tested | Easing reconstruction on by default; catalog survives plugin reload via a persistent file token |
| Video transition apply | ✅ Functional, host-tested | Label readability is permanently constrained - `VideoTransition` exposes no properties at all, so no name can ever be verified the way effects are |
| **Audio** transition apply | ❌ Confirmed platform gap | `TransitionFactory` and `AudioClipTrackItem` (full class references checked) have no transition-related method at all - not unwired, not possible today |
| Insert existing Project item | ✅ Full parity, host-tested | Track auto-targets the current selection, avoids an occupied track, stretches Adjustment-Layer-like items to match a video selection - all three ported from `host.jsx` |
| Insert favorite item | 🔲 Not built yet, looks buildable | CEP uses `app.project.importSequences()`/`importFiles()` - both documented standard-DOM calls already used elsewhere in this project; likely the easiest remaining slice |
| Create generic item from scratch (Black Video, Color Matte, Bars & Tone, Transparent Video, Universal Counting Leader) | ❌ Confirmed platform gap | CEP itself only reaches these via `qe.project.newBlackVideo`-style calls - the undocumented legacy QE DOM this project has deliberately never used |
| Create generic item: Adjustment Layer specifically | ❌ Confirmed gap, CEP included | Not a UXP-only limitation - CEP has no creation API for this either and works around it by importing a template `.prproj`; a UXP equivalent would need the same kind of workaround, not a missing API call |
| Nest, auto-routed native/API | ✅ Full parity, host-tested | The routing signal itself (multi-track-audio detection) had gone silently stale under this migration and was restored via a new `diagnostics.read` field, not something UXP was missing |
| Nest: default codename, bin placement | ✅ Full parity, host-tested | `FXN-NNN` scheme and filing into a project bin (`FolderItem.createBinAction`/`createMoveItemAction`) both ported |
| Create a new Timeline track when none is free | ❌ Confirmed platform gap | Exhaustive check: every plausibly relevant class (`Sequence`, `SequenceEditor`, `VideoTrack`, `AudioTrack`, `SequenceSettings`, `Application`) plus the complete official changelog from the 25.2.0 public beta through 26.3.0 - track *renaming* was added along the way, track *creation* never was |
| Set a Timeline clip's Label | ❌ Confirmed platform gap (Stage 4) | No `TrackItem` label API, and no UXP equivalent to CEP's own `app.executeCommand()` escape hatch was found either (checked `Application` and the full class index) - CEP's own route to this is closed off in UXP twice over |
| Select a Label group | ❌ Confirmed platform gap (Stage 4) | Same absence; CEP itself only reaches this via a native OS keystroke, not through `bridge.js`/`host.jsx` at all, so there was never a DOM path to port in the first place |
| Set a **Project item's** color label | ⚠️ Implemented but narrow | `projectItems.setColorLabel` works and is tested, but the action still only accepts the hardcoded Violet label from its original diagnostic-probe form - generalizing to any of the ~8 label colors is a real but small remaining step, not a platform gap |
| Global shortcuts / Stream Deck F13-F24 bindings | — Not a UXP question | Native Win32 `RegisterHotKey` in the Python companion, unaffected by CEP vs UXP either way |
| Aliases, recent actions, actionable diagnostics | — Not a UXP question | Companion-side product features (search index, history, settings-panel health checks), independent of the execution backend |
| `reconstructEasing` as a real user-facing setting | ⚠️ Wired, not yet exposed | Defaults on and is overridable per action already; Stage 4's own recommendation to ship it as a visible companion setting (not a diagnostic-only default) is still open |

### Reading the gaps as a group

Every ❌ above traces back to exactly one of three walls, not five different problems:

1. **The legacy QE DOM** (`qe.project.*`) - CEP's own escape hatch for creating tracks and most
   generic items, deliberately out of scope for this project from the start (`TECHNICAL_PLAN.md`'s
   own "Scope and invariants"). Audio transitions and Timeline-clip Label are not QE-DOM cases -
   they are missing from *both* the documented and the QE surface.
2. **No generic command-execution API** - UXP has nothing resembling CEP's `app.executeCommand()`,
   so even the "just run the same menu command CEP runs" fallback that a couple of these gaps might
   otherwise have doesn't exist either.
3. **Read-only selection APIs** - `ProjectItemSelection` has no setter, which is why project-item
   insertion needed a path-based workaround rather than "select it, then insert the selection" the
   way CEP itself effectively works.

None of these three are things a future *action* in this project can work around - they would need
Adobe to add API surface. That is exactly what the monitoring plan below is for.

### Watching for future UXP releases

Adobe ships Premiere roughly every 1-3 months (`AdobeDocs/uxp-premiere-pro`'s own changelog:
25.0 Oct 2024, 25.1 Dec 2024, 25.2 Apr 2025, 25.3 Jun 2025, 25.4 Aug 2025, 25.6 Nov 2025, 26.0
Jan 2026 - also the "Premiere Pro" → "Premiere" rebrand, 26.2 and 26.3 since), but new UXP *API
surface* doesn't land in every point release - the changelog's own "New APIs" section only appears
some releases (26.3.0's, for example, added several; the releases between 25.6.0 and 26.2.0 added
none). The changelog itself is the one thing worth checking periodically, not Premiere's own release
notes: `github.com/AdobeDocs/uxp-premiere-pro`, `src/pages/changelog/index.md`. Each entry lists
exactly which classes/methods were added, so a future session can grep it for anything matching
"track", "command", "execute", "selection", or "audio transition" rather than re-reading the whole
API surface from scratch. Worth a look next time this project resumes after a gap of a few months,
or whenever the user mentions Premiere updated itself.
