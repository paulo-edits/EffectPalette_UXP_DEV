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

async function copyOutput(button) {
  const target = document.getElementById(button.dataset.copyTarget);
  if (!target) return;
  const originalLabel = button.textContent;

  try {
    await navigator.clipboard.setContent({ "text/plain": target.textContent || "" });
    button.textContent = "Copied!";
  } catch (error) {
    button.textContent = "Copy failed";
    console.error("Unable to copy diagnostic output:", error);
  }

  setTimeout(() => { button.textContent = originalLabel; }, 1600);
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
    impactMatchNameCount: matchNames.filter((name) => /impact/i.test(name)).length,
    impactMatchNames: matchNames.filter((name) => /impact/i.test(name)),
    sampleMatchName,
    sampleMatchNames: matchNames.slice(0, 10),
    sampleDisplayNames: displayNames.slice(0, 10),
    explanation: canReadDisplayNameBeforeInsertion
      ? "The runtime exposes an undocumented method; the proof of concept will not depend on it."
      : "VideoFilterComponent has no official display-name API, and Adobe does not document positional correspondence between the two catalog arrays.",
    durationMs: Date.now() - startedAt,
  };
}

async function readVideoTransitionCatalog() {
  const matchNames = await premiere.TransitionFactory.getVideoTransitionMatchNames();
  return {
    count: matchNames.length,
    matchNames,
    displayNamesAvailable: false,
    positionalMappingAvailable: false
  };
}

async function readVideoEffectCatalog() {
  const matchNames = await premiere.VideoFilterFactory.getMatchNames();
  const displayNames = await premiere.VideoFilterFactory.getDisplayNames();
  return {
    matchNameCount: matchNames.length,
    displayNameCount: displayNames.length,
    matchNames,
    displayNames,
    positionalPairingAssumed: false,
    warning: "The arrays are exported as independent evidence; Adobe does not document positional correspondence."
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

async function applyAudioEffectToSelection(action) {
  const requestedDisplayName = typeof action.payload.displayName === "string"
    ? action.payload.displayName.trim()
    : "";
  if (!requestedDisplayName) throw new Error("An audio-effect display name is required.");

  const availableDisplayNames = await premiere.AudioFilterFactory.getDisplayNames();
  const displayName = availableDisplayNames.find((candidate) => candidate === requestedDisplayName);
  if (!displayName) throw new Error("Audio-effect display name was not found in the official runtime catalog.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before applying an audio effect.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before applying an audio effect.");

  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const audioMediaTypes = new Set();
  const audioTrackCount = await sequence.getAudioTrackCount();
  for (let trackIndex = 0; trackIndex < audioTrackCount; trackIndex += 1) {
    const audioTrack = await sequence.getAudioTrack(trackIndex);
    audioMediaTypes.add(guidToString(await audioTrack.getMediaType()));
  }
  const selectedItemsWithMediaType = await Promise.all(
    (Array.isArray(selectedItems) ? selectedItems : []).map(async (item) => ({
      item,
      mediaType: guidToString(await item.getMediaType())
    }))
  );
  const selectedAudioClips = selectedItemsWithMediaType
    .filter((entry) => audioMediaTypes.has(entry.mediaType))
    .map((entry) => entry.item);
  if (selectedAudioClips.length === 0) {
    throw new Error("Select at least one audio clip in the Timeline.");
  }

  const componentChains = await Promise.all(selectedAudioClips.map((item) => item.getComponentChain()));
  const componentCountsBefore = await Promise.all(componentChains.map((chain) => chain.getComponentCount()));
  const components = await Promise.all(
    selectedAudioClips.map((item) =>
      premiere.AudioFilterFactory.createComponentByDisplayName(displayName, item)
    )
  );

  let transactionSucceeded = false;
  project.lockedAccess(() => {
    const actions = componentChains.map((chain, index) => chain.createAppendComponentAction(components[index]));
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      actions.forEach((itemAction) => compoundAction.addAction(itemAction));
    }, `FX.palette: Apply audio effect ${displayName}`);
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the audio-effect transaction.");
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
      const verifiedDisplayName = insertedComponent && typeof insertedComponent.getDisplayName === "function"
        ? await insertedComponent.getDisplayName()
        : null;

      return {
        clipName: typeof selectedAudioClips[index].getName === "function"
          ? await selectedAudioClips[index].getName()
          : null,
        componentCountBefore: componentCountsBefore[index],
        componentCountAfter,
        expectedComponentIndex,
        appended: componentCountAfter === componentCountsBefore[index] + 1,
        requestedDisplayName: displayName,
        verifiedDisplayName,
        verifiedMatchName,
        identityMatchesRequest: verifiedDisplayName === displayName
      };
    } catch (error) {
      return {
        requestedDisplayName: displayName,
        verified: false,
        error: error && error.message ? error.message : String(error)
      };
    }
  }));

  return {
    affectedItemCount: selectedAudioClips.length,
    displayName,
    verificationSucceeded: verification.every((item) => item.appended === true && item.identityMatchesRequest === true),
    verification,
    undoable: true
  };
}

