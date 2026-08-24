"use strict";

const { entrypoints, host, versions } = require("uxp");
const premiere = require("premierepro");
const executionAdapter = require("./execution-adapter.js");
const transport = require("./transport.js");
let capturedTransformCurveReference = null;
let importedEffectPresetCatalog = null;
let importedEffectPresetXml = null;
// A persistent token (require("uxp").storage) survives plugin reloads, so the user picks the
// .prfpset file once instead of every session - the zero-configuration bar the transport is held to.
const PRESET_CATALOG_TOKEN_KEY = "fxpalette.importedPresetCatalog.persistentToken";

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

async function describeTrackItem(item, audioMediaTypes) {
  const projectItem = typeof item.getProjectItem === "function" ? await item.getProjectItem() : null;
  const mediaType = typeof item.getMediaType === "function" ? guidToString(await item.getMediaType()) : null;
  return {
    name: typeof item.getName === "function" ? await item.getName() : null,
    type: typeof item.getType === "function" ? await item.getType() : null,
    trackIndex: typeof item.getTrackIndex === "function" ? await item.getTrackIndex() : null,
    mediaType,
    // Same classification every apply-effect/transition handler already does (compare against the
    // sequence's own audio-track media types) - just exposed here as a read instead of a side effect.
    isAudio: audioMediaTypes instanceof Set ? audioMediaTypes.has(mediaType) : null,
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
  // 30 bisection steps resolve the parameter below float32 storage precision, so the solver stops
  // being a measurable error source next to the host's own values.
  for (let index = 0; index < 30; index += 1) {
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

async function getSelectedVideoClips(sequence) {
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
  if (clips.length === 0) throw new Error("Select at least one video clip.");
  return clips;
}

async function getSingleSelectedVideoClip(sequence) {
  const clips = await getSelectedVideoClips(sequence);
  if (clips.length !== 1) throw new Error("Select exactly one video clip.");
  return clips[0];
}

async function getSelectedAudioClips(sequence) {
  const selection = await sequence.getSelection();
  const selectedItems = selection ? await selection.getTrackItems() : [];
  const audioMediaTypes = new Set();
  for (let index = 0; index < await sequence.getAudioTrackCount(); index += 1) {
    audioMediaTypes.add(guidToString(await (await sequence.getAudioTrack(index)).getMediaType()));
  }
  const clips = [];
  for (const item of Array.isArray(selectedItems) ? selectedItems : []) {
    if (audioMediaTypes.has(guidToString(await item.getMediaType()))) clips.push(item);
  }
  if (clips.length === 0) throw new Error("Select at least one audio clip.");
  return clips;
}

// Premiere's .prfpset serializes one AudioFilterComponent per channel configuration an audio effect
// instance could run under (mono/stereo/5.1/...), not one per logical application: three entries
// with the same identity are three variants of one effect, not three effects to apply. The official
// AudioFilterFactory.createComponentByDisplayName(displayName, item) is channel-aware from the
// target item and only ever needs one component, matching the single component the already-tested
// timeline.applyAudioEffect probe produces per clip - so reconstruction must collapse these variants
// back to one before creating anything, never apply all of them.
//
// Which variant is authoritative cannot be decided by matching the target's actual channel count:
// the official UXP surface has no channel-count accessor on AudioClipTrackItem (confirmed absent
// from the reference and from a documented gap versus ExtendScript's getAudioChannelMapping()).
// Host evidence instead points to file order: PRESET TEST + DISTORTION's first-listed variant
// (FilterPreset Index 0) was the one a manual drag onto the actual (stereo) source clip reproduced,
// while the later-listed mono/5.1 variants held unrelated values. A majority-vote-by-value heuristic
// tried first picked those later variants instead and was measurably wrong; first-in-file-order is
// this project's current best explanation, not a confirmed general rule.
function dedupeAudioFilterVariants(filters) {
  const seenMatchNames = new Set();
  return filters.filter((filter) => {
    if (!filter.isAudio) return true;
    if (seenMatchNames.has(filter.matchName)) return false;
    seenMatchNames.add(filter.matchName);
    return true;
  });
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
        // Video components nest their common Component payload one level deep
        // (VideoFilterComponent > Component). Audio components nest it one level deeper still, inside
        // AudioComponent (AudioFilterComponent > AudioComponent > Component); AudioComponent itself
        // has no DisplayName/Params of its own, so resolving only the outer level silently returns an
        // empty component - the tag name is the reliable signal, not a MediaType GUID.
        const isAudio = componentElement.tagName === "AudioFilterComponent";
        const componentPayload = isAudio
          ? xmlChild(xmlChild(componentElement, "AudioComponent"), "Component") || componentElement
          : xmlChild(componentElement, "Component") || componentElement;
        const parameters = [];
        const paramsElement = xmlChild(componentPayload, "Params");
        (paramsElement ? paramsElement.children.filter((child) => child.tagName === "Param") : []).forEach((parameterReference) => {
          const parameterElement = objectIndex[parameterReference.attributes.ObjectRef];
          if (!parameterElement) return;
          // ArbVideoComponentParam (observed on Lumetri Color's curve/LUT/HSL-secondary controls)
          // serializes its value as a separate base64 StartKeyframeValue element rather than the
          // comma-separated StartKeyframe text every other parameter type uses. Reading StartKeyframe
          // on one of these returns nothing, and Number("") coerces to 0 in JS - silently turning a
          // parameter we cannot represent into a wrong static value instead of failing closed. This
          // is marked explicitly instead, so callers can refuse it outright.
          const isArbitrary = parameterElement.tagName === "ArbVideoComponentParam";
          const startKeyframe = xmlText(xmlChild(parameterElement, "StartKeyframe"));
          const startParts = startKeyframe ? startKeyframe.split(",") : [];
          parameters.push({
            index: Number(parameterReference.attributes.Index),
            name: xmlText(xmlChild(parameterElement, "Name")) || null,
            parameterId: xmlText(xmlChild(parameterElement, "ParameterID")) || null,
            controlType: xmlText(xmlChild(parameterElement, "ParameterControlType")) || null,
            timeVarying: xmlText(xmlChild(parameterElement, "IsTimeVarying")) === "true",
            value: isArbitrary ? null : (startParts.length > 1 ? startParts[1].trim() : null),
            startKeyframe,
            keyframes: xmlText(xmlChild(parameterElement, "Keyframes")) || null,
            arbitrary: isArbitrary
          });
        });
        // FilterPreset's own AnchorInPoint is the shared origin every filter in the preset was
        // captured against. Different parameters/filters can start their animation at different
        // offsets from it, so it - not a parameter's own first key - is the correct zero point for
        // reconstructing relative timing across more than one animated parameter.
        const anchorInPointTicks = Number(xmlText(xmlChild(filterElement, "AnchorInPoint"))) || 0;
        // Premiere serializes one AudioFilterComponent per channel configuration an audio effect
        // instance could run under (mono/stereo/5.1/...), not one per logical application; this is
        // parsed only for diagnostic visibility, since reconstruction collapses these back to one
        // component (see dedupeAudioFilterVariants).
        let audioChannelCount = null;
        if (isAudio) {
          try {
            const parsedChannelConfig = JSON.parse(xmlText(xmlChild(componentElement, "ChannelConfigData")));
            const inputLayout = parsedChannelConfig && Array.isArray(parsedChannelConfig.in) ? parsedChannelConfig.in[0] : null;
            audioChannelCount = inputLayout && Array.isArray(inputLayout.layout) ? inputLayout.layout.length : null;
          } catch (error) { audioChannelCount = null; }
        }
        filters.push({
          matchName: xmlText(xmlChild(filterElement, "FilterMatchName")),
          displayName: xmlText(xmlChild(componentPayload, "DisplayName")),
          mediaType: xmlText(xmlChild(filterElement, "MediaType")),
          // Audio filters have no official match-name-based creation API; AudioFilterFactory
          // resolves by display name, same as the already-tested timeline.applyAudioEffect probe.
          // The matchName captured above is a Premiere-internal identifier, not a usable lookup key.
          isAudio,
          audioChannelCount,
          anchorInPointTicks,
          // Premiere marks fixed effects that exist on every clip (Motion, Opacity, Time
          // Remapping) with this flag; such a component must be targeted in place rather than
          // created and appended, since it cannot be duplicated and VideoFilterFactory does not
          // produce it. Absent for ordinary filters, where it correctly reads as false.
          intrinsic: xmlText(xmlChild(componentPayload, "Intrinsic")) === "true",
          parameters
        });
      });
      presets.push({
        name: xmlText(xmlPath(element, ["TreeItemBase", "Name"])) || "?",
        category: categoryParts.join(" > "),
        sourceObjectId: element.attributes.ObjectID || null,
        dataObjectId: dataRef.attributes.ObjectRef || null,
        filters
      });
    });
  }
  traverse(rootBin, []);
  return presets;
}

