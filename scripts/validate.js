"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const manifestPath = path.join(root, "manifest.json");
const errors = [];
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const identityMatrixPath = path.join(root, "EFFECT_IDENTITY_MATRIX.json");

function expect(condition, message) { if (!condition) errors.push(message); }

expect(manifest.manifestVersion === 5, "manifestVersion must be 5");
expect(typeof manifest.id === "string" && manifest.id.length > 0, "id is required");
expect(/^\d+\.\d+\.\d+$/.test(manifest.version), "version must be major.minor.patch");
expect(manifest.main === "index.html", "main must point to index.html");
expect(manifest.host && manifest.host.app === "premierepro", "host.app must be premierepro");
expect(manifest.host && manifest.host.minVersion === "25.6.0", "host.minVersion must be 25.6.0");
expect(Array.isArray(manifest.entrypoints) && manifest.entrypoints.length > 0, "at least one entrypoint is required");
expect(manifest.entrypoints.some((item) => item.type === "command" && item.id === "headlessSetVioletLabel"), "headless command entrypoint is required");
// The shipped manifest deliberately declares no "panel" entrypoint - the real product has no UI
// inside Premiere at all, only the companion's own search palette. The former diagnostics panel
// was removed; its git history holds it if a probe UI is ever needed again.
expect(!manifest.entrypoints.some((item) => item.type === "panel"), "the shipped manifest must not declare a panel entrypoint");
// Stage 5 (TECHNICAL_PLAN.md) added one exact, allowlisted localhost network permission for the
// optional companion transport; this check still fails on any other/extra permission appearing.
const expectedNetworkDomains = ["ws://localhost:58756"];
expect(
  manifest.requiredPermissions &&
    Object.keys(manifest.requiredPermissions).length === 3 &&
    manifest.requiredPermissions.clipboard === "readAndWrite" &&
    manifest.requiredPermissions.localFileSystem === "fullAccess" &&
    manifest.requiredPermissions.network &&
    Object.keys(manifest.requiredPermissions.network).length === 1 &&
    Array.isArray(manifest.requiredPermissions.network.domains) &&
    manifest.requiredPermissions.network.domains.length === expectedNetworkDomains.length &&
    manifest.requiredPermissions.network.domains.every((domain, index) => domain === expectedNetworkDomains[index]),
  "must request only clipboard readAndWrite, fullAccess localFileSystem (auto-locates the user's .prfpset, no per-file picker) and the exact stage-5 localhost network domain"
);
expect(fs.existsSync(path.join(root, manifest.main)), "manifest main file does not exist");
expect(fs.existsSync(identityMatrixPath), "EFFECT_IDENTITY_MATRIX.json is required");
if (fs.existsSync(identityMatrixPath)) {
  const identityMatrix = JSON.parse(fs.readFileSync(identityMatrixPath, "utf8"));
  expect(identityMatrix.schemaVersion === 1, "effect identity matrix schemaVersion must be 1");
  expect(identityMatrix.summary && identityMatrix.summary.totalEntries === 829, "effect identity matrix must contain 829 entries");
  expect(Array.isArray(identityMatrix.entries) && identityMatrix.entries.length === 829, "effect identity matrix entries are incomplete");
}

for (const filename of ["index.js", "execution-adapter.js", "transport.js"]) {
  const source = fs.readFileSync(path.join(root, filename), "utf8");
  try { new vm.Script(source, { filename }); } catch (error) { errors.push(`${filename}: ${error.message}`); }
}

if (errors.length) {
  console.error(errors.map((error) => `- ${error}`).join("\n"));
  process.exitCode = 1;
} else {
  console.log("Manifest and JavaScript validation passed.");
}
