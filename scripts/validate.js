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
expect(manifest.entrypoints.some((item) => item.type === "panel" && item.id === "effectPaletteDiagnostics"), "diagnostics panel entrypoint is required");
expect(manifest.entrypoints.some((item) => item.type === "command" && item.id === "headlessSetVioletLabel"), "headless command entrypoint is required");
expect(
  manifest.requiredPermissions &&
    Object.keys(manifest.requiredPermissions).length === 2 &&
    manifest.requiredPermissions.clipboard === "readAndWrite" &&
    manifest.requiredPermissions.localFileSystem === "request",
  "PoC must request only clipboard readAndWrite and user-requested localFileSystem permissions"
);
expect(fs.existsSync(path.join(root, manifest.main)), "manifest main file does not exist");
expect(fs.existsSync(identityMatrixPath), "EFFECT_IDENTITY_MATRIX.json is required");
if (fs.existsSync(identityMatrixPath)) {
  const identityMatrix = JSON.parse(fs.readFileSync(identityMatrixPath, "utf8"));
  expect(identityMatrix.schemaVersion === 1, "effect identity matrix schemaVersion must be 1");
  expect(identityMatrix.summary && identityMatrix.summary.totalEntries === 829, "effect identity matrix must contain 829 entries");
  expect(Array.isArray(identityMatrix.entries) && identityMatrix.entries.length === 829, "effect identity matrix entries are incomplete");
}

for (const filename of ["index.js", "execution-adapter.js"]) {
  const source = fs.readFileSync(path.join(root, filename), "utf8");
  try { new vm.Script(source, { filename }); } catch (error) { errors.push(`${filename}: ${error.message}`); }
}

if (errors.length) {
  console.error(errors.map((error) => `- ${error}`).join("\n"));
  process.exitCode = 1;
} else {
  console.log("Manifest and JavaScript validation passed.");
}