function loadPrfpsetFileIntoCatalog(file, xml) {
  const presets = parsePrfpsetCatalog(xml);
  importedEffectPresetXml = xml;
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
    catalogStorage: "in-memory, re-read on plugin load from a stored persistent token",
    mutation: "none"
  };
}

async function importPrfpsetCatalog() {
  const { localFileSystem } = require("uxp").storage;
  const file = await localFileSystem.getFileForOpening({ types: ["prfpset"] });
  if (!file) throw new Error("No .prfpset file was selected.");
  const xml = await file.read();
  const result = loadPrfpsetFileIntoCatalog(file, xml);
  try {
    const token = await localFileSystem.createPersistentToken(file);
    localStorage.setItem(PRESET_CATALOG_TOKEN_KEY, token);
    result.persistentTokenStored = true;
  } catch (error) {
    // Not fatal: the catalog is already loaded for this session, only the next-session
    // auto-restore is affected. Surfaced in the result rather than thrown.
    result.persistentTokenStored = false;
    result.persistentTokenError = error && error.message ? error.message : String(error);
  }
  return result;
}

// Runs from entrypoints.plugin.create() so a previously imported catalog survives a plugin
// reload without asking the user to re-pick the file. Best-effort: any failure (revoked
// permission, moved/deleted file, no token stored yet) just leaves the catalog unset, exactly
// as if importPrfpsetCatalog() had never been called - existing preset actions already fail
// closed with "Import a .prfpset catalog first." in that case.
async function restoreImportedPresetCatalogFromToken() {
  const token = localStorage.getItem(PRESET_CATALOG_TOKEN_KEY);
  if (!token) return { restored: false, reason: "no-stored-token" };
  try {
    const { localFileSystem } = require("uxp").storage;
    const file = await localFileSystem.getEntryForPersistentToken(token);
    const xml = await file.read();
    loadPrfpsetFileIntoCatalog(file, xml);
    return { restored: true, fileName: file.name, presetCount: importedEffectPresetCatalog.presets.length };
  } catch (error) {
    localStorage.removeItem(PRESET_CATALOG_TOKEN_KEY);
    return { restored: false, reason: error && error.message ? error.message : String(error) };
  }
}

function findImportedEffectPresets(name, category) {
  const requestedName = String(name || "").trim();
  const requestedCategory = String(category || "").trim().replace(/^Presets\s*>\s*/i, "");
  if (!requestedName) throw new Error("Preset name is required.");
  const named = importedEffectPresetCatalog.presets.filter((preset) => preset.name.toLocaleLowerCase() === requestedName.toLocaleLowerCase());
  const matches = requestedCategory
    ? named.filter((preset) => preset.category.toLocaleLowerCase() === requestedCategory.toLocaleLowerCase())
    : named;
  return { requestedName, requestedCategory, named, matches };
}

