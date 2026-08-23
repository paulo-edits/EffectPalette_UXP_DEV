# Technical plan

## Scope and invariants

This proof of concept establishes actual Premiere UXP capability boundaries without changing the stable Python + CEP product. It uses only official UXP and Premiere DOM APIs. It does not contain CEP, ExtendScript, native shortcuts, a network listener, Python communication, arbitrary script execution, or filesystem permissions.

The production architecture must not require a visible Premiere panel. A panel may remain available for diagnostics and settings, while the operational plugin context should load automatically and run invisibly. This lifecycle requirement must be proven in Premiere before any Python transport is designed.

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

Responses are plain serializable objects with `ok`, `schemaVersion`, `actionType`, `requestId`, and either `data` or `error`. The allowlist currently contains only `diagnostics.read`. Unknown actions fail closed. There is no transport yet; a future optional localhost transport must be authenticated and must only dispatch allowlisted typed actions.

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
this repository's diagnostic-only defaults. Stage 5 (optional transport design) and actual CEP
retirement remain separate decisions outside this proof of concept's scope.
