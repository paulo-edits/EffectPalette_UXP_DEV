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

async function resolveVideoEffectCatalog() {
  const startedAt = Date.now();
  const matchNames = await premiere.VideoFilterFactory.getMatchNames();
  const displayNames = await premiere.VideoFilterFactory.getDisplayNames();
  const sampleMatchName = matchNames[0] || null;
  const sampleComponent = sampleMatchName
    ? await premiere.VideoFilterFactory.createComponent(sampleMatchName)
    : null;
  const canReadDisplayNameBeforeInsertion = Boolean(
    sampleComponent && typeof sampleComponent.getDisplayName === "function"
  );

  return {
    status: canReadDisplayNameBeforeInsertion ? "runtime-extension-detected" : "unsupported-by-official-api",
    matchNameCount: matchNames.length,
    displayNameCount: displayNames.length,
    canReadDisplayNameBeforeInsertion,
    positionalPairingAssumed: false,
    sampleMatchName,
    sampleMatchNames: matchNames.slice(0, 10),
    sampleDisplayNames: displayNames.slice(0, 10),
    explanation: canReadDisplayNameBeforeInsertion
      ? "The runtime exposes an undocumented method; the proof of concept will not depend on it."
      : "VideoFilterComponent has no official display-name API, and Adobe does not document positional correspondence between the two catalog arrays.",
    durationMs: Date.now() - startedAt,
  };
}

async function setSelectedProjectItemLabel(action) {
  const allowedLabels = {
    VIOLET: premiere.Constants.ProjectItemColorLabel.VIOLET
  };
  const labelName = action.payload.labelName;
  const labelIndex = allowedLabels[labelName];
  if (typeof labelIndex !== "number") {
    throw new Error("Requested color label is not allowlisted.");
  }

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before setting a color label.");

  const selection = await premiere.ProjectUtils.getSelection(project);
  const selectedItems = selection ? await selection.getItems() : [];
  if (!Array.isArray(selectedItems) || selectedItems.length === 0) {
    throw new Error("Select at least one item in the Project panel.");
  }

  let transactionSucceeded = false;
  project.lockedAccess(() => {
    const actions = selectedItems.map((item) => item.createSetColorLabelAction(labelIndex));
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      actions.forEach((itemAction) => compoundAction.addAction(itemAction));
    }, "FX.palette: Set project item label to Violet");
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the color-label transaction.");
  return {
    affectedItemCount: selectedItems.length,
    labelConstant: labelName,
    labelIndex,
    displayColorDependsOnUserPalette: true,
    undoable: true
  };
}