async function countVideoTransitions(sequence) {
  let count = 0;
  const videoTrackCount = await sequence.getVideoTrackCount();
  for (let trackIndex = 0; trackIndex < videoTrackCount; trackIndex += 1) {
    const videoTrack = await sequence.getVideoTrack(trackIndex);
    const transitions = await videoTrack.getTrackItems(premiere.Constants.TrackItemType.TRANSITION, false);
    count += Array.isArray(transitions) ? transitions.length : 0;
  }
  return count;
}

async function applyVideoTransitionToSelection(action) {
  const requestedMatchName = typeof action.payload.matchName === "string"
    ? action.payload.matchName.trim()
    : "";
  const position = action.payload.position === "END" ? "END" : "START";
  if (!requestedMatchName) throw new Error("A video-transition match name is required.");

  const availableMatchNames = await premiere.TransitionFactory.getVideoTransitionMatchNames();
  const matchName = availableMatchNames.find((candidate) => candidate === requestedMatchName);
  if (!matchName) throw new Error("Video-transition match name was not found in the official runtime catalog.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before applying a video transition.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before applying a video transition.");

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
  if (selectedVideoClips.length === 0) throw new Error("Select at least one video clip in the Timeline.");

  const transitionCountBefore = await countVideoTransitions(sequence);
  const transitions = await Promise.all(
    selectedVideoClips.map(() => premiere.TransitionFactory.createVideoTransition(matchName))
  );
  const options = selectedVideoClips.map(() => {
    const transitionOptions = premiere.AddTransitionOptions();
    transitionOptions.setApplyToStart(position === "START");
    return transitionOptions;
  });

  let transactionSucceeded = false;
  project.lockedAccess(() => {
    const actions = selectedVideoClips.map((clip, index) =>
      clip.createAddVideoTransitionAction(transitions[index], options[index])
    );
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      actions.forEach((itemAction) => compoundAction.addAction(itemAction));
    }, `FX.palette: Apply video transition ${matchName}`);
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the video-transition transaction.");
  const transitionCountAfter = await countVideoTransitions(sequence);
  return {
    affectedItemCount: selectedVideoClips.length,
    matchName,
    position,
    transitionCountBefore,
    transitionCountAfter,
    transitionCountDelta: transitionCountAfter - transitionCountBefore,
    verificationSucceeded: transitionCountAfter > transitionCountBefore,
    identityVerification: "visual-only",
    identityReason: "The official VideoTransition class exposes no methods or properties.",
    hostDefaultDurationAndAlignment: true,
    undoable: true
  };
}

