"use strict";

// Stages only the files the UXP plugin actually loads at runtime into packaging/plugin-src/, so
// UDT's own Package command can be pointed at a clean folder.
//
// Why this exists: UDT packages the whole folder that contains the manifest.json it was given. The
// plugin's manifest lives at the repo root, so the 0.53.0 .ccx shipped to users contained the
// entire repository - 1063 entries including .git with the full commit history, companion/,
// packaging/, and every .md - 7.2 MB where the plugin needs about 370 KB. Nothing outside the list
// below is ever read at runtime: index.html is manifest.main, it loads index.js, which requires
// ./execution-adapter.js and ./transport.js, and reads exactly the two bundled template files
// through localFileSystem.getPluginFolder() (readBundledTemplateJson /
// getBundledTemplateProjectPath).
//
//   node scripts/stage-plugin.js
//
// then in UDT: Add Plugin -> packaging/plugin-src/manifest.json -> Package. Re-run this after any
// plugin edit; the staged folder is a copy, not a link, and is gitignored.

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const outDir = path.join(root, "packaging", "plugin-src");

const RUNTIME_FILES = [
  "manifest.json",
  "index.html",
  "index.js",
  "execution-adapter.js",
  "transport.js",
  "assets/template_project/template_project.prproj",
  "assets/template_project/generic_item_templates.json"
];

fs.rmSync(outDir, { recursive: true, force: true });

let totalBytes = 0;
for (const relative of RUNTIME_FILES) {
  const source = path.join(root, relative);
  if (!fs.existsSync(source)) {
    console.error(`Missing required plugin file: ${relative}`);
    process.exitCode = 1;
    continue;
  }
  const destination = path.join(outDir, relative);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
  totalBytes += fs.statSync(source).size;
}

if (process.exitCode === 1) return;

// The staged manifest is the one that ships, so fail loudly rather than package a broken plugin.
const manifest = JSON.parse(fs.readFileSync(path.join(outDir, "manifest.json"), "utf8"));
if (manifest.main !== "index.html") {
  console.error("manifest.main is not index.html - the staged file list is out of date.");
  process.exitCode = 1;
  return;
}

console.log(`Staged ${RUNTIME_FILES.length} files (${(totalBytes / 1024).toFixed(0)} KB) for plugin ${manifest.id} ${manifest.version}`);
console.log(`  ${outDir}`);
console.log("In UDT: Add Plugin -> that folder's manifest.json -> flyout menu -> Package.");
