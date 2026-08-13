"use strict";

const { entrypoints, host, versions } = require("uxp");
const premiere = require("premierepro");
const executionAdapter = require("./execution-adapter.js");
let capturedTransformCurveReference = null;
let importedEffectPresetCatalog = null;

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

function serializePresetProbeValue(value) {
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) return value.map(serializePresetProbeValue);

  const result = {};
  ["value", "x", "y", "red", "green", "blue", "alpha", "ticks", "seconds"].forEach((key) => {
    try {
      if (value[key] !== undefined && value[key] !== value) {
        result[key] = serializePresetProbeValue(value[key]);
      }
    } catch (_) { /* Some host proxy properties can throw when read. */ }
  });
  return Object.keys(result).length > 0
    ? result
    : { hostType: value.constructor && value.constructor.name ? value.constructor.name : typeof value };
}

function cubicBezierCoordinate(t, firstControl, secondControl) {
  const inverse = 1 - t;
  return (3 * inverse * inverse * t * firstControl) + (3 * inverse * t * t * secondControl) + (t * t * t);
}

function cubicBezierProgress(progress, x1, y1, x2, y2) {
  let low = 0;
  let high = 1;
  let parameter = progress;
  for (let index = 0; index < 18; index += 1) {
    parameter = (low + high) / 2;
    const x = cubicBezierCoordinate(parameter, x1, x2);
    if (x < progress) low = parameter;
    else high = parameter;
  }
  return cubicBezierCoordinate(parameter, y1, y2);
}

function unwrapNumericHostValue(value) {
  let current = value;
  for (let depth = 0; depth < 4; depth += 1) {
    if (typeof current === "number") return current;
    if (!current || typeof current !== "object" || !("value" in current)) break;
    current = current.value;
  }
  return typeof current === "number" ? current : null;
}

async function getSequenceFramesPerSecond(sequence) {
  const settings = await sequence.getSettings();
  if (settings && typeof settings.getVideoFrameRate === "function") {
    const frameRate = await settings.getVideoFrameRate();
    if (frameRate && Number(frameRate.value) > 0) return Number(frameRate.value);
  }
  const timebase = Number(await sequence.getTimebase());
  if (timebase > 0) return 254016000000 / timebase;
  throw new Error("The sequence frame rate could not be read through the official API.");
}

async function getSingleSelectedVideoClip(sequence) {
  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const videoMediaTypes = new Set();
  for (let index = 0; index < await sequence.getVideoTrackCount(); index += 1) {
    videoMediaTypes.add(guidToString(await (await sequence.getVideoTrack(index)).getMediaType()));
  }
  const clips = [];
  for (const item of Array.isArray(selectedItems) ? selectedItems : []) {
    if (videoMediaTypes.has(guidToString(await item.getMediaType()))) clips.push(item);
  }
  if (clips.length !== 1) throw new Error("Select exactly one video clip.");
  return clips[0];
}

async function findLastComponentByMatchName(chain, matchName) {
  for (let index = (await chain.getComponentCount()) - 1; index >= 0; index -= 1) {
    const component = await chain.getComponentAtIndex(index);
    if (await component.getMatchName() === matchName) return { component, index };
  }
  return null;
}

function normalizeCapturedValue(value) {
  let current = value;
  for (let depth = 0; depth < 4; depth += 1) {
    if (typeof current === "number") return { type: "number", value: current };
    if (typeof current === "boolean") return { type: "boolean", value: current };
    if (typeof current === "string") return { type: "string", value: current };
    if (Array.isArray(current) && current.length === 2 && current.every(Number.isFinite)) {
      return { type: "point", value: [current[0], current[1]] };
    }
    if (current && typeof current === "object" && Number.isFinite(current.x) && Number.isFinite(current.y)) {
      return { type: "point", value: [current.x, current.y] };
    }
    if (!current || typeof current !== "object" || !("value" in current)) break;
    current = current.value;
  }
  return { type: "unsupported", value: serializePresetProbeValue(value) };
}

function createHostValue(captured) {
  if (captured.type === "point") return new premiere.PointF(captured.value[0], captured.value[1]);
  if (["number", "boolean", "string"].includes(captured.type)) return captured.value;
  throw new Error(`Unsupported captured value type: ${captured.type}`);
}

