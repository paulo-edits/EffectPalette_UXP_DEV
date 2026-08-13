# FX.palette â€” UXP Project Handoff

Read this file before starting the separate UXP proof-of-concept project.

## Repository separation

- Stable application: `paulo-edits/Effect-Palette` and `paulo-edits/Effect-Palette_DEV`.
- UXP laboratory: a separate private repository named `paulo-edits/EffectPalette_UXP_DEV`.
- Do not move experimental UXP code into the stable Python + CEP repository during the proof of concept.
- Do not replace or remove CEP until UXP feature parity has been demonstrated.

## Current stable product

FX.palette is a floating Windows companion for Adobe Premiere Pro. The user interface and global shortcuts run in Python/PySide6. Premiere integration currently uses a headless CEP worker (`bridge.js`) and ExtendScript host (`scripts/host.jsx`).

The product model already emits serializable action dictionaries. Premiere execution passes through `PremiereExecutionAdapter` and `execute_effect_through_adapter()`. A future UXP integration should add another backend behind this boundary instead of coupling product features directly to UXP.

Working capabilities include:

- effect, preset and transition search/application;
- project, favorite and generic-item insertion;
- automatic Nest routing between Premiere-native and API implementations;
- custom Nest names and organization under `Nested Sequences`;
- Premiere Label actions and Label-group selection;
- global shortcuts and Stream Deck-friendly F13â€“F24 bindings;
- aliases, recent successful actions and actionable diagnostics;
- automatic duration matching for Adjustment Layers and similar infinite-duration items with one or more selected clips.

The visual macro experiment was deliberately removed. Do not treat macros as current scope.

## UXP proof-of-concept objective

Target Premiere Pro 25.6 or newer. Build the smallest plugin that can establish the real UXP capability boundaries for:

1. host/version and active-project diagnostics;
2. active sequence and timeline selection;
3. effect and preset catalog access;
4. applying effects and presets;
5. Labels and Label-group selection;
6. Nest creation and naming;
7. project-item and generic-item insertion.

Create and maintain a capability matrix with one row per operation and columns for UXP, CEP, native-shortcut fallback, limitations and tested Premiere versions. Record unsupported or undocumented behavior rather than emulating it prematurely.

## Integration constraints

- Keep the Python companion for the floating palette, global shortcuts, Windows integration, installer and updates.
- Preserve the existing serializable action schema where possible.
- Any local communication with Python must be optional, localhost-only and authenticated with a token.
- Never expose arbitrary command or script execution.
- Do not design the large UI rework until the proof of concept clarifies what belongs in the Windows companion versus a Premiere panel.
- Prefer official Premiere UXP APIs and document every fallback.

## Suggested first deliverables

1. Minimal loadable UXP plugin and development instructions.
2. Diagnostics panel showing Premiere version, project and active sequence.
3. `CAPABILITY_MATRIX.md` with initial findings and test evidence.
4. Read-only selection/catalog experiments.
5. One safe end-to-end action through a provisional UXP execution adapter.

See `ARCHITECTURE.md` and `CODEX_HANDOFF.md` in the stable repository for additional product context.
