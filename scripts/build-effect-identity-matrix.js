"use strict";

const fs = require("fs");
const path = require("path");

const [uxpInput, cepInput, output] = process.argv.slice(2);
if (!uxpInput || !cepInput || !output) {
  throw new Error("Usage: node scripts/build-effect-identity-matrix.js <uxp-export.json> <cep-catalog.json> <output.json>");
}

const uxp = JSON.parse(fs.readFileSync(path.resolve(uxpInput), "utf8"));
const cep = JSON.parse(fs.readFileSync(path.resolve(cepInput), "utf8"));
const data = uxp && uxp.data;
const matchNames = data && Array.isArray(data.matchNames) ? data.matchNames : [];
const displayNames = data && Array.isArray(data.displayNames) ? data.displayNames : [];
if (matchNames.length === 0 || matchNames.length !== displayNames.length) {
  throw new Error("UXP export must contain non-empty matchNames/displayNames arrays with equal lengths.");
}

const cepVideoNames = new Set(
  Array.isArray(cep.effects)
    ? cep.effects.filter((item) => item && item.type === "video").map((item) => item.name)
    : []
);

function providerFor(matchName) {
  if (/^AE\.Impact/i.test(matchName)) return "Film Impact";
  if (/^AE\.BCC|^BCC/i.test(matchName)) return "Boris FX / BCC";
  if (/^AE\.S_/i.test(matchName)) return "Boris FX / Sapphire";
  if (/^AE\.(Universe|RG_UNI)/i.test(matchName)) return "Maxon / Universe";
  if (/^AE\.Mettle/i.test(matchName)) return "Adobe VR / Mettle";
  if (/^(PR\.ADBE|AE\.ADBE|ADBE)/i.test(matchName)) return "Adobe";
  return "Other / unidentified";
}

const entries = matchNames.map((matchName, index) => {
  const displayName = displayNames[index];
  const confirmedModernMosaic = matchName === "AE.Impact_Mosaic_FX" && displayName === "Mosaic";
  const confirmedLegacyMosaic = matchName === "AE.ADBE Mosaic" && displayName === "Mosaic (Legacy)";
  return {
    index,
    provider: providerFor(matchName),
    matchName,
    displayName,
    status: confirmedModernMosaic || confirmedLegacyMosaic
      ? "confirmed_post_insertion"
      : "runtime_positional_candidate",
    presentInCepDisplayCatalog: cepVideoNames.has(displayName)
  };
});

const providers = {};
for (const entry of entries) providers[entry.provider] = (providers[entry.provider] || 0) + 1;

const matrix = {
  schemaVersion: 1,
  generatedFrom: {
    premiereVersion: "26.3.2",
    uxpRuntime: "uxp-9.3.0-local",
    uxpActionType: uxp.actionType || null,
    uxpRequestId: uxp.requestId || null,
    cepCatalogPremiereVersion: cep.premiere_version || null
  },
  evidencePolicy: {
    positionalPairingOfficiallyDocumented: false,
    positionalPairingObservedInThisSnapshot: true,
    runtimeCandidateMeaning: "The two values occurred at the same index in one UXP runtime export; use only after post-insertion confirmation.",
    cepUsage: "Read-only development evidence; never a runtime fallback."
  },
  summary: {
    totalEntries: entries.length,
    confirmedPostInsertion: entries.filter((entry) => entry.status === "confirmed_post_insertion").length,
    runtimePositionalCandidates: entries.filter((entry) => entry.status === "runtime_positional_candidate").length,
    displayNamesAlsoPresentInCep: entries.filter((entry) => entry.presentInCepDisplayCatalog).length,
    providers
  },
  entries
};

fs.writeFileSync(path.resolve(output), `${JSON.stringify(matrix, null, 2)}\n`, "utf8");
