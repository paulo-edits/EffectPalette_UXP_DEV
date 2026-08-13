# Video effect identity matrix

`EFFECT_IDENTITY_MATRIX.json` inventories every video effect exposed by the tested Premiere UXP runtime. It is generated from the complete official UXP export and the read-only CEP display-name snapshot supplied for development comparison.

## Interpretation

- `confirmed_post_insertion`: the pair was applied and read back through the official `Component` API.
- `runtime_positional_candidate`: both values occurred at the same array index in this Premiere 26.3.2 / UXP 9.3 snapshot. Adobe does not document positional correspondence, so this is evidence for testing, not a production guarantee.
- `presentInCepDisplayCatalog`: the visible name also appeared in the independently generated QE/CEP catalog. This increases cross-source evidence but does not confirm the UXP pair.

The CEP snapshot is never loaded by the plugin and is not a fallback. Trusted product mappings must be promoted only after official post-insertion identity verification.

## Current coverage

- 829 total UXP video effects.
- Adobe, Film Impact, Boris FX/BCC, Boris FX/Sapphire, Maxon/Universe, Adobe VR/Mettle and other identifiers are included.
- Eight pairs are confirmed across Adobe, Film Impact, BCC, Sapphire, Universe and Adobe VR/Mettle, including modern and Legacy Mosaic.
- All remaining entries are runtime positional candidates pending representative or individual confirmation.

Regenerate with:

```powershell
node scripts/build-effect-identity-matrix.js <uxp-export.json> <cep-catalog.json> EFFECT_IDENTITY_MATRIX.json
```
