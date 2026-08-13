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

async function describeProjectItem(item) {
  return {
    name: item.name || null,
    type: typeof item.type === "number" ? item.type : null,
    id: typeof item.getId === "function" ? await item.getId() : null,
    colorLabelIndex: typeof item.getColorLabelIndex === "function" ? await item.getColorLabelIndex() : null
  };
}

async function describeTrackItem(item) {
  const projectItem = typeof item.getProjectItem === "function" ? await item.getProjectItem() : null;
  return {
    name: typeof item.getName === "function" ? await item.getName() : null,
    type: typeof item.getType === "function" ? await item.getType() : null,
    trackIndex: typeof item.getTrackIndex === "function" ? await item.getTrackIndex() : null,
    mediaType: typeof item.getMediaType === "function" ? guidToString(await item.getMediaType()) : null,
    projectItem: projectItem ? {
      name: projectItem.name || null,
      id: typeof projectItem.getId === "function" ? await projectItem.getId() : null
    } : null
  };
}

async function readCatalogs() {
  const videoDisplayNames = await premiere.VideoFilterFactory.getDisplayNames();
  const videoMatchNames = await premiere.VideoFilterFactory.getMatchNames();
  const audioDisplayNames = await premiere.AudioFilterFactory.getDisplayNames();
  const videoTransitionMatchNames = await premiere.TransitionFactory.getVideoTransitionMatchNames();

  return {
    videoEffects: {
      count: videoMatchNames.length,
      displayNameCount: videoDisplayNames.length,
      sampleDisplayNames: videoDisplayNames.slice(0, 20),
      sampleMatchNames: videoMatchNames.slice(0, 20)
    },
    audioEffects: {
      count: audioDisplayNames.length,
      sampleDisplayNames: audioDisplayNames.slice(0, 20)
    },
    videoTransitions: {
      count: videoTransitionMatchNames.length,
      sampleMatchNames: videoTransitionMatchNames.slice(0, 20)
    },
    effectPresets: {
      status: "unknown",
      reason: "No official effect-preset catalog API identified in the current Premiere UXP reference."
    }
  };
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
    projectSelection: { count: 0, items: [] },
    timelineSelection: { count: 0, items: [] },
    catalogs: await readCatalogs()
  };

  const project = await premiere.Project.getActiveProject();
  if (!project) return result;

  result.project = {
    name: project.name || null,
    guid: guidToString(project.guid)
  };

  const projectSelection = await premiere.ProjectUtils.getSelection(project);
  if (projectSelection) {
    const projectItems = await projectSelection.getItems();
    result.projectSelection.items = await Promise.all(projectItems.map(describeProjectItem));
    result.projectSelection.count = result.projectSelection.items.length;
  }

  const sequence = await project.getActiveSequence();
  if (!sequence) return result;

  result.sequence = {
    name: sequence.name || null,
    guid: guidToString(sequence.guid)
  };

  const selection = await sequence.getSelection();
  if (selection) {
    const trackItems = await selection.getTrackItems();
    const selectedTrackItems = Array.isArray(trackItems) ? trackItems : [];
    result.timelineSelection.count = selectedTrackItems.length;
    result.timelineSelection.items = await Promise.all(selectedTrackItems.map(describeTrackItem));
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
  text("project-selection-count", data && data.projectSelection.count);
  text("timeline-selection-count", data && data.timelineSelection.count);
  text("project-selection-output", JSON.stringify(data ? data.projectSelection.items : [], null, 2));
  text("timeline-selection-output", JSON.stringify(data ? data.timelineSelection.items : [], null, 2));
  text("video-effect-count", data && data.catalogs.videoEffects.count);
  text("audio-effect-count", data && data.catalogs.audioEffects.count);
  text("video-transition-count", data && data.catalogs.videoTransitions.count);
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
