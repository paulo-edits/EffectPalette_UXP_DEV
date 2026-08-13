# Technical and product decisions

## Optional preset easing reconstruction

- **Status:** Accepted
- **Date:** 2026-08-13
- **Scope:** Final EffectPalette preset application workflow

Reconstruction of the easing serialized in a Premiere effect preset will be an optional user setting, disabled by default. The serializable execution request will carry an explicit boolean such as `reconstructEasing`; the future Python boundary may select it, but no Python communication is implemented in this UXP proof of concept.

When disabled, the executor applies the preset's effects, static values, principal keyframe values and principal keyframe times. It does not generate dense helper keys solely to imitate unavailable easing handles.

When enabled, the executor may generate intermediate frame-sampled keyframes to approximate the original curve. The result must be labeled as an approximation whenever the official UXP surface cannot restore the original handles—especially for Point parameters such as Position and Anchor Point. The setting does not imply exact fidelity.

This is a generic execution policy, not a per-preset rule. Presets are classified by parameter/value structure at runtime; no preset-name allowlist is intended.