async function createSubsequenceFromSelection(action) {
  const requestedName = typeof action.payload.name === "string" ? action.payload.name.trim() : "";
  if (!requestedName) throw new Error("A subsequence name is required.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before creating a subsequence.");
  const sourceSequence = await project.getActiveSequence();
  if (!sourceSequence) throw new Error("Open a sequence before creating a subsequence.");

  const selection = await sourceSequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  if (!Array.isArray(selectedItems) || selectedItems.length === 0) {
    throw new Error("Select at least one Timeline clip before creating a subsequence.");
  }

  const sequencesBefore = await project.getSequences();
  const createdSequence = await sourceSequence.createSubsequence(true);
  if (!createdSequence) throw new Error("Premiere did not return the created subsequence.");

  const createdProjectItem = await createdSequence.getProjectItem();
  const generatedName = createdSequence.name || (createdProjectItem && createdProjectItem.name) || null;
  let renameTransactionSucceeded = false;
  if (createdProjectItem && typeof createdProjectItem.createSetNameAction === "function") {
    project.lockedAccess(() => {
      const renameAction = createdProjectItem.createSetNameAction(requestedName);
      renameTransactionSucceeded = project.executeTransaction((compoundAction) => {
        compoundAction.addAction(renameAction);
      }, `FX.palette: Rename subsequence to ${requestedName}`);
    });
  }

  const sequencesAfter = await project.getSequences();
  return {
    sourceSequence: {
      name: sourceSequence.name || null,
      guid: guidToString(sourceSequence.guid)
    },
    selectedItemCount: selectedItems.length,
    ignoreTrackTargeting: true,
    sequenceCountBefore: sequencesBefore.length,
    sequenceCountAfter: sequencesAfter.length,
    sequenceCountDelta: sequencesAfter.length - sequencesBefore.length,
    createdSequence: {
      generatedName,
      requestedName,
      sequenceNameAfterRename: createdSequence.name || null,
      projectItemNameAfterRename: createdProjectItem ? createdProjectItem.name || null : null,
      guid: guidToString(createdSequence.guid),
      projectItemId: createdProjectItem && typeof createdProjectItem.getId === "function"
        ? await createdProjectItem.getId()
        : null,
      parentBinName: createdProjectItem && typeof createdProjectItem.getParentBin === "function"
        ? ((await createdProjectItem.getParentBin()) || {}).name || null
        : null
    },
    renameTransactionSucceeded,
    creationUndoability: "unknown",
    creationUndoabilityReason: "Sequence.createSubsequence() returns a Sequence directly, not an Action.",
    selectionReplacementBehavior: "pending-host-test"
  };
}

