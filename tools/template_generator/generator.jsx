// generator.jsx - standalone ExtendScript, NOT part of any stable EffectPalette CEP repo. Runs only
// inside this throwaway dev-tool panel (tools/template_generator), loaded on demand by index.js.
//
// Purpose: build BT_TEMPLATE_*/BV_TEMPLATE_*/TV_TEMPLATE_* sequences (Bars and Tone, Black Video,
// Transparent Video) at the same resolutions the CEP-sourced AL_TEMPLATE_* sequences already use in
// EffectPalette_UXP/assets/template_project/template_project.prproj, so the UXP plugin's
// ensureGenericProjectItem (index.js) can import them the same way it already imports Adjustment
// Layer. Run this with that project open in Premiere.
//
// Mirrors host.jsx's _getSequenceCreateSettings/_createGenericProjectItem (read-only reference in
// the stable EffectPalette repo - never modified) closely enough to reuse its exact API calls, but
// clones an existing AL_TEMPLATE_* sequence for its settings instead of app.project.createNewSequence,
// since cloning guarantees byte-identical width/height/timebase/PAR/audio to the sequence it's
// standing in for - no separate settings-matching step required.

var GENERIC_ITEM_SPECS = [
  { key: "bars_and_tone", prefix: "BT_TEMPLATE_", label: "Bars and Tone", resultKey: "barsAndTone" },
  { key: "black_video", prefix: "BV_TEMPLATE_", label: "Black Video", resultKey: "blackVideo" },
  { key: "transparent_video", prefix: "TV_TEMPLATE_", label: "Transparent Video", resultKey: "transparentVideo" }
];

// Direct port of host.jsx's _getSequenceCreateSettings.
function _sequenceCreateSettings(sequence) {
  var settings = {
    width: 1920,
    height: 1080,
    timeBase: 254016000000 / 30,
    parNum: 1,
    parDen: 1,
    audioSampleRate: 48000
  };
  try {
    if (sequence && typeof sequence.getSettings === "function") {
      var seqSettings = sequence.getSettings();
      if (seqSettings) {
        if (seqSettings.videoFrameWidth) settings.width = Number(seqSettings.videoFrameWidth) || settings.width;
        if (seqSettings.videoFrameHeight) settings.height = Number(seqSettings.videoFrameHeight) || settings.height;
        if (seqSettings.videoPixelAspectRatioNumerator) settings.parNum = Number(seqSettings.videoPixelAspectRatioNumerator) || settings.parNum;
        if (seqSettings.videoPixelAspectRatioDenominator) settings.parDen = Number(seqSettings.videoPixelAspectRatioDenominator) || settings.parDen;
        if (seqSettings.audioSampleRate) settings.audioSampleRate = Number(seqSettings.audioSampleRate) || settings.audioSampleRate;
      }
    }
  } catch (e0) {}
  try {
    if (sequence && sequence.timebase) settings.timeBase = Number(sequence.timebase) || settings.timeBase;
  } catch (e1) {}
  return settings;
}

function _generateGuid() {
  function seg() { return Math.floor((1 + Math.random()) * 0x10000).toString(16).substring(1); }
  return seg() + seg() + "-" + seg() + "-" + seg() + "-" + seg() + "-" + seg() + seg() + seg();
}

function _findOrCreateRootBin(name) {
  var root = app.project.rootItem;
  for (var i = 0; i < root.children.numItems; i++) {
    var child = root.children[i];
    if (String(child.name || "") === name && String(child.type) === "BIN") return child;
  }
  var created = root.createBin(name);
  return created || null;
}

// Locates the ProjectItem representation of a just-created/renamed sequence at project root (a
// Sequence and its Project-panel entry are different objects in classic ExtendScript) and moves it
// into the given bin, purely for the human's own tidiness - UXP's importSequences only cares about
// the sequenceID recorded in generic_item_templates.json, not where anything sits in this project.
function _moveSequenceItemIntoBin(expectedSeqName, bin) {
  if (!bin) return false;
  try {
    var root = app.project.rootItem;
    for (var i = 0; i < root.children.numItems; i++) {
      var child = root.children[i];
      if (String(child.name || "") === expectedSeqName) {
        return child.moveBin(bin) === 0;
      }
    }
  } catch (e) {}
  return false;
}

function _audioTrackCount(sequence) {
  try { return sequence.audioTracks.numTracks; } catch (e) { return -1; }
}

function _videoTrackCount(sequence) {
  try { return sequence.videoTracks.numTracks; } catch (e) { return -1; }
}

function _zeroTime() {
  var t = new Time();
  t.ticks = "0";
  try { t.seconds = 0; } catch (e) {}
  return t;
}

function _existingSequenceIdSet() {
  var ids = {};
  var sequences = app.project.sequences;
  for (var i = 0; i < sequences.numSequences; i++) {
    try { ids[String(sequences[i].sequenceID)] = true; } catch (e) {}
  }
  return ids;
}

