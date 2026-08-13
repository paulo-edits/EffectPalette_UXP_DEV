"use strict";

const { entrypoints, host, versions } = require("uxp");
const premiere = require("premierepro");
const executionAdapter = require("./execution-adapter.js");

function text(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value == null || value === "" ? "—" : String(value);
}

function guidToString(guid) {
  return guid && typeof guid.toString === "function" ? guid.toString() : null;
}

async function readDiagnostics() {
  const result = {
    capturedAt: new Date().toISOString(),
    host: {
      name: host.name || "Adobe Premiere",
      version: host.version || null,
      uxpVersion: versions.uxp || null
    },
    project: null,
    sequence: null,
    timelineSelection: { count: 0 }
  };

  const project = await premiere.Project.getActiveProject();
  if (!project) return result;

  result.project = {
    name: project.name || null,
    guid: guidToString(project.guid)
  };

  const sequence = await project.getActiveSequence();
  if (!sequence) return result;

  result.sequence = {
    name: sequence.name || null,
    guid: guidToString(sequence.guid)
  };

  const selection = await sequence.getSelection();
  if (selection) {
    const trackItems = await selection.getTrackItems();
    result.timelineSelection.count = Array.isArray(trackItems) ? trackItems.length : 0;
  }

  return result;
}

function render(result) {
  const data = result.ok ? result.data : null;
  text("host-name", data && data.host.name);
  text("host-version", data && data.host.version);
  text("uxp-version", data && data.host.uxpVersion);
  text("project-name", data && data.project && data.project.name);
  text("project-guid", data && data.project && data.project.guid);
  text("sequence-name", data && data.sequence && data.sequence.name);
  text("sequence-guid", data && data.sequence && data.sequence.guid);
  text("selection-count", data && data.timelineSelection.count);
  text("serialized-output", JSON.stringify(result, null, 2));

  const status = document.getElementById("status");
  if (!status) return;
  status.classList.toggle("error", !result.ok);
  status.textContent = result.ok ? "Diagnostics refreshed." : result.error.message;
}

async function refresh() {
  const button = document.getElementById("refresh");
  if (button) button.disabled = true;
  text("status", "Reading Premiere context…");

  const result = await executionAdapter.execute(
    { type: "diagnostics.read", requestId: String(Date.now()), payload: {} },
    { "diagnostics.read": readDiagnostics }
  );
  render(result);
  if (button) button.disabled = false;
}

function wirePanel() {
  const button = document.getElementById("refresh");
  if (button && !button.dataset.wired) {
    button.addEventListener("click", refresh);
    button.dataset.wired = "true";
  }
  refresh();
}

entrypoints.setup({
  panels: {
    effectPaletteDiagnostics: {
      create() { wirePanel(); },
      show() { wirePanel(); }
    }
  }
});