async function createNestFromSelection(action) {
  const requestedName = typeof action.payload.name === "string" ? action.payload.name.trim() : "";
  if (!requestedName) throw new Error("A Nest name is required.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before creating a Nest.");
  const sourceSequence = await project.getActiveSequence();
  if (!sourceSequence) throw new Error("Open a sequence before creating a Nest.");

  const selection = await sourceSequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  if (!Array.isArray(selectedItems) || selectedItems.length === 0) {
    throw new Error("Select at least one Timeline clip before creating a Nest.");
  }

  const videoMediaTypes = new Set();
  const audioMediaTypes = new Set();
  const videoTrackCount = await sourceSequence.getVideoTrackCount();
  const audioTrackCount = await sourceSequence.getAudioTrackCount();
  for (let index = 0; index < videoTrackCount; index += 1) {
    videoMediaTypes.add(guidToString(await (await sourceSequence.getVideoTrack(index)).getMediaType()));
  }
  for (let index = 0; index < audioTrackCount; index += 1) {
    audioMediaTypes.add(guidToString(await (await sourceSequence.getAudioTrack(index)).getMediaType()));
  }

  const selectedDetails = await Promise.all(selectedItems.map(async (item) => ({
    item,
    name: typeof item.getName === "function" ? await item.getName() : null,
    mediaType: guidToString(await item.getMediaType()),
    trackIndex: await item.getTrackIndex(),
    startTime: await item.getStartTime()
  })));
  const videoItems = selectedDetails.filter((entry) => videoMediaTypes.has(entry.mediaType));
  const audioItems = selectedDetails.filter((entry) => audioMediaTypes.has(entry.mediaType));
  if (videoItems.length === 0) throw new Error("The first Nest probe requires at least one selected video clip.");

  const earliest = selectedDetails.reduce((current, entry) =>
    !current || entry.startTime.seconds < current.seconds ? entry.startTime : current
  , null);
  const targetVideoTrackIndex = Math.min(...videoItems.map((entry) => entry.trackIndex));
  const targetAudioTrackIndex = audioItems.length > 0
    ? Math.min(...audioItems.map((entry) => entry.trackIndex))
    : 0;
  const sequencesBefore = await project.getSequences();
  const createdSequence = await sourceSequence.createSubsequence(true);
  if (!createdSequence) throw new Error("Premiere did not return the created Nest sequence.");
  const createdProjectItem = await createdSequence.getProjectItem();
  if (!createdProjectItem) throw new Error("Premiere did not return the Nest project item.");

  const sequenceEditor = premiere.SequenceEditor.getEditor(sourceSequence);
  let replacementTransactionSucceeded = false;
  project.lockedAccess(() => {
    const renameAction = createdProjectItem.createSetNameAction(requestedName);
    const removeAction = sequenceEditor.createRemoveItemsAction(
      selection,
      false,
      premiere.Constants.MediaType.ANY,
      false
    );
    const overwriteAction = sequenceEditor.createOverwriteItemAction(
      createdProjectItem,
      earliest,
      targetVideoTrackIndex,
      targetAudioTrackIndex
    );
    replacementTransactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(renameAction);
      compoundAction.addAction(removeAction);
      compoundAction.addAction(overwriteAction);
    }, `FX.palette: Create Nest ${requestedName}`);
  });

  if (!replacementTransactionSucceeded) {
    throw new Error("Premiere created the subsequence but rejected the Timeline replacement transaction.");
  }

  const createdProjectItemId = await createdProjectItem.getId();
  const insertedInstances = [];
  const collectInsertedInstances = async (track, mediaKind) => {
    const trackItems = await track.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
    for (const trackItem of trackItems) {
      const startTime = await trackItem.getStartTime();
      if (String(startTime.ticks) !== String(earliest.ticks)) continue;
      const projectItem = await trackItem.getProjectItem();
      if (!projectItem || await projectItem.getId() !== createdProjectItemId) continue;
      insertedInstances.push({ trackItem, mediaKind, nameBefore: await trackItem.getName() });
    }
  };
  await collectInsertedInstances(await sourceSequence.getVideoTrack(targetVideoTrackIndex), "video");
  if (audioItems.length > 0) {
    await collectInsertedInstances(await sourceSequence.getAudioTrack(targetAudioTrackIndex), "audio");
  }

  let instanceRenameTransactionSucceeded = false;
  if (insertedInstances.length > 0) {
    project.lockedAccess(() => {
      const renameActions = insertedInstances.map((entry) => entry.trackItem.createSetNameAction(requestedName));
      instanceRenameTransactionSucceeded = project.executeTransaction((compoundAction) => {
        renameActions.forEach((renameAction) => compoundAction.addAction(renameAction));
      }, `FX.palette: Name Nest instances ${requestedName}`);
    });
  }

  const renamedInstances = await Promise.all(insertedInstances.map(async (entry) => ({
    mediaKind: entry.mediaKind,
    nameBefore: entry.nameBefore,
    nameAfter: await entry.trackItem.getName()
  })));

  const sequencesAfter = await project.getSequences();
  return {
    sourceSequence: { name: sourceSequence.name || null, guid: guidToString(sourceSequence.guid) },
    selectedItemCount: selectedItems.length,
    selectedVideoItemCount: videoItems.length,
    selectedAudioItemCount: audioItems.length,
    selectedItems: selectedDetails.map((entry) => ({
      name: entry.name,
      trackIndex: entry.trackIndex,
      startSeconds: entry.startTime.seconds,
      mediaKind: videoMediaTypes.has(entry.mediaType) ? "video" : "audio"
    })),
    insertion: {
      startSeconds: earliest.seconds,
      startTicks: earliest.ticks,
      videoTrackIndex: targetVideoTrackIndex,
      audioTrackIndex: targetAudioTrackIndex,
      editMode: "overwrite"
    },
    sequenceCountBefore: sequencesBefore.length,
    sequenceCountAfter: sequencesAfter.length,
    sequenceCountDelta: sequencesAfter.length - sequencesBefore.length,
    createdSequence: {
      requestedName,
      sequenceName: createdSequence.name || null,
      projectItemName: createdProjectItem.name || null,
      guid: guidToString(createdSequence.guid),
      projectItemId: createdProjectItemId
    },
    replacementTransactionSucceeded,
    instanceRenameTransactionSucceeded,
    insertedInstanceCount: insertedInstances.length,
    renamedInstances,
    originalSelectionExpectedRemoved: true,
    nestedItemExpectedInserted: true,
    undoModelExpected: [
      "Undo Timeline instance rename transaction",
      "Undo replacement and ProjectItem rename transaction",
      "Undo subsequence creation"
    ]
  };
}

function readNonNegativeTrackIndex(value, label) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return parsed;
}

