# Technical plan

> This is an append-only log of the migration, slice by slice. Early sections still say "proof of
> concept" and cite smaller numbers (e.g. "23 actions"); those are the state at the time of writing,
> not today. For the current state read `STATUS.md`. The fourteenth slice onward, the "proof of
> concept" framing no longer applies.

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

Responses are plain serializable objects with `ok`, `schemaVersion`, `actionType`, `requestId`, and either `data` or `error`. The allowlist (`execution-adapter.js`'s `SUPPORTED_ACTIONS`) matches every mutation and read the companion actually sends; unknown actions fail closed. It grew to 32 during the capability-probe phase, then dropped back to 21 in the 2026-08 cleanup once the diagnostic probes and the rejected preset bridge were removed (see `STATUS.md`).

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
same 23-action handler map (`ACTION_HANDLERS` in `index.js`) the diagnostics panel's own buttons use.
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

Still deferred: favorite-item import; Timeline-clip label and label-group selection. `isSequence`
project items (inserting a whole sequence as a nested item, distinct from Nest) are not specially
handled by this slice either - host.jsx branches on it explicitly and this port does not yet.

## Sixth slice: generic items via template import

CEP creates Bars and Tone/Black Video/Color Matte/Transparent Video fresh every time, via
`app.project.newBarsAndTone` (documented) or `qe.project.newBlackVideo`-style calls (undocumented
QE DOM) - UXP has neither. Adjustment Layer already worked around the same gap (no creation API on
either platform) by importing a pre-built `.prproj`'s sequence via `Project.importSequences`; this
slice extends that trick to Bars and Tone, Black Video, and Transparent Video (Color Matte stays
out - neither platform can set its color after creation, so a template built once could never be
recolored per use, matching the user's own reasoning for excluding it).

`index.js`'s `ensureGenericProjectItem` is generic across all four `GENERIC_ITEM_TEMPLATES` entries:
find an already-organized item of the right size anywhere in the project; otherwise pick the
closest-resolution entry from `assets/template_project/generic_item_templates.json`, import that
sequence, move whatever landed at root into `FX.palette_Assets`, locate the item by its expected
name, delete the now-empty imported wrapper sequence. Host-tested for all four types, in a fresh
sequence to force a real import as well as a reused already-existing item, with `sequenceCleanup`
in the response confirming the wrapper sequence was actually deleted (a name-based match here
originally left it behind silently - fixed to match by guid).

Building the three new template sequences needed a generator no CEP precedent exists for (host.jsx
only ever *imports* the Adjustment Layer template, never builds one) - `tools/template_generator/`
is a throwaway, unsigned CEP dev panel (not part of the product, not part of any stable repo) that
clones each existing `AL_TEMPLATE_WxH` sequence for its settings, creates the generic item on it,
and records the resulting sequenceID. Notable dead ends before it worked, kept here so they aren't
re-attempted: `app.project.createNewSequence()` opens an interactive "New Sequence" dialog and
blocks waiting for it - undocumented, discovered live, unusable from an unattended script;
`app.project.newBarsAndTone`'s return value is not a real `ProjectItem` despite the docs and
host.jsx's own successful `.moveBin()` usage suggesting otherwise (`.type` reads `undefined`,
`insertClip`/`overwriteClip` throw "Illegal Parameter type") - the actual item has to be located by
diffing the project root afterward, same as the QE-DOM-created items already required; a
name-based "already recorded" check in the generator's own JSON-merge step left stale, since-deleted
sequenceIDs in the config whenever a sequence got rebuilt between debug rounds, which is what made
`Project.importSequences` on the UXP side return `true` while importing nothing.

## Seventh slice: Timeline-clip Label and Label group

Re-verified from scratch against the current official API surface (`TrackItem`, `VideoClipTrackItem`,
`AudioClipTrackItem` - every property and method listed, none related to color/label) rather than
trusting the Stage 4 conclusion unchecked: still no DOM API on either platform. But CEP's own real
mechanism for this was never DOM in the first place - it resolves `cmd.edit.label.N`/
`cmd.edit.labelgroup`'s keyboard-shortcut binding out of the user's active `.kys` profile and
synthesizes the matching keystroke (`keybd_event`), independent of CEP vs UXP. `companion/app.py`
already carried this ported verbatim (`send_native_shortcut`, `find_premiere_command_shortcut`,
`load_premiere_label_preferences`, `_execute_label_action`, wired into both the Tk and Qt search/apply
paths) - it never depended on the execution backend, so there was no UXP-side code to write. The only
real gap was that this repo had no equivalent of the CEP installer's `configure_premiere.ps1`, which
guarantees those 16 label commands plus `cmd.edit.labelgroup`/`cmd.clip.nestify` have a shortcut bound
by adding an obscure internal one (`Ctrl+Alt+Shift+<key>`) for any that don't. Ported as
`scripts/configure_premiere_shortcuts.ps1` (same logic, only the log destination adapted since this
repo has no installer/InstallDir concept yet). Host-run: on this machine every command was already
bound from the earlier CEP install, so nothing needed adding - but the script is what makes this
resilient on a machine that never had CEP installed at all.

## Eighth slice: favorite items, and a deeper finding about catalog data

Investigating "insert favorite item" surfaced something bigger than the feature itself: every
catalog this product lists from (effects, presets, project items, favorites) was still being read
from files the legacy CEP extension's own headless worker (`bridge.js`/`worker.html`) writes to
`%APPDATA%\Adobe\CEP\extensions\EffectPalette\data\`. Prior slices had only replaced the
*execution* layer (applying/inserting); *discovery* (what's available to apply) silently still
depended on that CEP worker being installed and periodically running - the video-transition catalog
was the only exception, already ported UXP-native in an earlier slice. This slice ports favorites
the same way, and leaves the same gap open for effects/presets/project items as a known follow-up,
not something this slice's scope covered.

A favorite is not a flag on an arbitrary item - the user opens the bundled `template_project.prproj`
in Premiere, drags media/sequences into a root bin named `FX.palette_Favorites` (sub-bins become the
favorite's category), and CEP's `getTemplateFavoritesListSafe()` (host.jsx, read-only reference)
scans it - but only while that specific project is the one currently open, since there's no API on
either platform to inspect an unopened project's bin structure. `readFavoritesCatalog` (`index.js`)
ports this exact guard using `Project.path` against the plugin's own bundled template path, and the
companion re-requests it on a 5s timer (`FAVORITES_REFRESH_INTERVAL_MS`) so a favorite curated
mid-session shows up without a plugin reload - mirroring how the CEP worker polled every ~3s.
`companion/app.py`'s `_load_favorites` now prefers `uxp_favorites.json` (UXP-native) over
`premiere_favorites.json` (CEP-sourced) once it exists, same override pattern as transitions.

Insertion (`resolveFavoriteProjectItem`) mirrors host.jsx's `_importFavoriteProjectItem`: search the
whole project for an already-imported copy first (by media path via the newly-verified
`ClipProjectItem.getMediaFilePath()`, or by name+`isSequence()` for a sequence favorite - a favorite
sequence has no separately-tracked identity once imported, so name is what CEP itself matches on
too), otherwise import fresh (`Project.importFiles` for media, `Project.importSequences` for a
sequence - both host-tested working) and organize into `FX.palette_Assets`. Unlike a generic item's
throwaway wrapper sequence, an imported favorite sequence is real content and is kept, not deleted.

Two real bugs found and fixed via host-tested iteration, not guessing:
- `Project.path` was observed returning Windows' `\\?\` extended-length-path prefix
  (`\\?\C:\Users\...`) while the plugin's own bundled-file path (`localFileSystem`/`nativePath`)
  does not carry it - otherwise byte-identical for the same file, so the template-project guard
  silently never matched until `normalizePath` was taught to strip that prefix.
- A diagnostic dedup guard (only log `catalog.favorites.read`'s `applicable` value on change) wasn't
  reset on disconnect, so a value carried over from a dead connection suppressed the first log line
  of the next one - fixed by resetting it in `_on_disconnected`, unrelated to the feature itself but
  what made the `\\?\` finding visible at all.

Host-tested: first-time import (real file path, `verificationSucceeded: true`) and re-click dedup
(reused the same project item, no duplicate import) for a media favorite. The sequence-favorite path
shares the same import/organize/insert mechanism, host-tested elsewhere in this project for
generic items, but has not itself been exercised against a real favorited sequence.

## Ninth slice: video/audio effects, presets, and project items become UXP-native catalogs

Closes the discovery-layer gap the eighth slice found: `readVideoEffectCatalog` now also returns
`AudioFilterFactory.getDisplayNames()` (audio has no `getMatchNames`/matchName-based creation at
all, confirmed against the official reference - already how `applyAudioEffectToSelection` resolves
by display name, so the catalog needed nothing new for identity); `readProjectItemCatalog` walks
the current project unconditionally (no template-project guard - CEP's own `getProjectItemsListSafe`
has none either), reconstructing `treePath` by hand the same way favorites' `sourceTreePath` already
does, since UXP documents no `treePath` property directly; `readEffectPresetCatalog` exposes
whatever `.prfpset` catalog is already loaded via the existing one-time-picker-plus-persistent-token
flow (`importPrfpsetCatalog`/`restoreImportedPresetCatalogFromToken`) - no new consent needed on this
machine, since preset application was already tested earlier in this project. Companion mirrors the
same override-file pattern for all three (`uxp_effects.json`/`uxp_project_items.json`/
`uxp_presets.json`), with project items re-polled on a timer like favorites (they change as the user
edits) and effects/presets fetched once per connection (Premiere's own installed catalog doesn't).
Host-tested: video effect, audio effect, preset, and project-item insertion, all sourced from the new
files, all applying correctly.

One real bug found and fixed: `restoreImportedPresetCatalogFromToken()` is fire-and-forget from
`entrypoints.plugin.create()`, started around the same time as the transport itself - the companion's
first `catalog.effectPresets.read` could genuinely arrive before that async restore finished reading
and parsing the `.prfpset` file, intermittently reporting "no catalog" right after a plugin reload.
Fixed with one companion-side retry after a short delay rather than blocking the whole transport's
connection on presets specifically.

Presets remain the one catalog with a real (if one-time) UX cost: UXP has no silent-scan equivalent
of CEP's `fs.readdirSync` walk of `Documents/Adobe/Premiere Pro/*/Profile-*/` - every path outside
the plugin's own sandboxed folders requires a user-approved picker, at least once, ever (a persisted
token then makes every later session fully silent). Not a new limitation introduced here - the
existing `importPrfpsetCatalog` picker this session reused already had this constraint.

## Tenth slice: two real preset-reconstruction bugs, found via the ninth slice's own catalog switch

Making presets UXP-native (ninth slice) surfaced real, pre-existing bugs in `reconstructEasing`
(unrelated to the catalog source itself - a preset that already worked kept working) that the user
found by actually using the palette: one preset's curve came out visibly wrong, and a
many-keyframe preset silently failed to apply at all.

**Failed-to-apply**: `reconstructEasing` writes one keyframe per frame across an animated
parameter's whole duration - a preset with many animated parameters over several seconds can need
thousands of `createKeyframe`/`setTemporalInterpolationMode` calls, well past
`APPLY_STATUS_TIMEOUT_MS` (5s). Fixed the same way the sixth slice fixed this for generic items:
`PRESET_APPLY_STATUS_TIMEOUT_MS` (30s), `apply_status_timeout_ms` (`companion/app.py`).

**Wrong curve**: diagnosed with the project's own existing A/B comparison tooling
(`captureTransformCurveReference`/`compareImportedTransformWithCapture`, already built and
host-tested earlier in this project) rather than reading raw `.prfpset` bytes blind - the user
applied the preset manually via Premiere's own UI, we captured its real per-frame values, and
compared them numerically against the reconstruction. Two distinct bugs found this way:

1. `parsePrfpsetKeyframeEase` captured Hold/Bezier interpolation codes but neither sampling
   function ever branched on them - every segment was bezier/linear-eased regardless. Fixed:
   `isHoldOutgoing` makes a Hold-outgoing segment a step function (constant until the next
   keyframe, then jump), matching real Hold semantics.
2. Raw `.prfpset` keyframe speed fields turned out to be scored per-FRAME, not per-second, while
   `averageSpeed` here is measured from real tick deltas and is genuinely per-second - a real units
   mismatch, confirmed empirically: the reconstructed-vs-real peak velocity ratio was within 1% of
   the sequence's own frame rate (60fps). Fixed by dividing raw speed by fps before taking the ratio
   against `averageSpeed`, in `derivePrfpsetTemporalCurve`.

The fps fix measurably improved the tested case (worst-sample error 0.94 → 0.66, normalized units)
but didn't fully resolve it - the same preset's Position keyframe also has 100% influence on both
sides (an extreme, uncommon ease setting), which bunches most of the bezier parameter range near an
inflection point and produces a "flat then sudden" shape the real curve doesn't have. Confirmed not
a wrong-root solver bug (the curve is still monotonic); it's the simplified speed/influence-to-bezier
model not matching whatever additional correction Adobe's own undocumented conversion applies for
extreme influence values. Left as a documented, known limitation (comment on
`derivePrfpsetTemporalCurve`) rather than guessed at further - fully solving it would need many more
real capture/compare data points across different speed/influence combinations to empirically derive
that correction, which the user chose not to pursue further this session.

## Eleventh slice: reconstructEasing as a real setting, and confirming the no-panel architecture

`reconstructEasing` was a code-level default (`RECONSTRUCT_EASING_DEFAULT`, `uxp_execution_adapter.py`)
with no user-facing control on the companion side (the plugin's own diagnostics-panel checkbox was
a testing-only affordance, not reachable by a real user). Exposed as a persistent checkbox in the
Qt settings dialog's General tab (`companion/app.py`), following the exact same pattern as the
existing `animations` toggle: `DEFAULT_APP_PREFERENCES`/`load_app_preferences`/`save_app_preferences`
extended for a `reconstructEasing` key in the same `"app"` settings.json sub-dict,
`QtSettingsCenter`'s General tab gets a matching `QCheckBox`, and `execute_effect_through_adapter`
injects the live `palette.reconstruct_easing_enabled` value onto a preset effect's dict before
`adapter.execute()` (the sole point deciding this, since `EffectsLoader` never puts the key on a
preset's own catalog entry). Host-tested: toggled off in the running companion, applied a preset,
and the plugin log confirmed `reconstructEasing: False` reached it and produced
`principal-keyframes-only` output as expected - the setting genuinely reaches the execution path,
not just the UI.

Separately, confirmed (not just re-asserted from the architecture note) that the palette works with
the diagnostics panel never opened: closing the panel (while leaving the plugin loaded) and applying
a preset through the companion succeeded, log-verified end to end. This does depend on the plugin
staying loaded, though - closing UDT itself unloads an unsigned dev-mode plugin entirely (confirmed:
it disappears from Window > UXP Plugins once UDT closes), which is expected behavior for how
unsigned plugins are sideloaded for development, not a gap in this project's own "no panel required"
design - a properly signed, distributed build would be loaded by Premiere itself, with no external
tool needing to stay open at all.

## Twelfth slice: closing the track-creation gap

The Fifth slice's "confirmed platform boundary" conclusion about track creation still holds for
what UXP itself can do - but the user found a workable path around it entirely outside UXP's API
surface: Premiere's own "Add Tracks..." dialog, reachable via the same native-keystroke mechanism
already used for Timeline-clip Label (an unbound-but-real internal command, `cmd.sequence.addtracks`,
resolved from the user's own `.kys` profile at runtime and bound to a fresh internal shortcut by
`scripts/configure_premiere_shortcuts.ps1` exactly like the Label commands were). Host-confirmed:
the dialog's Video/Audio Amount fields default to their track type's normal add-count with the
value pre-selected on open (so typing overwrites it directly, no select-all needed), Tab from the
video Amount field reaches the audio Amount field, and Enter confirms from either field - zeroing
out the Amount for a track type that isn't needed keeps the dialog from creating an unused track
every time. `fill_and_confirm_native_add_tracks_dialog`/`schedule_native_add_tracks_dialog`
(`companion/app.py`) drive this, mirroring the existing Nest dialog's own "wait for the dialog to
take the foreground, then fill it" pattern.

**First design, abandoned after a real host hang**: since `timeline.insertProjectItem` always
inserts unconditionally (the existing `trackFallback: {video, audio}` flags only report that an
occupied track was *reused*, never skip the insertion to ask first), the first attempt tried
undoing that placement after the fact - locate the just-inserted TrackItem(s), build a
`TrackItemSelection` via `premiere.TrackItemSelection.createEmptySelection`/`addItem` (documented,
but never used anywhere in this project before this attempt), remove them with the same
`SequenceEditor.createRemoveItemsAction` the Nest feature already uses successfully, signal the
companion to create the track, then re-send the original request. A real host test (a sequence with
every track already occupied) showed the item still landed in Premiere, but the response never
reached the companion at all - not even an error, just a silent timeout. `createEmptySelection`/
`addItem` is the prime suspect (the only genuinely new, unverified API in that path), but the exact
failure was never root-caused; the whole remove-and-signal branch was reverted rather than shipped
on a guess.

**Current design**: check *before* inserting instead of undoing after. A new read-only
`timeline.checkTrackAvailability` action (`checkTrackAvailability` in `index.js`) reuses the
existing, already-proven `resolveInsertionTracks`/`findAvailableVideoTrackAtTicks`/
`findAvailableAudioTrackAtTicks` - no new Premiere API at all - to report whether a free track
already exists, at the current playhead, for whatever media kind(s) the item needs. Which kind(s)
an item needs is known precisely for generic items (`GENERIC_ITEM_MEDIA_KINDS`, since those are
fixed templates); for an arbitrary favorite or Project-panel item, both kinds are assumed needed
rather than guessed at (no `hasVideo`/`hasAudio`-style API exists on `ClipProjectItem` per the
official docs) - the accepted tradeoff is an occasional harmless extra empty track in the rare
all-tracks-occupied case, not a wrong or duplicated placement. `companion/app.py`'s `_begin_apply`
now runs this check first for `project_item`/`generic_item`/`favorite_item` effects
(`_begin_apply_with_track_check`), drives the Add Tracks dialog only for the missing kind(s), and
then dispatches the real (entirely unchanged) insertion either way - so any imprecision in the
check (adapter unavailable, timeout, dialog failure) just falls through to the insertion's own
already-safe accurate fallback (reusing an occupied track), never to a worse outcome.

**Two real bugs found via host testing, both fixed**: (1) the palette window itself still held OS
focus at the moment the native shortcut needed to reach Premiere, so the very first end-to-end test
sent `Ctrl+Alt+Shift+B` nowhere useful and the dialog never opened (`dialog_timeout` in telemetry
every time) - worse, the palette's own focus-loss recovery (`_on_focus_out`/`_hide_if_focus_lost`,
Tk only) would have fought to reclaim focus the instant it was stolen. Fixed by forcing focus onto
Premiere first (`activate_window_handle_native`) and, on the Tk build, extending
`_focus_out_grace_until` to cover the whole dialog interaction so the palette's own recovery logic
stays quiet during it. (2) Once focus was fixed, the dialog reliably opened and the track was
visibly created, but the very next insertion still reported `trackFallback.video: true` at exactly
the pre-creation track count - sending Enter only dispatches the keystroke, it doesn't wait for
Premiere to finish acting on it, so the real insertion was firing before the new track was actually
committed. Fixed by polling for the dialog to actually close (foreground back on Premiere's main
window) before telling the caller it's safe to insert. Host-confirmed working end to end after both
fixes: a new track is created only for the missing media kind, and the item lands on it cleanly.

## Thirteenth slice: dropping the diagnostics panel, and closing the preset-picker gap for real

Moving from "proof of concept" toward a real installer started with the manifest itself:
`manifest.json`'s `id`/`name` changed to definitive values (`com.effectpalette.uxp`/`FX.palette`),
and its `"panel"` entrypoint was removed entirely - real users never open Premiere's own UXP UI at
all, only the companion's hotkey-triggered search palette, so the panel had no reason to exist in
the shipped build. UXP still requires at least one entrypoint; the existing `"command"` one
(originally the 0.16.0 no-panel-required proof) stays as that minimum, invisible unless someone
digs into Window > UXP Plugins on purpose. `index.html`/`wirePanel()`'s diagnostics UI code is
untouched for local development - only its manifest declaration and `entrypoints.setup()`'s
`panels` wiring were removed - re-add both temporarily to use it again.

**A real regression, caught by the user immediately after reloading**: presets stopped applying.
The `id` change reset `.prfpset` access - UXP's persistent-token grant (and the whole
`importPrfpsetCatalog`/`restoreImportedPresetCatalogFromToken` flow the ninth slice built) turned
out to be scoped to the plugin's own `id`, so changing it silently invalidated the prior consent.
Worse, the *only* way to re-grant it had been a button inside the diagnostics panel just removed -
a real, permanent product gap this exposed rather than caused (any fresh install would have hit the
same dead end, `id` change or not).

First fix (superseded below, but still in place as a fallback): a new "Preset catalog" row in the
Settings dialog's Diagnostics tab, wired to a new `PremiereUxpExecutionAdapter.import_prfpset_catalog()`
(reusing the existing `_blocking_request` pattern, just with a much longer timeout since it blocks
on a real native file-picker interaction, not a quick round trip).

**Real fix**: the user pushed back - a Settings-tab button to fix is still a manual step, not the
"zero-configuration" bar this project holds itself to. Checking Adobe's own distribution docs (never
consulted before this slice) turned up `requiredPermissions.localFileSystem: "fullAccess"` - grants
the plugin unrestricted file access with **one confirmation at install time**, not per file, unlike
`"request"`'s per-file picker. `manifest.json` now declares `"fullAccess"`; a new
`readPrfpsetFileAtPath`/`catalog.effectPresets.readFromPath` action reads a `.prfpset` at a
companion-supplied absolute path directly (`localFileSystem.getEntryWithUrl("file:/" + path)`), no
picker at all. The companion locates that path itself - `find_prfpset_file()`
(`uxp_execution_adapter.py`) mirrors the stable CEP product's own `bridge.js::findPresetFile()`
glob (`Documents/Adobe/Premiere Pro/*/Profile-*/Effect Presets and Custom Items.prfpset`), something
only practical because the *companion*, unlike the sandboxed plugin, always had unrestricted
filesystem access. `readPrfpsetFileAtPath`'s response is shaped identically to the existing
`readEffectPresetCatalog`'s, so it reuses `_handle_effect_preset_catalog_response` unchanged.
Host-confirmed: after reloading the plugin with the new manifest, the companion found and loaded
1844 presets with zero user interaction, and a real preset application succeeded end to end.

The manual picker (`importPrfpsetCatalog`) and the Settings-tab fallback button both stay in place
- a safety net for a non-default install location the automatic glob wouldn't find - but are no
longer the primary path.

## Fourteenth slice: a real installer

Ports `EffectPalette/packaging/`'s own PyInstaller + Inno Setup shape (`packaging/pyinstaller/
FXPalette.spec`, `packaging/inno/FXPalette.iss`, `packaging/build_release.ps1`), with one
structural difference the CEP version never had to deal with: the UXP plugin and the Python
companion are no longer one bundle in one folder. The plugin ships as a `.ccx` (built once via the
UXP Developer Tool's own "Package" menu - no signing required, confirmed against Adobe's own
distribution docs, and not something scriptable from here since UDT has no CLI for it) and gets
installed silently through Adobe's `UnifiedPluginInstallerAgent.exe /install` - no Marketplace
review, no per-user click-through beyond the one install-time permission confirmation. The
companion still installs and runs exactly like the CEP product's own `.exe` always did (same
single-instance mutex name, so `AppMutex`/`CloseApplications` behave identically).

Host-tested: PyInstaller output launched standalone (dev-mode companion stopped first), reconnected
to the already-loaded plugin, auto-loaded the preset catalog with zero interaction - confirming the
thirteenth slice's fixes hold in a frozen build too, not just running from source.

One real bug, caught by the user's own first real install attempt: the installer reported "Creative
Cloud Desktop not found" despite it being genuinely installed. Root cause - Inno Setup builds
32-bit by default, so `{commoncf}` (Common Files) silently resolved to the WOW64-redirected
`Program Files (x86)\Common Files`, not the real 64-bit path Creative Cloud Desktop actually
installs UPIA into (host-confirmed: the 32-bit path had no Adobe folder at all, the real one
existed exactly where expected). Fixed with `ArchitecturesAllowed=x64compatible`/
`ArchitecturesInstallIn64BitMode=x64compatible` in `[Setup]`. Host-confirmed working end to end
after the fix: full silent install, plugin registered, companion running, no UDT or Developer Mode
involved at any point - the original "proof of concept" framing no longer applies.

## Fifteenth slice: Motion Tracker, ported from pFX_moTracker

Re-hosts a separate, previously-CEP extension (`pFX-Tracker`, point tracking → stabilize/follow,
writing keyframes onto a Transform effect) inside FX.palette's own Qt companion, per the plan at
`idempotent-weaving-pebble.md`. The UXP-native port attempt (`pFX-Tracker_UXP`) was unusably slow
for the interactive scrub/click/drag UI (UXP's `<canvas>` has no real `drawImage`, forcing DOM
reflows on every frame); the tracking algorithm and Premiere-side operations were host-agnostic
already, so only the interactive UI needed rebuilding, natively, in Qt (`QGraphicsView` +
`QGraphicsPixmapItem`, a single `setPixmap()` blit per scrub step). New package
`companion/motracker/` (`tracker_engine.py` ported verbatim, `extraction.py` rebuilt on `QProcess`
instead of asyncio, `qt_tracker_window.py` new); two new `index.js` actions
(`motracker.getClipInfo`, `motracker.applyTrack`) plus adapter methods
(`get_clip_info`/`begin_apply_track`); a new `TOOL_WINDOWS` palette entry
(`show_motion_tracker`), Qt-only (no Tk fallback). All six staged steps host-tested working.

**Apply Track ("follow" mode)'s coordinate mapping — three wrong guesses before the real fix.**
Stabilize (target === the tracked footage) worked immediately: found via the real, working CEP
`host.jsx` that Position is normalised over the target clip's own native frame on this Premiere
build (not always plain pixels, despite Effect Controls always displaying pixel-looking numbers),
mirrored using the Anchor Point exactly as `host.jsx` does. Follow (target is an unrelated,
differently-sized object, e.g. a small icon layered over footage) kept moving the object
imperceptibly or overflowing to `32767,0`, through three wrong hypotheses about what pixel base
Follow's delta should scale against:
1. Sequence-normalised (`1/seqW`) — numerically plausible, visually no movement. Ruled out only
   after adding a *full-range* min/max diagnostic (203 frames, not just 6-7 sampled ones — the
   earlier per-sample diagnostic could miss mid-clip motion entirely).
2. Confirmed via real Effect Controls values (`189.0 → 194.6` across the clip) that
   `189.0 = 0.525 × 360` exactly — the scale base is the *followed object's own* native pixel width
   (~360 for that PNG), not the sequence's (1920). This ruled out guess 1 with hard evidence, not
   reasoning alone.
3. Tried reading that native size *from Premiere itself*, via the target clip's own built-in
   `"AE.ADBE Motion"` effect's Anchor Point (hypothesis: unlike our own Transform, Motion's Anchor
   reads as real, non-normalised pixels). Host-tested and disproven: the lookup silently returned
   `null` (fell back to `seqW`/`seqH`, reproducing guess 1's exact wrong scale) — Motion's own
   Anchor Point is *also* normalised at the scripting-API level, despite showing pixel-looking
   values in Effect Controls, same pattern as Position.

**Real fix**: stopped guessing which Premiere-side parameter reveals real pixels at all. New action
`motracker.getFollowTargetMediaPath` resolves the followed clip and returns its media file path;
companion-side `get_follow_target_native_size()` reads the *actual* file directly via OpenCV
(`cv2.imread`, falling back to `cv2.VideoCapture` for video media) — unambiguous, independently
verifiable, no Premiere-side reference-frame ambiguity at all. `qt_tracker_window.py`'s
`_on_apply_clicked` fetches this only for follow mode and threads it into the `applyTrack` request
as `followedObjectW`/`followedObjectH`; `applyTrack` uses it directly (falling back to the sequence
size only if the companion couldn't read it). Host-confirmed working: logged diagnostics showed
`followNativeW/H: 360/360` (the real PNG's own pixel size, not the previous fallback's 1920/1080),
and Position moved from `0.5` to `~0.73` across the tracked range — a real, visible follow, where
every earlier attempt had been imperceptible or overflowed. User-confirmed: "OPA! AGORA SIM
FUNCIONOU!"

The temporary `motrackerDebug` diagnostics (per-frame readback, full-range min/max, reset-state
tracking) added during this debugging arc have been removed from `applyTrack`'s response now that
the mapping is confirmed correct.

**Still open**: `GEOMETRY2_USE_COMP_SHUTTER_INDEX = 9` (the no-display-name motion-blur toggle
param) has not yet been independently re-verified against this Premiere build — it was carried over
from the source project's own comment, not re-derived here. Packaging (`cv2` + bundled `ffmpeg.exe`
inside a frozen PyInstaller build, and the resulting installer size) has not yet been re-validated
since this feature was added.

### Sixteenth slice: nesting the apply target, tried and reverted (2026-08-27)

The user asked for a structural change: whatever is selected in the Timeline goes into a Nest, and
the tracking data is applied to that Nest, identically for Stabilize and Seguir Rastro. Asked where
the keyframes should land, they chose the outer nest clip. Asked whether to keep the fifteenth
slice's nest-and-normalize step (resize the Nest to the clip's native pixel resolution, reset the
inner clip's intrinsic Motion, carry the original Motion onto the outer clip), **this session
recommended dropping it as a simplification, and that recommendation was wrong** - it is the load-
bearing part of the whole design. Everything below followed from that mistake.

**Why the native-resolution resize is load-bearing.** The stable CEP `pFX-Tracker`'s `mt_applyTrack`
(read as a behaviour reference) documents the property the whole feature rests on: `AE.ADBE
Geometry2` exposes two *different* normalised spaces - Position normalised over the **sequence**
frame, Anchor Point normalised over the **clip's own** frame. The tracker measures motion in the
clip's own extracted pixels, so Anchor is the only property whose space matches the data. Write
there, never touch Position, and mixed aspect ratios need no sequence math at all:
`coordScale = 1/extractedW, 1/extractedH` is simply "fraction of the clip's own frame". CEP's step 2
is explicitly titled "Add Transform DIRECTLY to the clip - NO nesting", and lists avoiding nest
audio/duration/offset bugs and natural mixed-aspect handling as the reasons.

A sequence-sized Nest replaces "the clip's own frame" (1440x2560 here) with the sequence frame
(1920x1080), breaking that match. The fifteenth slice's native-resolution resize existed precisely
to restore it. Removing the resize while keeping the Nest left the coordinate space silently wrong.

**The wrong-hypothesis chain that followed**, recorded so it is not repeated:

1. First retest came back skewed in both modes. Diagnosed as the separate X/Y factors
   (`1/exW`, `1/exH`) encoding "the clip fills the frame" per-axis, and replaced with a single
   uniform `displayScale` derived from the tracked clip's Motion Scale. The Scale-to-percentage
   mapping was genuinely confirmed against the host (user read `42,2` in Effect Controls where an
   earlier API probe read `42.1875`; `42.1875 = 1080/2560`, a 2560-tall source fitted to a 1080-tall
   frame). But the premise was wrong: those factors were never "fill the frame", they were
   "fraction of the clip's own frame", which is correct for Anchor - under the Nest they had merely
   stopped describing the target.
2. Next retest: motion correct, framing off. Diagnostics ruled out a spurious offset in what gets
   written - `anchorStartRaw` and `appliedFirst` both `[0.5, 0.5]`, `expectedPxRangeX/Y` 337.7/296.0
   px matching an offline prediction, `clipInPointSeconds: 0`, and the user independently read
   `960, 540` in Effect Controls at the seed frame.
3. Then misread the user's "the adjustment is manual" as a complaint about full-lock semantics, and
   replaced Stabilize's applied series with a de-shake residual `P_raw - P_smooth`. Reverted: the
   user had said the motion was correct, so this answered a question they had not asked; and it was
   inert anyway, because `one_euro_zero_phase` is velocity-adaptive
   (`cutoff = mincutoff + beta*|v|`) and this fixture's ~1200 px/s drives the cutoff to ~24 Hz, so
   the residual collapses to nearly zero ("o negócio mal se mexe"). One-Euro is the wrong tool for a
   stabilisation reference path; a fixed-cutoff window average would be needed if de-shake is ever
   wanted as a real mode.

**Resolution.** The user chose to drop nesting entirely - "era a minha ideia original, só pensei na
Nest pois talvez pudesse dar menos trabalho". `applyTrack` no longer nests; `motrackerNestTarget`
and the whole nest-and-normalize path are gone (`motrackerPerformNest` stays, it still backs
`timeline.createNest`). The coordinate math and keyframe-value math are restored to the proven form.
`displayScale`/`trackedScale` and `getClipInfo`'s `motionScale` read are removed with them. Follow
is restored to its last host-confirmed configuration (Position, scaled by the followed object's own
native size via `motracker.getFollowTargetMediaPath` + OpenCV) rather than redesigned, since the
user deferred that decision until Stabilize is confirmed.

Kept from this pass: the `motracker.testNest` / `test_nest` / `[TESTE]` scaffolding is removed for
good, the apply target is always the current Timeline selection in both modes (the user's call,
replacing CEP's dual loaded-clip/selection resolution), and the `motrackerDebug` block now also
reports `anchorStartRaw`, `appliedFirst`, `appliedRangeX/Y`, `expectedPxRangeX/Y` and
`clipInPointSeconds`, which is what made step 2 above conclusive.

**Isolated by the user's own controlled experiment (2026-08-27): VFR source footage is the culprit.**
This supersedes the session's earlier zoom explanation, which was wrong - the user reproduced the
same track in After Effects with a single point and it locked for the whole clip, so a one-point
tracker's translation-only limit cannot be what breaks it here.

The user then tested a clip with **30 fps in a 60 fps sequence** and **3828x2160 in a 1080x1920
sequence**, with **no VFR**: tracking was perfect. By elimination:

- **Resolution / aspect ratio is not a factor.** A 4K landscape source in a vertical HD sequence
  tracked perfectly, which independently confirms the Anchor Point math (`1/extractedW`,
  `1/extractedH`, the clip's own frame) and closes that line of investigation for good.
- **Frame-rate mismatch alone is not a factor.** 30 fps in a 60 fps sequence survived a 2x
  mismatch. The failing clip's 59.97-in-60 is a 0.05% difference - 1.25 ms over 2.5 s.
- **VFR is the remaining variable, and the one that breaks it.**

**Mechanism**, and it is one of this project's own features working against itself: `extraction.py`
collects each frame's real `pts_time` via ffmpeg `showinfo`, and `applyTrack` places keyframes at
those times. Premiere, however, **conforms VFR footage to a constant rate on import** - clip frame
*i* is displayed at `i / conformed_rate` regardless of its real pts. So the plugin times keyframes
by real file time while Premiere lays the frames on a uniform grid. On CFR footage the two agree and
everything works; on VFR they diverge progressively, and a divergence that grows across the clip is
exactly "follows the motion but never locks, and one constant reframe only fixes one frame".

Two aggravating details in the failing clip: `parse_source_meta` reported `vfrSuspect=False` (its
fps/tbr heuristic missed it - the same blind spot recorded earlier in this slice), and
`frameTimestamps` are used whenever their count matches the extracted frames, without consulting
`vfrSuspect` at all.

**CONFIRMED IN THE HOST (2026-08-27): VFR was the culprit, and the user was right.** They asserted
it three times against this session's pushback, which was wrong twice over - first dismissed on a
flawed argument (only the *final* keyframe offset was compared, 3.405778 s vs 3.400 s, and called
negligible; accumulated deviation is not the same as evenly distributed error), then replaced with a
camera-zoom explanation that the user's own After Effects result disproved.

Measured on the failing source with ffmpeg `showinfo`: inter-frame intervals are 16.667 ms (60 fps
exactly) throughout, except **one interval of 22.223 ms** - 33% long - between frames 140 and 160.
The cumulative deviation from a uniform grid sits at 0.21 ms up to frame 140, then steps to 5.78 ms
and stays there. So the file is effectively CFR with a single hiccup, not continuously variable,
which is why the earlier `vfrSuspect` heuristic missed it and why the end-to-end offset looked tiny.

Transcoding the same clip to true CFR 60 (`-vf fps=60`, verified: worst deviation from the uniform
grid 0.000 ms) and re-running the identical workflow **locked perfectly**. Same track data, same
code path, only the source timing changed.

**Remaining artifact, also diagnosed and already fixable:** on the CFR clip the user noticed the
keyframes stop being evenly spaced at one point, with a visible deviation there. No keyframes are
missing - 205 written for frames 0..204. It is phase slip: `lastOffsetSeconds` 3.405778 over 204
intervals is 16.695 ms per keyframe against the sequence's 16.667 ms, so keyframes drift 0.028 ms
per frame, 5.78 ms (0.35 frame) across the clip. That is under one frame in total, but when the
drift crosses half a frame the rounding flips once: one sequence frame receives two keyframes and
the next receives none. Exactly one such crossing, hence exactly one gap.

The fix is the timing toggle added this session: unchecking "Usar timestamps reais do arquivo" uses
`frameCount / durationSec` (60.000000 here) instead of the container's sniffed 59.97, placing every
keyframe at `i/60`, exactly on the sequence's own frame boundaries. Whether this should become the
default - and whether extraction should resample to a constant rate so VFR sources are conformed the
same way Premiere conforms them - is the next decision, and needs one more host run to confirm the
gap disappears.

**Host-confirmed (2026-08-27): unchecking the toggle removed the gap.** Uniform timing at
`frameCount / durationSec` is therefore now the **default**, and the checkbox is relabelled as a
diagnostic. On the CFR clip this produces a clean lock with evenly spaced keyframes throughout.

**What is settled and what is not.** Settled: the coordinate math (Anchor Point in the clip's own
frame), the tracker's accuracy (sub-2 px against the frame-0 patch over 205 frames), the write path
(Effect Controls matches the computed values to within the de-shake residual), and keyframe timing.
Not settled: **a VFR source still has to be transcoded to CFR by hand** before it tracks correctly -
the user did that manually to get the working result. Making that automatic means conforming the
source during extraction (`-vf fps=R`) so the plugin's frame *i* is the same frame Premiere shows at
*i/R*, instead of relying on the file's own irregular timing. The open question there is which R:
`extraction.py` already records that a `,fps={fps}` resample to the **sequence** rate was removed for
causing a real bug, so the rate would have to be the clip's own conformed rate, and how to obtain
that before extraction has not been established.

**Shipped instead of the auto-conform (2026-08-27, user's call): detect and warn.** Weighing it
honestly, automating the choice between the two timing models was worth little - on effectively-CFR
footage both agree, and on genuinely irregular footage neither works. Automating the *conform*
(probe the timestamps, then extract with `-vf fps=R` when the source is irregular) is the real fix,
but it changes `extraction.py`, which already has a recorded bug caused by an fps filter, it depends
on ffmpeg conforming identically to Premiere - unverified - and validating it costs host cycles that
this session showed to be expensive. The user chose the warning: "acho melhor por um aviso do que
inventar moda."

`extraction.py` gained `analyse_frame_timing`, which flags any inter-frame interval departing more
than 20% from the clip's own median. The signal is a single irregular interval, not accumulated
drift: the failing clip was 16.667 ms throughout with one interval of 22.2 ms, while its total
deviation stayed under half a frame and would not have caught it. Comparing against the clip's own
median rather than a nominal rate avoids trusting the container, which advertised 59.97 on a file
whose real frames sit at 60.000. Validated offline against both of this session's real files: the
original flags (1 of 240 intervals, worst +33.3%), the transcoded control does not (0 of 240).

Host-confirmed: the warning appears on the original VFR clip and stays absent on the transcoded
CFR control. The tracker window shows it as an amber status line naming the count and telling the user to convert
the clip, with the mechanism in the tooltip, and logs the same detail. This does not fix VFR - it
converts the worst failure mode, a plausible-looking wrong result with no error anywhere, into
something actionable. The auto-conform stays available as a later decision, now with its cost
understood rather than estimated.

**Superseded proposal, kept for the reasoning:** have ffmpeg emit exactly the frames Premiere
shows - resample to a constant rate during extraction and go back to frame-index timing, so frame
*i* is at `i/rate` on both sides by construction rather than by compensation. Two things must be
settled first: (1) which rate - it has to be the conformed rate Premiere assigned the clip, not the
sequence rate nor the file's average, and whether the UXP API exposes a clip/projectItem frame rate
needs checking in the host; (2) this slice already records that a `,fps={fps}` resample was removed
for causing a real bug, and that real timestamps were adopted specifically to fix VFR drift, so that
earlier decision must be re-read before being reversed.

**Superseded (2026-08-27): the single-point zoom explanation.** Two Program Monitor screenshots (first and last frame, Stabilize
applied, clip at Motion Scale 100 so no scale conversion is involved at all) settled it:

- Distances between fixed features on the subject grew by a consistent factor across both axes
  (X->B 177->205 px, Y->A 187->218 px, both ~1.16). The camera moved ~16% closer during the clip.
- The tracked feature itself moved ~13 screenshot px between the two frames, against ~45 px for one
  nearby button and ~73 px for another. It is 3-6x more stable than everything around it, i.e. the
  correction is being applied and is roughly 98% effective (the raw track spans ~800 px).

A one-point tracker yields translation only. It can pin exactly one point; everything else scales
around it, and no constant reframe can compensate a scale that changes over time. That is exactly
the reported "só no frame que eu faço o ajuste fica correto", and it explains why the symptom
survived every geometry change tried this session (nested/not nested, Motion Scale 100 or 42.1875)
and why the CEP tool behaves identically - it shares the algorithm, not the porting.

Residual on the tracked point itself is partly self-inflicted: Stabilize pins the *smoothed* track,
so `P_raw - P_smooth` remains. Suavização 0 pins the raw track exactly and removes that component.

Real compensation for zoom/rotation needs two or more tracked points (their separation gives scale,
their angle gives rotation), writing Scale and Rotation keyframes alongside position. That is a new
feature, not a fix, and is not attempted here.

**Still to verify in the host:** that Stabilize is correct again without the Nest, and then the
Follow decision (CEP writes the Anchor for both modes with the sign flipped, which needs no object
size lookup at all but scales by the object's own frame; this port uses Position plus the real
object size, which the user previously confirmed working).

### Seventeenth slice: Seguir Rastro's coordinate conversion (2026-08-27)

Retesting Follow after the sixteenth slice's revert - it had been restored to its last confirmed
configuration but never re-run - showed the object moving with roughly the right shape but not
actually following. The diagnostics answered it directly rather than by hypothesis, which is the
difference from the previous slice: the object is a 1000x1500 PNG (recovered from `followScaleX/Y`),
Position moved `1.0669` of its frame in X where `0.80021` was needed, and `0.19745` in Y where
`0.46803` was needed. X ran 1.33x too far, Y 2.37x too short, a ratio of 3.16 between the axes -
distorted, not merely mis-scaled, which is exactly "tenta fazer o mesmo movimento, porém não chega a
seguir".

Same defect class as the previous slice, in the other mode. `followScaleX = (seqW / exW) / objW` and
its Y twin encode "the tracked footage fills the sequence frame" as a separate factor per axis, so
whenever the footage's aspect differs from the sequence's the two disagree. The tracked clip here is
1440x2560 in a 1920x1080 sequence, giving 1.333 in X against 0.422 in Y.

The conversion needs three measured numbers instead:

1. **The tracked clip's own Motion Scale**, read by `getClipInfo` before anything touches it - how
   large the footage actually appears in the sequence. This is the `displayScale` concept the
   sixteenth slice built and then discarded; the concept was right and was applied to the wrong
   mode. Stabilize genuinely does not need it (the Anchor Point is normalised over the very frame
   the tracker measures in, so no conversion exists to get wrong); Follow does, because it converts
   between two different clips' spaces.
2. **The object's real pixel size**, from the companion's OpenCV read of its media file - unchanged.
3. **The object's own Motion Scale**, newly read in `applyTrack`. This Transform renders BEFORE the
   object's intrinsic Motion, so a Position change moves content inside the object's frame and
   Motion then scales the result on its way to the sequence.

`f = trackedDelta x trackedScale / (objectSize x objectScale)`. Verified offline against the host's
own measured numbers before applying: reproduces `0.80021` and `0.46803` exactly. Host-confirmed by
the user immediately afterwards - the object now follows correctly. Both scales fall back to 100
when unreadable, which can only make the follow the wrong size, never distorted.

Item 3 was host-confirmed the same day with a deliberately scaled object, so the reading that this
Transform's Position displacement is multiplied by the object's own Motion Scale is verified rather
than deduced. `MOTRACKER_GEOMETRY2_USE_COMP_SHUTTER_INDEX = 9` was confirmed in the same pass,
closing an item this repository had carried unverified since the fifteenth slice. `motrackerDebug` reports `trackedScalePct`, `targetScalePct`
and `targetScaleReadable`, so a follow with the right shape at the wrong distance points straight at
it.

Also in this slice: the keyframe-timing checkbox added during the VFR investigation was removed once
uniform timing was confirmed as the default - it had served its purpose as an A/B and would only
invite regressions. Uniform timing at `frameCount / durationSec` is now unconditional.

### Eighteenth slice: the Motion Tracker leaves this repository (2026-08-27)

The user rebuilt the Motion Tracker as its own Premiere UXP panel plugin, reaching satisfactory
performance and more features than the version here had, and removed it from FX.palette. Notably it
reverses this project's own fifteenth-slice conclusion that a UXP-hosted interactive tracker was
unusably slow - that finding was real when measured, and is now superseded by the user's own working
panel rather than by argument.

Removed from the companion: `companion/motracker/` (engine, ffmpeg extraction, Qt window), the
`TOOL_WINDOWS` palette entry with its `tool_window` dispatch and `show_motion_tracker`, and the
adapter's `get_clip_info` / `get_follow_target_native_size` / `begin_apply_track`. Removed from the
plugin: the three `motracker.*` actions, their handlers and every tracker-only helper. Dropped from
`requirements.txt`: `opencv-contrib-python` and `numpy`, verified used nowhere else in the companion;
the vendored ~200 MB `ffmpeg.exe` and its `.gitignore` entry went too.

`performNest` (formerly `motrackerPerformNest`) stays and was renamed, because it backs
`timeline.createNest` and only ever carried that prefix from having been extracted during the
tracker's work. Its wrapper/helper split is kept as-is rather than re-merged: with one caller it is
now redundant, but merging it changes working code for no benefit, and the comment explaining why
nest creation is not re-derived from the API docs is worth keeping attached to it.

Verified after removal: `npm run validate` passes, every companion module compiles, no reference to
the tracker survives in code, and the companion starts cleanly with all catalogs loading and the
hotkey registering.

Everything above this section stays as written. Slices fifteen through seventeen record how the
tracker was built, what it cost, and the three wrong hypotheses chased along the way; that history
is the point of an append-only log, and the VFR and coordinate-space findings in particular apply to
anything that writes keyframes against extracted frames - including the plugin this feature moved
into.

### Nineteenth slice: the transport dropped every error it was told about (2026-09-04)

The user reported an animated preset failing to apply onto a clip that already had a keyframed
effect: the palette showed "applying" for a few seconds, then nothing landed. The beta telemetry
(`Documents/FX.palette_Beta_Report/telemetry_events.jsonl`) held the shape of the answer already -
32 `apply_failed` entries across four sessions, **every one of them at ~5.04s**, none anywhere near
the 30s or 45s the code believed it was allowing.

Two independent defects, both on the companion/transport seam rather than in Premiere:

1. **Every plugin-side error was silently dropped.** `execution-adapter.js`'s `failure()` never
   included `requestId`, while `execute()`'s success branch always did. The companion correlates
   responses by `requestId` alone (`if request_id in self._pending`), so a failure response was an
   unknown frame: discarded, request left pending, and 5s later relabelled `error_timeout` with the
   plugin's actual message gone. Every error this project is careful to fail closed with - the
   arbitrary-parameter guard, `"Select at least one video clip."`, the clip-too-short guard, a
   rejected transaction - reached the user as the same anonymous timeout. Fixed by echoing
   `requestId` on all five failure paths; `tests/execution-adapter.test.js` now asserts it for
   EXECUTION_FAILED, MISSING_HANDLER, UNSUPPORTED_ACTION and MISSING_ACTION_TYPE, and asserts the
   deliberate `null` for INVALID_ACTION, where there is no action object to read one from.

   Verified off-host, since this is protocol logic and needs no Premiere: the real
   `execution-adapter.js` run under node, its output fed into the companion's real routing and
   `_resolve_pending`. Pre-fix `requestId: undefined` -> dropped; post-fix -> `error_execution_failed`
   carrying the plugin's own message, immediately.

2. **The per-effect timeouts had never once been in effect.** `poll_status` applied a flat
   `REQUEST_TIMEOUT_SECONDS = 5.0` to every request, and `_poll_apply_status` acts on that terminal
   status *before* it consults `apply_status_timeout_ms`. So `PRESET_APPLY_STATUS_TIMEOUT_MS` (30s,
   added by the tenth slice above) and `GENERIC_ITEM_APPLY_STATUS_TIMEOUT_MS` (45s, sixth slice)
   were dead from the day they were written - `git log -L` confirms the 5s cap predates both and was
   never touched by either, and the uniformly ~5.04s telemetry confirms it empirically. The
   deadlines now live once, in `uxp_execution_adapter.EFFECT_TIMEOUT_SECONDS`, enforced where they
   are actually checked; `apply_status_timeout_ms` reads that table and adds a grace margin, since
   its only remaining job is to backstop an adapter that never transitions at all (the stubs, whose
   `poll_status` always returns `None`).

   This also means the tenth slice's "fixed with a 30s timeout" conclusion was wrong about its own
   mechanism. Whatever made that preset appear to start working, it was not that constant.

**The reported bug itself was not reproduced.** Six host applies this session all succeeded in
64-82ms, including the exact scenario as described - `Slide OUT DOWN` onto a clip with
`componentCountBefore=4`, already carrying the keyframed Transform from a previous apply. That
falsifies the pre-existing-keyframe hypothesis (and the intrinsic-collision theory built on it:
`createSetTimeVaryingAction(true)` plus `createAddKeyframeAction` over a parameter that already has
keys is not, on this evidence, rejected). The original trigger is still unidentified. What changed
is that it can no longer fail mutely: `_resolve_pending` now writes an `adapter_response` telemetry
event per response - ok, error code and message, true `elapsed_ms`, and whether it beat the
deadline - so the next occurrence names its own cause instead of leaving another ~5.04s entry.

Worth recording as method: the deciding evidence here was the user's own beta telemetry, read
before touching any code. The uniform 5.04s across 32 failures spanning weeks is what ruled out
"the preset is slow" and pointed at a fixed cap, and it is also what proves the per-effect
constants never fired. The instrumentation that was missing - the plugin's actual verdict - had
been going to `print()` only, invisible in the installed windowed build.

## Parity assessment (2026-08-24)

Requested by the user after five slices: how close is this to the stable CEP product today, and
how should future UXP releases be watched for capabilities that close the remaining gaps.

### Per capability, against `UXP_HANDOFF.md`'s own list of CEP's working capabilities

| Capability | Status | Notes |
| --- | --- | --- |
| Video/audio effect apply | ✅ Full parity, host-tested | Identity-verified both directions (video: same-index candidate + post-insert display-name check; audio: exact `displayName` match, no guessing needed); catalog (the listing itself, not just apply-time resolution) is UXP-native since the ninth slice |
| Preset apply | ✅ Full parity, host-tested | Easing reconstruction on by default; catalog is located and loaded fully automatically (`fullAccess` + companion-side glob, no picker at all - see thirteenth slice), no ongoing CEP dependency. Curve fidelity is an approximation with two real fixes and one known-remaining gap - see tenth slice |
| Video transition apply | ✅ Functional, host-tested | Label readability is permanently constrained - `VideoTransition` exposes no properties at all, so no name can ever be verified the way effects are |
| **Audio** transition apply | ❌ Confirmed platform gap | `TransitionFactory` and `AudioClipTrackItem` (full class references checked) have no transition-related method at all - not unwired, not possible today |
| Insert existing Project item | ✅ Full parity, host-tested | Track auto-targets the current selection, avoids an occupied track, stretches Adjustment-Layer-like items to match a video selection - all three ported from `host.jsx`; catalog is UXP-native since the ninth slice, no template-project guard (scans whatever's currently open, matching CEP) |
| Insert favorite item (media) | ✅ Full parity, host-tested | Import-then-dedup mirrors host.jsx's `_importFavoriteProjectItem` exactly, using documented `Project.importFiles`; catalog scanning is now UXP-native too (see eighth slice below), replacing the CEP worker dependency this capability quietly still had |
| Insert favorite item (sequence) | ⚠️ Built, not yet host-tested; intentionally not full parity | Imports and inserts as a **nested** clip, not host.jsx's flatten-then-fallback-to-nest behavior - flattening (`_insertSequenceContentsAtPlayhead`) is QE-DOM/dynamic-track-creation-gated, the same confirmed platform wall as the track-creation row below. Accepted by the user, who doesn't favorite whole sequences in practice but wants the path to exist |
| Create generic item: Adjustment Layer, Bars and Tone, Black Video, Transparent Video | ✅ Full parity, host-tested | Not built fresh on either platform - CEP has no creation API for these either (for Adjustment Layer) or only reaches them via undocumented QE DOM (the other three); both work around it the same way, importing a pre-built `.prproj` template. `tools/template_generator/` (a throwaway CEP dev panel) built the three new templates; sixth-slice section above has the details |
| Create generic item: Color Matte, Universal Counting Leader | ❌ Confirmed platform gap (Color Matte: by product decision) | Color Matte: neither platform can set its color after creation, so a template built once could never be recolored per use - excluded by the user's own call, not attempted. Universal Counting Leader: CEP only reaches it via undocumented QE DOM and no template was built for it - not attempted |
| Nest, auto-routed native/API | ✅ Full parity, host-tested | The routing signal itself (multi-track-audio detection) had gone silently stale under this migration and was restored via a new `diagnostics.read` field, not something UXP was missing |
| Nest: default codename, bin placement | ✅ Full parity, host-tested | `FXN-NNN` scheme and filing into a project bin (`FolderItem.createBinAction`/`createMoveItemAction`) both ported |
| Create a new Timeline track when none is free | ✅ Full parity, host-tested (via native automation, not a UXP API) | UXP itself still has no track-creation API (exhaustive check: every plausibly relevant class plus the full changelog through 26.3.0 confirms this). Closed anyway by checking availability before inserting (`timeline.checkTrackAvailability`) and, when needed, driving Premiere's own "Add Tracks..." dialog via `cmd.sequence.addtracks` - see twelfth slice |
| Set a Timeline clip's Label | ✅ Full parity, host-tested | Neither CEP nor UXP has a `TrackItem` label API - CEP's own real mechanism was never DOM at all, it's `companion/app.py` resolving `cmd.edit.label.N`'s bound shortcut out of the user's `.kys` profile and synthesizing the keystroke (`send_native_shortcut`/`find_premiere_command_shortcut`), independent of CEP vs UXP. That code was already ported verbatim; the only missing piece was `scripts/configure_premiere_shortcuts.ps1` (ported from the CEP installer's `configure_premiere.ps1`) to guarantee the 16 `cmd.edit.label.N` + `cmd.edit.labelgroup` shortcuts exist in the user's profile - on this machine they already did, from the earlier CEP install |
| Select a Label group | ✅ Full parity, host-tested | Same mechanism (`cmd.edit.labelgroup`), same fix |
| Set a **Project item's** color label | — Removed by product decision | `projectItems.setColorLabel` worked and was tested, but the user confirmed it isn't a real workflow they use - removed from `SUPPORTED_ACTIONS`/`ACTION_HANDLERS` rather than kept as unused surface area. `setSelectedProjectItemLabel` itself stays as a private helper, called directly (not through the shared allowlist) only so the 0.16.0 headless-command proof keeps working |
| Global shortcuts / Stream Deck F13-F24 bindings | — Not a UXP question | Native Win32 `RegisterHotKey` in the Python companion, unaffected by CEP vs UXP either way |
| Aliases, recent actions, actionable diagnostics | — Not a UXP question | Companion-side product features (search index, history, settings-panel health checks), independent of the execution backend |
| `reconstructEasing` as a real user-facing setting | ✅ Full parity, host-tested | Checkbox in the Qt Settings dialog's General tab, persisted in settings.json, confirmed to actually reach the execution path (see eleventh slice) |

### Reading the gaps as a group

Every ❌ above traces back to exactly one of three walls, not five different problems:

1. **The legacy QE DOM** (`qe.project.*`) - CEP's own escape hatch for creating tracks and Universal
   Counting Leader, deliberately out of scope for this project from the start (`TECHNICAL_PLAN.md`'s
   own "Scope and invariants"). Audio transitions are not a QE-DOM case - it is missing from *both*
   the documented and the QE surface. (Timeline-clip Label/Label group looked like a QE-DOM case too,
   but turned out not to be a DOM question at all - see the seventh slice above.)
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

## Slop audit: dead code, reliability, performance (2026-09-04)

Requested by the user as a "slop audit": find unusable tests, performance and reliability issues,
fix them, and fold the remaining branches into `main`. A full copy of the repository (working tree
plus `.git`, plus a `git bundle --all`) was taken first at
`Projetos Claude/EffectPalette_UXP_backup_2026-09-04`, and the pre-audit commit is tagged
`backup/pre-slop-audit-2026-09-04`.

**Evidence level for everything below: off-host only.** `npm run validate` (manifest checks, the two
plugin test files, the companion unittest suite) passes, and an offscreen Qt smoke run constructs the
palette, debug window, tray, hotkey listener, settings center and hotkey editor without error. No
Premiere host test was run in this pass; nothing here changes a `CAPABILITY_MATRIX.md` row.

### Findings and fixes

| Area | Finding | Fix |
| --- | --- | --- |
| `companion/app.py` loader | `_load_effects` returned the fallback list unless the **CEP** worker's `premiere_effects.json` existed, and in fallback mode never read presets/project items/favorites either. A fresh install without the old CEP product could never leave fallback, whatever the UXP plugin reported. Masked on the dev machine by the legacy CEP data folder. | Loader reads only `companion/data/uxp_*.json` (effects + transitions, presets, project items, favorites); `source` is `"uxp"` or `"fallback"`. Covered by `test_app.py`. |
| `companion/app.py` native Nest | `arm_native_nest_watch` wrote `premiere_cmd.json` and `dispatch_when_native_nest_watch_ready` polled it for `"done"` - written by the CEP worker, which no longer exists. Every native Nest therefore waited the full 2 s deadline and logged `native_nest_watch_arm_timeout`. | Watch removed; the keystroke is sent 80 ms after Premiere is focused, as before minus the wait. |
| `companion/app.py` tkinter | Two parallel UIs (the user's own STATUS.md called dropping tk the largest available simplification). The Qt class even reached into the tk class for `CATEGORY_TYPE_FILTERS` and `_build_result_row_model`. | tk UI, `PaletteResultsController`, `DebugWindow`, tk helpers/dataclasses/constants and `EFFECT_PALETTE_UI` removed; shared members hoisted to module level. pyflakes clean; vulture used to find the leftovers. 8818 -> ~5300 lines. |
| `companion/app.py` debug window | `QtDebugWindow` tailed the CEP `worker.log` and sent `exportEffects`/`diagnose`/`clearBridge` to the bridge file. | Shows the companion's own `beta_report` log, a live `collect_diagnostics()` dump, and re-requests the catalogs through the adapter's new `refresh_catalogs()` (also behind the palette's refresh button, which used to write a bridge command). |
| `transport.js` | (1) The close listener referenced the module-level `socket`, so a superseded socket's late `close` nulled the live one and scheduled a second reconnect. (2) `stop()` closed the socket, whose close event then scheduled a reconnect - `destroy()` was not final. (3) A handler that finished after its socket died sent its result down whichever socket was current. | Listeners are bound to their own socket and bail if it is no longer the live one; `stopped` flag; results go back on the socket the request arrived on. `tests/transport.test.js` drives all three against a fake `WebSocket`. `readyState` is consulted only when the runtime exposes it. |
| `uxp_execution_adapter.py` lifecycle | `_on_new_connection` closed the previous socket *after* installing the new one; the old socket's `disconnected` then ran `_on_disconnected`, wiping `_client`/`_authenticated` for the new connection (a UDT reload reconnects exactly like this). | Signals carry their socket; a superseded socket's events are ignored. Per-connection state is initialised in `__init__` instead of via `getattr` defaults. Covered by `test_reconnect_supersedes_without_wiping_the_live_client`. |
| `uxp_execution_adapter.py` catalogs | Favorites and project items are re-requested every 5 s and were rewritten to disk on every response even when identical (favorites also carried an `exported_at` timestamp guaranteeing a change). Each rewrite fired watchdog, which rebuilt the whole search index. A failed `catalog.videoEffects.read` was never retried, so a transient error at connect time left video effects unresolvable all session. | `_write_catalog` skips unchanged content; `exported_at` dropped; one 3 s retry for the effect catalog. |
| `uxp_execution_adapter.py` bookkeeping | `_prune_pending` only dropped terminal entries, so a request nobody polled stayed in memory for the session. `_blocking_request` sat out its full timeout if the client dropped mid-wait. | Pending entries are pruned once deadline + retention has passed; the nested loop also quits on `disconnected`. |
| `execution-adapter.js` allowlist | `timeline.createSubsequence` and `timeline.insertGenericItem` were in the allowlist and `ACTION_HANDLERS` but the companion never sends them (it routes Nest through `createNest` and generic items through `insertProjectItem`). | Both removed with their handlers (~300 lines); the adapter test asserts they stay rejected. Allowlist is 15. `index.js` scanned for now-unreferenced functions: none. |
| `beta_report.py` | Every event (`write_event` runs per hotkey press, per adapter response, per poll) did `mkdir` + `exists()` + two `stat()`s before appending. The report bundled CEP files that no longer exist. | mkdir once per process; single stat; report bundles the `uxp_*.json` catalogs and the shortcut-configuration log. `FX_PALETTE_REPORT_DIR` env override so tests never write into the real report folder. |
| Tests | Only the adapter allowlist test existed; nothing exercised `transport.js` or any companion code. | Added `tests/transport.test.js` (fake WebSocket; handshake, echo, reconnect, stale socket, stop) and `companion/tests/` (real `PremiereUxpExecutionAdapter` on an OS-chosen port against an in-process `QWebSocket`; loader/search/helpers). `npm run validate` runs all of it. |
| Leftovers | `capturedTransformCurveReference` (probe global), `QtHotkeyEditor` (dead dialog), tk-compat shims on `QtRootAdapter`, write-only nest-mode preference, ~50 unused constants, three mojibake comment lines, `PIL.ImageTk` hidden import in the PyInstaller spec, "proof of concept" wording in the adapter's error message, `package.json` name. | Removed / corrected. `tkinter` is now explicitly excluded from the PyInstaller bundle. |

### Deliberately not changed

- Settings path (`%APPDATA%/Adobe/CEP/extensions/EffectPalette/settings.json`): moving it would
  drop every existing user's aliases/hotkeys/language. Product decision, not cleanup.
- `EFFECT_IDENTITY_MATRIX.json` fixture and its 829-entry assertion in `validate.js`.
- `.gitattributes` stays untracked (pending EOL decision). `CLAUDE.md` is now tracked.
- No push to `origin` was made; the remote is far behind local `main` and pushing is the user's call.

### Branches

`motracker-vfr-root-cause` was strictly ahead of `main` (seven commits, fast-forward). The audit was
done on `slop-audit`, then `main` was fast-forwarded to it and both branches deleted, so `main` is
the only local branch.

## Native Nest regression from the audit, and its UXP-side replacement (2026-09-04, same day)

The user's first host session after the audit: "the native Nest isn't really working, the API Nest
is working". Telemetry for the four native attempts that session: two never saw Premiere's Nest
dialog within 3 s (`native_nest_dialog_autofill_unavailable: dialog_timeout`), two did but ended
with `custom_name: false`.

### Root cause: the audit's "dead watch" claim was wrong on this machine

The audit removed `arm_native_nest_watch` / `dispatch_when_native_nest_watch_ready` as dead code,
on the reasoning that nothing reads `premiere_cmd.json` any more. On the dev machine that is false:
the stable **CEP extension is still installed** (`%APPDATA%\Adobe\CEP\extensions\EffectPalette`,
CSXS host range `[14.0,99.9]`) and its worker runs inside Premiere - `premiere_cmd.json` carried a
`watchNativeNest ... "status": "done"` from 18:10 that day, `worker.log` and the `premiere_*.json`
exports were still being refreshed during the session, and the telemetry has **zero**
`native_nest_watch_arm_timeout` events across 20 native dispatches. So the watch was being served,
and it did two things the removal lost:

1. `bridge.js`'s `armNativeNestWatcher` snapshotted the project's sequences, acknowledged, then
   polled every 750 ms for a sequence not in the snapshot and ran `organizeCreatedNest` on it:
   rename to the requested name (or the `FXN-NNN` codename) and file it into the "Nested Clips"
   bin. Without it a native Nest stays "Nested Sequence 01" at the project root.
2. Its acknowledgement was an ExtendScript round trip, so the keystroke went out a few hundred
   milliseconds after `activate_window_handle_native()` rather than the audit's fixed 80 ms.

For a user **without** the CEP product (every installed build) the watch always timed out after
2 s and organised nothing - so the organise step never existed on the UXP-only product at all.

### Fix

- **`timeline.organizeNativeNest`** (new plugin action, allowlist now 16): payload
  `{ baselineSequenceGuids, name, binName }`. Finds any sequence whose GUID is not in the baseline,
  renames its project item if the dialog typing did not take, files it into the bin through the
  existing `ensureBinAndMoveProjectItem`, and returns `{ found: false }` while Premiere has not
  created it yet. `diagnostics.read` now also returns `project.sequences` (`name` + `guid`) so the
  companion can take the baseline.
- **Companion** (`_execute_timeline_action`): a blank name resolves to the `FXN-NNN` codename
  *before* dispatch (via `next_nest_codename()`), so Premiere's own dialog gets it typed - CEP's
  `_uniqueNestSequenceName` behaviour. The sequence baseline is taken before hiding the palette.
  After `native_nest_dialog_autofill` confirms, `schedule_native_nest_organization` polls the new
  action every 750 ms for up to 20 s (`native_nest_organized` / `native_nest_organize_timeout`).
- **Keystroke timing**: both native paths (Nest and Label) now go through
  `dispatch_when_window_foreground`, which waits (80 ms minimum, 1.5 s maximum) until Premiere is
  actually the foreground window and logs `native_dispatch_focus` with the wait. In the two host
  runs below Premiere was already foreground at ~95 ms, so the focus-timing hypothesis for the two
  `dialog_timeout` misses is **not confirmed** - the wait stays as a bounded, logged guard, and the
  telemetry will show `foreground: false` if that is ever the cause.

### Evidence (host-tested, Premiere 26.0 profile, project `TESTE MOTRACK`, sequence `Test`)

Two consecutive native Nests driven end to end by automation (clip click, Ctrl+Space, `nest`,
Enter, Enter with a blank name), companion restarted on the new code, plugin hot-reloaded by UDT:

| Run | `native_dispatch_focus` | dialog | organised |
| --- | --- | --- | --- |
| 1 | foreground true, 92.8 ms | confirmed, `custom_name: true` | attempt 1: `FXN-001`, bin "Nested Clips" created, move ok |
| 2 | foreground true, 96.2 ms | confirmed, `custom_name: true` | attempt 1: `FXN-002`, bin existed, move ok |

Screenshot after run 1: the V1 clip reads `FXN-001` and "Nested Clips" is in the Project panel.
Both nests were left in the user's `Test` sequence (undo twice to remove them). Off-host:
`npm run validate` passes (27 companion tests, including the new snapshot/organize round trip).
Two runs is a small sample; the user's own sessions will say whether the intermittent miss is gone.

User confirmation, same evening: native Nest works again in their own session ("Now it works").