async function applyVideoEffectToSelection(action) {
  const requestedMatchName = typeof action.payload.matchName === "string"
    ? action.payload.matchName.trim()
    : "";
  if (!requestedMatchName) throw new Error("A video-effect match name is required.");

  const availableMatchNames = await premiere.VideoFilterFactory.getMatchNames();
  const matchName = availableMatchNames.find((candidate) => candidate === requestedMatchName);
  if (!matchName) throw new Error("Video-effect match name was not found in the official runtime catalog.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before applying a video effect.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before applying a video effect.");

  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const videoMediaTypes = new Set();
  const videoTrackCount = await sequence.getVideoTrackCount();
  for (let trackIndex = 0; trackIndex < videoTrackCount; trackIndex += 1) {
    const videoTrack = await sequence.getVideoTrack(trackIndex);
    videoMediaTypes.add(guidToString(await videoTrack.getMediaType()));
  }
  const selectedItemsWithMediaType = await Promise.all(
    (Array.isArray(selectedItems) ? selectedItems : []).map(async (item) => ({
      item,
      mediaType: guidToString(await item.getMediaType())
    }))
  );
  const selectedVideoClips = selectedItemsWithMediaType
    .filter((entry) => videoMediaTypes.has(entry.mediaType))
    .map((entry) => entry.item);
  if (selectedVideoClips.length === 0) {
    throw new Error("Select at least one video clip in the Timeline.");
  }

  const componentChains = await Promise.all(
    selectedVideoClips.map((item) => item.getComponentChain())
  );
  const componentCountsBefore = await Promise.all(
    componentChains.map((chain) => chain.getComponentCount())
  );
  const components = await Promise.all(
    selectedVideoClips.map(() => premiere.VideoFilterFactory.createComponent(matchName))
  );

  let transactionSucceeded = false;
  project.lockedAccess(() => {
    const actions = componentChains.map((chain, index) =>
      chain.createAppendComponentAction(components[index])
    );
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      actions.forEach((itemAction) => compoundAction.addAction(itemAction));
    }, `FX.palette: Apply video effect ${matchName}`);
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the video-effect transaction.");
  const verification = await Promise.all(componentChains.map(async (chain, index) => {
    try {
      const componentCountAfter = await chain.getComponentCount();
      const expectedComponentIndex = componentCountsBefore[index];
      const insertedComponent = componentCountAfter > expectedComponentIndex
        ? await chain.getComponentAtIndex(expectedComponentIndex)
        : null;
      const verifiedMatchName = insertedComponent && typeof insertedComponent.getMatchName === "function"
        ? await insertedComponent.getMatchName()
        : null;
      const displayName = insertedComponent && typeof insertedComponent.getDisplayName === "function"
        ? await insertedComponent.getDisplayName()
        : null;

      return {
        clipName: typeof selectedVideoClips[index].getName === "function"
          ? await selectedVideoClips[index].getName()
          : null,
        componentCountBefore: componentCountsBefore[index],
        componentCountAfter,
        expectedComponentIndex,
        appended: componentCountAfter === componentCountsBefore[index] + 1,
        requestedMatchName: matchName,
        verifiedMatchName,
        displayName,
        identityMatchesRequest: verifiedMatchName === matchName
      };
    } catch (error) {
      return {
        requestedMatchName: matchName,
        verified: false,
        error: error && error.message ? error.message : String(error)
      };
    }
  }));

  return {
    affectedItemCount: selectedVideoClips.length,
    matchName,
    verificationSucceeded: verification.every((item) =>
      item.appended === true && item.identityMatchesRequest === true && typeof item.displayName === "string"
    ),
    verification,
    undoable: true
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

async function runSetVioletLabel() {
  const button = document.getElementById("set-label-violet");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "projectItems.setColorLabel",
      requestId: String(Date.now()),
      payload: { labelName: "VIOLET" }
    },
    { "projectItems.setColorLabel": setSelectedProjectItemLabel }
  );

  if (result.ok) await refresh();
  text("action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runApplyVideoEffect() {
  const button = document.getElementById("apply-video-effect");
  const input = document.getElementById("video-effect-match-name");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.applyVideoEffect",
      requestId: String(Date.now()),
      payload: { matchName: input ? input.value : "" }
    },
    { "timeline.applyVideoEffect": applyVideoEffectToSelection }
  );

  text("effect-action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runResolveVideoEffectCatalog() {
  const button = document.getElementById("resolve-video-effect-catalog");
  if (button) button.disabled = true;
  text("catalog-resolution-status", "Probing official effect identity capabilities…");

  const result = await executionAdapter.execute(
    {
      type: "catalog.videoEffects.resolve",
      requestId: String(Date.now()),
      payload: {}
    },
    { "catalog.videoEffects.resolve": resolveVideoEffectCatalog }
  );

  if (result.ok) {
    text(
      "catalog-resolution-status",
      `Probe: ${result.data.status}. ${result.data.matchNameCount} match name(s), ${result.data.displayNameCount} display name(s), completed in ${result.data.durationMs} ms.`
    );
    text("catalog-resolution-output", JSON.stringify(result.data, null, 2));
  } else {
    text("catalog-resolution-status", result.error.message);
    text("catalog-resolution-output", JSON.stringify(result, null, 2));
  }
  if (button) button.disabled = false;
}

function wirePanel() {
  const button = document.getElementById("refresh");
  if (button && !button.dataset.wired) {
    button.addEventListener("click", refresh);
    button.dataset.wired = "true";
  }
  const labelButton = document.getElementById("set-label-violet");
  if (labelButton && !labelButton.dataset.wired) {
    labelButton.addEventListener("click", runSetVioletLabel);
    labelButton.dataset.wired = "true";
  }
  const effectButton = document.getElementById("apply-video-effect");
  if (effectButton && !effectButton.dataset.wired) {
    effectButton.addEventListener("click", runApplyVideoEffect);
    effectButton.dataset.wired = "true";
  }
  const catalogButton = document.getElementById("resolve-video-effect-catalog");
  if (catalogButton && !catalogButton.dataset.wired) {
    catalogButton.addEventListener("click", runResolveVideoEffectCatalog);
    catalogButton.dataset.wired = "true";
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