async function findProjectItemInstancesAtTime(sequence, projectItemId, tickTime) {
  const matches = [];
  const collectTrack = async (track, mediaKind, trackIndex) => {
    const trackItems = await track.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
    for (const trackItem of trackItems) {
      const startTime = await trackItem.getStartTime();
      if (String(startTime.ticks) !== String(tickTime.ticks)) continue;
      const linkedProjectItem = await trackItem.getProjectItem();
      if (!linkedProjectItem || await linkedProjectItem.getId() !== projectItemId) continue;
      matches.push({
        mediaKind,
        trackIndex,
        name: await trackItem.getName(),
        startSeconds: startTime.seconds,
        startTicks: startTime.ticks
      });
    }
  };

  const videoTrackCount = await sequence.getVideoTrackCount();
  const audioTrackCount = await sequence.getAudioTrackCount();
  for (let index = 0; index < videoTrackCount; index += 1) {
    await collectTrack(await sequence.getVideoTrack(index), "video", index);
  }
  for (let index = 0; index < audioTrackCount; index += 1) {
    await collectTrack(await sequence.getAudioTrack(index), "audio", index);
  }
  return matches;
}

async function insertSelectedProjectItem(action) {
  const editMode = action.payload.editMode === "OVERWRITE" ? "OVERWRITE" : "INSERT";
  const videoTrackIndex = readNonNegativeTrackIndex(action.payload.videoTrackIndex, "Video track index");
  const audioTrackIndex = readNonNegativeTrackIndex(action.payload.audioTrackIndex, "Audio track index");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before inserting a Project item.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before inserting a Project item.");
  const selection = await premiere.ProjectUtils.getSelection(project);
  const projectItems = selection ? await selection.getItems() : [];
  if (!Array.isArray(projectItems) || projectItems.length !== 1) {
    throw new Error("Select exactly one item in the Project panel before insertion.");
  }

  const projectItem = projectItems[0];
  if (!projectItem || typeof projectItem.getId !== "function") {
    throw new Error("The selected Project item is not insertable.");
  }
  const projectItemId = await projectItem.getId();
  const playhead = await sequence.getPlayerPosition();
  const instancesBefore = await findProjectItemInstancesAtTime(sequence, projectItemId, playhead);
  const editor = premiere.SequenceEditor.getEditor(sequence);
  let transactionSucceeded = false;

  project.lockedAccess(() => {
    const insertionAction = editMode === "OVERWRITE"
      ? editor.createOverwriteItemAction(projectItem, playhead, videoTrackIndex, audioTrackIndex)
      : editor.createInsertProjectItemAction(projectItem, playhead, videoTrackIndex, audioTrackIndex, false);
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(insertionAction);
    }, `FX.palette: ${editMode === "OVERWRITE" ? "Overwrite" : "Insert"} ${projectItem.name || "Project item"}`);
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the Project item insertion transaction.");

  const instancesAfter = await findProjectItemInstancesAtTime(sequence, projectItemId, playhead);
  const insertedInstanceCount = Math.max(0, instancesAfter.length - instancesBefore.length);
  return {
    projectItem: {
      id: projectItemId,
      name: projectItem.name || null,
      type: projectItem.type
    },
    editMode,
    playhead: { seconds: playhead.seconds, ticks: playhead.ticks },
    requestedTracks: { videoTrackIndex, audioTrackIndex },
    matchingInstanceCountBefore: instancesBefore.length,
    matchingInstanceCountAfter: instancesAfter.length,
    insertedInstanceCount,
    insertedInstances: instancesAfter.slice(instancesBefore.length),
    verificationSucceeded: insertedInstanceCount > 0,
    transactionSucceeded,
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

async function runApplyAudioEffect() {
  const button = document.getElementById("apply-audio-effect");
  const input = document.getElementById("audio-effect-display-name");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.applyAudioEffect",
      requestId: String(Date.now()),
      payload: { displayName: input ? input.value : "" }
    },
    { "timeline.applyAudioEffect": applyAudioEffectToSelection }
  );

  text("audio-effect-action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runApplyVideoTransition() {
  const button = document.getElementById("apply-video-transition");
  const matchNameInput = document.getElementById("video-transition-match-name");
  const positionInput = document.getElementById("video-transition-position");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.applyVideoTransition",
      requestId: String(Date.now()),
      payload: {
        matchName: matchNameInput ? matchNameInput.value : "",
        position: positionInput ? positionInput.value : "START"
      }
    },
    { "timeline.applyVideoTransition": applyVideoTransitionToSelection }
  );

  text("video-transition-action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runCreateSubsequence() {
  const button = document.getElementById("create-subsequence");
  const input = document.getElementById("subsequence-name");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.createSubsequence",
      requestId: String(Date.now()),
      payload: { name: input ? input.value : "" }
    },
    { "timeline.createSubsequence": createSubsequenceFromSelection }
  );

  text("subsequence-action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runCreateNest() {
  const button = document.getElementById("create-nest");
  const input = document.getElementById("subsequence-name");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.createNest",
      requestId: String(Date.now()),
      payload: { name: input ? input.value : "" }
    },
    { "timeline.createNest": createNestFromSelection }
  );

  text("subsequence-action-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runInsertProjectItem() {
  const button = document.getElementById("insert-project-item");
  const editModeInput = document.getElementById("project-item-edit-mode");
  const videoTrackInput = document.getElementById("project-item-video-track");
  const audioTrackInput = document.getElementById("project-item-audio-track");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.insertProjectItem",
      requestId: String(Date.now()),
      payload: {
        editMode: editModeInput ? editModeInput.value : "INSERT",
        videoTrackIndex: videoTrackInput ? videoTrackInput.value : "0",
        audioTrackIndex: audioTrackInput ? audioTrackInput.value : "0"
      }
    },
    { "timeline.insertProjectItem": insertSelectedProjectItem }
  );

  text("project-item-insertion-output", JSON.stringify(result, null, 2));
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