function _findNewSequence(beforeIds) {
  var sequences = app.project.sequences;
  for (var i = 0; i < sequences.numSequences; i++) {
    var seq = sequences[i];
    var id = null;
    try { id = String(seq.sequenceID); } catch (e) { continue; }
    if (id && !beforeIds[id]) return seq;
  }
  return null;
}

function _findFirstNewProjectItem(beforeCount) {
  var root = app.project.rootItem;
  var total = root.children.numItems;
  for (var i = beforeCount; i < total; i++) {
    var item = root.children[i];
    if (item) return item;
  }
  return null;
}

function _clearSequenceVideoTrack(sequence, trackIndex) {
  try {
    var track = sequence.videoTracks[trackIndex];
    if (!track || !track.clips) return;
    for (var i = track.clips.numItems - 1; i >= 0; i--) {
      try { track.clips[i].remove(0, 0); } catch (eRemove) {}
    }
  } catch (eTrack) {}
}

// app.project.newBarsAndTone's return value looked like the created ProjectItem (host.jsx passes it
// straight into _organizeGenericAsset/.moveBin(), which works in the live product), but diagnostics
// here showed otherwise: the returned object's .type read as undefined, and passing it into
// insertClip/overwriteClip threw "Illegal Parameter type" on every attempt, every resolution - not a
// ProjectItem at all, matching the docs' oddly literal description ("Creates a new Sequence object
// ... based on the specified preset"). So this treats newBarsAndTone's return value as untrusted
// (used only as a truthy success signal) and locates the actual new ProjectItem by diffing the root
// bin's children afterward, exactly like the QE-DOM-created black_video/transparent_video items
// already have to be located (qe.project.* calls only ever returned a plain success boolean).
function _createGenericSourceItem(genericKey, settings) {
  var beforeCount = app.project.rootItem.children.numItems;

  if (genericKey === "bars_and_tone") {
    try {
      var barsAndToneResult = app.project.newBarsAndTone(
        settings.width, settings.height, settings.timeBase,
        settings.parNum, settings.parDen, settings.audioSampleRate, "Bars and Tone"
      );
      if (!barsAndToneResult) return null;
    } catch (e) {
      return null;
    }
    return _findFirstNewProjectItem(beforeCount);
  }

  try {
    app.enableQE();
    if (typeof qe === "undefined" || !qe.project) return null;
    var created = false;
    if (genericKey === "black_video") {
      created = !!qe.project.newBlackVideo(settings.width, settings.height, settings.timeBase, settings.parNum, settings.parDen);
    } else if (genericKey === "transparent_video") {
      created = !!qe.project.newTransparentVideo(settings.width, settings.height, settings.timeBase, settings.parNum, settings.parDen);
    }
    if (!created) return null;
    return _findFirstNewProjectItem(beforeCount);
  } catch (eQe) {
    return null;
  }
}

// A sequence is only treated as "already done" if it also already has a clip on its video track -
// a name match alone isn't enough, since a prior run can leave a renamed-but-empty sequence behind
// (item creation or insertion failed after the rename already happened).
function _sequenceHasVideoClip(sequence) {
  try {
    var track = sequence.videoTracks[0];
    return !!(track && track.clips && track.clips.numItems > 0);
  } catch (e) {
    return false;
  }
}

// Turned out the AL_TEMPLATE_* clones already carry audio tracks (Premiere's own default HD 1080p
// preset: 3 video + 4 audio - confirmed via the diagnostics below once this was actually checked,
// disproving the original "missing audio track" theory). insertClip(item, new Time(...), 0, 0) still
// failed 100% of the time for Bars and Tone specifically regardless. host.jsx's own
// _insertResolvedProjectItem (read-only reference, proven working in the live CEP product) never
// relies on a single call shape for a combined video+audio item either - it tries overwriteClip with
// a plain seconds-string time first, then insertClip with a Time-like object, then insertClip with a
// plain ticks-string (no Time wrapper at all). This ports that exact fallback cascade instead of the
// single call this script originally made.
// Returns rich diagnostics instead of a bare boolean: every attempt's return value (or thrown
// error, which the earlier boolean-only version was silently discarding) so a persistent failure
// can be diagnosed from what Premiere actually says instead of guessing another call shape blind.
function _insertAtStart(sequence, projectItem) {
  var attempts = [];

  function attempt(label, fn) {
    var record = { label: label, returned: null, error: null };
    try {
      record.returned = fn();
    } catch (e) {
      record.error = e && e.message ? String(e.message) : String(e);
    }
    attempts.push(record);
    return record.returned === true;
  }

  var ok =
    attempt("overwriteClip(seconds-string)", function () { return sequence.overwriteClip(projectItem, "0", 0, 0); }) ||
    attempt("insertClip(Time obj)", function () { return sequence.insertClip(projectItem, _zeroTime(), 0, 0); }) ||
    attempt("insertClip(ticks-string)", function () { return sequence.insertClip(projectItem, "0", 0, 0); });

  var diagnostics = { videoTracks: _videoTrackCount(sequence), audioTracks: _audioTrackCount(sequence) };
  try {
    var settings = sequence.getSettings();
    diagnostics.audioChannelType = settings ? settings.audioChannelType : null;
    diagnostics.audioChannelCount = settings ? settings.audioChannelCount : null;
  } catch (eSettings) {}
  try {
    diagnostics.itemAudioChannelMapping = typeof projectItem.getAudioChannelMapping === "function"
      ? String(projectItem.getAudioChannelMapping())
      : "n/a";
  } catch (eMapping) {}
  try {
    diagnostics.itemType = String(projectItem.type);
  } catch (eType) {}

  return { ok: ok, attempts: attempts, diagnostics: diagnostics };
}

