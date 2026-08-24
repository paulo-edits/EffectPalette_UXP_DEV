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
