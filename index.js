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
  // Audio piggybacks on this same action/response rather than a separate one: applyAudioEffectToSelection
  // already resolves by display name only (AudioFilterFactory has no getMatchNames/matchName-based
  // creation at all, confirmed against the official reference), so the catalog side needs nothing
  // beyond what's already used for application - no separate identity concern to track for it the
  // way video's same-index-candidate guess needs the matchNames array for.
  const audioDisplayNames = await premiere.AudioFilterFactory.getDisplayNames();
  return {
    matchNameCount: matchNames.length,
    displayNameCount: displayNames.length,
    matchNames,
    displayNames,
    audioDisplayNames,
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

// Registered as the catalog.effectPresets.read handler. Only name/category, not the full
// filters/parameters detail: timeline.applyImportedEffectPreset (execute()) already sends just
// {name, category} and re-resolves against this same in-memory catalog itself
// (findImportedEffectPresets) - the companion never needs the deep structure to dispatch a click,
// only enough to list one.
async function readEffectPresetCatalog() {
  if (!importedEffectPresetCatalog) return { available: false, presets: [] };
  return {
    available: true,
    fileName: importedEffectPresetCatalog.fileName || null,
    presets: importedEffectPresetCatalog.presets.map((preset) => ({ name: preset.name, category: preset.category }))
  };
}

// Registered as catalog.effectPresets.readFromPath - the companion already knows exactly where
// the user's .prfpset lives (it globs Documents/Adobe/Premiere Pro/*/Profile-*/ itself, mirroring
// the stable CEP product's own bridge.js::findPresetFile(), no plugin-side directory listing
// needed) and hands this the absolute path directly. Requires "fullAccess" in manifest.json's
// localFileSystem permission - granted once at install time, unlike importPrfpsetCatalog's native
// file picker below, which still exists as a manual fallback if this path ever doesn't resolve
// (a non-default install location, for instance).
async function readPrfpsetFileAtPath(action) {
  const rawPath = typeof action.payload.path === "string" ? action.payload.path.trim() : "";
  if (!rawPath) throw new Error("A .prfpset file path is required.");
  const { localFileSystem } = require("uxp").storage;
  // The file:/ URL scheme wants forward slashes even for a Windows path (file:/C:/Users/...).
  const file = await localFileSystem.getEntryWithUrl("file:/" + rawPath.replace(/\\/g, "/"));
  const xml = await file.read();
  loadPrfpsetFileIntoCatalog(file, xml);
  return readEffectPresetCatalog();
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

function resolveUniqueImportedEffectPreset(name, category) {
  const result = findImportedEffectPresets(name, category);
  if (result.matches.length !== 1) {
    const candidates = result.matches.length ? result.matches : result.named;
    const categories = candidates.slice(0, 20).map((preset) => preset.category || "(root)").join("; ");
    throw new Error(`Expected one imported preset match, found ${result.matches.length}.${categories ? ` Candidate categories: ${categories}` : ""}`);
  }
  return { ...result, preset: result.matches[0] };
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
//
// A real host comparison (captureTransformCurveReference vs. this reconstruction, "Slide In Up")
// found the reconstructed curve overshooting far past a keyframe value the real curve never crosses
// at all - traced to raw outgoingSpeed/incomingSpeed producing a bezier y-control an order of
// magnitude too large. The ratio between the reconstructed and real peak velocity in that case was
// within 1% of the sequence's own frame rate (60fps), which is what raw .prfpset speed fields turned
// out to actually be scaled by - per-FRAME, not per-second, despite averageSpeed here (measured
// from real tick deltas) being genuinely per-second. Dividing the raw speed by fps before taking the
// ratio against averageSpeed corrects the units mismatch and was confirmed to reduce the "Slide In
// Up" case's worst-sample error from 0.94 to 0.66 (normalized position units).
//
// KNOWN REMAINING LIMITATION, confirmed against the same real capture, not yet solved: "Slide In
// Up"'s Position keyframe has 100% outgoing/incoming influence on both sides (x1=1, x2=0 after
// clamping) - an extreme, uncommon ease setting. That specific x-envelope bunches most of the
// bezier parameter t range toward its x=0.5 inflection, so the eased value stays near-constant for
// a wide span of requested progress before changing rapidly - not what the real curve does. This
// wasn't a wrong-root bisection bug (the x(t) curve is still monotonic here, confirmed by hand);
// it's the simplified speed/influence model itself not matching whatever additional correction
// Adobe's own (undocumented, proprietary) conversion applies for extreme influence values. Fixing
// this fully would need many more real capture/compare data points across different speed/influence
// combinations to empirically derive that correction - not attempted here after this one data point
// showed the fps fix alone doesn't fully resolve it.
function derivePrfpsetTemporalCurve(sourceKeys, measureDistance, fps) {
  const first = sourceKeys[0];
  const second = sourceKeys[1];
  const durationSeconds = (second.ticks - first.ticks) / 254016000000;
  const startEase = parsePrfpsetKeyframeEase(first.parts);
  const endEase = parsePrfpsetKeyframeEase(second.parts);
  const distance = measureDistance(first.value.value, second.value.value);
  const averageSpeed = durationSeconds > 0 ? distance / durationSeconds : 0;
  const framesPerSecond = fps > 0 ? fps : 1;
  // Speed keeps its sign against a signed average: a keyframe reached while travelling opposite to
  // the segment's overall direction has overshot its own value and is on the way back, which pushes
  // the control point outside 0..1. Taking magnitudes here would fold that overshoot the wrong way.
  const speedRatio = (speedPerFrame) => {
    const speedPerSecond = speedPerFrame / framesPerSecond;
    return Math.abs(averageSpeed) > 1e-12 ? speedPerSecond / averageSpeed : 0;
  };
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
    model: "prfpset-speed-influence-per-frame"
  };
}

function pointDistance(start, end) {
  return Math.sqrt(end.reduce((sum, value, index) => sum + ((value - start[index]) * (value - start[index])), 0));
}

function derivePrfpsetPointCurve(sourceKeys, fps) {
  return derivePrfpsetTemporalCurve(sourceKeys, pointDistance, fps);
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

// A Hold keyframe (outgoingInterpolationCode 4) is a step function, not a curve: the value stays
// at the segment's starting keyframe for the whole segment and only jumps to the ending keyframe's
// value at the exact next keyframe time. parsePrfpsetKeyframeEase already captured this code, but
// neither sampling function below branched on it before - every segment was bezier/linear-eased
// regardless, silently smoothing what should have snapped, matching a real "curve reconstructed
// incorrectly" preset report.
function isHoldOutgoing(segmentStartKey) {
  return parsePrfpsetKeyframeEase(segmentStartKey.parts).outgoingInterpolationCode === 4;
}

function sampleImportedPointCurve(sourceKeys, offsetSeconds, fps) {
  const originTicks = sourceKeys[0].ticks;
  const requestedTicks = originTicks + (Math.max(0, offsetSeconds) * 254016000000);
  let segmentIndex = sourceKeys.length - 2;
  for (let index = 0; index < sourceKeys.length - 1; index += 1) {
    if (requestedTicks <= sourceKeys[index + 1].ticks) { segmentIndex = index; break; }
  }
  const segmentKeys = [sourceKeys[segmentIndex], sourceKeys[segmentIndex + 1]];
  if (isHoldOutgoing(segmentKeys[0])) {
    const holdValue = requestedTicks >= segmentKeys[1].ticks ? segmentKeys[1].value.value : segmentKeys[0].value.value;
    return { value: holdValue, segmentIndex, segmentProgress: requestedTicks >= segmentKeys[1].ticks ? 1 : 0, curve: null, hold: true };
  }
  const curve = derivePrfpsetPointCurve(segmentKeys, fps);
  const segmentTicks = segmentKeys[1].ticks - segmentKeys[0].ticks;
  const progress = segmentTicks > 0 ? Math.max(0, Math.min(1, (requestedTicks - segmentKeys[0].ticks) / segmentTicks)) : 0;
  return { value: importedPointAtProgress(segmentKeys, curve, progress), segmentIndex, segmentProgress: progress, curve };
}

// Scalars carry a signed delta so the ratio above keeps its meaning in both directions. Point
// parameters instead measure distance along the path, which is unsigned by construction.
function derivePrfpsetScalarCurve(sourceKeys, fps) {
  return derivePrfpsetTemporalCurve(sourceKeys, (start, end) => end - start, fps);
}

function sampleImportedScalarCurve(sourceKeys, offsetSeconds, fps) {
  const originTicks = sourceKeys[0].ticks;
  const requestedTicks = originTicks + (Math.max(0, offsetSeconds) * 254016000000);
  let segmentIndex = sourceKeys.length - 2;
  for (let index = 0; index < sourceKeys.length - 1; index += 1) {
    if (requestedTicks <= sourceKeys[index + 1].ticks) { segmentIndex = index; break; }
  }
  const segmentKeys = [sourceKeys[segmentIndex], sourceKeys[segmentIndex + 1]];
  if (isHoldOutgoing(segmentKeys[0])) {
    const holdValue = requestedTicks >= segmentKeys[1].ticks ? segmentKeys[1].value.value : segmentKeys[0].value.value;
    return { value: holdValue, segmentIndex, segmentProgress: requestedTicks >= segmentKeys[1].ticks ? 1 : 0, curve: null, hold: true };
  }
  const curve = derivePrfpsetScalarCurve(segmentKeys, fps);
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
async function ensureBin(project, binName) {
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
  return { targetBin, binCreated };
}

async function ensureBinAndMoveProjectItem(project, projectItem, binName) {
  const rootItem = await project.getRootItem();
  const { targetBin, binCreated } = await ensureBin(project, binName);

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

  // motrackerPerformNest also returns two live (non-JSON-serializable) object references for the
  // Motion Tracker's own caller - strip them here, they can't cross the transport boundary.
  const { createdSequenceObject, insertedInstanceObjects, ...serializable } =
    await motrackerPerformNest(project, sourceSequence, requestedName, binName);
  return serializable;
}

// Extracted from createNestFromSelection's own body (it's now a thin wrapper above, unchanged
// behavior) so the Motion Tracker's Nest-and-normalize step (see TECHNICAL_PLAN.md's Motion
// Tracker slice) can reuse the exact same, already host-tested nest-creation mechanics rather than
// re-deriving them - deliberately NOT re-implemented from the API docs a second time, after this
// project's own history of a different Selection-related API (TrackItemSelection.
// createEmptySelection/addItem) hanging the plugin the one other time this codebase tried to
// build something new on top of a plausible-looking but never-actually-exercised primitive.
// Returns the same serializable diagnostic shape createNestFromSelection always returned, PLUS two
// live (non-serializable) object references the caller needs for further work - `createdSequenceObject`
// and `insertedInstanceObjects` - which createNestFromSelection's own wrapper strips back out
// before handing the result to the transport (a live Premiere object can't cross that boundary).
async function motrackerPerformNest(project, sourceSequence, requestedName, binName) {
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
    // Live object references for callers that need to keep working with the result (the Motion
    // Tracker's own Nest-and-normalize step) - see this function's own docstring. Not serializable,
    // stripped by createNestFromSelection's wrapper before anything crosses the transport.
    createdSequenceObject: createdSequence,
    insertedInstanceObjects: insertedInstances,
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

// The companion's catalog identifies a Project item by a full bin path (e.g.
// "\Project.prproj\FX.palette_Assets\Adjustment Layer_1920x1080"), captured by the CEP-era
// exporter this app.py copy still reads. That value is a real, human-visible path through the
// project tree - not an opaque ID - so it can be walked with the same findDirectChildBin() the
// Nest bin-placement work already proved out, rather than relying on any cross-system identifier
// (the "nodeId" field on the same catalog entries is an ExtendScript-only value with no UXP
// counterpart, and was never usable here).
async function findProjectItemByTreePath(project, treePath) {
  const segments = String(treePath || "").split("\\").map((part) => part.trim()).filter(Boolean);
  if (segments.length === 0) return null;
  const itemName = segments[segments.length - 1];
  const binSegments = segments.slice(1, -1); // drop the leading "Project.prproj" segment
  let folder = await project.getRootItem();
  for (const segment of binSegments) {
    const next = await findDirectChildBin(folder, segment);
    if (!next) return null;
    folder = next;
  }
  const children = await folder.getItems();
  return (Array.isArray(children) ? children : []).find((child) => (child.name || "") === itemName) || null;
}

// CEP's host.jsx builds all five generic items from documented ExtendScript/QE-DOM factory calls
// (app.project.newBarsAndTone, qe.project.newColorMatte, etc.), none of which UXP has an equivalent
// for. The template-import trick (_importAdjustmentLayerFromTemplate, host.jsx) ports cleanly since
// Project.importSequences is documented UXP too: the bundled template .prproj carries a
// wrapper-sequence per resolution for bars_and_tone/black_video/transparent_video, mapped by
// generic_item_templates.json (those sequences were batch-built once by a throwaway CEP dev panel,
// removed after; see the sixth slice in TECHNICAL_PLAN.md). Color Matte is deliberately excluded:
// neither CEP nor UXP has any way to set its color after creation, so a template built once could
// never be recolored per use.
const GENERIC_ITEM_TEMPLATES = {
  adjustment_layer: { configKey: "adjustmentLayer", displayLabel: "Adjustment Layer" },
  bars_and_tone: { configKey: "barsAndTone", displayLabel: "Bars and Tone" },
  black_video: { configKey: "blackVideo", displayLabel: "Black Video" },
  transparent_video: { configKey: "transparentVideo", displayLabel: "Transparent Video" }
};

// Which Timeline track type(s) each generic item actually places a clip on - known statically
// (they're fixed templates, not arbitrary media), unlike a favorite or Project-panel item, which
// could be anything. checkTrackAvailability uses this to avoid asking the companion to create a
// track type an item was never going to use in the first place.
const GENERIC_ITEM_MEDIA_KINDS = {
  adjustment_layer: { video: true, audio: false },
  bars_and_tone: { video: true, audio: true },
  black_video: { video: true, audio: false },
  transparent_video: { video: true, audio: false }
};

async function readBundledTemplateJson(relativePath) {
  const { localFileSystem } = require("uxp").storage;
  const pluginFolder = await localFileSystem.getPluginFolder();
  const entry = await pluginFolder.getEntry(relativePath);
  return JSON.parse(await entry.read());
}

async function getBundledTemplateProjectPath() {
  const { localFileSystem } = require("uxp").storage;
  const pluginFolder = await localFileSystem.getPluginFolder();
  const entry = await pluginFolder.getEntry("assets/template_project/template_project.prproj");
  return entry.nativePath;
}

// --- Favorites (host.jsx's FX.palette_Favorites bin, read-only reference) --------------------
//
// The user curates a favorite by opening the bundled template_project.prproj (the same file the
// generic-item templates live in) and dragging media/sequences into a root bin named
// FX.palette_Favorites (legacy alias EffectPalette_Favorites) - optionally in sub-bins, which
// become the favorite's category. UXP has no documented way to inspect an unopened project's bin
// structure, so scanning (unlike importing a specific sequence by id, which importSequences can do
// blind) can only happen while that template project is the one the user currently has open -
// exactly the same guard host.jsx's own getTemplateFavoritesListSafe uses.

const FAVORITES_BIN_NAMES = ["FX.palette_Favorites", "EffectPalette_Favorites"];

// Project.path was observed to come back with Windows' "\\?\" extended-length-path prefix
// (\\?\C:\...) while the plugin's own bundled-file path (localFileSystem/nativePath) does not -
// otherwise byte-identical for the same file. Stripped here so the two are actually comparable.
function normalizePath(value) {
  return String(value || "").trim().toLowerCase().replace(/\//g, "\\").replace(/^\\\\\?\\/, "").replace(/\\+$/, "");
}

async function findFavoritesBin(rootItem) {
  for (const name of FAVORITES_BIN_NAMES) {
    const found = await findDirectChildBin(rootItem, name);
    if (found) return found;
  }
  return null;
}

// Mirrors host.jsx's _collectFavoriteItemsRecursive: sub-bins become the favorite's "category"
// path (joined the same way, " > "); each leaf is classified as a "sequence" favorite (resolvable
// sequence guid) or a "media" favorite (resolvable media file path).
async function collectFavoriteItems(folderItem, categoryPath, treeSegments, out) {
  const children = await folderItem.getItems();
  for (const child of Array.isArray(children) ? children : []) {
    let childFolder = null;
    try { childFolder = premiere.FolderItem.cast(child); } catch (error) { childFolder = null; }
    if (childFolder) {
      const nextCategory = categoryPath ? `${categoryPath} > ${child.name}` : String(child.name || "");
      await collectFavoriteItems(childFolder, nextCategory, [...treeSegments, child.name || ""], out);
      continue;
    }

    let clipItem = null;
    try { clipItem = premiere.ClipProjectItem.cast(child); } catch (error) { clipItem = null; }
    if (!clipItem) continue;

    let isSeq = false;
    try { isSeq = await clipItem.isSequence(); } catch (error) { isSeq = false; }

    let sequenceID = "";
    let mediaPath = "";
    if (isSeq) {
      try {
        const sequence = await clipItem.getSequence();
        sequenceID = sequence ? guidToString(sequence.guid) : "";
      } catch (error) { sequenceID = ""; }
    } else {
      try { mediaPath = (await clipItem.getMediaFilePath()) || ""; } catch (error) { mediaPath = ""; }
    }

    out.push({
      name: child.name || "",
      category: categoryPath || "Favoritos",
      favoriteType: isSeq ? "sequence" : "media",
      sourceTreePath: [...treeSegments, child.name || ""].join("\\"),
      mediaPath,
      sequenceID,
      isSequence: isSeq,
      itemType: String(child.type || "")
    });
  }
}

// Registered as the catalog.favorites.read handler.
async function readFavoritesCatalog() {
  const project = await premiere.Project.getActiveProject();
  if (!project) return { applicable: false, items: [], activeProjectPath: null, templatePath: null };

  const templatePath = await getBundledTemplateProjectPath();
  if (normalizePath(project.path) !== normalizePath(templatePath)) {
    return { applicable: false, items: [], activeProjectPath: project.path, templatePath };
  }

  const rootItem = await project.getRootItem();
  const favoritesBin = await findFavoritesBin(rootItem);
  if (!favoritesBin) return { applicable: true, items: [], sourceProjectPath: project.path };

  const items = [];
  await collectFavoriteItems(favoritesBin, "", [], items);
  return { applicable: true, items, sourceProjectPath: project.path };
}

// --- Project items catalog (host.jsx's getProjectItemsListSafe/_collectProjectItemsRecursive,
// read-only reference) ---------------------------------------------------------------------
//
// Unlike favorites, this scans whatever project is currently open unconditionally - CEP itself has
// no template-project guard here either. The one thing worth excluding is the bundled template
// project's own contents (Adjustment Layer/Bars and Tone/... templates, favorites) if the user
// happens to have it open: none of that is meant to show up as an ordinary "project item" to insert,
// only through the generic-item/favorite mechanisms that already exist for it.
//
// treePath mirrors host.jsx's own convention exactly, since findProjectItemByTreePath (already
// built and host-tested for timeline.insertProjectItem) parses this exact shape: a leading "\",
// the project's own filename (with extension), then each bin name down to the item, all "\"-joined.
async function collectProjectItemCatalog(folderItem, categoryPath, treeSegments, out) {
  const children = await folderItem.getItems();
  for (const child of Array.isArray(children) ? children : []) {
    let childFolder = null;
    try { childFolder = premiere.FolderItem.cast(child); } catch (error) { childFolder = null; }
    if (childFolder) {
      const nextCategory = categoryPath ? `${categoryPath} > ${child.name}` : String(child.name || "");
      await collectProjectItemCatalog(childFolder, nextCategory, [...treeSegments, child.name || ""], out);
      continue;
    }

    let clipItem = null;
    try { clipItem = premiere.ClipProjectItem.cast(child); } catch (error) { clipItem = null; }
    let isSeq = false;
    let mediaPath = "";
    if (clipItem) {
      try { isSeq = await clipItem.isSequence(); } catch (error) { isSeq = false; }
      if (!isSeq) {
        try { mediaPath = (await clipItem.getMediaFilePath()) || ""; } catch (error) { mediaPath = ""; }
      }
    }

    let nodeId = "";
    try { nodeId = await child.getId(); } catch (error) { nodeId = ""; }

    out.push({
      name: child.name || "",
      category: categoryPath || "Projeto",
      nodeId,
      itemType: String(child.type || ""),
      isSequence: isSeq,
      treePath: "\\" + [...treeSegments, child.name || ""].join("\\"),
      mediaPath
    });
  }
}

// Registered as the catalog.projectItems.read handler.
async function readProjectItemCatalog() {
  const project = await premiere.Project.getActiveProject();
  if (!project) return { items: [] };

  const templatePath = await getBundledTemplateProjectPath();
  if (normalizePath(project.path) === normalizePath(templatePath)) {
    // The template project's own contents (generic-item templates, favorites) aren't ordinary
    // project items - matching host.jsx's isTemplateAsset exclusion, just done by skipping the
    // whole scan here instead of filtering per-item, since this entire project is template-only.
    return { items: [] };
  }

  const projectFileName = String(project.path || "").split(/[\\/]/).pop() || project.name || "Project";
  const rootItem = await project.getRootItem();
  const items = [];
  await collectProjectItemCatalog(rootItem, "", [projectFileName], items);
  return { items };
}

// Mirrors host.jsx's _importFavoriteProjectItem: search the whole project for an already-imported
// copy first (by media path for a media favorite, by name+isSequence for a sequence favorite,
// since a favorite sequence has no separately-tracked identity once imported), otherwise import
// fresh via the matching documented UXP call and organize the result into FX.palette_Assets.
// Unlike a generic item's template wrapper sequence, an imported favorite SEQUENCE is the actual
// favorited content, not throwaway packaging - it is kept, not deleted, after import.
async function resolveFavoriteProjectItem(project, favorite) {
  const rootItem = await project.getRootItem();
  const name = String(favorite.name || "");

  if (favorite.favoriteType === "sequence" || favorite.sequenceID) {
    const existing = await findFirstProjectItemRecursive(rootItem, async (item) => {
      if ((item.name || "") !== name) return false;
      let clipItem = null;
      try { clipItem = premiere.ClipProjectItem.cast(item); } catch (error) { return false; }
      try { return await clipItem.isSequence(); } catch (error) { return false; }
    });
    if (existing) return { projectItem: existing, imported: false };

    if (!favorite.sequenceID) throw new Error(`Favorite "${name}" has no sequenceID to import.`);
    const sourcePath = String(favorite.sourceProjectPath || "").trim() || await getBundledTemplateProjectPath();

    const beforeChildren = await rootItem.getItems();
    const beforeIds = new Set();
    for (const item of Array.isArray(beforeChildren) ? beforeChildren : []) {
      try { beforeIds.add(await item.getId()); } catch (error) { /* ignore */ }
    }

    const importSucceeded = await project.importSequences(sourcePath, [premiere.Guid.fromString(favorite.sequenceID)]);
    if (!importSucceeded) throw new Error(`Premiere rejected importing the favorite sequence "${name}".`);

    const afterChildren = await rootItem.getItems();
    const newRootItems = [];
    for (const item of Array.isArray(afterChildren) ? afterChildren : []) {
      let id = null;
      try { id = await item.getId(); } catch (error) { id = null; }
      if (id === null || !beforeIds.has(id)) newRootItems.push(item);
    }

    for (const candidate of newRootItems) {
      await ensureBinAndMoveProjectItem(project, candidate, "FX.palette_Assets");
    }

    const resolved = await findFirstProjectItemRecursive(rootItem, async (item) => {
      if ((item.name || "") !== name) return false;
      let clipItem = null;
      try { clipItem = premiere.ClipProjectItem.cast(item); } catch (error) { return false; }
      try { return await clipItem.isSequence(); } catch (error) { return false; }
    });
    if (!resolved) throw new Error(`Imported the favorite sequence but could not find "${name}" afterward.`);
    return { projectItem: resolved, imported: true };
  }

  const mediaPath = String(favorite.mediaPath || "").trim();
  if (!mediaPath) throw new Error(`Favorite "${name}" has no mediaPath to import.`);

  const existingMedia = await findFirstProjectItemRecursive(rootItem, async (item) => {
    let clipItem = null;
    try { clipItem = premiere.ClipProjectItem.cast(item); } catch (error) { return false; }
    let path = "";
    try { path = (await clipItem.getMediaFilePath()) || ""; } catch (error) { path = ""; }
    return !!path && normalizePath(path) === normalizePath(mediaPath);
  });
  if (existingMedia) return { projectItem: existingMedia, imported: false };

  // Unlike the sequence branch (importSequences always lands new items at project root,
  // regardless of any bin), importFiles takes the destination bin directly - no separate
  // find-then-move step needed here.
  const { targetBin } = await ensureBin(project, "FX.palette_Assets");
  const importSucceeded = await project.importFiles([mediaPath], true, targetBin, false);
  if (!importSucceeded) throw new Error(`Premiere rejected importing the favorite media "${name}".`);

  const resolvedMedia = await findFirstProjectItemRecursive(rootItem, async (item) => {
    let clipItem = null;
    try { clipItem = premiere.ClipProjectItem.cast(item); } catch (error) { return false; }
    let path = "";
    try { path = (await clipItem.getMediaFilePath()) || ""; } catch (error) { path = ""; }
    return !!path && normalizePath(path) === normalizePath(mediaPath);
  });
  if (!resolvedMedia) throw new Error(`Imported the favorite media but could not find "${name}" afterward.`);
  return { projectItem: resolvedMedia, imported: true };
}

// Mirrors _collectProjectItemsMatching/_findFirstProjectItemMatching (host.jsx): the matcher is
// applied to every node in the project tree, root included, not just leaves. seenIds/depth guard
// against a hang if the tree ever has a cycle (a bin nested inside itself) - a real search here was
// observed to hang indefinitely with no error, which a bare recursive walk gives no way to diagnose
// or recover from. matcher may be sync or async (always awaited) - a plain boolean-returning
// function works unchanged, since await on a non-promise value just resolves to that value.
async function findFirstProjectItemRecursive(folderItem, matcher, seenIds, depth) {
  seenIds = seenIds || new Set();
  depth = depth || 0;
  if (depth > 64) return null;
  let selfId = null;
  try { selfId = await folderItem.getId(); } catch (error) { selfId = null; }
  if (selfId !== null) {
    if (seenIds.has(selfId)) return null;
    seenIds.add(selfId);
  }
  if (await matcher(folderItem)) return folderItem;
  const children = await folderItem.getItems();
  for (const child of Array.isArray(children) ? children : []) {
    if (await matcher(child)) return child;
    let childFolder = null;
    try { childFolder = premiere.FolderItem.cast(child); } catch (error) { childFolder = null; }
    if (childFolder) {
      const found = await findFirstProjectItemRecursive(childFolder, matcher, seenIds, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

function expectedGenericItemName(displayLabel, width, height) {
  return `${displayLabel}_${width}x${height}`;
}

// Mirrors _importAdjustmentLayerFromTemplate (host.jsx): reuse an already-organized item of the
// right size if one exists anywhere in the project; otherwise import the closest-resolution
// template sequence, move whatever landed at project root into FX.palette_Assets, locate the item
// by its expected name, then delete the now-empty imported wrapper sequence(s) - same order host.jsx
// itself uses, including its quirk of stopping at the first candidate that contains a match rather
// than moving every newly-added root item unconditionally.
async function ensureGenericProjectItem(project, genericKey, targetWidth, targetHeight) {
  // Fire-and-forget progress lines to the companion's own console (transport.js's sendDiagnosticLog)
  // - added to pin down exactly which step a hang was in for black_video/transparent_video (the
  // WebSocket response itself only ever gets sent once this whole function resolves/throws, so a
  // stuck step produces zero visible output otherwise; the plugin's own UXP DevTools console is
  // harder to reach than the companion's log, which was already being captured).
  const log = (step) => transport.sendDiagnosticLog(`[ensureGenericProjectItem:${genericKey}] ${step}`);

  log("start");
  const config = GENERIC_ITEM_TEMPLATES[genericKey];
  if (!config) throw new Error(`Generic item "${genericKey}" has no UXP template mapping yet.`);

  log("reading bundled template json");
  const json = await readBundledTemplateJson("assets/template_project/generic_item_templates.json");
  const section = json[config.configKey];
  const templates = section && Array.isArray(section.templates) ? section.templates : [];
  if (templates.length === 0) throw new Error(`No template entries configured for "${genericKey}".`);

  let chosen = null;
  let bestScore = Infinity;
  for (const entry of templates) {
    if (!entry || !entry.sequenceID) continue;
    const width = Number(entry.width) || 0;
    const height = Number(entry.height) || 0;
    const score = Math.abs(width - targetWidth) + Math.abs(height - targetHeight);
    if (score < bestScore) { bestScore = score; chosen = entry; }
  }
  if (!chosen) throw new Error(`No usable template entry found for "${genericKey}".`);
  log(`chose template "${chosen.name}" (${chosen.sequenceID})`);

  const expectedName = expectedGenericItemName(config.displayLabel, chosen.width, chosen.height);
  const rootItem = await project.getRootItem();

  log("searching for an already-existing item");
  const existing = await findFirstProjectItemRecursive(rootItem, (item) => (item.name || "") === expectedName);
  if (existing) { log("found existing item, done"); return { projectItem: existing, imported: false, templateName: chosen.name }; }

  log("resolving bundled template project path");
  const templateProjectPath = await getBundledTemplateProjectPath();
  // Identified by id, not by name: a name-based diff here previously came back empty on every
  // attempt once a first hung/incomplete run had already left an unmoved orphan of the same name at
  // root - every later import legitimately added a new (differently-identified) item, but the name
  // match against that leftover orphan made it look like nothing new had appeared, so the item never
  // even got looked for, let alone moved - the exact same class of bug the sequence-cleanup diff
  // below was already fixed for.
  const beforeChildren = await rootItem.getItems();
  const beforeIds = new Set();
  for (const item of Array.isArray(beforeChildren) ? beforeChildren : []) {
    try { beforeIds.add(await item.getId()); } catch (error) { /* items with no id can't be diffed by id */ }
  }
  const beforeSequences = await project.getSequences();
  const beforeSequenceGuids = new Set(beforeSequences.map((sequence) => guidToString(sequence.guid)));

  log(`calling Project.importSequences(${templateProjectPath}, [${chosen.sequenceID}])`);
  const importSucceeded = await project.importSequences(templateProjectPath, [premiere.Guid.fromString(chosen.sequenceID)]);
  log(`importSequences returned ${importSucceeded}`);
  if (!importSucceeded) throw new Error("Premiere rejected importing the generic-item template project.");

  const afterChildren = await rootItem.getItems();
  const newRootItems = [];
  for (const item of Array.isArray(afterChildren) ? afterChildren : []) {
    let id = null;
    try { id = await item.getId(); } catch (error) { id = null; }
    if (id === null || !beforeIds.has(id)) newRootItems.push(item);
  }
  log(`${newRootItems.length} new root item(s) after import`);

  let resolvedItem = null;
  for (const candidate of newRootItems) {
    let candidateFolder = null;
    try { candidateFolder = premiere.FolderItem.cast(candidate); } catch (error) { candidateFolder = null; }
    log(`moving candidate "${candidate.name}" into FX.palette_Assets`);
    await ensureBinAndMoveProjectItem(project, candidate, "FX.palette_Assets");
    const found = candidateFolder
      ? await findFirstProjectItemRecursive(candidateFolder, (item) => (item.name || "") === expectedName)
      : ((candidate.name || "") === expectedName ? candidate : null);
    if (found) { resolvedItem = found; break; }
  }
  if (!resolvedItem) {
    log("no match among moved candidates, searching whole project as fallback");
    resolvedItem = await findFirstProjectItemRecursive(rootItem, (item) => (item.name || "") === expectedName);
  }
  if (!resolvedItem) throw new Error(`Imported the template but could not find "${expectedName}" afterward.`);
  log("resolved item, cleaning up imported sequence(s)");

  // Identified by guid difference, not by name === chosen.name: a name-based filter previously
  // used here left this array empty (silently, since deleteSequence was then just never called)
  // whenever the imported sequence's actual name didn't exactly match, which is why the
  // AL_TEMPLATE_* wrapper sequence was observed still sitting in the project after a real import.
  const afterSequences = await project.getSequences();
  const importedSequences = afterSequences.filter((sequence) => !beforeSequenceGuids.has(guidToString(sequence.guid)));

  const sequenceCleanup = [];
  for (const sequence of importedSequences) {
    let deleted = false;
    let deleteError = null;
    try {
      deleted = await project.deleteSequence(sequence);
    } catch (error) {
      deleteError = error && error.message ? error.message : String(error);
    }
    sequenceCleanup.push({ name: sequence.name || null, deleted, deleteError });
  }

  log("done");
  return { projectItem: resolvedItem, imported: true, templateName: chosen.name, sequenceCleanup };
}

// Everything below ports host.jsx's _resolveInsertionTracks/_findAvailableVideoTrackAtTicks/
// _findAvailableAudioTrackAtTicks/_findAvailableVideoTrackInRange/_projectItemShouldSpanSelection/
// _selectionVideoSpan (read-only reference) to documented UXP calls. What CEP does beyond this -
// creating a brand new track via qe.project.addTracks() when every existing track is occupied -
// is QE-DOM-only with no known UXP equivalent, so this falls back to the originally resolved
// track instead, exactly like host.jsx's own fallback when track creation isn't attempted/fails.

async function trackHasClipAtTicks(track, ticks) {
  const items = await track.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
  for (const item of items) {
    const start = BigInt((await item.getStartTime()).ticks);
    const end = BigInt((await item.getEndTime()).ticks);
    if (start <= ticks && ticks < end) return true;
  }
  return false;
}

async function trackHasClipInRange(track, startTicks, endTicks) {
  const items = await track.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
  for (const item of items) {
    const start = BigInt((await item.getStartTime()).ticks);
    const end = BigInt((await item.getEndTime()).ticks);
    if (start < endTicks && startTicks < end) return true;
  }
  return false;
}

// Each returns { index, hadAvailableTrack }. When no existing track is free, CEP's own fallback
// (host.jsx) is to give up and reuse the original starting track - but UXP has no documented way
// to create a new track (checked exhaustively: Sequence, SequenceEditor, VideoTrack, AudioTrack,
// SequenceSettings, Application, and the full official changelog from the 25.2.0 beta through
// 26.3.0 - track renaming was added, track creation never was; CEP's own qe.project.addTracks is
// the legacy QE DOM this project has deliberately never used). Falling back to the *last* existing
// track instead of the original starting one is a deliberate improvement over host.jsx's own
// fallback: reusing the starting track risks silently overlapping the very clip the user just
// selected, which the last track is less likely to already contain. hadAvailableTrack lets the
// caller report when this fallback path was taken instead of hiding it.
async function findAvailableVideoTrackAtTicks(sequence, startIndex, ticks) {
  const count = await sequence.getVideoTrackCount();
  for (let index = startIndex; index < count; index += 1) {
    if (!(await trackHasClipAtTicks(await sequence.getVideoTrack(index), ticks))) return { index, hadAvailableTrack: true };
  }
  return { index: count > 0 ? count - 1 : startIndex, hadAvailableTrack: false };
}

async function findAvailableVideoTrackInRange(sequence, startIndex, startTicks, endTicks) {
  const count = await sequence.getVideoTrackCount();
  for (let index = startIndex; index < count; index += 1) {
    if (!(await trackHasClipInRange(await sequence.getVideoTrack(index), startTicks, endTicks))) return { index, hadAvailableTrack: true };
  }
  return { index: count > 0 ? count - 1 : startIndex, hadAvailableTrack: false };
}

async function findAvailableAudioTrackAtTicks(sequence, startIndex, ticks) {
  const count = await sequence.getAudioTrackCount();
  for (let index = startIndex; index < count; index += 1) {
    if (!(await trackHasClipAtTicks(await sequence.getAudioTrack(index), ticks))) return { index, hadAvailableTrack: true };
  }
  return { index: count > 0 ? count - 1 : startIndex, hadAvailableTrack: false };
}

// Mirrors _resolveInsertionTracks: the track of the first selected item of each kind, or 0 if
// nothing of that kind is selected.
async function resolveInsertionTracks(sequence) {
  const videoTrackCount = await sequence.getVideoTrackCount();
  const audioTrackCount = await sequence.getAudioTrackCount();
  const audioMediaTypes = new Set();
  for (let index = 0; index < audioTrackCount; index += 1) {
    audioMediaTypes.add(guidToString(await (await sequence.getAudioTrack(index)).getMediaType()));
  }
  const selection = await sequence.getSelection();
  const items = selection ? await selection.getTrackItems() : [];
  let videoTrackIndex = 0;
  let audioTrackIndex = 0;
  for (const item of Array.isArray(items) ? items : []) {
    if (!audioMediaTypes.has(guidToString(await item.getMediaType()))) continue;
    const trackIndex = await item.getTrackIndex();
    if (trackIndex >= 0 && trackIndex < audioTrackCount) { audioTrackIndex = trackIndex; break; }
  }
  for (const item of Array.isArray(items) ? items : []) {
    if (audioMediaTypes.has(guidToString(await item.getMediaType()))) continue;
    const trackIndex = await item.getTrackIndex();
    if (trackIndex >= 0 && trackIndex < videoTrackCount) { videoTrackIndex = trackIndex; break; }
  }
  return { videoTrackIndex, audioTrackIndex };
}

// Mirrors _projectItemShouldSpanSelection's name regex exactly - CEP itself has no official
// per-item-type flag for this either, it matches display name the same way.
function projectItemShouldSpanSelection(name) {
  return /adjustment layer|bars and tone|black video|color matte|transparent video|universal counting leader/i
    .test(String(name || ""));
}

// Mirrors _selectionVideoSpan: the [min start, max end) across every selected *video* item.
async function selectionVideoSpan(sequence) {
  const videoTrackCount = await sequence.getVideoTrackCount();
  const videoMediaTypes = new Set();
  for (let index = 0; index < videoTrackCount; index += 1) {
    videoMediaTypes.add(guidToString(await (await sequence.getVideoTrack(index)).getMediaType()));
  }
  const selection = await sequence.getSelection();
  const items = selection ? await selection.getTrackItems() : [];
  let minStart = null;
  let maxEnd = null;
  let count = 0;
  for (const item of Array.isArray(items) ? items : []) {
    if (!videoMediaTypes.has(guidToString(await item.getMediaType()))) continue;
    const start = BigInt((await item.getStartTime()).ticks);
    const end = BigInt((await item.getEndTime()).ticks);
    if (end <= start) continue;
    if (minStart === null || start < minStart) minStart = start;
    if (maxEnd === null || end > maxEnd) maxEnd = end;
    count += 1;
  }
  if (minStart === null || maxEnd === null || count < 1) return null;
  return { startTicks: minStart, endTicks: maxEnd, count };
}

// Read-only pre-flight for the companion's own "no free track -> drive Add Tracks... first" flow
// (UXP has no API to create a Timeline track itself, so that has to happen via a native keystroke
// before insertion, not after - trying to undo an already-committed insertion instead was tried
// first and caused a real host hang, see TECHNICAL_PLAN.md). Deliberately checks at the current
// playhead rather than reproducing insertSelectedProjectItem's selection-span logic: this only ever
// decides whether to bother creating a track before the real insertion, which still runs its own
// exact (and unchanged) availability check regardless, so an imprecise pre-check here can only ever
// cost a redundant "add track" round trip, never a wrong final placement.
async function checkTrackAvailability(action) {
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before checking Timeline tracks.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before checking Timeline tracks.");

  const genericKey = typeof action.payload.genericKey === "string" ? action.payload.genericKey.trim() : "";
  // Only a known generic item's fixed template tells us for sure which media kind(s) it needs -
  // a favorite or Project-panel item is arbitrary media, so both kinds are assumed needed for
  // those rather than guessing from e.g. a file extension.
  const mediaKinds = GENERIC_ITEM_MEDIA_KINDS[genericKey] || { video: true, audio: true };

  const resolvedTracks = await resolveInsertionTracks(sequence);
  const insertionTicks = BigInt((await sequence.getPlayerPosition()).ticks);
  const videoTrackResult = await findAvailableVideoTrackAtTicks(sequence, resolvedTracks.videoTrackIndex, insertionTicks);
  const audioTrackResult = await findAvailableAudioTrackAtTicks(sequence, resolvedTracks.audioTrackIndex, insertionTicks);

  return {
    needsVideo: mediaKinds.video,
    needsAudio: mediaKinds.audio,
    videoAvailable: videoTrackResult.hadAvailableTrack,
    audioAvailable: audioTrackResult.hadAvailableTrack,
    videoTrackIndex: resolvedTracks.videoTrackIndex,
    audioTrackIndex: resolvedTracks.audioTrackIndex
  };
}

async function insertSelectedProjectItem(action) {
  const editMode = action.payload.editMode === "OVERWRITE" ? "OVERWRITE" : "INSERT";
  const treePath = typeof action.payload.treePath === "string" ? action.payload.treePath.trim() : "";
  // Explicit track indices (the diagnostics panel's own numeric inputs) are honored as before;
  // omitting them (the companion's own payload) triggers the same selection-based auto-targeting
  // host.jsx always did, rather than defaulting to hardcoded track 0.
  const explicitVideoTrackIndex = action.payload.videoTrackIndex !== undefined
    ? readNonNegativeTrackIndex(action.payload.videoTrackIndex, "Video track index") : null;
  const explicitAudioTrackIndex = action.payload.audioTrackIndex !== undefined
    ? readNonNegativeTrackIndex(action.payload.audioTrackIndex, "Audio track index") : null;

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("Open a project before inserting a Project item.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("Open a sequence before inserting a Project item.");

  const genericKey = typeof action.payload.genericKey === "string" ? action.payload.genericKey.trim() : "";
  const favorite = action.payload.favorite && typeof action.payload.favorite === "object" ? action.payload.favorite : null;

  let projectItem;
  let genericItemResolution = null;
  let favoriteResolution = null;
  if (favorite) {
    // Companion-driven "favorite item" request: find-or-import from the favorite's own source
    // project (usually the same bundled template project favorites are curated in) rather than
    // requiring the item to already be selected in the Project panel.
    if (!favorite.name) throw new Error("A favorite item name is required.");
    const resolved = await resolveFavoriteProjectItem(project, favorite);
    projectItem = resolved.projectItem;
    favoriteResolution = { favoriteType: favorite.favoriteType || null, imported: resolved.imported };
  } else if (genericKey) {
    // Companion-driven "generic item" request (Adjustment Layer, Bars and Tone, ...): find-or-import
    // from the bundled template project rather than resolving an already-existing selection/path.
    const frameSize = await sequence.getFrameSize();
    const resolved = await ensureGenericProjectItem(project, genericKey, Number(frameSize.width) || 0, Number(frameSize.height) || 0);
    projectItem = resolved.projectItem;
    genericItemResolution = {
      genericKey,
      imported: resolved.imported,
      templateName: resolved.templateName,
      sequenceCleanup: resolved.sequenceCleanup || null
    };
  } else if (treePath) {
    // Companion-driven: resolve by path instead of requiring the item to already be selected in
    // the Project panel - there is no official API to set that selection (ProjectItemSelection is
    // read-only), so a search-then-apply flow has no other way to target a specific item.
    projectItem = await findProjectItemByTreePath(project, treePath);
    if (!projectItem) throw new Error(`No Project item found at path "${treePath}".`);
  } else {
    // Diagnostics-panel probe: still supports "select in the Project panel, then click apply".
    const selection = await premiere.ProjectUtils.getSelection(project);
    const projectItems = selection ? await selection.getItems() : [];
    if (!Array.isArray(projectItems) || projectItems.length !== 1) {
      throw new Error("Select exactly one item in the Project panel before insertion.");
    }
    projectItem = projectItems[0];
  }
  if (!projectItem || typeof projectItem.getId !== "function") {
    throw new Error("The selected Project item is not insertable.");
  }
  const projectItemId = await projectItem.getId();

  const resolvedTracks = await resolveInsertionTracks(sequence);
  let videoTrackIndex = explicitVideoTrackIndex !== null ? explicitVideoTrackIndex : resolvedTracks.videoTrackIndex;
  let audioTrackIndex = explicitAudioTrackIndex !== null ? explicitAudioTrackIndex : resolvedTracks.audioTrackIndex;

  const shouldSpanSelection = projectItemShouldSpanSelection(projectItem.name);
  const span = shouldSpanSelection ? await selectionVideoSpan(sequence) : null;

  let insertionTicks = span ? span.startTicks : BigInt((await sequence.getPlayerPosition()).ticks);
  const insertionTime = premiere.TickTime.createWithTicks(insertionTicks.toString());

  const videoTrackResult = span
    ? await findAvailableVideoTrackInRange(sequence, videoTrackIndex, span.startTicks, span.endTicks)
    : await findAvailableVideoTrackAtTicks(sequence, videoTrackIndex, insertionTicks);
  const audioTrackResult = await findAvailableAudioTrackAtTicks(sequence, audioTrackIndex, insertionTicks);
  videoTrackIndex = videoTrackResult.index;
  audioTrackIndex = audioTrackResult.index;

  const instancesBefore = await findProjectItemInstancesAtTime(sequence, projectItemId, insertionTime);
  const editor = premiere.SequenceEditor.getEditor(sequence);
  let transactionSucceeded = false;

  project.lockedAccess(() => {
    const insertionAction = editMode === "OVERWRITE"
      ? editor.createOverwriteItemAction(projectItem, insertionTime, videoTrackIndex, audioTrackIndex)
      : editor.createInsertProjectItemAction(projectItem, insertionTime, videoTrackIndex, audioTrackIndex, false);
    transactionSucceeded = project.executeTransaction((compoundAction) => {
      compoundAction.addAction(insertionAction);
    }, `FX.palette: ${editMode === "OVERWRITE" ? "Overwrite" : "Insert"} ${projectItem.name || "Project item"}`);
  });

  if (!transactionSucceeded) throw new Error("Premiere rejected the Project item insertion transaction.");

  const instancesAfter = await findProjectItemInstancesAtTime(sequence, projectItemId, insertionTime);
  const insertedInstanceCount = Math.max(0, instancesAfter.length - instancesBefore.length);

  // Mirrors host.jsx: after insertion, stretch the newly inserted clip's end to match the
  // selection span exactly, rather than leaving it at the item's own default/still-image duration.
  let spanTrimSucceeded = null;
  if (span && insertedInstanceCount > 0) {
    const insertedOnVideoTrack = instancesAfter
      .slice(instancesBefore.length)
      .find((entry) => entry.mediaKind === "video" && entry.trackIndex === videoTrackIndex);
    if (insertedOnVideoTrack) {
      // Re-locate the inserted TrackItem object by start time + name, matching host.jsx's own
      // _findClipByStartOnTrack - findProjectItemInstancesAtTime already confirmed it exists but
      // only returns plain data, not the object createSetEndAction needs to be called on.
      const videoTrack = await sequence.getVideoTrack(videoTrackIndex);
      const trackItems = await videoTrack.getTrackItems(premiere.Constants.TrackItemType.CLIP, false);
      let targetItem = null;
      for (const item of trackItems) {
        const start = BigInt((await item.getStartTime()).ticks);
        if (start === span.startTicks && String(await item.getName()) === projectItem.name) { targetItem = item; break; }
      }
      if (targetItem && typeof targetItem.createSetEndAction === "function") {
        project.lockedAccess(() => {
          const setEndAction = targetItem.createSetEndAction(premiere.TickTime.createWithTicks(span.endTicks.toString()));
          spanTrimSucceeded = project.executeTransaction((compoundAction) => {
            compoundAction.addAction(setEndAction);
          }, `FX.palette: Stretch ${projectItem.name || "Project item"} to selection`);
        });
      } else {
        spanTrimSucceeded = false;
      }
    }
  }

  const insertedInstances = instancesAfter.slice(instancesBefore.length);
  // videoTrackResult/audioTrackResult are computed unconditionally for every insertion (Premiere's
  // own createInsertProjectItemAction takes both track indices regardless of what the item actually
  // contains), so an occupied video track would read as a "fallback" even for an audio-only item
  // that never touched a video track at all. Cross-checking against what actually landed
  // (insertedInstances' own mediaKind) is what keeps a fallback flag limited to a media kind this
  // specific insertion really needed a track for.
  const trackFallback = {
    video: !videoTrackResult.hadAvailableTrack && insertedInstances.some((entry) => entry.mediaKind === "video"),
    audio: !audioTrackResult.hadAvailableTrack && insertedInstances.some((entry) => entry.mediaKind === "audio")
  };

  return {
    projectItem: {
      id: projectItemId,
      name: projectItem.name || null,
      type: projectItem.type
    },
    genericItemResolution,
    favoriteResolution,
    editMode,
    insertionPoint: { ticks: insertionTicks.toString() },
    spanSelection: span ? { startTicks: span.startTicks.toString(), endTicks: span.endTicks.toString(), sourceItemCount: span.count } : null,
    spanTrimSucceeded,
    requestedTracks: { videoTrackIndex, audioTrackIndex },
    // true only when this insertion actually placed a clip of that media kind AND every existing
    // track of that kind at the target time/range was occupied, so UXP had no way to create a new
    // one and placed the item on the last existing track instead (see the comment above
    // findAvailableVideoTrackAtTicks) - a confirmed platform limit, not a bug.
    trackFallback,
    matchingInstanceCountBefore: instancesBefore.length,
    matchingInstanceCountAfter: instancesAfter.length,
    insertedInstanceCount,
    insertedInstances,
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
                ? sampleImportedPointCurve(item.sourceKeys, localOffsetSeconds, fps).value
                : sampleImportedScalarCurve(item.sourceKeys, localOffsetSeconds, fps).value;
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
      ? "Frame-sampled easing is an approximation; the official UXP API exposes no Point/scalar tangent or velocity surface to restore exact curves. Host-verified accurate for typical speed/influence values; a keyframe with 100% influence on both sides (an extreme, uncommon ease setting) is a known-inaccurate case - see derivePrfpsetTemporalCurve's comment."
      : "Effects, static values and principal keyframe values/times are preserved; no dense helper keys are generated to imitate unavailable easing.",
    undoModelExpected: creations.length
      ? ["Undo imported preset parameters", "Undo inserted preset effects"]
      : ["Undo imported preset parameters"]
  };
}

// --- Motion Tracker (ported from pFX-Tracker_UXP/uxp/premiere.js, read-only reference project -
// see TECHNICAL_PLAN.md's Motion Tracker slice) -------------------------------------------------
//
// getClipInfo/applyTrack mirror the CEP product's own jsx/host.jsx mt_getClipInfo/mt_applyTrack,
// ported onto the official premierepro module instead of ExtendScript + the undocumented QE DOM.
// guidToString already exists above (line 19) and is reused as-is - everything else here is new.

function motrackerNorm(s) {
  return String(s == null ? "" : s).toLowerCase().replace(/\s+/g, " ").trim();
}

const MOTRACKER_TRANSFORM_MATCH_NAME = "AE.ADBE Geometry2";

async function motrackerGetVideoMediaTypes(sequence) {
  const count = await sequence.getVideoTrackCount();
  const types = new Set();
  for (let i = 0; i < count; i += 1) {
    const track = await sequence.getVideoTrack(i);
    types.add(guidToString(await track.getMediaType()));
  }
  return types;
}

/** First selected TrackItem that sits on a video track, or null. */
async function motrackerGetSelectedVideoClip(sequence) {
  const selection = await sequence.getSelection();
  const items = selection ? await selection.getTrackItems() : [];
  const videoTypes = await motrackerGetVideoMediaTypes(sequence);
  for (const item of items) {
    if (videoTypes.has(guidToString(await item.getMediaType()))) return item;
  }
  return null;
}

/** Last component on the chain whose matchName equals matchName, or null. */
async function motrackerFindComponentByMatchName(chain, matchName) {
  const count = await chain.getComponentCount();
  for (let i = count - 1; i >= 0; i -= 1) {
    const component = await chain.getComponentAtIndex(i);
    if ((await component.getMatchName()) === matchName) return { component, index: i };
  }
  return null;
}

/** First param on a component whose displayName matches one of the given
 * (already-lowercased) candidates, or whose displayName contains `contains`. */
async function motrackerFindParamByName(component, exactCandidates, contains) {
  const count = await component.getParamCount();
  for (let i = 0; i < count; i += 1) {
    const param = await component.getParam(i);
    const dn = motrackerNorm(param.displayName);
    if (exactCandidates && exactCandidates.indexOf(dn) !== -1) return param;
    if (contains && dn.indexOf(contains) !== -1) return param;
  }
  return null;
}

async function getClipInfo() {
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("No active project.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("No active sequence.");

  const clip = await motrackerGetSelectedVideoClip(sequence);
  if (!clip) {
    throw new Error("Select the clip you want to track in the timeline, then click Load.");
  }

  const projectItem = await clip.getProjectItem();
  let clipProjectItem = null;
  try { clipProjectItem = premiere.ClipProjectItem.cast(projectItem); } catch (error) { clipProjectItem = null; }
  const mediaPath = clipProjectItem ? await clipProjectItem.getMediaFilePath() : "";
  if (!mediaPath) {
    throw new Error("Cannot read the media file path. Synthetic clips (bars, black video) cannot be tracked.");
  }

  const settings = await sequence.getSettings();
  let fps = 30;
  try {
    const frameRate = settings.getVideoFrameRate();
    if (frameRate && Number(frameRate.value) > 0) fps = Number(frameRate.value);
  } catch (error) { /* fall through to timebase */ }
  if (fps === 30) {
    try {
      const timebase = Number(await sequence.getTimebase());
      if (timebase > 0) fps = 254016000000 / timebase;
    } catch (error) { /* keep default */ }
  }

  const frameSize = await sequence.getFrameSize();
  const frameW = Number(frameSize.width) || 1920;
  const frameH = Number(frameSize.height) || 1080;

  const trackIdx = await clip.getTrackIndex();
  const startTime = await clip.getStartTime();
  const endTime = await clip.getEndTime();
  const clipInPoint = await clip.getInPoint(); // relative to the source project item

  const durationSec = endTime.seconds - startTime.seconds;
  if (durationSec <= 0) {
    throw new Error("The selected clip has zero duration on the timeline.");
  }

  // The tracked clip's own display scale in the sequence, read from its intrinsic Motion effect.
  // Only Follow uses it (see applyTrack): Stabilize writes the Anchor Point, which is normalised
  // over the very frame the tracker measures in, so it needs no conversion at all. Follow targets a
  // different clip and must convert between two coordinate spaces, which requires knowing how large
  // the tracked footage actually appears. A failed read returns null and Follow falls back to its
  // previous, measurably wrong assumption rather than refusing to run.
  let motionScale = null;
  try {
    const trackedChain = await clip.getComponentChain();
    const motionFound = await motrackerFindComponentByMatchName(trackedChain, "AE.ADBE Motion");
    if (motionFound) {
      const scaleParam = await motrackerFindParamByName(motionFound.component, null, "scale");
      if (scaleParam) {
        const start = await scaleParam.getStartValue();
        const raw = start && start.value && start.value.value;
        if (typeof raw === "number" && Number.isFinite(raw)) motionScale = raw;
      }
    }
  } catch (error) { /* diagnostic only */ }

  return {
    ok: true,
    mediaPath,
    motionScale,
    srcRangeStart: clipInPoint.seconds,
    durationSec,
    fps,
    frameW,
    frameH,
    seqName: sequence.name,
    clipName: await clip.getName(),
    trackIdx,
    clipStartTicks: startTime.ticks,
    seqInPoint: startTime.seconds,
    seqOutPoint: endTime.seconds
  };
}

// Registered as motracker.getFollowTargetMediaPath. Follow mode needs the FOLLOWED object's own
// native pixel size to scale Position correctly (see applyTrack's followScaleX/Y) - rather than
// guess which Premiere-side parameter reflects real pixels (Position and even the built-in
// Motion effect's own Anchor Point both turned out to be normalised at the API level despite
// showing pixel-like numbers in Effect Controls - see TECHNICAL_PLAN.md's Motion Tracker slice),
// this hands the companion the target's media file path so it can read the real dimensions
// directly (the companion already has OpenCV for the tracker engine) - no more guessing.
async function getFollowTargetMediaPath() {
  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("No active project.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("No active sequence.");
  const clip = await motrackerGetSelectedVideoClip(sequence);
  if (!clip) throw new Error("Select the clip (object) you want to attach to the track first.");
  const projectItem = await clip.getProjectItem();
  let clipProjectItem = null;
  try { clipProjectItem = premiere.ClipProjectItem.cast(projectItem); } catch (error) { clipProjectItem = null; }
  const mediaPath = clipProjectItem ? await clipProjectItem.getMediaFilePath() : "";
  if (!mediaPath) throw new Error("Cannot read the media file path for the selected clip.");
  return { ok: true, mediaPath };
}

/** Clear every existing keyframe on a param, then set it to a plain static
 * PointF base value (self-heals a stale time-varying state from a previous
 * apply, matching host.jsx's re-run behavior). */
async function motrackerResetParamToStatic(project, param, baseX, baseY) {
  const times = await param.getKeyframeListAsTickTimes();
  if (times && times.length > 0) {
    let cleared = false;
    project.lockedAccess(() => {
      cleared = project.executeTransaction((compound) => {
        compound.addAction(param.createRemoveKeyframeRangeAction(times[0], times[times.length - 1], true));
      }, "FX.palette: Clear existing motion-tracker keyframes");
    });
    if (!cleared) throw new Error("Could not clear existing keyframes before re-applying.");
  }
  const baseKeyframe = await param.createKeyframe(new premiere.PointF(baseX, baseY));
  let staticSet = false;
  project.lockedAccess(() => {
    staticSet = project.executeTransaction((compound) => {
      compound.addAction(param.createSetValueAction(baseKeyframe, true));
    }, "FX.palette: Reset motion-tracker base value");
  });
  if (!staticSet) throw new Error("Could not reset the base value before re-applying.");
}

/* AE.ADBE Geometry2's "Use Composition's Shutter Angle" toggle is a boolean param with NO display
 * name at all (confirmed against this project's own host-tested parameter map for Premiere
 * 26.3.2: Anchor Point=0, Position=1, unnamed boolean=2, Scale Height=3, Scale Width=4, Skew=5,
 * Skew Axis=6, Rotation=7, Opacity=8, unnamed boolean=9 (use-composition toggle), Shutter
 * Angle=10, Sampling=11) - so it can never be found by display-name matching. Re-verify this
 * index against the host Premiere build in use before trusting it blindly (see
 * TECHNICAL_PLAN.md); Shutter Angle itself does have a real display name, so that one is still
 * found by name as a safety net against a future Premiere build reordering these. */
const MOTRACKER_GEOMETRY2_USE_COMP_SHUTTER_INDEX = 9;

async function motrackerSetMotionBlur(component, on, angle, project) {
  let useCompParam = null;
  try {
    const candidate = await component.getParam(MOTRACKER_GEOMETRY2_USE_COMP_SHUTTER_INDEX);
    if (candidate && motrackerNorm(candidate.displayName) === "") useCompParam = candidate;
  } catch (error) { /* fall through */ }
  const shutterParam = await motrackerFindParamByName(component, ["shutter angle", "ângulo do obturador", "angulo do obturador"], null);

  if (useCompParam) {
    const keyframe = await useCompParam.createKeyframe(false);
    project.lockedAccess(() => {
      project.executeTransaction((compound) => {
        compound.addAction(useCompParam.createSetValueAction(keyframe, true));
      }, "FX.palette: Use own shutter angle");
    });
  }
  if (shutterParam) {
    const value = on ? (angle > 0 ? angle : 180) : 0;
    const keyframe = await shutterParam.createKeyframe(value);
    project.lockedAccess(() => {
      project.executeTransaction((compound) => {
        compound.addAction(shutterParam.createSetValueAction(keyframe, true));
      }, "FX.palette: Set motion-tracker motion blur");
    });
  }
}

async function applyTrack(action) {
  const request = action.payload || {};
  const mode = request.mode;
  const trackData = request.trackData || [];
  const fps = Number(request.fps) || 30;
  const seqW = Number(request.seqW) || 1920;
  const seqH = Number(request.seqH) || 1080;
  const extractedW = Number(request.extractedW) || 0;
  const extractedH = Number(request.extractedH) || 0;
  const mblurOn = !!request.mblurOn;
  const mblurAngle = Number(request.mblurAngle) || 180;
  // Real per-frame timestamps from the companion's own ffmpeg extraction (see extraction.py's
  // build_ffmpeg_args/FrameExtractor docstrings) - a real host report + Premiere's own "Variable
  // Frame Rate Detected" on the source confirmed "frame index / a single constant fps" silently
  // drifts on genuinely VFR footage (a real per-frame duration variance of ~1.5-33% was measured
  // against the actual clip, not assumed). Array is indexed by frame number, 0-based relative to
  // the first extracted frame - null/absent falls back to the old constant-fps math below.
  const frameTimestamps = Array.isArray(request.frameTimestamps) ? request.frameTimestamps : null;

  const project = await premiere.Project.getActiveProject();
  if (!project) throw new Error("No active project.");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error("No active sequence.");

  // Target is ALWAYS the current Timeline selection now, both modes (the user's own call - see
  // TECHNICAL_PLAN.md's Motion Tracker slice): Stabilize -> select the tracked clip itself;
  // Seguir Rastro -> select the object that should follow the tracked point.
  let targetClip = await motrackerGetSelectedVideoClip(sequence);
  if (!targetClip) {
    throw new Error(mode === "stabilize"
      ? "Select the tracked clip in the Timeline, then click Stabilize."
      : "Select the clip/object that should follow the track, then click Seguir Rastro.");
  }

  const origName = await targetClip.getName();
  const chain = await targetClip.getComponentChain();

  // Stage 1 of the Nest-and-normalize plan (TECHNICAL_PLAN.md's Motion Tracker slice) -
  // diagnostic-only read of the clip's own built-in Motion effect (never read by this codebase
  // before, unlike Position/Anchor on our own Transform). Confirms the real value shapes (Scale
  // especially - a plain scalar, never exercised here) before anything gets built assuming them.
  // Wrapped so a read failure here can never break the actual apply - this block writes nothing.
  let motrackerMotionProbe = null;
  try {
    const motionFound = await motrackerFindComponentByMatchName(chain, "AE.ADBE Motion");
    if (motionFound) {
      const motionComponent = motionFound.component;
      const scaleParam = await motrackerFindParamByName(motionComponent, null, "scale");
      const positionParam = await motrackerFindParamByName(motionComponent, ["position"], null);
      const anchorParam = await motrackerFindParamByName(motionComponent, null, "anchor");
      const rotationParam = await motrackerFindParamByName(motionComponent, null, "rotation");
      const readRaw = async (param) => {
        if (!param) return { found: false };
        const start = await param.getStartValue();
        return { found: true, raw: start && start.value, rawJson: JSON.stringify(start && start.value) };
      };
      motrackerMotionProbe = {
        motionComponentFound: true,
        scale: await readRaw(scaleParam),
        position: await readRaw(positionParam),
        anchor: await readRaw(anchorParam),
        rotation: await readRaw(rotationParam)
      };
    } else {
      motrackerMotionProbe = { motionComponentFound: false };
    }
  } catch (error) {
    motrackerMotionProbe = { error: error && error.message ? error.message : String(error) };
  }

  let transformComponent = null;
  const existing = await motrackerFindComponentByMatchName(chain, MOTRACKER_TRANSFORM_MATCH_NAME);
  if (existing) {
    transformComponent = existing.component;
  } else {
    const created = await premiere.VideoFilterFactory.createComponent(MOTRACKER_TRANSFORM_MATCH_NAME);
    let inserted = false;
    project.lockedAccess(() => {
      inserted = project.executeTransaction((compound) => {
        compound.addAction(chain.createAppendComponentAction(created));
      }, "FX.palette: Add motion-tracker Transform");
    });
    if (!inserted) throw new Error("Premiere rejected adding the Transform effect.");
    const newIndex = (await chain.getComponentCount()) - 1;
    transformComponent = await chain.getComponentAtIndex(newIndex);
  }

  const posParam = await motrackerFindParamByName(transformComponent, ["position"], null);
  if (!posParam) throw new Error("Position parameter not found on the Transform effect.");
  const anchorParam = await motrackerFindParamByName(transformComponent, null, "anchor");

  // Real, host-tested finding, refined twice now (see TECHNICAL_PLAN.md's Motion Tracker slice
  // for the full trail): Transform's Position, like Anchor Point, is normalised over the TARGET
  // CLIP's OWN native frame, not the sequence - confirmed empirically (Effect Controls' displayed
  // pixel-equivalent for Position exactly equalled the raw fraction times the followed object's
  // own native width, not the sequence width) after an earlier assumption that it was
  // sequence-normalised caused Follow to displace far too little to be visible (a real, if small,
  // fraction of the WRONG - much larger - reference frame). posRawX/Y (below) capture Position's
  // own current raw value regardless of normalisation, since Follow needs it as a base to add the
  // tracked delta onto, not overwrite absolutely.
  let posIsNorm = false;
  let cx = seqW / 2;
  let cy = seqH / 2;
  let posRawX = 0.5;
  let posRawY = 0.5;
  try {
    const startKeyframe = await posParam.getStartValue();
    // getValueAtTime()/getStartValue() report a 2D point as { value: [x, y] } (confirmed by
    // reading back a real write - NOT a PointF-shaped {x, y} object, despite what the value
    // carried INTO createKeyframe() looks like).
    const raw = startKeyframe && startKeyframe.value && startKeyframe.value.value;
    const vx = Array.isArray(raw) ? Number(raw[0]) : (raw && typeof raw.x === "number" ? raw.x : null);
    const vy = Array.isArray(raw) ? Number(raw[1]) : (raw && typeof raw.y === "number" ? raw.y : null);
    if (typeof vx === "number" && typeof vy === "number" && !Number.isNaN(vx) && !Number.isNaN(vy)) {
      posRawX = vx;
      posRawY = vy;
      if (Math.abs(vx) <= 2 && Math.abs(vy) <= 2) posIsNorm = true;
      else { cx = vx; cy = vy; }
    }
  } catch (error) { /* keep defaults */ }

  // Anchor-point math (below) is only valid when the ANCHOR's own normalisation base (the
  // TARGET clip's own native frame) matches the tracked data's frame - true for Stabilize
  // (target === the tracked footage itself) but NOT for Follow, where the target is a different,
  // arbitrarily-sized object (e.g. a small icon) with no relation to the tracked footage's own
  // pixel dimensions. Follow uses Position instead (delta-based, see followScaleX/Y below), scaled
  // by the FOLLOWED object's own native size rather than the tracked footage's.
  const useAnchor = mode === "stabilize" && posIsNorm && !!anchorParam;
  const kfParam = useAnchor ? anchorParam : posParam;

  // Never actually verified until now: the Anchor Point's own resting value. Position's is read
  // above (posRawX/Y) and used as Follow's base, but Stabilize just WRITES 0.5/0.5 to the Anchor on
  // the assumption that is its default. If it is not, every keyframe is displaced by a constant
  // while the motion between them stays correct - which is exactly the reported symptom. Read-only.
  let anchorStartRaw = null;
  try {
    if (anchorParam) {
      const anchorStart = await anchorParam.getStartValue();
      const raw = anchorStart && anchorStart.value && anchorStart.value.value;
      if (Array.isArray(raw)) anchorStartRaw = [Number(raw[0]), Number(raw[1])];
      else if (raw && typeof raw.x === "number") anchorStartRaw = [raw.x, raw.y];
    }
  } catch (error) { /* diagnostic only */ }

  const goodFrames = trackData.filter((t) => t.conf > 0);
  if (goodFrames.length === 0) throw new Error("No valid track data (all frames lost or deleted).");
  // The reference frame for every delta below MUST be the frame the user actually clicked the
  // point on (the tracking seed), not just "chronologically first in trackData" - for anything
  // other than pure forward tracking (bidirectional, or backward), trackData is sorted by frame
  // number ascending, so goodFrames[0] silently picks whichever frame BACKWARD tracking reached
  // (often frame 0 of the whole clip) instead of the actual seed. That mismatched baseline was a
  // real, confirmed bug (found by reading this code, not guessed) behind a real host report of
  // Stabilize/Follow being "the right kind of motion, but systematically misaligned" - every
  // frame's delta was measured against the wrong starting point. Falls back to goodFrames[0] only
  // if the seed frame itself somehow isn't in the data (defensive, shouldn't normally happen).
  const seedFrame = request.seedFrame != null && Number.isFinite(Number(request.seedFrame))
    ? goodFrames.find((t) => t.frame === Number(request.seedFrame))
    : null;
  const firstFrame = seedFrame || goodFrames[0];

  const frameW = posIsNorm ? 1 : (Math.abs(cx) > 0.0001 ? cx * 2 : seqW);
  const frameH = posIsNorm ? 1 : (Math.abs(cy) > 0.0001 ? cy * 2 : seqH);
  const exW = extractedW > 0 ? extractedW : (seqW > 1280 ? 1280 : seqW);
  const exH = extractedH > 0 ? extractedH : (seqH > 1280 ? 1280 : seqH);
  const coordScaleX = frameW / exW;
  const coordScaleY = frameH / exH;

  // Follow-only. Converting a delta measured in the tracked footage's own pixels into the followed
  // object's frame needs THREE numbers, and getting any of them from the sequence dimensions is the
  // mistake this code made twice:
  //   1. how large the tracked footage appears in the sequence - its own Motion Scale, read by
  //      getClipInfo before anything touches it. The previous `(seqW/exW)`/`(seqH/exH)` pair stood
  //      in for this, which silently assumed the footage fills the sequence frame AND used a
  //      separate factor per axis, so whenever the aspects differed the tracked path came out
  //      distorted rather than merely mis-scaled. Measured on a 1440x2560 clip in a 1920x1080
  //      sequence: X ran 1.33x too far while Y ran 2.37x too short.
  //   2. the object's real pixel size, from the companion's own OpenCV read of its media file (no
  //      Premiere parameter reports real pixels - both Position and Motion's Anchor Point were
  //      tried and disproven).
  //   3. the object's own Motion Scale, because this Transform renders BEFORE that Motion: a
  //      Position change moves content inside the object's frame, and Motion then scales the result
  //      on its way to the sequence.
  // Screen displacement = f x objectNativeSize x objectScale, and we want it to equal
  // trackedDelta x trackedScale, hence f = trackedDelta x trackedScale / (objectSize x objectScale).
  let followNativeW = seqW;
  let followNativeH = seqH;
  let trackedScale = Number(request.trackedScale);
  let targetScale = null;
  if (mode !== "stabilize") {
    const suppliedW = Number(request.targetNativeW) || 0;
    const suppliedH = Number(request.targetNativeH) || 0;
    if (suppliedW > 0 && suppliedH > 0) {
      followNativeW = suppliedW;
      followNativeH = suppliedH;
    }
    try {
      const targetMotion = await motrackerFindComponentByMatchName(chain, "AE.ADBE Motion");
      if (targetMotion) {
        const targetScaleParam = await motrackerFindParamByName(targetMotion.component, null, "scale");
        if (targetScaleParam) {
          const start = await targetScaleParam.getStartValue();
          const raw = start && start.value && start.value.value;
          if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) targetScale = raw;
        }
      }
    } catch (error) { /* fall through to 100 */ }
  }
  // Both fall back to 100 (unscaled), which reduces to "one tracked pixel is one sequence pixel" -
  // wrong when the clip really is scaled, but uniform across the axes, so an unreadable value can
  // only make the follow the wrong SIZE, never distorted.
  const trackedScalePct = Number.isFinite(trackedScale) && trackedScale > 0 ? trackedScale : 100;
  const targetScalePct = targetScale != null ? targetScale : 100;
  const followScaleX = (trackedScalePct / 100) / (followNativeW * (targetScalePct / 100));
  const followScaleY = (trackedScalePct / 100) / (followNativeH * (targetScalePct / 100));

  // Reset the target param (and Position too, if we're about to self-heal an older run that
  // mistakenly keyframed Position instead of Anchor). Follow's base is Position's OWN current raw
  // value (posRawX/Y) - the tracked motion is added onto it as a delta, never overwritten
  // absolutely, so the object's existing placement is preserved.
  const baseX = useAnchor ? 0.5 : (mode === "stabilize" ? cx : posRawX);
  const baseY = useAnchor ? 0.5 : (mode === "stabilize" ? cy : posRawY);
  await motrackerResetParamToStatic(project, kfParam, baseX, baseY);
  if (useAnchor) await motrackerResetParamToStatic(project, posParam, 0.5, 0.5);

  // Effect param time is clip-local, anchored at the clip's own in point.
  const clipInPoint = await targetClip.getInPoint();

  // Records the values actually written, so a host report of "still off" can be checked against
  // Effect Controls numerically instead of by eye - the parameter's own px readout at the extreme
  // frames tells us directly whether the normalisation base is right, independently of any
  // reasoning about it.
  const appliedXs = [];
  const appliedYs = [];
  const appliedTimes = [];
  let lastOffsetSeconds = 0;

  const preparedKeyframes = [];
  for (const t of goodFrames) {
    const realTimestamp = frameTimestamps && t.frame >= 0 && t.frame < frameTimestamps.length
      ? Number(frameTimestamps[t.frame])
      : null;
    const offsetSeconds = Number.isFinite(realTimestamp) ? realTimestamp : t.frame / fps;
    const time = clipInPoint.add(premiere.TickTime.createWithSeconds(offsetSeconds));

    let vx;
    let vy;
    if (useAnchor) {
      // Stabilize only (useAnchor now implies mode === "stabilize") - pin the anchor opposite
      // the tracked jitter while Position stays put.
      const dxN = (t.x - firstFrame.x) * coordScaleX;
      const dyN = (t.y - firstFrame.y) * coordScaleY;
      vx = 0.5 + dxN;
      vy = 0.5 + dyN;
    } else if (mode === "stabilize") {
      vx = cx + (firstFrame.x - t.x) * coordScaleX;
      vy = cy + (firstFrame.y - t.y) * coordScaleY;
    } else {
      // Follow: add the tracked delta (converted through sequence pixels into a fraction of the
      // FOLLOWED object's own native size - see followScaleX/Y above) onto Position's own current
      // value, exactly like Stabilize's anchor math adds its delta onto a base - never an
      // absolute overwrite, which is what the two earlier (wrong) attempts both did.
      const dxF = (t.x - firstFrame.x) * followScaleX;
      const dyF = (t.y - firstFrame.y) * followScaleY;
      vx = posRawX + dxF;
      vy = posRawY + dyF;
    }

    appliedXs.push(vx);
    appliedYs.push(vy);
    if (appliedTimes.length < 2) appliedTimes.push(offsetSeconds);
    lastOffsetSeconds = offsetSeconds;

    const keyframe = await kfParam.createKeyframe(new premiere.PointF(vx, vy));
    keyframe.position = time;
    try { await keyframe.setTemporalInterpolationMode(premiere.Constants.InterpolationMode.LINEAR); } catch (error) { /* keep default */ }
    preparedKeyframes.push(keyframe);
  }

  let keyframesWritten = false;
  project.lockedAccess(() => {
    keyframesWritten = project.executeTransaction((compound) => {
      compound.addAction(kfParam.createSetTimeVaryingAction(true));
      preparedKeyframes.forEach((keyframe) => compound.addAction(kfParam.createAddKeyframeAction(keyframe)));
    }, "FX.palette: Write motion-tracker keyframes");
  });
  if (!keyframesWritten) throw new Error("Premiere rejected the tracked-keyframe transaction.");

  await motrackerSetMotionBlur(transformComponent, mblurOn, mblurAngle, project);

  return {
    ok: true,
    keyframes: preparedKeyframes.length,
    clipName: origName,
    mblur: mblurOn,
    // Temporary diagnostics for the vertical-clip-in-horizontal-sequence report (TECHNICAL_PLAN.md
    // Motion Tracker slice) - our coordScaleX/Y math matches the real, working CEP host.jsx
    // verbatim, so the bug (if it's here at all) isn't visible from reading the code alone this
    // time; need real numbers from an actual repro before touching anything. Remove once resolved.
    motrackerDebug: {
      mode, posIsNorm, useAnchor, cx, cy, seqW, seqH, extractedW, extractedH,
      frameW, frameH, exW, exH, coordScaleX, coordScaleY, posRawX, posRawY,
      followScaleX, followScaleY,
      // Follow-only inputs. targetScaleReadable false means the object's own Motion Scale could not
      // be read and 100 was assumed - the follow would then be uniformly the wrong size, not
      // distorted, which is what to check first if it tracks the right shape at the wrong distance.
      trackedScalePct, targetScalePct,
      targetScaleReadable: targetScale != null,
      followNativeW, followNativeH,
      // What was actually written, and what those normalised values mean in pixels under the
      // assumption the code is built on (x over the frame width, y over the frame height). If
      // Effect Controls disagrees with expectedPxRangeX/Y for this same parameter, the
      // normalisation base is wrong and the ratio between the two is the missing factor.
      appliedParam: useAnchor ? "anchor" : "position",
      // The resting value of the parameter being keyframed, before this run touched it. For
      // Stabilize this is the Anchor: anything other than [0.5, 0.5] means the 0.5 base baked into
      // the math is wrong and is the source of a constant displacement.
      anchorStartRaw,
      appliedFirst: [appliedXs[0], appliedYs[0]],
      appliedLast: [appliedXs[appliedXs.length - 1], appliedYs[appliedYs.length - 1]],
      appliedMinX: Math.min(...appliedXs),
      appliedMaxX: Math.max(...appliedXs),
      appliedMinY: Math.min(...appliedYs),
      appliedMaxY: Math.max(...appliedYs),
      // The same extremes expressed in the units Effect Controls shows for this parameter, so the
      // written values can be compared against the host directly rather than inferred.
      uiMinX: Math.min(...appliedXs) * exW,
      uiMaxX: Math.max(...appliedXs) * exW,
      uiMinY: Math.min(...appliedYs) * exH,
      uiMaxY: Math.max(...appliedYs) * exH,
      // The tracked input itself, in extracted pixels - if the written range does not equal this,
      // the conversion is at fault; if it does, the values are right and the fault is elsewhere.
      trackedRangeX: Math.max(...goodFrames.map((t) => t.x)) - Math.min(...goodFrames.map((t) => t.x)),
      trackedRangeY: Math.max(...goodFrames.map((t) => t.y)) - Math.min(...goodFrames.map((t) => t.y)),
      appliedRangeX: Math.max(...appliedXs) - Math.min(...appliedXs),
      appliedRangeY: Math.max(...appliedYs) - Math.min(...appliedYs),
      expectedPxRangeX: (Math.max(...appliedXs) - Math.min(...appliedXs)) * exW,
      expectedPxRangeY: (Math.max(...appliedYs) - Math.min(...appliedYs)) * exH,
      // Keyframe placement: the nest instance's own in point plus the first/last offsets. A
      // non-zero in point that does not line up with the tracked range would put every keyframe at
      // the wrong time, which looks like a broken track rather than a mis-scaled one.
      clipInPointSeconds: clipInPoint.seconds,
      firstOffsetSeconds: appliedTimes[0],
      lastOffsetSeconds,
      // requestedSeedFrame: what the companion actually sent. resolvedFirstFrame: which frame the
      // code actually used as the delta reference (should match requestedSeedFrame if the fix is
      // live - if this whole block still doesn't show up in a fresh test, the UXP plugin was not
      // reloaded and is still running the pre-fix index.js).
      requestedSeedFrame: request.seedFrame,
      resolvedFirstFrame: firstFrame.frame,
      goodFramesFirst: goodFrames[0].frame,
      goodFramesLast: goodFrames[goodFrames.length - 1].frame,
      // fps actually used to convert each tracked frame index into real elapsed time
      // (offsetSeconds = t.frame / fps below) - confirms whether the companion's native-source-fps
      // fix (extraction.py) actually reached this request, or if it's still sending the old
      // sequence fps.
      fpsUsed: fps,
      usedRealFrameTimestamps: !!frameTimestamps,
      frameTimestampsCount: frameTimestamps ? frameTimestamps.length : 0,
      motionProbe: motrackerMotionProbe
    }
  };
}

// The transport (stage 5, TECHNICAL_PLAN.md) dispatches through this exact map, so a command
// received over the network can never do anything the diagnostics panel's own buttons could not
// already do - it is the same allowlisted, schema-validated action set, just a different caller.
const ACTION_HANDLERS = {
  "diagnostics.read": readDiagnostics,
  "catalog.videoEffects.read": readVideoEffectCatalog,
  "catalog.videoTransitions.read": readVideoTransitionCatalog,
  "catalog.favorites.read": readFavoritesCatalog,
  "catalog.projectItems.read": readProjectItemCatalog,
  "timeline.applyVideoEffect": applyVideoEffectToSelection,
  "catalog.effectPresets.read": readEffectPresetCatalog,
  "catalog.effectPresets.readFromPath": readPrfpsetFileAtPath,
  "catalog.effectPresets.importPrfpset": importPrfpsetCatalog,
  "timeline.applyImportedEffectPreset": applyImportedEffectPreset,
  "timeline.applyAudioEffect": applyAudioEffectToSelection,
  "timeline.applyVideoTransition": applyVideoTransitionToSelection,
  "timeline.createSubsequence": createSubsequenceFromSelection,
  "timeline.createNest": createNestFromSelection,
  "timeline.insertProjectItem": insertSelectedProjectItem,
  "timeline.insertGenericItem": insertGenericItemAcrossSelection,
  "timeline.checkTrackAvailability": checkTrackAvailability,
  "motracker.getClipInfo": getClipInfo,
  "motracker.getFollowTargetMediaPath": getFollowTargetMediaPath,
  "motracker.applyTrack": applyTrack
};

// projectItems.setColorLabel is not a product feature (the user confirmed they don't use Project-
// panel item labels) and was removed from ACTION_HANDLERS/SUPPORTED_ACTIONS - it is no longer
// reachable via the transport or the diagnostics panel. setSelectedProjectItemLabel itself stays,
// called directly here rather than through executionAdapter.execute(), only so the 0.16.0
// headless-command proof ("the plugin can run with no panel open at all") keeps working without
// needing a second action wired up just to prove the same thing again.
async function runHeadlessSetVioletLabelCommand() {
  let result;
  try {
    const data = await setSelectedProjectItemLabel({ payload: { labelName: "VIOLET" } });
    result = { ok: true, schemaVersion: 1, actionType: "projectItems.setColorLabel", data };
  } catch (error) {
    result = {
      ok: false, schemaVersion: 1, actionType: "projectItems.setColorLabel",
      error: { code: "EXECUTION_FAILED", message: error && error.message ? error.message : String(error) }
    };
  }
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
  }
  // No "panels" entry: the shipped plugin shows no UI inside Premiere at all - everything
  // user-facing is the companion's own hotkey-triggered search palette. The former diagnostics
  // panel (index.html body, styles.css, wirePanel and the run* wrappers) was removed once it was
  // confirmed unused; its git history holds it if a probe UI is ever needed again.
});