function generateGenericItemTemplates() {
  var results = { barsAndTone: [], blackVideo: [], transparentVideo: [] };
  var errors = [];
  var skipped = [];

  var sourceSequences = [];
  for (var i = 0; i < app.project.sequences.numSequences; i++) {
    var seq = app.project.sequences[i];
    // The resolution is parsed straight from the AL_TEMPLATE_WxH name, not from getSettings() -
    // getSettings() proved unreliable when called on a sequence that isn't the active one in the
    // Premiere UI (it silently returned the *active* sequence's own width/height instead of the
    // instance it was actually called on), which collapsed 19 of 20 resolutions onto whichever one
    // happened to be open when the button was clicked.
    var nameMatch = /^AL_TEMPLATE_(\d+)x(\d+)$/.exec(String(seq.name || ""));
    if (nameMatch) sourceSequences.push({ sequence: seq, width: Number(nameMatch[1]), height: Number(nameMatch[2]) });
  }
  if (sourceSequences.length === 0) {
    return JSON.stringify({ ok: false, error: "No AL_TEMPLATE_* sequences found - open template_project.prproj before running this." });
  }

  var bins = {
    barsAndTone: _findOrCreateRootBin("Bars and Tone"),
    blackVideo: _findOrCreateRootBin("Black Video"),
    transparentVideo: _findOrCreateRootBin("Transparent Video")
  };

  for (var si = 0; si < sourceSequences.length; si++) {
    var sourceSeq = sourceSequences[si].sequence;
    var width = sourceSequences[si].width;
    var height = sourceSequences[si].height;

    for (var gi = 0; gi < GENERIC_ITEM_SPECS.length; gi++) {
      var spec = GENERIC_ITEM_SPECS[gi];
      var expectedSeqName = spec.prefix + width + "x" + height;

      var existingSeq = null;
      for (var ei = 0; ei < app.project.sequences.numSequences; ei++) {
        if (String(app.project.sequences[ei].name || "") === expectedSeqName) { existingSeq = app.project.sequences[ei]; break; }
      }
      if (existingSeq && _sequenceHasVideoClip(existingSeq)) {
        // Still recorded into results (not just "skipped") with its CURRENT real sequenceID: an
        // existing sequence found here may have been rebuilt since the JSON was last written (a
        // stale GUID in the JSON pointing at a since-deleted sequence is exactly what made
        // Project.importSequences report success while importing nothing, on the UXP plugin side).
        skipped.push(expectedSeqName);
        results[spec.resultKey].push({
          name: expectedSeqName,
          width: width,
          height: height,
          sequenceID: String(existingSeq.sequenceID)
        });
        continue;
      }

      try {
        try { app.project.activeSequence = sourceSeq; } catch (eActivateSource) {}
        var beforeIds = _existingSequenceIdSet();
        if (!sourceSeq.clone()) { errors.push(expectedSeqName + ": clone() failed"); continue; }
        var newSeq = _findNewSequence(beforeIds);
        if (!newSeq) { errors.push(expectedSeqName + ": could not locate the cloned sequence afterward"); continue; }
        newSeq.name = expectedSeqName;
        _clearSequenceVideoTrack(newSeq, 0);
        try { app.project.activeSequence = newSeq; } catch (eActivateNew) {}

        var itemSettings = _sequenceCreateSettings(newSeq);
        itemSettings.width = width;
        itemSettings.height = height;
        var createdItem = _createGenericSourceItem(spec.key, itemSettings);
        if (!createdItem) { errors.push(expectedSeqName + ": item creation failed"); continue; }

        var expectedItemName = spec.label + "_" + width + "x" + height;
        try { createdItem.name = expectedItemName; } catch (eRename) {}

        var insertResult = _insertAtStart(newSeq, createdItem);
        if (!insertResult.ok) {
          errors.push(expectedSeqName + ": insertion failed | diagnostics=" + JSON.stringify(insertResult.diagnostics) + " | attempts=" + JSON.stringify(insertResult.attempts));
          continue;
        }

        _moveSequenceItemIntoBin(expectedSeqName, bins[spec.resultKey]);

        results[spec.resultKey].push({
          name: expectedSeqName,
          width: width,
          height: height,
          sequenceID: String(newSeq.sequenceID)
        });
      } catch (eLoop) {
        errors.push(expectedSeqName + ": " + String(eLoop));
      }
    }
  }

  return JSON.stringify({ ok: true, results: results, errors: errors, skipped: skipped });
}