function decodeXmlText(value) {
  return String(value || "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'").replace(/&amp;/g, "&")
    .replace(/&#(\d+);/g, (_, number) => String.fromCharCode(Number(number)))
    .replace(/&#x([0-9a-f]+);/gi, (_, number) => String.fromCharCode(parseInt(number, 16)));
}

function parseXmlTree(xml) {
  const root = { tagName: "#document", attributes: {}, children: [], text: "" };
  const stack = [root];
  const tokens = String(xml).match(/<!\[CDATA\[[\s\S]*?\]\]>|<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<[^>]+>|[^<]+/g) || [];
  tokens.forEach((token) => {
    if (token.startsWith("<?") || token.startsWith("<!--") || /^<!DOCTYPE/i.test(token)) return;
    if (token.startsWith("<![CDATA[")) {
      stack[stack.length - 1].text += token.slice(9, -3);
      return;
    }
    if (token.startsWith("</")) { if (stack.length > 1) stack.pop(); return; }
    if (token.startsWith("<")) {
      const selfClosing = /\/\s*>$/.test(token);
      const content = token.slice(1, selfClosing ? token.lastIndexOf("/") : -1).trim();
      const nameMatch = content.match(/^([^\s/>]+)/);
      if (!nameMatch || nameMatch[1].startsWith("!")) return;
      const node = { tagName: nameMatch[1], attributes: {}, children: [], text: "" };
      const attributeText = content.slice(nameMatch[0].length);
      const attributePattern = /([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
      let match;
      while ((match = attributePattern.exec(attributeText))) node.attributes[match[1]] = decodeXmlText(match[2] !== undefined ? match[2] : match[3]);
      stack[stack.length - 1].children.push(node);
      if (!selfClosing) stack.push(node);
      return;
    }
    stack[stack.length - 1].text += decodeXmlText(token);
  });
  return root;
}

function xmlDescendants(node, tagName, output) {
  const result = output || [];
  (node.children || []).forEach((child) => {
    if (!tagName || child.tagName === tagName) result.push(child);
    xmlDescendants(child, tagName, result);
  });
  return result;
}

function xmlChild(node, tagName) {
  return (node && node.children || []).find((child) => child.tagName === tagName) || null;
}

function xmlPath(node, path) {
  return path.reduce((current, tagName) => xmlChild(current, tagName), node);
}

function xmlText(node) {
  if (!node) return "";
  return (String(node.text || "") + (node.children || []).map(xmlText).join("")).trim();
}

function parsePrfpsetCatalog(xml) {
  const documentNode = parseXmlTree(xml);
  const objectIndex = {};
  xmlDescendants(documentNode).forEach((element) => {
    if (element.attributes.ObjectID) objectIndex[element.attributes.ObjectID] = element;
  });
  let rootBin = null;
  for (const bin of xmlDescendants(documentNode, "BinTreeItem")) {
    if (xmlText(xmlPath(bin, ["TreeItemBase", "Name"])) === "Presets") { rootBin = bin; break; }
  }
  if (!rootBin) throw new Error("The selected file contains no root Presets bin.");
  const presets = [];
  function traverse(binElement, categoryParts) {
    const itemsContainer = xmlChild(binElement, "Items");
    const items = itemsContainer ? Array.from(itemsContainer.children || []).filter((child) => child.tagName === "Item") : [];
    items.forEach((item) => {
      const element = objectIndex[item.attributes.ObjectRef];
      if (!element) return;
      if (element.tagName === "BinTreeItem") {
        traverse(element, categoryParts.concat(xmlText(xmlPath(element, ["TreeItemBase", "Name"])) || "?"));
        return;
      }
      if (element.tagName !== "TreeItem") return;
      const dataRef = xmlPath(element, ["TreeItemBase", "Data"]);
      const dataElement = dataRef ? objectIndex[dataRef.attributes.ObjectRef] : null;
      if (!dataElement) return;
      const filters = [];
      const filterPresets = xmlPath(dataElement, ["FilterPresets"]);
      (filterPresets ? filterPresets.children.filter((child) => child.tagName === "FilterPreset") : []).forEach((filterReference) => {
        const filterElement = objectIndex[filterReference.attributes.ObjectRef];
        if (!filterElement) return;
        const componentReference = xmlChild(filterElement, "Component");
        const componentElement = componentReference ? objectIndex[componentReference.attributes.ObjectRef] : null;
        if (!componentElement) return;
        const parameters = [];
        const paramsElement = xmlChild(componentElement, "Params");
        (paramsElement ? paramsElement.children.filter((child) => child.tagName === "Param") : []).forEach((parameterReference) => {
          const parameterElement = objectIndex[parameterReference.attributes.ObjectRef];
          if (!parameterElement) return;
          const startKeyframe = xmlText(xmlChild(parameterElement, "StartKeyframe"));
          const startParts = startKeyframe ? startKeyframe.split(",") : [];
          parameters.push({
            index: Number(parameterReference.attributes.Index),
            name: xmlText(xmlChild(parameterElement, "Name")) || null,
            parameterId: xmlText(xmlChild(parameterElement, "ParameterID")) || null,
            controlType: xmlText(xmlChild(parameterElement, "ParameterControlType")) || null,
            timeVarying: xmlText(xmlChild(parameterElement, "IsTimeVarying")) === "true",
            value: startParts.length > 1 ? startParts[1].trim() : null,
            startKeyframe,
            keyframes: xmlText(xmlChild(parameterElement, "Keyframes")) || null
          });
        });
        filters.push({
          matchName: xmlText(xmlChild(filterElement, "FilterMatchName")),
          displayName: xmlText(xmlChild(componentElement, "DisplayName")),
          mediaType: xmlText(xmlChild(filterElement, "MediaType")),
          parameters
        });
      });
      presets.push({
        name: xmlText(xmlPath(element, ["TreeItemBase", "Name"])) || "?",
        category: categoryParts.join(" > "),
        filters
      });
    });
  }
  traverse(rootBin, []);
  return presets;
}

async function importPrfpsetCatalog() {
  const { localFileSystem } = require("uxp").storage;
  const file = await localFileSystem.getFileForOpening({ types: ["prfpset"] });
  if (!file) throw new Error("No .prfpset file was selected.");
  const xml = await file.read();
  const presets = parsePrfpsetCatalog(xml);
  importedEffectPresetCatalog = { schemaVersion: 1, fileName: file.name, presets };
  const transformPresets = presets.filter((preset) => preset.filters.some((filter) => filter.matchName === "AE.ADBE Geometry2"));
  return {
    fileName: file.name,
    presetCount: presets.length,
    filterCount: presets.reduce((total, preset) => total + preset.filters.length, 0),
    transformPresetCount: transformPresets.length,
    transformPresets: transformPresets.slice(0, 200).map((preset) => ({
      name: preset.name,
      category: preset.category,
      transformParameterCount: preset.filters.find((filter) => filter.matchName === "AE.ADBE Geometry2").parameters.length
    })),
    truncated: transformPresets.length > 200,
    catalogStorage: "in-memory-until-plugin-reload",
    mutation: "none"
  };
}

async function captureTransformCurveReference() {
  const matchName = "AE.ADBE Geometry2";
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before capturing a reference.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before capturing a reference.");
  const clip = await getSingleSelectedVideoClip(sequence);
  const chain = await clip.getComponentChain();
  const resolved = await findLastComponentByMatchName(chain, matchName);
  if (!resolved) throw new Error("Apply a Transform effect/preset manually to the selected reference clip first.");
  const fps = await getSequenceFramesPerSecond(sequence);
  const parameterCount = await resolved.component.getParamCount();
  const parameters = [];
  for (let parameterIndex = 0; parameterIndex < parameterCount; parameterIndex += 1) {
    const parameter = await resolved.component.getParam(parameterIndex);
    const timeVarying = await parameter.isTimeVarying();
    const times = timeVarying ? Array.from(await parameter.getKeyframeListAsTickTimes()) : [];
    if (timeVarying && times.length >= 2) {
      const firstTime = times[0];
      const lastTime = times[times.length - 1];
      const durationSeconds = lastTime.seconds - firstTime.seconds;
      const segmentCount = Math.max(1, Math.min(1200, Math.round(durationSeconds * fps)));
      const samples = [];
      for (let frame = 0; frame <= segmentCount; frame += 1) {
        const offsetSeconds = frame === segmentCount ? durationSeconds : Math.min(durationSeconds, frame / fps);
        const time = firstTime.add(premiere.TickTime.createWithSeconds(offsetSeconds));
        const value = normalizeCapturedValue(await parameter.getValueAtTime(time));
        if (value.type === "unsupported") throw new Error(`Unsupported animated value at Transform parameter ${parameterIndex}.`);
        samples.push({ offsetSeconds, value });
      }
      parameters.push({ index: parameterIndex, displayName: parameter.displayName || null, mode: "animated", durationSeconds, samples });
    } else {
      const value = normalizeCapturedValue(await parameter.getStartValue());
      parameters.push({ index: parameterIndex, displayName: parameter.displayName || null, mode: "static", value });
    }
  }
  capturedTransformCurveReference = {
    schemaVersion: 2, matchName, fps, parameters
  };
  const animated = parameters.filter((item) => item.mode === "animated");
  const unsupported = parameters.filter((item) => item.mode === "static" && item.value.type === "unsupported");
  return {
    captured: true,
    sourceClipName: await clip.getName(),
    componentIndex: resolved.index,
    matchName,
    parameterCount,
    staticParameterCount: parameters.length - animated.length,
    animatedParameterCount: animated.length,
    unsupportedStaticParameters: unsupported.map((item) => ({ index: item.index, displayName: item.displayName, value: item.value.value })),
    fps,
    parameters: parameters.map((item) => item.mode === "animated"
      ? { index: item.index, displayName: item.displayName, mode: item.mode, durationSeconds: item.durationSeconds, sampleCount: item.samples.length, valueType: item.samples[0].value.type }
      : { index: item.index, displayName: item.displayName, mode: item.mode, valueType: item.value.type, value: item.value.value }),
    mutation: "none",
    captureScope: "in-memory-until-plugin-reload"
  };
}

async function applyTransformCurveReference() {
  if (!capturedTransformCurveReference) throw new Error("Capture the manually applied reference curve first.");
  const reference = capturedTransformCurveReference;
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before applying the reference.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before applying the reference.");
  const target = await getSingleSelectedVideoClip(sequence);
  const targetDuration = await target.getDuration();
  const longestDuration = Math.max(0, ...reference.parameters.filter((item) => item.mode === "animated").map((item) => item.durationSeconds));
  if (targetDuration.seconds < longestDuration) throw new Error("The target clip is shorter than the longest captured Transform curve.");
  const targetInPoint = await target.getInPoint();
  const chain = await target.getComponentChain();
  const componentIndex = await chain.getComponentCount();
  const created = await premiere.VideoFilterFactory.createComponent(reference.matchName);
  let insertionTransactionSucceeded = false;
  project.lockedAccess(() => {
    insertionTransactionSucceeded = project.executeTransaction((compound) => {
      compound.addAction(chain.createAppendComponentAction(created));
    }, "FX.palette: Insert captured Transform reference");
  });
  if (!insertionTransactionSucceeded) throw new Error("Premiere rejected the Transform insertion.");
  const component = await chain.getComponentAtIndex(componentIndex);
  const preparedParameters = [];
  const skippedParameters = [];
  for (const capturedParameter of reference.parameters) {
    if (capturedParameter.mode === "static" && capturedParameter.value.type === "unsupported") {
      skippedParameters.push({ index: capturedParameter.index, reason: "unsupported-static-value-type" });
      continue;
    }
    const parameter = await component.getParam(capturedParameter.index);
    if (capturedParameter.mode === "static") {
      const keyframe = await parameter.createKeyframe(createHostValue(capturedParameter.value));
      preparedParameters.push({ capturedParameter, parameter, staticKeyframe: keyframe, animatedKeyframes: [] });
    } else {
      const animatedKeyframes = [];
      for (const sample of capturedParameter.samples) {
        const keyframe = await parameter.createKeyframe(createHostValue(sample.value));
        keyframe.position = targetInPoint.add(premiere.TickTime.createWithSeconds(sample.offsetSeconds));
        await keyframe.setTemporalInterpolationMode(premiere.Constants.InterpolationMode.LINEAR);
        animatedKeyframes.push(keyframe);
      }
      preparedParameters.push({ capturedParameter, parameter, staticKeyframe: null, animatedKeyframes });
    }
  }
  let curveTransactionSucceeded = false;
  project.lockedAccess(() => {
    curveTransactionSucceeded = project.executeTransaction((compound) => {
      preparedParameters.forEach((entry) => {
        if (entry.capturedParameter.mode === "static") {
          compound.addAction(entry.parameter.createSetValueAction(entry.staticKeyframe, false));
        } else {
          compound.addAction(entry.parameter.createSetTimeVaryingAction(true));
          entry.animatedKeyframes.forEach((keyframe) => compound.addAction(entry.parameter.createAddKeyframeAction(keyframe)));
        }
      });
    }, "FX.palette: Apply captured Transform curve");
  });
  if (!curveTransactionSucceeded) throw new Error("Premiere rejected the captured curve transaction.");
  const verification = [];
  for (const entry of preparedParameters) {
    verification.push({
      index: entry.capturedParameter.index,
      displayName: entry.capturedParameter.displayName,
      mode: entry.capturedParameter.mode,
      requestedKeyframeCount: entry.animatedKeyframes.length,
      resultingKeyframeCount: entry.capturedParameter.mode === "animated"
        ? Array.from(await entry.parameter.getKeyframeListAsTickTimes()).length
        : 0,
      resultingStartValue: serializePresetProbeValue(await entry.parameter.getStartValue())
    });
  }
  return {
    targetClipName: await target.getName(), matchName: reference.matchName,
    componentIndex, sourceFramesPerSecond: reference.fps, longestSourceCurveSeconds: longestDuration,
    capturedParameterCount: reference.parameters.length,
    appliedParameterCount: preparedParameters.length,
    skippedParameters,
    verification,
    insertionTransactionSucceeded, curveTransactionSucceeded,
    reproductionModel: "frame-sampled-reference-values",
    undoModelExpected: ["Undo sampled Transform curve", "Undo inserted Transform effect"]
  };
}

async function probeVideoEffectParameters(action) {
  const requestedMatchName = typeof action.payload.matchName === "string"
    ? action.payload.matchName.trim()
    : "";
  if (!requestedMatchName) throw new Error("A video-effect match name is required.");

  const availableMatchNames = await premiere.VideoFilterFactory.getMatchNames();
  if (!availableMatchNames.includes(requestedMatchName)) {
    throw new Error("Video-effect match name was not found in the official runtime catalog.");
  }

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before probing effect parameters.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before probing effect parameters.");

  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const videoMediaTypes = new Set();
  const videoTrackCount = await sequence.getVideoTrackCount();
  for (let index = 0; index < videoTrackCount; index += 1) {
    const track = await sequence.getVideoTrack(index);
    videoMediaTypes.add(guidToString(await track.getMediaType()));
  }
  const selectedVideoClips = [];
  for (const item of Array.isArray(selectedItems) ? selectedItems : []) {
    if (videoMediaTypes.has(guidToString(await item.getMediaType()))) selectedVideoClips.push(item);
  }
  if (selectedVideoClips.length !== 1) {
    throw new Error("Select exactly one video clip for the parameter probe.");
  }

  const clip = selectedVideoClips[0];
  const chain = await clip.getComponentChain();
  const componentCountBefore = await chain.getComponentCount();
  const createdComponent = await premiere.VideoFilterFactory.createComponent(requestedMatchName);
  let transactionSucceeded = false;
  project.lockedAccess(() => {
    const appendAction = chain.createAppendComponentAction(createdComponent);
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(appendAction);
    }, `FX.palette: Probe parameters for ${requestedMatchName}`);
  });
  if (!transactionSucceeded) throw new Error("Premiere rejected the parameter-probe insertion transaction.");

  const componentCountAfter = await chain.getComponentCount();
  if (componentCountAfter !== componentCountBefore + 1) {
    throw new Error("Effect insertion could not be verified before parameter inspection.");
  }
  const component = await chain.getComponentAtIndex(componentCountBefore);
  const parameterCount = await component.getParamCount();
  const parameters = [];
  for (let index = 0; index < parameterCount; index += 1) {
    const parameter = await component.getParam(index);
    const entry = {
      index,
      displayName: parameter.displayName || null,
      keyframesSupported: null,
      timeVarying: null,
      startValue: null,
      startValueReadSucceeded: false
    };
    try { entry.keyframesSupported = await parameter.areKeyframesSupported(); } catch (error) {
      entry.keyframeSupportError = error && error.message ? error.message : String(error);
    }
    try { entry.timeVarying = await parameter.isTimeVarying(); } catch (error) {
      entry.timeVaryingError = error && error.message ? error.message : String(error);
    }
    try {
      entry.startValue = serializePresetProbeValue(await parameter.getStartValue());
      entry.startValueReadSucceeded = true;
    } catch (error) {
      entry.startValueError = error && error.message ? error.message : String(error);
    }
    parameters.push(entry);
  }

  return {
    clipName: typeof clip.getName === "function" ? await clip.getName() : null,
    requestedMatchName,
    verifiedMatchName: await component.getMatchName(),
    displayName: await component.getDisplayName(),
    componentIndex: componentCountBefore,
    componentCountBefore,
    componentCountAfter,
    parameterCount,
    parameters,
    mutation: "effect-appended-for-inspection",
    undoModelExpected: ["Undo parameter-probe effect insertion"]
  };
}

async function probeStaticVideoEffectParameter(action) {
  const matchName = typeof action.payload.matchName === "string" ? action.payload.matchName.trim() : "";
  const parameterIndex = Number(action.payload.parameterIndex);
  const requestedValue = Number(action.payload.value);
  if (!matchName) throw new Error("A video-effect match name is required.");
  if (!Number.isInteger(parameterIndex) || parameterIndex < 0) {
    throw new Error("Parameter index must be a non-negative integer.");
  }
  if (!Number.isFinite(requestedValue)) throw new Error("This probe requires a finite numeric value.");
  const availableMatchNames = await premiere.VideoFilterFactory.getMatchNames();
  if (!availableMatchNames.includes(matchName)) {
    throw new Error("Video-effect match name was not found in the official runtime catalog.");
  }

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before probing a static parameter.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before probing a static parameter.");
  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const videoMediaTypes = new Set();
  const videoTrackCount = await sequence.getVideoTrackCount();
  for (let index = 0; index < videoTrackCount; index += 1) {
    const track = await sequence.getVideoTrack(index);
    videoMediaTypes.add(guidToString(await track.getMediaType()));
  }
  const selectedVideoClips = [];
  for (const item of Array.isArray(selectedItems) ? selectedItems : []) {
    if (videoMediaTypes.has(guidToString(await item.getMediaType()))) selectedVideoClips.push(item);
  }
  if (selectedVideoClips.length !== 1) {
    throw new Error("Select exactly one video clip for the static-value probe.");
  }

  const clip = selectedVideoClips[0];
  const chain = await clip.getComponentChain();
  const componentCountBefore = await chain.getComponentCount();
  const createdComponent = await premiere.VideoFilterFactory.createComponent(matchName);
  const preInsertionParameterSurfaceAvailable = Boolean(
    createdComponent && typeof createdComponent.getParam === "function"
  );
  let insertionTransactionSucceeded = false;
  project.lockedAccess(() => {
    const appendAction = chain.createAppendComponentAction(createdComponent);
    insertionTransactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(appendAction);
    }, `FX.palette: Insert effect for static parameter probe`);
  });
  if (!insertionTransactionSucceeded) throw new Error("Premiere rejected the effect insertion transaction.");

  const component = await chain.getComponentAtIndex(componentCountBefore);
  const parameterCount = await component.getParamCount();
  if (parameterIndex >= parameterCount) {
    throw new Error(`Parameter index ${parameterIndex} is outside the component's ${parameterCount} parameters.`);
  }
  const parameter = await component.getParam(parameterIndex);
  const valueBefore = serializePresetProbeValue(await parameter.getStartValue());
  const keyframe = await parameter.createKeyframe(requestedValue);
  let valueTransactionSucceeded = false;
  project.lockedAccess(() => {
    const setValueAction = parameter.createSetValueAction(keyframe, false);
    valueTransactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(setValueAction);
    }, `FX.palette: Set static preset parameter`);
  });
  if (!valueTransactionSucceeded) throw new Error("Premiere rejected the static parameter transaction.");
  const valueAfter = serializePresetProbeValue(await parameter.getStartValue());

  return {
    clipName: typeof clip.getName === "function" ? await clip.getName() : null,
    matchName,
    displayName: await component.getDisplayName(),
    componentIndex: componentCountBefore,
    parameterIndex,
    parameterDisplayName: parameter.displayName || null,
    requestedValue,
    valueBefore,
    valueAfter,
    timeVaryingAfter: await parameter.isTimeVarying(),
    preInsertionParameterSurfaceAvailable,
    singleTransactionPreparationStatus: preInsertionParameterSurfaceAvailable
      ? "runtime-extension-detected-not-used"
      : "unavailable-on-documented-VideoFilterComponent-surface",
    insertionTransactionSucceeded,
    valueTransactionSucceeded,
    verificationRequiresHostValueReview: true,
    undoModelExpected: [
      "Undo static parameter value",
      "Undo diagnostic effect insertion"
    ]
  };
}

