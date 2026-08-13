# Technical plan

## Scope and invariants

This proof of concept establishes actual Premiere UXP capability boundaries without changing the stable Python + CEP product. It uses only official UXP and Premiere DOM APIs. It does not contain CEP, ExtendScript, native shortcuts, a network listener, Python communication, arbitrary script execution, or filesystem permissions.

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
2. **Read-only discovery** — investigate effect/preset/transition catalogs, project selection, labels, and project items using documented APIs; add probes only when an official API is identified.
3. **Safe mutation experiments** — one isolated, undoable action at a time through `Project.executeTransaction()` and the adapter. Record exact setup, version and outcome.
4. **Architecture decision** — compare verified UXP coverage with existing CEP and native-shortcut behavior. Define which operations can migrate and which remain outside UXP.
5. **Optional transport design** — only after capability boundaries are known: localhost-only, token-authenticated, schema-validated, no arbitrary commands.

## Validation gates

- `npm run validate` passes.
- UDT 2.2+ accepts `manifest.json` and reports a successful load against Premiere 25.6+.
- Panel opens from **Window > UXP Plugins**.
- Diagnostics work across empty/project/sequence/selection states without uncaught errors.
- UDT and Premiere App Logs contain no plugin errors during the test.
- The matrix distinguishes local validation, official documentation and actual Premiere evidence.

Packaging, communication with Python and product UI redesign are outside this stage.
