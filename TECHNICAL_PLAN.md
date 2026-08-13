# Technical plan

## Scope and invariants

This proof of concept establishes actual Premiere UXP capability boundaries without changing the stable Python + CEP product. It uses only official UXP and Premiere DOM APIs. It does not contain CEP, ExtendScript, native shortcuts, a network listener, Python communication, arbitrary script execution, or filesystem permissions.

The production architecture must not require a visible Premiere panel. A panel may remain available for diagnostics and settings, while the operational plugin context should load automatically and run invisibly. This lifecycle requirement must be proven in Premiere before any Python transport is designed.

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