async function probeAnimatedVideoEffectParameter(action) {
  const matchName = typeof action.payload.matchName === "string" ? action.payload.matchName.trim() : "";
  const parameterIndex = Number(action.payload.parameterIndex);
  const firstValue = Number(action.payload.firstValue);
  const secondValue = Number(action.payload.secondValue);
  const interpolationName = typeof action.payload.interpolationName === "string"
    ? action.payload.interpolationName.trim().toUpperCase()
    : "DEFAULT";
  const interpolationNames = ["DEFAULT", "LINEAR", "HOLD", "BEZIER", "TIME", "TIME_TRANSITION_START", "TIME_TRANSITION_END"];
  const approximateBezier = action.payload.approximateBezier === true || action.payload.approximateBezier === "true";
  const sampleEveryFrame = action.payload.sampleEveryFrame === true || action.payload.sampleEveryFrame === "true";
  let approximationSamples = Math.max(3, Math.min(120, Number(action.payload.approximationSamples) || 30));
  const bezierControls = ["x1", "y1", "x2", "y2"].map((key) => Number(action.payload[key]));
  if (!matchName) throw new Error("A video-effect match name is required.");
  if (!Number.isInteger(parameterIndex) || parameterIndex < 0) throw new Error("Parameter index must be a non-negative integer.");
  if (!Number.isFinite(firstValue) || !Number.isFinite(secondValue)) throw new Error("Both keyframe values must be finite numbers.");
  if (!interpolationNames.includes(interpolationName)) throw new Error("Interpolation mode is not allowlisted.");
  if (approximateBezier && (interpolationName !== "BEZIER" || bezierControls.some((value) => !Number.isFinite(value)))) {
    throw new Error("Bezier approximation requires BEZIER mode and four finite control values.");
  }
  if (!(await premiere.VideoFilterFactory.getMatchNames()).includes(matchName)) throw new Error("Video-effect match name was not found in the official runtime catalog.");

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before probing animated parameters.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before probing animated parameters.");
  let sequenceFramesPerSecond = null;
  if (approximateBezier && sampleEveryFrame) {
    try {
      sequenceFramesPerSecond = await getSequenceFramesPerSecond(sequence);
      approximationSamples = Math.max(3, Math.min(120, Math.round(sequenceFramesPerSecond)));
    } catch (_) { /* Explicit segment count remains the safe fallback. */ }
  }
  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const videoMediaTypes = new Set();
  for (let index = 0; index < await sequence.getVideoTrackCount(); index += 1) {
    videoMediaTypes.add(guidToString(await (await sequence.getVideoTrack(index)).getMediaType()));
  }
  const clips = [];
  for (const item of Array.isArray(selectedItems) ? selectedItems : []) {
    if (videoMediaTypes.has(guidToString(await item.getMediaType()))) clips.push(item);
  }
  if (clips.length !== 1) throw new Error("Select exactly one video clip for the animated-value probe.");

  const clip = clips[0];
  const duration = await clip.getDuration();
  if (duration.seconds <= 1) throw new Error("Select a video clip longer than one second.");
  const chain = await clip.getComponentChain();
  const componentIndex = await chain.getComponentCount();
  const createdComponent = await premiere.VideoFilterFactory.createComponent(matchName);
  let insertionTransactionSucceeded = false;
  project.lockedAccess(() => {
    const actionToAdd = chain.createAppendComponentAction(createdComponent);
    insertionTransactionSucceeded = project.executeTransaction((compoundAction) => compoundAction.addAction(actionToAdd), "FX.palette: Insert effect for keyframe probe");
  });
  if (!insertionTransactionSucceeded) throw new Error("Premiere rejected the effect insertion transaction.");

  const component = await chain.getComponentAtIndex(componentIndex);
  const parameterCount = await component.getParamCount();
  if (parameterIndex >= parameterCount) throw new Error(`Parameter index ${parameterIndex} is outside the component's ${parameterCount} parameters.`);
  const parameter = await component.getParam(parameterIndex);
  if (!(await parameter.areKeyframesSupported())) throw new Error("The selected parameter does not support keyframes.");

  const clipInPoint = await clip.getInPoint();
  const firstTime = premiere.TickTime.createWithTicks(clipInPoint.ticks);
  const secondTime = clipInPoint.add(premiere.TickTime.createWithSeconds(1));
  const firstKeyframe = await parameter.createKeyframe(firstValue);
  const secondKeyframe = await parameter.createKeyframe(secondValue);
  firstKeyframe.position = firstTime;
  secondKeyframe.position = secondTime;
  let interpolationPreparation = null;
  if (interpolationName !== "DEFAULT") {
    const interpolationMode = approximateBezier
      ? premiere.Constants.InterpolationMode.LINEAR
      : premiere.Constants.InterpolationMode[interpolationName];
    interpolationPreparation = {
      method: "Keyframe.setTemporalInterpolationMode",
      firstSucceeded: await firstKeyframe.setTemporalInterpolationMode(interpolationMode),
      secondSucceeded: await secondKeyframe.setTemporalInterpolationMode(interpolationMode)
    };
  }
  const helperKeyframes = [];
  if (approximateBezier) {
    const [x1, y1, x2, y2] = bezierControls;
    for (let sample = 1; sample < approximationSamples; sample += 1) {
      const linearProgress = sample / approximationSamples;
      const easedProgress = cubicBezierProgress(linearProgress, x1, y1, x2, y2);
      const helper = await parameter.createKeyframe(firstValue + ((secondValue - firstValue) * easedProgress));
      helper.position = firstTime.add(premiere.TickTime.createWithSeconds(linearProgress));
      const interpolationSucceeded = await helper.setTemporalInterpolationMode(premiere.Constants.InterpolationMode.LINEAR);
      helperKeyframes.push({ helper, interpolationSucceeded });
    }
  }
  let keyframeTransactionSucceeded = false;
  project.lockedAccess(() => {
    const timeVaryingAction = parameter.createSetTimeVaryingAction(true);
    const firstAction = parameter.createAddKeyframeAction(firstKeyframe);
    const secondAction = parameter.createAddKeyframeAction(secondKeyframe);
    const helperActions = helperKeyframes.map((entry) => parameter.createAddKeyframeAction(entry.helper));
    keyframeTransactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(timeVaryingAction);
      compoundAction.addAction(firstAction);
      helperActions.forEach((helperAction) => compoundAction.addAction(helperAction));
      compoundAction.addAction(secondAction);
    }, "FX.palette: Add preset keyframes");
  });
  if (!keyframeTransactionSucceeded) throw new Error("Premiere rejected the keyframe transaction.");

  const keyframeTimes = await parameter.getKeyframeListAsTickTimes();
  const keyframes = [];
  for (const time of keyframeTimes) {
    const keyframe = await parameter.getKeyframePtr(time);
    keyframes.push({
      seconds: time.seconds,
      ticks: time.ticks,
      value: serializePresetProbeValue(await parameter.getValueAtTime(time)),
      interpolationMode: typeof keyframe.getTemporalInterpolationMode === "function"
        ? await keyframe.getTemporalInterpolationMode()
        : null
    });
  }
  return {
    clipName: await clip.getName(),
    matchName,
    displayName: await component.getDisplayName(),
    componentIndex,
    parameterIndex,
    parameterDisplayName: parameter.displayName || null,
    clipInPoint: { seconds: clipInPoint.seconds, ticks: clipInPoint.ticks },
    requestedKeyframes: [
      { seconds: firstTime.seconds, ticks: firstTime.ticks, value: firstValue },
      { seconds: secondTime.seconds, ticks: secondTime.ticks, value: secondValue }
    ],
    requestedInterpolationName: interpolationName,
    requestedInterpolationValue: interpolationName === "DEFAULT"
      ? null
      : premiere.Constants.InterpolationMode[interpolationName],
    interpolationPreparation,
    bezierApproximation: approximateBezier ? {
      strategy: "linear-helper-keyframes",
      controls: { x1: bezierControls[0], y1: bezierControls[1], x2: bezierControls[2], y2: bezierControls[3] },
      segmentSeconds: 1,
      requestedSamples: approximationSamples,
      sampleEveryFrame,
      detectedSequenceFramesPerSecond: sequenceFramesPerSecond,
      expectedHelperKeyframes: approximationSamples - 1,
      helperInterpolationPreparationSucceeded: helperKeyframes.every((entry) => entry.interpolationSucceeded === true),
      fidelity: "sampled-approximation-not-native-bezier-handles"
    } : null,
    runtimeInterpolationConstants: {
      LINEAR: premiere.Constants.InterpolationMode.LINEAR,
      HOLD: premiere.Constants.InterpolationMode.HOLD,
      BEZIER: premiere.Constants.InterpolationMode.BEZIER,
      TIME: premiere.Constants.InterpolationMode.TIME,
      TIME_TRANSITION_START: premiere.Constants.InterpolationMode.TIME_TRANSITION_START,
      TIME_TRANSITION_END: premiere.Constants.InterpolationMode.TIME_TRANSITION_END
    },
    timeVaryingAfter: await parameter.isTimeVarying(),
    keyframeCountAfter: keyframes.length,
    keyframes,
    insertionTransactionSucceeded,
    keyframeTransactionSucceeded,
    timingModelUnderTest: "clip-source-time-anchored-at-track-item-in-point",
    undoModelExpected: ["Undo time-varying state and both keyframes", "Undo diagnostic effect insertion"]
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

async function insertGenericItemAcrossSelection(action) {
  const videoTrackIndex = readNonNegativeTrackIndex(action.payload.videoTrackIndex, "Video track index");
  const audioTrackIndex = readNonNegativeTrackIndex(action.payload.audioTrackIndex, "Audio track index");
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before inserting a generic item.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before inserting a generic item.");

  const projectSelection = await premiere.ProjectUtils.getSelection(project);
  const projectItems = projectSelection ? await projectSelection.getItems() : [];
  if (!Array.isArray(projectItems) || projectItems.length !== 1) {
    throw new Error("Select exactly one generic item in the Project panel.");
  }
  const projectItem = projectItems[0];
  if (!projectItem || typeof projectItem.getId !== "function") {
    throw new Error("The selected Project item is not insertable.");
  }

  const timelineSelection = await sequence.getSelection();
  const timelineItems = timelineSelection ? await timelineSelection.getTrackItems() : [];
  if (!Array.isArray(timelineItems) || timelineItems.length === 0) {
    throw new Error("Select at least one video clip in the Timeline to define the duration.");
  }
  const videoMediaTypes = new Set();
  const videoTrackCount = await sequence.getVideoTrackCount();
  for (let index = 0; index < videoTrackCount; index += 1) {
    videoMediaTypes.add(guidToString(await (await sequence.getVideoTrack(index)).getMediaType()));
  }
  if (videoTrackIndex >= videoTrackCount) {
    throw new Error("The generic-item probe requires an existing destination video track.");
  }

  const selectedVideoItems = [];
  for (const trackItem of timelineItems) {
    if (!videoMediaTypes.has(guidToString(await trackItem.getMediaType()))) continue;
    selectedVideoItems.push({
      startTime: await trackItem.getStartTime(),
      endTime: await trackItem.getEndTime()
    });
  }
  if (selectedVideoItems.length === 0) {
    throw new Error("The Timeline selection contains no video clips.");
  }

  const rangeStart = selectedVideoItems.reduce((current, item) =>
    !current || item.startTime.seconds < current.seconds ? item.startTime : current
  , null);
  const rangeEnd = selectedVideoItems.reduce((current, item) =>
    !current || item.endTime.seconds > current.seconds ? item.endTime : current
  , null);
  if (!rangeStart || !rangeEnd || rangeEnd.seconds <= rangeStart.seconds) {
    throw new Error("The selected video range has no positive duration.");
  }

  const projectItemId = await projectItem.getId();
  const instancesBefore = await findProjectItemInstancesAtTime(sequence, projectItemId, rangeStart);
  const editor = premiere.SequenceEditor.getEditor(sequence);
  let clipProjectItem = null;
  let atomicActionOwner = null;
  let sourceInPoint = null;
  let sourceOutPoint = null;
  let atomicDurationPrepared = false;
  let atomicActionMethod = null;
  let atomicActionSource = null;
  let atomicActionMethodsAvailable = [];
  const atomicAttemptErrors = [];
  const atomicActionSurface = {
    projectItem: {
      getInPoint: typeof projectItem.getInPoint === "function",
      getOutPoint: typeof projectItem.getOutPoint === "function",
      createSetOutPointAction: typeof projectItem.createSetOutPointAction === "function",
      createSetInOutPointsAction: typeof projectItem.createSetInOutPointsAction === "function"
    },
    castClipProjectItem: null
  };
  try {
    clipProjectItem = premiere.ClipProjectItem.cast(projectItem);
    atomicActionSurface.castClipProjectItem = {
      getInPoint: typeof clipProjectItem.getInPoint === "function",
      getOutPoint: typeof clipProjectItem.getOutPoint === "function",
      createSetOutPointAction: typeof clipProjectItem.createSetOutPointAction === "function",
      createSetInOutPointsAction: typeof clipProjectItem.createSetInOutPointsAction === "function"
    };
  } catch (error) {
    clipProjectItem = null;
  }
  const atomicCandidates = [
    { source: "projectItem", value: projectItem },
    { source: "castClipProjectItem", value: clipProjectItem }
  ];
  for (const candidate of atomicCandidates) {
    if (!candidate.value || typeof candidate.value.getInPoint !== "function" || typeof candidate.value.getOutPoint !== "function") continue;
    const methods = [];
    if (typeof candidate.value.createSetOutPointAction === "function") methods.push("createSetOutPointAction");
    if (typeof candidate.value.createSetInOutPointsAction === "function") methods.push("createSetInOutPointsAction");
    if (methods.length === 0) continue;
    try {
      sourceInPoint = await candidate.value.getInPoint(premiere.Constants.MediaType.VIDEO);
      sourceOutPoint = await candidate.value.getOutPoint(premiere.Constants.MediaType.VIDEO);
      if (!sourceInPoint || !sourceOutPoint) continue;
      atomicActionOwner = candidate.value;
      atomicActionSource = candidate.source;
      atomicActionMethodsAvailable = methods;
      atomicDurationPrepared = true;
      break;
    } catch (error) {}
  }
  const rangeDuration = rangeEnd.subtract(rangeStart);
  const temporaryOutPoint = atomicDurationPrepared ? sourceInPoint.add(rangeDuration) : null;
  let insertionTransactionSucceeded = false;
  const executeInsertionTransaction = (method) => {
    project.lockedAccess(() => {
      const insertionAction = editor.createOverwriteItemAction(
        projectItem,
        rangeStart,
        videoTrackIndex,
        audioTrackIndex
      );
      let temporaryOutAction = null;
      let restoreOutAction = null;
      if (method === "createSetOutPointAction") {
        temporaryOutAction = atomicActionOwner.createSetOutPointAction(temporaryOutPoint);
        restoreOutAction = atomicActionOwner.createSetOutPointAction(sourceOutPoint);
      } else if (method === "createSetInOutPointsAction") {
        temporaryOutAction = atomicActionOwner.createSetInOutPointsAction(sourceInPoint, temporaryOutPoint);
        restoreOutAction = atomicActionOwner.createSetInOutPointsAction(sourceInPoint, sourceOutPoint);
      }
      insertionTransactionSucceeded = project.executeTransaction((compoundAction) => {
        if (temporaryOutAction) compoundAction.addAction(temporaryOutAction);
        compoundAction.addAction(insertionAction);
        if (restoreOutAction) compoundAction.addAction(restoreOutAction);
      }, `FX.palette: Insert generic item ${projectItem.name || ""}`);
    });
  };
  if (atomicDurationPrepared) {
    for (const method of atomicActionMethodsAvailable) {
      try {
        insertionTransactionSucceeded = false;
        executeInsertionTransaction(method);
        if (insertionTransactionSucceeded) {
          atomicActionMethod = method;
          break;
        }
        atomicAttemptErrors.push({ method, message: "Premiere rejected the transaction." });
      } catch (error) {
        atomicAttemptErrors.push({
          method,
          message: error && error.message ? error.message : String(error)
        });
      }
    }
  }
  if (!insertionTransactionSucceeded) {
    atomicDurationPrepared = false;
    atomicActionMethod = null;
    atomicActionSource = null;
    executeInsertionTransaction(null);
  }
  if (!insertionTransactionSucceeded) throw new Error("Premiere rejected the generic-item insertion transaction.");

  const destinationTrack = await sequence.getVideoTrack(videoTrackIndex);
  const destinationItems = await destinationTrack.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
  let insertedTrackItem = null;
  for (const trackItem of destinationItems) {
    const startTime = await trackItem.getStartTime();
    if (String(startTime.ticks) !== String(rangeStart.ticks)) continue;
    const linkedProjectItem = await trackItem.getProjectItem();
    if (linkedProjectItem && await linkedProjectItem.getId() === projectItemId) {
      insertedTrackItem = trackItem;
      break;
    }
  }
  if (!insertedTrackItem) {
    throw new Error("The item was inserted, but its video TrackItem could not be resolved for duration matching.");
  }

  const endBefore = await insertedTrackItem.getEndTime();
  const isAdjustmentLayer = typeof insertedTrackItem.isAdjustmentLayer === "function"
    ? await insertedTrackItem.isAdjustmentLayer()
    : null;
  const atomicDurationSucceeded = String(endBefore.ticks) === String(rangeEnd.ticks);
  let durationTransactionSucceeded = false;
  if (!atomicDurationSucceeded) {
    project.lockedAccess(() => {
      const setEndAction = insertedTrackItem.createSetEndAction(
        premiere.TickTime.createWithTicks(String(rangeEnd.ticks))
      );
      durationTransactionSucceeded = project.executeTransaction((compoundAction) => {
        compoundAction.addAction(setEndAction);
      }, `FX.palette: Match generic item duration ${projectItem.name || ""}`);
    });
    if (!durationTransactionSucceeded) {
      throw new Error("The item was inserted, but Premiere rejected its duration-matching transaction.");
    }
  }

  const endAfter = await insertedTrackItem.getEndTime();
  let sourceOutPointAfter = null;
  if (atomicActionOwner && sourceOutPoint) {
    try {
      sourceOutPointAfter = await atomicActionOwner.getOutPoint(premiere.Constants.MediaType.VIDEO);
    } catch (error) {
      sourceOutPointAfter = null;
    }
  }
  const instancesAfter = await findProjectItemInstancesAtTime(sequence, projectItemId, rangeStart);
  return {
    projectItem: { id: projectItemId, name: projectItem.name || null, type: projectItem.type },
    selectedVideoItemCount: selectedVideoItems.length,
    selectedRange: {
      startSeconds: rangeStart.seconds,
      startTicks: rangeStart.ticks,
      endSeconds: rangeEnd.seconds,
      endTicks: rangeEnd.ticks
    },
    requestedTracks: { videoTrackIndex, audioTrackIndex },
    insertedTrackItem: {
      name: await insertedTrackItem.getName(),
      isAdjustmentLayer,
      endBeforeSeconds: endBefore.seconds,
      endBeforeTicks: endBefore.ticks,
      endAfterSeconds: endAfter.seconds,
      endAfterTicks: endAfter.ticks
    },
    matchingInstanceCountBefore: instancesBefore.length,
    matchingInstanceCountAfter: instancesAfter.length,
    insertionTransactionSucceeded,
    atomicDurationPrepared,
    atomicActionMethod,
    atomicActionSource,
    atomicActionMethodsAvailable,
    atomicAttemptErrors,
    atomicActionSurface,
    atomicDurationSucceeded,
    projectItemOutPointRestored: sourceOutPoint && sourceOutPointAfter
      ? String(sourceOutPoint.ticks) === String(sourceOutPointAfter.ticks)
      : null,
    durationTransactionSucceeded,
    verificationSucceeded: String(endAfter.ticks) === String(rangeEnd.ticks),
    durationStrategy: atomicDurationSucceeded ? "temporary-project-item-out-point" : "post-insertion-track-item-fallback",
    undoModelExpected: atomicDurationSucceeded
      ? ["Undo generic item insertion and duration"]
      : ["Undo duration matching", "Undo generic item insertion"]
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

async function runProbeVideoEffectParameters() {
  const button = document.getElementById("probe-video-effect-parameters");
  const input = document.getElementById("video-effect-match-name");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute(
    {
      type: "timeline.probeVideoEffectParameters",
      requestId: String(Date.now()),
      payload: { matchName: input ? input.value : "" }
    },
    { "timeline.probeVideoEffectParameters": probeVideoEffectParameters }
  );
  text("effect-parameter-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runProbeStaticVideoEffectParameter() {
  const button = document.getElementById("probe-static-video-effect-parameter");
  const matchNameInput = document.getElementById("video-effect-match-name");
  const parameterIndexInput = document.getElementById("static-parameter-index");
  const valueInput = document.getElementById("static-parameter-value");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute(
    {
      type: "timeline.probeStaticVideoEffectParameter",
      requestId: String(Date.now()),
      payload: {
        matchName: matchNameInput ? matchNameInput.value : "",
        parameterIndex: parameterIndexInput ? parameterIndexInput.value : "0",
        value: valueInput ? valueInput.value : "20"
      }
    },
    { "timeline.probeStaticVideoEffectParameter": probeStaticVideoEffectParameter }
  );
  text("effect-parameter-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runProbeAnimatedVideoEffectParameter() {
  const button = document.getElementById("probe-animated-video-effect-parameter");
  const matchNameInput = document.getElementById("video-effect-match-name");
  const parameterIndexInput = document.getElementById("static-parameter-index");
  const firstValueInput = document.getElementById("animated-first-value");
  const secondValueInput = document.getElementById("animated-second-value");
  const interpolationInput = document.getElementById("animated-interpolation-mode");
  const approximationInput = document.getElementById("approximate-bezier-easing");
  const approximationSamplesInput = document.getElementById("bezier-approximation-samples");
  const sampleEveryFrameInput = document.getElementById("bezier-sample-every-frame");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.probeAnimatedVideoEffectParameter",
    requestId: String(Date.now()),
    payload: {
      matchName: matchNameInput ? matchNameInput.value : "",
      parameterIndex: parameterIndexInput ? parameterIndexInput.value : "0",
      firstValue: firstValueInput ? firstValueInput.value : "10",
      secondValue: secondValueInput ? secondValueInput.value : "20",
      interpolationName: interpolationInput ? interpolationInput.value : "DEFAULT",
      approximateBezier: approximationInput ? approximationInput.checked : false,
      approximationSamples: approximationSamplesInput ? approximationSamplesInput.value : "30",
      sampleEveryFrame: sampleEveryFrameInput ? sampleEveryFrameInput.checked : true,
      x1: "0.625",
      y1: "0",
      x2: "0.375",
      y2: "1"
    }
  }, { "timeline.probeAnimatedVideoEffectParameter": probeAnimatedVideoEffectParameter });
  text("effect-parameter-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runCaptureTransformCurveReference() {
  const button = document.getElementById("capture-transform-reference");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.captureTransformCurveReference", requestId: String(Date.now()), payload: {}
  }, { "timeline.captureTransformCurveReference": captureTransformCurveReference });
  text("transform-reference-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runApplyTransformCurveReference() {
  const button = document.getElementById("apply-transform-reference");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.applyTransformCurveReference", requestId: String(Date.now()), payload: {}
  }, { "timeline.applyTransformCurveReference": applyTransformCurveReference });
  text("transform-reference-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runImportPrfpsetCatalog() {
  const button = document.getElementById("import-prfpset-catalog");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "catalog.effectPresets.importPrfpset", requestId: String(Date.now()), payload: {}
  }, { "catalog.effectPresets.importPrfpset": importPrfpsetCatalog });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
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

async function runInsertGenericItem() {
  const button = document.getElementById("insert-generic-item");
  const videoTrackInput = document.getElementById("project-item-video-track");
  const audioTrackInput = document.getElementById("project-item-audio-track");
  if (button) button.disabled = true;

  const result = await executionAdapter.execute(
    {
      type: "timeline.insertGenericItem",
      requestId: String(Date.now()),
      payload: {
        videoTrackIndex: videoTrackInput ? videoTrackInput.value : "0",
        audioTrackIndex: audioTrackInput ? audioTrackInput.value : "0"
      }
    },
    { "timeline.insertGenericItem": insertGenericItemAcrossSelection }
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
  const effectParameterButton = document.getElementById("probe-video-effect-parameters");
  if (effectParameterButton && !effectParameterButton.dataset.wired) {
    effectParameterButton.addEventListener("click", runProbeVideoEffectParameters);
    effectParameterButton.dataset.wired = "true";
  }
  const staticParameterButton = document.getElementById("probe-static-video-effect-parameter");
  if (staticParameterButton && !staticParameterButton.dataset.wired) {
    staticParameterButton.addEventListener("click", runProbeStaticVideoEffectParameter);
    staticParameterButton.dataset.wired = "true";
  }
  const animatedParameterButton = document.getElementById("probe-animated-video-effect-parameter");
  if (animatedParameterButton && !animatedParameterButton.dataset.wired) {
    animatedParameterButton.addEventListener("click", runProbeAnimatedVideoEffectParameter);
    animatedParameterButton.dataset.wired = "true";
  }
  const captureTransformButton = document.getElementById("capture-transform-reference");
  if (captureTransformButton && !captureTransformButton.dataset.wired) {
    captureTransformButton.addEventListener("click", runCaptureTransformCurveReference);
    captureTransformButton.dataset.wired = "true";
  }
  const applyTransformButton = document.getElementById("apply-transform-reference");
  if (applyTransformButton && !applyTransformButton.dataset.wired) {
    applyTransformButton.addEventListener("click", runApplyTransformCurveReference);
    applyTransformButton.dataset.wired = "true";
  }
  const importPrfpsetButton = document.getElementById("import-prfpset-catalog");
  if (importPrfpsetButton && !importPrfpsetButton.dataset.wired) {
    importPrfpsetButton.addEventListener("click", runImportPrfpsetCatalog);
    importPrfpsetButton.dataset.wired = "true";
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
  const insertGenericItemButton = document.getElementById("insert-generic-item");
  if (insertGenericItemButton && !insertGenericItemButton.dataset.wired) {
    insertGenericItemButton.addEventListener("click", runInsertGenericItem);
    insertGenericItemButton.dataset.wired = "true";
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

async function runHeadlessSetVioletLabelCommand() {
  const result = await executionAdapter.execute(
    {
      type: "projectItems.setColorLabel",
      requestId: String(Date.now()),
      payload: { labelName: "VIOLET" }
    },
    { "projectItems.setColorLabel": setSelectedProjectItemLabel }
  );
  console.log("FX.palette headless command result:", JSON.stringify(result));
  return result;
}

entrypoints.setup({
  commands: {
    headlessSetVioletLabel: runHeadlessSetVioletLabelCommand
  },
  panels: {
    effectPaletteDiagnostics: {
      create() { wirePanel(); },
      show() { wirePanel(); }
    }
  }
});