function stablePresetAlias(preset) {
  const identity = `${preset.category}>${preset.name}>${preset.sourceObjectId || ""}`;
  let hash = 2166136261;
  for (let index = 0; index < identity.length; index += 1) {
    hash ^= identity.charCodeAt(index);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  const suffix = preset.name.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "PRESET";
  return `FXP_${hash.toString(16).toUpperCase().padStart(8, "0")}__${suffix}`;
}

function inspectImportedPresetBridgeCandidate(action) {
  if (!importedEffectPresetCatalog || !importedEffectPresetXml) throw new Error("Import a .prfpset catalog first.");
  const { preset } = resolveUniqueImportedEffectPreset(action.payload.name, action.payload.category);
  if (!preset.sourceObjectId) throw new Error("The imported preset has no source ObjectID.");
  const documentNode = parseXmlTree(importedEffectPresetXml);
  const objectIndex = {};
  xmlDescendants(documentNode).forEach((element) => {
    if (element.attributes.ObjectID) objectIndex[element.attributes.ObjectID] = element;
  });
  const visited = new Set();
  const unresolved = new Set();
  const tagCounts = {};
  function visitObject(objectId) {
    if (!objectId || visited.has(objectId)) return;
    const element = objectIndex[objectId];
    if (!element) { unresolved.add(objectId); return; }
    visited.add(objectId);
    tagCounts[element.tagName] = (tagCounts[element.tagName] || 0) + 1;
    xmlDescendants(element).forEach((node) => {
      if (node.attributes.ObjectRef) visitObject(node.attributes.ObjectRef);
    });
  }
  visitObject(preset.sourceObjectId);
  return {
    preset: { name: preset.name, category: preset.category, sourceObjectId: preset.sourceObjectId, dataObjectId: preset.dataObjectId },
    alias: stablePresetAlias(preset),
    sameNameCount: importedEffectPresetCatalog.presets.filter((entry) => entry.name.toLocaleLowerCase() === preset.name.toLocaleLowerCase()).length,
    dependencyObjectCount: visited.size,
    dependencyTagCounts: tagCounts,
    unresolvedObjectRefs: Array.from(unresolved),
    preservesOpaquePayloads: unresolved.size === 0,
    nextMutation: "none-export-not-yet-enabled",
    mutation: "none"
  };
}

function encodeXmlText(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function encodeXmlAttribute(value) {
  return encodeXmlText(value).replace(/"/g, "&quot;");
}

function serializeXmlNode(node) {
  if (node.tagName === "#document") return (node.children || []).map(serializeXmlNode).join("");
  const attributes = Object.keys(node.attributes || {}).map((name) => ` ${name}="${encodeXmlAttribute(node.attributes[name])}"`).join("");
  const content = encodeXmlText(node.text || "") + (node.children || []).map(serializeXmlNode).join("");
  return content ? `<${node.tagName}${attributes}>${content}</${node.tagName}>` : `<${node.tagName}${attributes}/>`;
}

function buildImportedPresetBridge(action) {
  if (!importedEffectPresetCatalog || !importedEffectPresetXml) throw new Error("Import a .prfpset catalog first.");
  const { preset } = resolveUniqueImportedEffectPreset(action.payload.name, action.payload.category);
  const alias = stablePresetAlias(preset);
  const documentNode = parseXmlTree(importedEffectPresetXml);
  const objectIndex = {};
  xmlDescendants(documentNode).forEach((element) => {
    if (element.attributes.ObjectID) objectIndex[element.attributes.ObjectID] = element;
  });
  const sourceTreeItem = objectIndex[preset.sourceObjectId];
  if (!sourceTreeItem || sourceTreeItem.tagName !== "TreeItem") throw new Error("Could not resolve the preset TreeItem for bridge generation.");
  const nameElement = xmlPath(sourceTreeItem, ["TreeItemBase", "Name"]);
  if (!nameElement) throw new Error("Could not resolve the preset name element.");
  nameElement.text = alias;
  nameElement.children = [];
  let rootBin = null;
  for (const bin of xmlDescendants(documentNode, "BinTreeItem")) {
    if (xmlText(xmlPath(bin, ["TreeItemBase", "Name"])) === "Presets") { rootBin = bin; break; }
  }
  if (!rootBin) throw new Error("Could not resolve the root Presets bin.");
  const items = xmlChild(rootBin, "Items");
  if (!items) throw new Error("The root Presets bin has no Items container.");
  items.children = (items.children || []).filter((item) => item.tagName === "Item" && item.attributes.ObjectRef === preset.sourceObjectId);
  if (items.children.length !== 1) throw new Error("The selected preset is not a direct child of the root Presets bin; nested bridge wrapping is not implemented yet.");
  const xml = `<?xml version="1.0" encoding="UTF-8"?>${serializeXmlNode(documentNode)}`;
  const reparsed = parsePrfpsetCatalog(xml);
  if (reparsed.length !== 1 || reparsed[0].name !== alias) throw new Error("Generated bridge failed its one-preset alias validation.");
  if (reparsed[0].filters.length !== preset.filters.length) throw new Error("Generated bridge lost one or more filters during validation.");
  const originalSignature = JSON.stringify(preset.filters);
  const generatedSignature = JSON.stringify(reparsed[0].filters.map((filter) => ({ ...filter })));
  if (originalSignature !== generatedSignature) throw new Error("Generated bridge changed the parsed filter payload.");
  return { xml, alias, preset, reparsedPreset: reparsed[0] };
}

async function exportImportedPresetBridge(action) {
  const bridge = buildImportedPresetBridge(action);
  const { localFileSystem } = require("uxp").storage;
  const file = await localFileSystem.getFileForSaving(`${bridge.alias}.prfpset`, { types: ["prfpset"] });
  if (!file) return { cancelled: true, mutation: "none" };
  await file.write(bridge.xml);
  return {
    fileName: file.name,
    alias: bridge.alias,
    originalPreset: { name: bridge.preset.name, category: bridge.preset.category },
    presetCount: 1,
    filterCount: bridge.reparsedPreset.filters.length,
    parameterCount: bridge.reparsedPreset.filters.reduce((total, filter) => total + filter.parameters.length, 0),
    validation: "reparsed-name-filter-and-parameter-payload-exact",
    originalCatalogUnmodified: true,
    mutation: "user-approved-new-file"
  };
}

function resolveUniqueImportedEffectPreset(name, category) {
  const result = findImportedEffectPresets(name, category);
  if (result.matches.length !== 1) {
    const candidates = result.matches.length ? result.matches : result.named;
    const categories = candidates.slice(0, 20).map((preset) => preset.category || "(root)").join("; ");
    throw new Error(`Expected one imported preset match, found ${result.matches.length}.${categories ? ` Candidate categories: ${categories}` : ""}`);
  }
  return { ...result, preset: result.matches[0] };
}

function inspectImportedEffectPreset(action) {
  if (!importedEffectPresetCatalog) throw new Error("Import a .prfpset catalog first.");
  const { requestedName, requestedCategory, named, matches } = findImportedEffectPresets(action.payload.name, action.payload.category);
  if (matches.length !== 1) {
    return {
      requestedName,
      requestedCategory,
      exactMatchCount: matches.length,
      namedMatchCount: named.length,
      candidates: named.slice(0, 50).map((preset) => ({ name: preset.name, category: preset.category, filterCount: preset.filters.length })),
      mutation: "none"
    };
  }
  const preset = matches[0];
  return {
    requestedName,
    requestedCategory,
    exactMatchCount: 1,
    preset,
    summary: {
      filterCount: preset.filters.length,
      filters: preset.filters.map((filter) => ({
        matchName: filter.matchName,
        displayName: filter.displayName,
        intrinsic: filter.intrinsic,
        isAudio: filter.isAudio,
        audioChannelCount: filter.audioChannelCount,
        parameterCount: filter.parameters.length,
        animatedParameterCount: filter.parameters.filter((parameter) => parameter.timeVarying || parameter.keyframes).length,
        arbitraryParameterCount: filter.parameters.filter((parameter) => parameter.arbitrary).length
      }))
    },
    mutation: "none"
  };
}

function parsePrfpsetValue(rawValue, controlType) {
  const raw = String(rawValue == null ? "" : rawValue).trim();
  if (String(controlType) === "6" && raw.includes(":")) {
    const coordinates = raw.split(":").map(Number);
    if (coordinates.length === 2 && coordinates.every(Number.isFinite)) return { type: "point", value: coordinates };
  }
  if (raw === "true" || raw === "false") return { type: "boolean", value: raw === "true" };
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) return { type: "number", value: numeric };
  throw new Error(`Unsupported .prfpset value '${raw}' for control type ${controlType}.`);
}

function parsePrfpsetKeyframes(parameter) {
  return String(parameter.keyframes || "").split(";").filter(Boolean).map((record) => {
    const parts = record.split(",");
    return { ticks: Number(parts[0]), value: parsePrfpsetValue(parts[1], parameter.controlType), parts };
  });
}

function parsePrfpsetStaticHostValue(parameter) {
  // Safety net: every caller should already have rejected an arbitrary parameter with fuller
  // context, but a missing value must never silently coerce to a number here (see parsePrfpsetCatalog).
  if (parameter.arbitrary) throw new Error(`Parameter ${parameter.index} (${parameter.name || "unnamed"}) is stored as opaque/arbitrary data with no official value or write path.`);
  if (String(parameter.controlType) !== "5") return createHostValue(parsePrfpsetValue(parameter.value, parameter.controlType));
  let decimal = String(parameter.value || "").replace(/^0+/, "") || "0";
  if (!/^\d+$/.test(decimal)) throw new Error(`Invalid packed .prfpset Color '${decimal}'.`);
  const wordsLowToHigh = [];
  for (let wordIndex = 0; wordIndex < 4; wordIndex += 1) {
    let quotient = "";
    let remainder = 0;
    for (const digit of decimal) {
      const current = (remainder * 10) + Number(digit);
      const quotientDigit = Math.floor(current / 65536);
      remainder = current % 65536;
      if (quotient || quotientDigit) quotient += String(quotientDigit);
    }
    wordsLowToHigh.push(remainder);
    decimal = quotient || "0";
  }
  if (decimal !== "0") throw new Error("Packed .prfpset Color exceeds 64 bits.");
  const [blueWord, greenWord, redWord, alphaWord] = wordsLowToHigh;
  const normalize = (word) => Math.max(0, Math.min(1, word / 65280));
  const color = new premiere.Color();
  color.red = normalize(redWord);
  color.green = normalize(greenWord);
  color.blue = normalize(blueWord);
  color.alpha = normalize(alphaWord);
  return color;
}

// Premiere serializes each .prfpset keyframe as 14 comma-separated fields. Fields 4-7 carry the
// temporal ease as After Effects speed/influence pairs; fields 8-9 carry the interpolation codes
// already validated against the host runtime (Bezier=5, Hold=4); fields 10-13 carry the spatial
// path tangents, which are independent of the temporal ease and are left to linear interpolation.
function parsePrfpsetKeyframeEase(parts) {
  const field = (index) => {
    const value = Number(parts[index]);
    return Number.isFinite(value) ? value : 0;
  };
  // Scalar records carry only the first 8 fields; the interpolation codes and spatial tangents are
  // present on Point records alone. Report the codes as absent rather than as a zero that would
  // read as Linear.
  const optionalField = (index) => {
    if (index >= parts.length) return null;
    const value = Number(parts[index]);
    return Number.isFinite(value) ? value : null;
  };
  return {
    incomingSpeed: field(4),
    incomingInfluence: field(5),
    outgoingSpeed: field(6),
    outgoingInfluence: field(7),
    outgoingInterpolationCode: optionalField(8),
    incomingInterpolationCode: optionalField(9)
  };
}

// Speed/influence pairs map onto cubic controls with the same relations Adobe's own After Effects
// exporters use: influence is the horizontal handle fraction, and speed relative to the segment's
// average speed is the vertical one. Zero influence with zero speed degenerates to linear, and a
// speed above the average pushes a control past 1 so overshoot survives the conversion.
function derivePrfpsetTemporalCurve(sourceKeys, measureDistance) {
  const first = sourceKeys[0];
  const second = sourceKeys[1];
  const durationSeconds = (second.ticks - first.ticks) / 254016000000;
  const startEase = parsePrfpsetKeyframeEase(first.parts);
  const endEase = parsePrfpsetKeyframeEase(second.parts);
  const distance = measureDistance(first.value.value, second.value.value);
  const averageSpeed = durationSeconds > 0 ? distance / durationSeconds : 0;
  // Speed keeps its sign against a signed average: a keyframe reached while travelling opposite to
  // the segment's overall direction has overshot its own value and is on the way back, which pushes
  // the control point outside 0..1. Taking magnitudes here would fold that overshoot the wrong way.
  const speedRatio = (speed) => (Math.abs(averageSpeed) > 1e-12 ? speed / averageSpeed : 0);
  const x1 = Math.max(0, Math.min(1, startEase.outgoingInfluence));
  const x2 = Math.max(0, Math.min(1, 1 - endEase.incomingInfluence));
  return {
    x1,
    y1: x1 * speedRatio(startEase.outgoingSpeed),
    x2,
    y2: 1 - ((1 - x2) * speedRatio(endEase.incomingSpeed)),
    durationSeconds,
    averageSpeed,
    outgoingSpeed: startEase.outgoingSpeed,
    outgoingInfluence: startEase.outgoingInfluence,
    incomingSpeed: endEase.incomingSpeed,
    incomingInfluence: endEase.incomingInfluence,
    outgoingInterpolationCode: startEase.outgoingInterpolationCode,
    incomingInterpolationCode: endEase.incomingInterpolationCode,
    model: "prfpset-speed-influence"
  };
}

function pointDistance(start, end) {
  return Math.sqrt(end.reduce((sum, value, index) => sum + ((value - start[index]) * (value - start[index])), 0));
}

function derivePrfpsetPointCurve(sourceKeys) {
  return derivePrfpsetTemporalCurve(sourceKeys, pointDistance);
}

// Fields 10-13 hold the motion-path handles as offsets from their own keyframe's value. Premiere
// stores collinear default handles for a straight move, so arc-length traversal of this cubic
// reduces to plain linear interpolation there and only bends where the preset really curves.
function derivePrfpsetSpatialPath(sourceKeys) {
  const start = sourceKeys[0].value.value;
  const end = sourceKeys[1].value.value;
  const tangent = (parts, offset) => start.map((_, index) => {
    const value = Number(parts[offset + index]);
    return Number.isFinite(value) ? value : 0;
  });
  const outgoing = tangent(sourceKeys[0].parts, 12);
  const incoming = tangent(sourceKeys[1].parts, 10);
  return {
    p0: start,
    p1: start.map((value, index) => value + outgoing[index]),
    p2: end.map((value, index) => value + incoming[index]),
    p3: end
  };
}

function evaluateCubicPoint(path, t) {
  const inverse = 1 - t;
  const a = inverse * inverse * inverse;
  const b = 3 * inverse * inverse * t;
  const c = 3 * inverse * t * t;
  const d = t * t * t;
  return path.p0.map((_, index) =>
    (a * path.p0[index]) + (b * path.p1[index]) + (c * path.p2[index]) + (d * path.p3[index]));
}

// Temporal easing yields distance travelled along the path, not the cubic's own parameter, so the
// path is walked by arc length. Values outside 0..1 come from overshoot and extrapolate along the
// nearest end tangent rather than being clamped, which would silently discard the overshoot.
function samplePathAtArcFraction(path, fraction) {
  // 1024 chords hold this table's own contribution near 1e-6, an order below the residual actually
  // measured against the host, so the traversal is not the limiting factor in a comparison.
  const steps = 1024;
  const points = [];
  for (let step = 0; step <= steps; step += 1) points.push(evaluateCubicPoint(path, step / steps));
  const cumulative = [0];
  for (let step = 1; step <= steps; step += 1) {
    cumulative.push(cumulative[step - 1] + pointDistance(points[step - 1], points[step]));
  }
  const total = cumulative[steps];
  if (!(total > 1e-12)) return path.p3.slice();
  const extrapolate = (from, to, scale) => from.map((value, index) => value + ((to[index] - value) * scale));
  if (fraction < 0) return extrapolate(points[0], points[1], (fraction * total) / Math.max(1e-12, cumulative[1]));
  if (fraction > 1) {
    const tailLength = Math.max(1e-12, total - cumulative[steps - 1]);
    return extrapolate(points[steps - 1], points[steps], ((fraction - 1) * total + tailLength) / tailLength);
  }
  const target = fraction * total;
  for (let step = 1; step <= steps; step += 1) {
    if (cumulative[step] >= target) {
      const span = cumulative[step] - cumulative[step - 1];
      return extrapolate(points[step - 1], points[step], span > 1e-12 ? (target - cumulative[step - 1]) / span : 0);
    }
  }
  return points[steps].slice();
}

function importedPointAtProgress(sourceKeys, curve, progress) {
  const eased = cubicBezierProgress(progress, curve.x1, curve.y1, curve.x2, curve.y2);
  return samplePathAtArcFraction(derivePrfpsetSpatialPath(sourceKeys), eased);
}

function sampleImportedPointCurve(sourceKeys, offsetSeconds) {
  const originTicks = sourceKeys[0].ticks;
  const requestedTicks = originTicks + (Math.max(0, offsetSeconds) * 254016000000);
  let segmentIndex = sourceKeys.length - 2;
  for (let index = 0; index < sourceKeys.length - 1; index += 1) {
    if (requestedTicks <= sourceKeys[index + 1].ticks) { segmentIndex = index; break; }
  }
  const segmentKeys = [sourceKeys[segmentIndex], sourceKeys[segmentIndex + 1]];
  const curve = derivePrfpsetPointCurve(segmentKeys);
  const segmentTicks = segmentKeys[1].ticks - segmentKeys[0].ticks;
  const progress = segmentTicks > 0 ? Math.max(0, Math.min(1, (requestedTicks - segmentKeys[0].ticks) / segmentTicks)) : 0;
  return { value: importedPointAtProgress(segmentKeys, curve, progress), segmentIndex, segmentProgress: progress, curve };
}

// Scalars carry a signed delta so the ratio above keeps its meaning in both directions. Point
// parameters instead measure distance along the path, which is unsigned by construction.
function derivePrfpsetScalarCurve(sourceKeys) {
  return derivePrfpsetTemporalCurve(sourceKeys, (start, end) => end - start);
}

function sampleImportedScalarCurve(sourceKeys, offsetSeconds) {
  const originTicks = sourceKeys[0].ticks;
  const requestedTicks = originTicks + (Math.max(0, offsetSeconds) * 254016000000);
  let segmentIndex = sourceKeys.length - 2;
  for (let index = 0; index < sourceKeys.length - 1; index += 1) {
    if (requestedTicks <= sourceKeys[index + 1].ticks) { segmentIndex = index; break; }
  }
  const segmentKeys = [sourceKeys[segmentIndex], sourceKeys[segmentIndex + 1]];
  const curve = derivePrfpsetScalarCurve(segmentKeys);
  const segmentTicks = segmentKeys[1].ticks - segmentKeys[0].ticks;
  const progress = segmentTicks > 0 ? Math.max(0, Math.min(1, (requestedTicks - segmentKeys[0].ticks) / segmentTicks)) : 0;
  const eased = cubicBezierProgress(progress, curve.x1, curve.y1, curve.x2, curve.y2);
  return {
    value: segmentKeys[0].value.value + ((segmentKeys[1].value.value - segmentKeys[0].value.value) * eased),
    segmentIndex,
    segmentProgress: progress,
    curve
  };
}

function compareImportedTransformWithCapture(action) {
  if (!importedEffectPresetCatalog) throw new Error("Import a .prfpset catalog first.");
  if (!capturedTransformCurveReference) throw new Error("Capture the manually applied Transform preset from clip A first.");
  const requestedName = String(action.payload.name || "").trim();
  const requestedCategory = String(action.payload.category || "").trim().replace(/^Presets\s*>\s*/i, "");
  const matches = importedEffectPresetCatalog.presets.filter((preset) =>
    preset.name.toLocaleLowerCase() === requestedName.toLocaleLowerCase() &&
    preset.category.toLocaleLowerCase() === requestedCategory.toLocaleLowerCase());
  if (matches.length !== 1) throw new Error(`Expected one exact imported preset, found ${matches.length}.`);
  // The compared filter follows whatever was captured, so intrinsic Motion and the Transform effect
  // are both usable. Animated parameters are paired by index rather than assumed to sit at a fixed
  // one, because Position is index 1 on Transform and index 0 on Motion.
  const capturedMatchName = capturedTransformCurveReference.matchName;
  const transform = matches[0].filters.find((filter) => filter.matchName === capturedMatchName);
  if (!transform) throw new Error(`The imported preset contains no ${capturedMatchName} filter.`);
  const pointPair = transform.parameters
    .filter((parameter) => parameter.timeVarying && parameter.keyframes)
    .map((parameter) => {
      let keys = null;
      try { keys = parsePrfpsetKeyframes(parameter); } catch (error) { return null; }
      if (keys.length < 2 || keys.some((key) => key.value.type !== "point")) return null;
      const capturedParameter = capturedTransformCurveReference.parameters
        .find((entry) => entry.index === parameter.index && entry.mode === "animated");
      return capturedParameter ? { source: parameter, captured: capturedParameter, keys } : null;
    })
    .find(Boolean);
  const source = pointPair ? pointPair.source : null;
  const captured = pointPair ? pointPair.captured : null;
  if (!source || !captured) {
    const scalarComparisons = transform.parameters.filter((parameter) => parameter.timeVarying && parameter.keyframes)
      .map((scalarSource) => {
        const scalarCaptured = capturedTransformCurveReference.parameters.find((parameter) => parameter.index === scalarSource.index && parameter.mode === "animated");
        if (!scalarCaptured || scalarCaptured.samples.some((sample) => sample.value.type !== "number")) return null;
        const sourceKeys = parsePrfpsetKeyframes(scalarSource);
        if (sourceKeys.length < 2 || sourceKeys.some((key) => key.value.type !== "number")) return null;
        const samples = scalarCaptured.samples.map((sample) => {
          const derived = sampleImportedScalarCurve(sourceKeys, sample.offsetSeconds);
          const manual = sample.value.value;
          const delta = derived.value - manual;
          return { offsetSeconds: sample.offsetSeconds, manual, derived: derived.value, delta, absoluteError: Math.abs(delta), segmentIndex: derived.segmentIndex, segmentProgress: derived.segmentProgress };
        });
        const sumSquares = samples.reduce((sum, sample) => sum + (sample.delta * sample.delta), 0);
        const worst = samples.reduce((current, sample) => !current || sample.absoluteError > current.absoluteError ? sample : current, null);
        return {
          index: scalarSource.index, name: scalarSource.name, importedKeyframeCount: sourceKeys.length,
          importedDurationSeconds: (sourceKeys[sourceKeys.length - 1].ticks - sourceKeys[0].ticks) / 254016000000,
          capturedDurationSeconds: scalarCaptured.durationSeconds,
          segmentCurves: sourceKeys.slice(0, -1).map((_, index) => derivePrfpsetScalarCurve([sourceKeys[index], sourceKeys[index + 1]])),
          sampleCount: samples.length, rootMeanSquareError: Math.sqrt(sumSquares / Math.max(1, samples.length)),
          maximumAbsoluteError: worst ? worst.absoluteError : 0, worstSample: worst, samples
        };
      }).filter(Boolean);
    if (scalarComparisons.length === 0) throw new Error("No comparable animated Position or scalar Transform parameters were found.");
    return {
      preset: { name: matches[0].name, category: matches[0].category },
      scalarComparisonCount: scalarComparisons.length,
      scalarComparisons,
      mutation: "none",
      comparisonModel: "manual-host-samples-vs-prfpset-speed-influence"
    };
  }
  const sourceKeys = parsePrfpsetKeyframes(source);
  if (sourceKeys.length < 2 || sourceKeys.some((key) => key.value.type !== "point")) throw new Error("Expected an imported Point curve with at least two keys.");
  const importedDurationSeconds = (sourceKeys[sourceKeys.length - 1].ticks - sourceKeys[0].ticks) / 254016000000;
  const segmentCurves = sourceKeys.slice(0, -1).map((_, index) => derivePrfpsetPointCurve([sourceKeys[index], sourceKeys[index + 1]]));
  const samples = captured.samples.map((sample) => {
    const progress = captured.durationSeconds > 0 ? Math.min(1, sample.offsetSeconds / captured.durationSeconds) : 0;
    const sampled = sampleImportedPointCurve(sourceKeys, sample.offsetSeconds);
    const derived = sampled.value;
    const manual = sample.value.value;
    const delta = derived.map((value, index) => value - manual[index]);
    const distance = Math.sqrt(delta.reduce((sum, value) => sum + (value * value), 0));
    return { offsetSeconds: sample.offsetSeconds, progress, segmentIndex: sampled.segmentIndex, segmentProgress: sampled.segmentProgress, manual, derived, delta, distance };
  });
  const sumSquares = samples.reduce((sum, sample) => sum + (sample.distance * sample.distance), 0);
  const worst = samples.reduce((current, sample) => !current || sample.distance > current.distance ? sample : current, null);
  return {
    preset: { name: matches[0].name, category: matches[0].category },
    matchName: capturedMatchName,
    parameter: { index: source.index, name: source.name },
    importedDurationSeconds,
    capturedDurationSeconds: captured.durationSeconds,
    importedKeyframeCount: sourceKeys.length,
    segmentCount: segmentCurves.length,
    segmentCurves,
    sampleCount: samples.length,
    rootMeanSquareDistance: Math.sqrt(sumSquares / Math.max(1, samples.length)),
    maximumDistance: worst ? worst.distance : 0,
    worstSample: worst,
    samples,
    mutation: "none",
    comparisonModel: "manual-host-samples-vs-prfpset-speed-influence"
  };
}

async function captureTransformCurveReference(action) {
  // Defaults to the Transform effect, but any match name works, including the intrinsic
  // `AE.ADBE Motion` that every clip already carries.
  const matchName = String((action && action.payload && action.payload.matchName) || "").trim() || "AE.ADBE Geometry2";
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before capturing a reference.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before capturing a reference.");
  const clip = await getSingleSelectedVideoClip(sequence);
  const chain = await clip.getComponentChain();
  const resolved = await findLastComponentByMatchName(chain, matchName);
  if (!resolved) throw new Error(`The selected clip has no ${matchName} component to capture.`);
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

async function findDirectChildBin(folderItem, name) {
  const children = await folderItem.getItems();
  for (const child of Array.isArray(children) ? children : []) {
    if ((child.name || "") !== name) continue;
    try {
      return premiere.FolderItem.cast(child);
    } catch (error) {
      // Same name, but not a folder - keep looking rather than treat it as a match.
    }
  }
  return null;
}

// CEP's _ensureNestedSequencesBin/_organizeNestSequenceObject (host.jsx) always filed a newly
// created Nest into a project bin, creating it on first use. UXP has no single call for this -
// FolderItem.createBinAction/createMoveItemAction are separate Actions, so this is its own
// find-or-create-then-move transaction rather than part of the nest-creation transaction itself.
//
// createMoveItemAction's exact calling convention is undocumented beyond its parameter list, and
// a first attempt (called on the item's own current parent bin) reported success but moved
// nothing. The official AdobeDocs/uxp-premiere-pro-samples reference panel (projectPanel.ts,
// moveItem()) always calls it on the project's rootItem regardless of the item's actual current
// location, with the destination explicitly re-cast via FolderItem.cast() - both details this
// function now matches exactly rather than the plausible-looking assumption that failed silently.
async function ensureBinAndMoveProjectItem(project, projectItem, binName) {
  const rootItem = await project.getRootItem();
  let targetBin = await findDirectChildBin(rootItem, binName);
  let binCreated = false;
  if (!targetBin) {
    let createTransactionSucceeded = false;
    project.lockedAccess(() => {
      const createAction = rootItem.createBinAction(binName, false);
      createTransactionSucceeded = project.executeTransaction((compoundAction) => {
        compoundAction.addAction(createAction);
      }, `FX.palette: Create ${binName} bin`);
    });
    if (!createTransactionSucceeded) throw new Error(`Premiere rejected creating the "${binName}" bin.`);
    targetBin = await findDirectChildBin(rootItem, binName);
    if (!targetBin) throw new Error(`Premiere created the "${binName}" bin but it could not be found afterward.`);
    binCreated = true;
  }

  let moveTransactionSucceeded = false;
  project.lockedAccess(() => {
    const moveAction = rootItem.createMoveItemAction(projectItem, premiere.FolderItem.cast(targetBin));
    moveTransactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(moveAction);
    }, `FX.palette: Move ${projectItem.name || "Nest"} into ${binName}`);
  });
  if (!moveTransactionSucceeded) throw new Error(`Premiere rejected moving the Nest into the "${binName}" bin.`);
  return { binName, binCreated, moveTransactionSucceeded };
}

async function createNestFromSelection(action) {
  const requestedName = typeof action.payload.name === "string" ? action.payload.name.trim() : "";
  if (!requestedName) throw new Error("A Nest name is required.");
  const binName = typeof action.payload.binName === "string" && action.payload.binName.trim()
    ? action.payload.binName.trim()
    : "Nested Clips";

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
    binPlacement: await (async () => {
      // A separate try/catch on purpose: by this point the Nest itself already exists and
      // replaced the selection successfully, so a bin-placement failure (e.g. a name collision
      // Premiere itself rejects) should be reported, not thrown - throwing here would misreport
      // an already-successful Nest creation as a total failure.
      try {
        return await ensureBinAndMoveProjectItem(project, createdProjectItem, binName);
      } catch (error) {
        return { binName, error: error && error.message ? error.message : String(error) };
      }
    })(),
    originalSelectionExpectedRemoved: true,
    nestedItemExpectedInserted: true,
    undoModelExpected: [
      "Undo move into bin",
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

  const projectSequences = await project.getSequences();
  result.project = {
    name: project.name || null,
    guid: guidToString(project.guid),
    // Lets a caller compute a collision-free default name (e.g. the companion's FXN-NNN Nest
    // codename scheme) against real project state instead of guessing at a number.
    sequenceNames: (Array.isArray(projectSequences) ? projectSequences : []).map((entry) => entry.name || null)
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
    const audioMediaTypes = new Set();
    const audioTrackCount = await sequence.getAudioTrackCount();
    for (let index = 0; index < audioTrackCount; index += 1) {
      const audioTrack = await sequence.getAudioTrack(index);
      audioMediaTypes.add(guidToString(await audioTrack.getMediaType()));
    }
    result.timelineSelection.items = await Promise.all(
      selectedTrackItems.map((item) => describeTrackItem(item, audioMediaTypes))
    );
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
  const matchNameInput = document.getElementById("capture-reference-match-name");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.captureTransformCurveReference", requestId: String(Date.now()),
    payload: { matchName: matchNameInput ? matchNameInput.value : "" }
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

async function runInspectImportedEffectPreset() {
  const button = document.getElementById("inspect-imported-prfpset");
  const nameInput = document.getElementById("prfpset-preset-name");
  const categoryInput = document.getElementById("prfpset-preset-category");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "catalog.effectPresets.inspectImported",
    requestId: String(Date.now()),
    payload: { name: nameInput ? nameInput.value : "", category: categoryInput ? categoryInput.value : "" }
  }, { "catalog.effectPresets.inspectImported": inspectImportedEffectPreset });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runInspectPresetBridgeCandidate() {
  const button = document.getElementById("inspect-preset-bridge-candidate");
  const nameInput = document.getElementById("prfpset-preset-name");
  const categoryInput = document.getElementById("prfpset-preset-category");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "catalog.effectPresets.inspectBridgeCandidate",
    requestId: String(Date.now()),
    payload: { name: nameInput ? nameInput.value : "", category: categoryInput ? categoryInput.value : "" }
  }, { "catalog.effectPresets.inspectBridgeCandidate": inspectImportedPresetBridgeCandidate });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runExportPresetBridge() {
  const button = document.getElementById("export-preset-bridge");
  const nameInput = document.getElementById("prfpset-preset-name");
  const categoryInput = document.getElementById("prfpset-preset-category");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "catalog.effectPresets.exportBridge",
    requestId: String(Date.now()),
    payload: { name: nameInput ? nameInput.value : "", category: categoryInput ? categoryInput.value : "" }
  }, { "catalog.effectPresets.exportBridge": exportImportedPresetBridge });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function inspectSelectedVideoComponents() {
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before inspecting components.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before inspecting components.");
  const clip = await getSingleSelectedVideoClip(sequence);
  const chain = await clip.getComponentChain();
  const componentCount = await chain.getComponentCount();
  const components = [];
  for (let componentIndex = 0; componentIndex < componentCount; componentIndex += 1) {
    const component = await chain.getComponentAtIndex(componentIndex);
    const parameterCount = await component.getParamCount();
    const parameters = [];
    for (let parameterIndex = 0; parameterIndex < parameterCount; parameterIndex += 1) {
      const parameter = await component.getParam(parameterIndex);
      parameters.push({
        index: parameterIndex,
        displayName: parameter.displayName || null,
        timeVarying: await parameter.isTimeVarying(),
        startValue: serializePresetProbeValue(await parameter.getStartValue())
      });
    }
    components.push({
      index: componentIndex,
      matchName: typeof component.getMatchName === "function" ? await component.getMatchName() : null,
      displayName: typeof component.getDisplayName === "function" ? await component.getDisplayName() : null,
      parameterCount,
      parameters
    });
  }
  return { clipName: await clip.getName(), componentCount, components, mutation: "none" };
}

async function applyImportedEffectPreset(action) {
  if (!importedEffectPresetCatalog) throw new Error("Import a .prfpset catalog first.");
  const { preset } = resolveUniqueImportedEffectPreset(action.payload.name, action.payload.category);
  if (preset.filters.length < 1) throw new Error("The imported preset contains no filters.");
  const reconstructEasing = action.payload.reconstructEasing === true;

  const dedupedFilters = dedupeAudioFilterVariants(preset.filters);
  const hasAudioFilters = dedupedFilters.some((filter) => filter.isAudio);
  const hasVideoFilters = dedupedFilters.some((filter) => !filter.isAudio);
  if (hasAudioFilters && hasVideoFilters) {
    throw new Error("This preset mixes video and audio filters, which this executor does not yet reconstruct together. Apply the video and audio parts of this preset separately.");
  }

  const runtimeFilters = dedupedFilters.slice().reverse().map((filter) => ({
    ...filter,
    preparedParameters: filter.parameters.map((source) => {
      // Fail closed rather than silently applying a coerced value: opaque parameters have no
      // official ComponentParam representation this project can write, per the Distortion/Lumetri
      // curve findings in PRESET_UXP_RESEARCH.md.
      if (source.arbitrary) {
        throw new Error(`Parameter ${source.index} (${source.name || "unnamed"}) of ${filter.matchName} is stored as opaque/arbitrary data with no official value or write path; this preset cannot be reconstructed.`);
      }
      if (!source.timeVarying || !source.keyframes) {
        return { source, mode: "static", hostValue: parsePrfpsetStaticHostValue(source) };
      }
      const sourceKeys = parsePrfpsetKeyframes(source);
      if (sourceKeys.length < 2) throw new Error(`Parameter ${source.index} of ${filter.matchName} is time-varying but has fewer than two keys.`);
      const valueType = sourceKeys[0].value.type;
      if (valueType !== "point" && valueType !== "number") {
        throw new Error(`Unsupported animated value type '${valueType}' at parameter ${source.index} of ${filter.matchName}.`);
      }
      if (sourceKeys.some((key) => key.value.type !== valueType)) {
        throw new Error(`Mixed keyframe value types at parameter ${source.index} of ${filter.matchName}.`);
      }
      return { source, mode: "animated", valueType, sourceKeys };
    })
  }));

  // The clip must be long enough for every animated parameter's last key relative to the shared
  // AnchorInPoint, not just its own internal span, since a parameter that starts late still needs
  // room to finish.
  const animatedEndOffsetsSeconds = runtimeFilters.flatMap((filter) =>
    filter.preparedParameters.filter((item) => item.mode === "animated")
      .map((item) => (item.sourceKeys[item.sourceKeys.length - 1].ticks - filter.anchorInPointTicks) / 254016000000));
  const longestSeconds = Math.max(0, ...animatedEndOffsetsSeconds);

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before applying the preset.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before applying the preset.");
  const targets = hasAudioFilters ? await getSelectedAudioClips(sequence) : await getSelectedVideoClips(sequence);

  // Validate every target before mutating any of them, so one short clip aborts cleanly instead of
  // leaving some clips modified and others not - partial application must never look like success.
  for (const target of targets) {
    const durationSeconds = (await target.getDuration()).seconds;
    if (durationSeconds < longestSeconds) throw new Error(`Clip "${await target.getName()}" is shorter than the imported preset animation.`);
  }

  const fps = (reconstructEasing && animatedEndOffsetsSeconds.length) ? await getSequenceFramesPerSecond(sequence) : null;

  // Resolve per-clip state up front: its component chain, source In Point, and per-filter
  // placement (existing intrinsic component vs. one that still needs to be created).
  const targetStates = [];
  for (const target of targets) {
    const chain = await target.getComponentChain();
    const targetInPoint = await target.getInPoint();
    const resolvedFilters = [];
    for (const filter of runtimeFilters) {
      // Premiere's own <Intrinsic> flag (see parsePrfpsetCatalog) identifies fixed effects that
      // already exist on the chain, such as Motion and Opacity, without needing a maintained list
      // of known match names: appending a second one is both unavailable through
      // VideoFilterFactory and semantically wrong, so these must be targeted in place instead.
      if (filter.intrinsic) {
        const resolved = await findLastComponentByMatchName(chain, filter.matchName);
        if (!resolved) throw new Error(`Clip "${await target.getName()}" has no existing ${filter.matchName} component to target.`);
        resolvedFilters.push({ filter, intrinsic: true, componentIndex: resolved.index });
      } else {
        resolvedFilters.push({ filter, intrinsic: false, componentIndex: null });
      }
    }
    targetStates.push({ target, chain, targetInPoint, resolvedFilters, componentCountBefore: await chain.getComponentCount() });
  }

  // Insertion happens in one compound transaction across every clip, matching the single-Undo
  // convention already established by this project's other multi-clip probes.
  const creations = [];
  for (const state of targetStates) {
    for (const entry of state.resolvedFilters.filter((candidate) => !candidate.intrinsic)) {
      // AudioFilterFactory resolves by display name and takes the target item so it can create a
      // component already matching that clip's channel configuration, unlike VideoFilterFactory's
      // plain match-name creation. This mirrors the already host-tested timeline.applyAudioEffect
      // probe; writing parameters onto the result afterward has not itself been host-verified yet.
      const component = entry.filter.isAudio
        ? await premiere.AudioFilterFactory.createComponentByDisplayName(entry.filter.displayName, state.target)
        : await premiere.VideoFilterFactory.createComponent(entry.filter.matchName);
      creations.push({ state, entry, component });
    }
  }
  let insertionTransactionSucceeded = true;
  if (creations.length) {
    insertionTransactionSucceeded = false;
    project.lockedAccess(() => {
      insertionTransactionSucceeded = project.executeTransaction((compound) => {
        creations.forEach(({ state, component }) => compound.addAction(state.chain.createAppendComponentAction(component)));
      }, `FX.palette: Insert ${preset.name}`);
    });
    if (!insertionTransactionSucceeded) throw new Error("Premiere rejected the preset component insertion.");
  }
  const createdCountByState = new Map();
  creations.forEach(({ state, entry }) => {
    const already = createdCountByState.get(state) || 0;
    entry.componentIndex = state.componentCountBefore + already;
    createdCountByState.set(state, already + 1);
  });

  const prepared = [];
  for (const state of targetStates) {
    for (const entry of state.resolvedFilters) {
      const filter = entry.filter;
      const component = await state.chain.getComponentAtIndex(entry.componentIndex);
      for (const item of filter.preparedParameters) {
        const parameter = await component.getParam(item.source.index);
        const filterLabel = filter.isAudio ? `${filter.displayName} (audio)` : filter.matchName;
        const context = `clip "${await state.target.getName()}", filter ${filterLabel}, parameter ${item.source.index} (${item.source.name || "unnamed"}, controlType ${item.source.controlType})`;
        try {
          if (item.mode === "static") {
            const keyframe = await parameter.createKeyframe(item.hostValue);
            prepared.push({ state, filter, item, parameter, mode: "static", keyframe });
            continue;
          }
          const originTicks = item.sourceKeys[0].ticks;
          const durationSeconds = (item.sourceKeys[item.sourceKeys.length - 1].ticks - originTicks) / 254016000000;
          // How far this parameter's own animation starts after the preset's shared AnchorInPoint.
          // Zero for a parameter that starts immediately; positive for one staggered relative to
          // another animated parameter in the same preset (see the AnchorInPoint comment above).
          const startOffsetSeconds = (originTicks - filter.anchorInPointTicks) / 254016000000;
          const keys = [];
          if (reconstructEasing) {
            const segments = Math.max(1, Math.round(durationSeconds * fps));
            for (let frame = 0; frame <= segments; frame += 1) {
              const localOffsetSeconds = durationSeconds * (frame / segments);
              const sampledValue = item.valueType === "point"
                ? sampleImportedPointCurve(item.sourceKeys, localOffsetSeconds).value
                : sampleImportedScalarCurve(item.sourceKeys, localOffsetSeconds).value;
              const keyframe = await parameter.createKeyframe(createHostValue({ type: item.valueType, value: sampledValue }));
              keyframe.position = state.targetInPoint.add(premiere.TickTime.createWithSeconds(startOffsetSeconds + localOffsetSeconds));
              if (typeof keyframe.setTemporalInterpolationMode === "function") {
                await keyframe.setTemporalInterpolationMode(premiere.Constants.InterpolationMode.LINEAR);
              }
              keys.push(keyframe);
            }
          } else {
            for (const sourceKey of item.sourceKeys) {
              const localOffsetSeconds = (sourceKey.ticks - originTicks) / 254016000000;
              const keyframe = await parameter.createKeyframe(createHostValue(sourceKey.value));
              keyframe.position = state.targetInPoint.add(premiere.TickTime.createWithSeconds(startOffsetSeconds + localOffsetSeconds));
              keys.push(keyframe);
            }
          }
          prepared.push({
            state, filter, item, parameter, mode: "animated", keys, durationSeconds, startOffsetSeconds,
            sampleStrategy: reconstructEasing ? "frame-sampled-approximation" : "principal-keys-only"
          });
        } catch (error) {
          const message = error && error.message ? error.message : String(error);
          throw new Error(`${message} (${context}, mode ${item.mode}${item.mode === "static" ? `, parsed value ${JSON.stringify(item.hostValue instanceof premiere.Color ? { r: item.hostValue.red, g: item.hostValue.green, b: item.hostValue.blue, a: item.hostValue.alpha } : item.hostValue)}` : `, valueType ${item.valueType}`}). The preset's effects were already inserted in a separate transaction; Undo once to remove them.`);
        }
      }
    }
  }

  let parameterTransactionSucceeded = false;
  project.lockedAccess(() => {
    parameterTransactionSucceeded = project.executeTransaction((compound) => {
      prepared.forEach((entry) => {
        if (entry.mode === "static") { compound.addAction(entry.parameter.createSetValueAction(entry.keyframe, false)); return; }
        compound.addAction(entry.parameter.createSetTimeVaryingAction(true));
        entry.keys.forEach((keyframe) => compound.addAction(entry.parameter.createAddKeyframeAction(keyframe)));
      });
    }, `FX.palette: Apply ${preset.name}`);
  });
  if (!parameterTransactionSucceeded) throw new Error("Premiere rejected the imported preset parameter transaction.");

  const perClip = [];
  for (const state of targetStates) {
    const verification = [];
    for (const entry of state.resolvedFilters) {
      const component = await state.chain.getComponentAtIndex(entry.componentIndex);
      // Read every parameter back from the host rather than trusting what was requested, so a wrong
      // value (or a wrong choice among audio channel-configuration variants) shows up here instead
      // of requiring a screenshot from Effect Controls to catch.
      const parameters = [];
      for (let parameterIndex = 0; parameterIndex < await component.getParamCount(); parameterIndex += 1) {
        const parameter = await component.getParam(parameterIndex);
        parameters.push({
          index: parameterIndex,
          displayName: parameter.displayName || null,
          startValue: serializePresetProbeValue(await parameter.getStartValue())
        });
      }
      verification.push({
        index: entry.componentIndex,
        intrinsic: entry.intrinsic,
        requestedMatchName: entry.filter.matchName,
        resultingMatchName: await component.getMatchName(),
        displayName: await component.getDisplayName(),
        parameters
      });
    }
    const clipPrepared = prepared.filter((entry) => entry.state === state);
    const clipAnimated = clipPrepared.filter((entry) => entry.mode === "animated");
    perClip.push({
      targetClipName: await state.target.getName(),
      componentCountBefore: state.componentCountBefore,
      componentCountAfter: await state.chain.getComponentCount(),
      staticParameterCount: clipPrepared.filter((entry) => entry.mode === "static").length,
      animatedParameterCount: clipAnimated.length,
      animatedParameters: clipAnimated.map((entry) => ({
        filterMatchName: entry.filter.matchName, index: entry.item.source.index, name: entry.item.source.name,
        valueType: entry.item.valueType, sourceKeyframeCount: entry.item.sourceKeys.length,
        appliedKeyframeCount: entry.keys.length, durationSeconds: entry.durationSeconds,
        startOffsetSeconds: entry.startOffsetSeconds, sampleStrategy: entry.sampleStrategy
      })),
      verification
    });
  }

  return {
    preset: { name: preset.name, category: preset.category },
    reconstructEasing,
    hasAudioFilters,
    targetClipCount: targets.length,
    sourceFilterCount: preset.filters.length,
    audioVariantsDeduped: preset.filters.length - dedupedFilters.length,
    sourceFilterOrder: preset.filters.map((filter) => filter.matchName),
    appliedFilterOrder: runtimeFilters.map((filter) => filter.matchName),
    longestAnimationSeconds: longestSeconds,
    framesPerSecond: fps,
    insertionSkipped: creations.length === 0,
    insertionTransactionSucceeded, parameterTransactionSucceeded,
    perClip,
    reproductionModel: reconstructEasing ? "prfpset-frame-sampled-approximation" : "prfpset-principal-keyframes-only",
    fidelityNote: reconstructEasing
      ? "Frame-sampled easing is an approximation; the official UXP API exposes no Point/scalar tangent or velocity surface to restore exact curves."
      : "Effects, static values and principal keyframe values/times are preserved; no dense helper keys are generated to imitate unavailable easing.",
    undoModelExpected: creations.length
      ? ["Undo imported preset parameters", "Undo inserted preset effects"]
      : ["Undo imported preset parameters"]
  };
}

async function runApplyImportedEffectPreset() {
  const button = document.getElementById("apply-imported-effect-preset");
  const nameInput = document.getElementById("prfpset-preset-name");
  const categoryInput = document.getElementById("prfpset-preset-category");
  const reconstructEasingInput = document.getElementById("prfpset-reconstruct-easing");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.applyImportedEffectPreset", requestId: String(Date.now()),
    payload: {
      name: nameInput ? nameInput.value : "",
      category: categoryInput ? categoryInput.value : "",
      reconstructEasing: Boolean(reconstructEasingInput && reconstructEasingInput.checked)
    }
  }, { "timeline.applyImportedEffectPreset": applyImportedEffectPreset });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runCompareImportedTransformPreset() {
  const button = document.getElementById("compare-imported-transform-preset");
  const nameInput = document.getElementById("prfpset-preset-name");
  const categoryInput = document.getElementById("prfpset-preset-category");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "catalog.effectPresets.compareImportedTransform",
    requestId: String(Date.now()),
    payload: { name: nameInput ? nameInput.value : "", category: categoryInput ? categoryInput.value : "" }
  }, { "catalog.effectPresets.compareImportedTransform": compareImportedTransformWithCapture });
  text("prfpset-catalog-output", JSON.stringify(result, null, 2));
  if (button) button.disabled = false;
}