async function runReadVideoTransitionCatalog() {
  const button = document.getElementById("read-video-transition-catalog");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "catalog.videoTransitions.read",
      requestId: String(Date.now()),
      payload: {}
    },
    { "catalog.videoTransitions.read": readVideoTransitionCatalog }
  );

  text("video-transition-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runReadVideoEffectCatalog() {
  const button = document.getElementById("read-video-effect-catalog");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "catalog.videoEffects.read",
      requestId: String(Date.now()),
      payload: {}
    },
    { "catalog.videoEffects.read": readVideoEffectCatalog }
  );

  text("video-effect-catalog-output", JSON.stringify(result, null, 2));
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
  const audioEffectButton = document.getElementById("apply-audio-effect");
  if (audioEffectButton && !audioEffectButton.dataset.wired) {
    audioEffectButton.addEventListener("click", runApplyAudioEffect);
    audioEffectButton.dataset.wired = "true";
  }
  const videoTransitionButton = document.getElementById("apply-video-transition");
  if (videoTransitionButton && !videoTransitionButton.dataset.wired) {
    videoTransitionButton.addEventListener("click", runApplyVideoTransition);
    videoTransitionButton.dataset.wired = "true";
  }
  const subsequenceButton = document.getElementById("create-subsequence");
  if (subsequenceButton && !subsequenceButton.dataset.wired) {
    subsequenceButton.addEventListener("click", runCreateSubsequence);
    subsequenceButton.dataset.wired = "true";
  }
  const nestButton = document.getElementById("create-nest");
  if (nestButton && !nestButton.dataset.wired) {
    nestButton.addEventListener("click", runCreateNest);
    nestButton.dataset.wired = "true";
  }
  const insertProjectItemButton = document.getElementById("insert-project-item");
  if (insertProjectItemButton && !insertProjectItemButton.dataset.wired) {
    insertProjectItemButton.addEventListener("click", runInsertProjectItem);
    insertProjectItemButton.dataset.wired = "true";
  }
  const catalogButton = document.getElementById("resolve-video-effect-catalog");
  if (catalogButton && !catalogButton.dataset.wired) {
    catalogButton.addEventListener("click", runResolveVideoEffectCatalog);
    catalogButton.dataset.wired = "true";
  }
  const transitionCatalogButton = document.getElementById("read-video-transition-catalog");
  if (transitionCatalogButton && !transitionCatalogButton.dataset.wired) {
    transitionCatalogButton.addEventListener("click", runReadVideoTransitionCatalog);
    transitionCatalogButton.dataset.wired = "true";
  }
  const videoEffectCatalogButton = document.getElementById("read-video-effect-catalog");
  if (videoEffectCatalogButton && !videoEffectCatalogButton.dataset.wired) {
    videoEffectCatalogButton.addEventListener("click", runReadVideoEffectCatalog);
    videoEffectCatalogButton.dataset.wired = "true";
  }
  document.querySelectorAll(".copy-json").forEach((copyButton) => {
    if (!copyButton.dataset.wired) {
      copyButton.addEventListener("click", () => copyOutput(copyButton));
      copyButton.dataset.wired = "true";
    }
  });
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