async function runInspectSelectedVideoComponents() {
  const button = document.getElementById("inspect-selected-video-components");
  if (button) button.disabled = true;
  const result = await executionAdapter.execute({
    type: "timeline.inspectSelectedVideoComponents", requestId: String(Date.now()), payload: {}
  }, { "timeline.inspectSelectedVideoComponents": inspectSelectedVideoComponents });
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

function renderTransportStatus(status) {
  text("transport-status", status.status);
  text("transport-url", transport.TRANSPORT_URL);
  text("transport-connected-since", status.connectedSince);
  text("transport-last-error", status.lastError);
}

function wireTransportPanel() {
  const reconnectButton = document.getElementById("transport-reconnect");
  if (reconnectButton && !reconnectButton.dataset.wired) {
    reconnectButton.addEventListener("click", () => {
      transport.stop();
      transport.start(ACTION_HANDLERS, executionAdapter);
    });
    reconnectButton.dataset.wired = "true";
  }
  if (!wireTransportPanel.subscribed) {
    transport.onStatusChange(renderTransportStatus);
    wireTransportPanel.subscribed = true;
  }
  renderTransportStatus(transport.getStatus());
}

function wirePanel() {
  wireTransportPanel();
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
  const inspectPrfpsetButton = document.getElementById("inspect-imported-prfpset");
  if (inspectPrfpsetButton && !inspectPrfpsetButton.dataset.wired) {
    inspectPrfpsetButton.addEventListener("click", runInspectImportedEffectPreset);
    inspectPrfpsetButton.dataset.wired = "true";
  }
  const inspectBridgeButton = document.getElementById("inspect-preset-bridge-candidate");
  if (inspectBridgeButton && !inspectBridgeButton.dataset.wired) {
    inspectBridgeButton.addEventListener("click", runInspectPresetBridgeCandidate);
    inspectBridgeButton.dataset.wired = "true";
  }
  const exportBridgeButton = document.getElementById("export-preset-bridge");
  if (exportBridgeButton && !exportBridgeButton.dataset.wired) {
    exportBridgeButton.addEventListener("click", runExportPresetBridge);
    exportBridgeButton.dataset.wired = "true";
  }
  const compareImportedPresetButton = document.getElementById("compare-imported-transform-preset");
  if (compareImportedPresetButton && !compareImportedPresetButton.dataset.wired) {
    compareImportedPresetButton.addEventListener("click", runCompareImportedTransformPreset);
    compareImportedPresetButton.dataset.wired = "true";
  }
  const inspectComponentsButton = document.getElementById("inspect-selected-video-components");
  if (inspectComponentsButton && !inspectComponentsButton.dataset.wired) {
    inspectComponentsButton.addEventListener("click", runInspectSelectedVideoComponents);
    inspectComponentsButton.dataset.wired = "true";
  }
  const applyImportedEffectPresetButton = document.getElementById("apply-imported-effect-preset");
  if (applyImportedEffectPresetButton && !applyImportedEffectPresetButton.dataset.wired) {
    applyImportedEffectPresetButton.addEventListener("click", runApplyImportedEffectPreset);
    applyImportedEffectPresetButton.dataset.wired = "true";
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

// The transport (stage 5, TECHNICAL_PLAN.md) dispatches through this exact map, so a command
// received over the network can never do anything the diagnostics panel's own buttons could not
// already do - it is the same allowlisted, schema-validated action set, just a different caller.
const ACTION_HANDLERS = {
  "diagnostics.read": readDiagnostics,
  "catalog.videoEffects.resolve": resolveVideoEffectCatalog,
  "catalog.videoEffects.read": readVideoEffectCatalog,
  "catalog.videoTransitions.read": readVideoTransitionCatalog,
  "projectItems.setColorLabel": setSelectedProjectItemLabel,
  "timeline.applyVideoEffect": applyVideoEffectToSelection,
  "timeline.probeVideoEffectParameters": probeVideoEffectParameters,
  "timeline.probeStaticVideoEffectParameter": probeStaticVideoEffectParameter,
  "timeline.probeAnimatedVideoEffectParameter": probeAnimatedVideoEffectParameter,
  "timeline.captureTransformCurveReference": captureTransformCurveReference,
  "timeline.applyTransformCurveReference": applyTransformCurveReference,
  "catalog.effectPresets.importPrfpset": importPrfpsetCatalog,
  "catalog.effectPresets.inspectImported": inspectImportedEffectPreset,
  "catalog.effectPresets.inspectBridgeCandidate": inspectImportedPresetBridgeCandidate,
  "catalog.effectPresets.exportBridge": exportImportedPresetBridge,
  "catalog.effectPresets.compareImportedTransform": compareImportedTransformWithCapture,
  "timeline.inspectSelectedVideoComponents": inspectSelectedVideoComponents,
  "timeline.applyImportedEffectPreset": applyImportedEffectPreset,
  "timeline.applyAudioEffect": applyAudioEffectToSelection,
  "timeline.applyVideoTransition": applyVideoTransitionToSelection,
  "timeline.createSubsequence": createSubsequenceFromSelection,
  "timeline.createNest": createNestFromSelection,
  "timeline.insertProjectItem": insertSelectedProjectItem,
  "timeline.insertGenericItem": insertGenericItemAcrossSelection
};

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
  plugin: {
    // Fires automatically when Premiere loads the plugin, independent of the diagnostics panel ever
    // being opened - the actual "no configuration, just works" requirement behind the transport.
    create() {
      transport.start(ACTION_HANDLERS, executionAdapter);
      restoreImportedPresetCatalogFromToken();
    },
    destroy() { transport.stop(); }
  },
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
