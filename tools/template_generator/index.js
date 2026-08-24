// index.js - panel JS for the throwaway "Template Generator" CEP dev tool. Not part of the real
// FX.palette product; this only exists to drive generator.jsx (ExtendScript) from a clickable panel
// and merge its results into EffectPalette_UXP/assets/template_project/generic_item_templates.json.

const cs = new CSInterface();
const fs = window.require("fs");
const path = window.require("path");

const EXT_DIR = cs.getSystemPath(SystemPath.EXTENSION);
const GENERATOR_JSX = path.join(EXT_DIR, "generator.jsx");
// Hardcoded, not derived from EXT_DIR: deploy_dev.ps1 *copies* this panel into
// %APPDATA%\Adobe\CEP\extensions\, so at runtime EXT_DIR has no relative path back to the source
// repo's assets/ folder at all - this is a one-off dev tool for this one repo on this one machine.
const CONFIG_JSON = "C:\\Users\\Paulo\\Documents\\Projetos Claude\\EffectPalette_UXP\\assets\\template_project\\generic_item_templates.json";

const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const buttonEl = document.getElementById("generate-btn");

function log(message) {
  logEl.textContent += message + "\n";
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(message) {
  statusEl.textContent = message;
}

function evalHostScript(script, callback) {
  const jsxPath = GENERATOR_JSX.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const loadScript = '$.evalFile("' + jsxPath + '")';
  cs.evalScript(loadScript, function (loadResult) {
    if (loadResult && String(loadResult).indexOf("EvalScript error") === 0) {
      callback(null, "Falha ao carregar generator.jsx: " + loadResult);
      return;
    }
    cs.evalScript(script, function (result) {
      callback(result, null);
    });
  });
}

function readExistingConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_JSON, "utf8");
    return JSON.parse(raw);
  } catch (error) {
    return {};
  }
}

function mergeResultsIntoConfig(results) {
  const config = readExistingConfig();
  ["barsAndTone", "blackVideo", "transparentVideo"].forEach(function (sectionKey) {
    const newTemplates = results[sectionKey] || [];
    if (newTemplates.length === 0) return;
    if (!config[sectionKey]) {
      config[sectionKey] = { projectPath: "template_project/template_project.prproj", templates: [] };
    }
    // Replace (not skip) a matching-name entry: the underlying sequence is routinely rebuilt across
    // debug/cleanup rounds and gets a fresh sequenceID each time, so keeping the OLD entry once a
    // name match was seen left the JSON pointing at a GUID for a sequence that had since been
    // deleted - Project.importSequences then returned true (no error) while importing nothing at
    // all, since it's a real UXP.Project method may not surface "that GUID doesn't exist anymore" as
    // a hard failure the way it fails on a genuinely malformed request.
    newTemplates.forEach(function (entry) {
      const index = config[sectionKey].templates.findIndex(function (existing) { return existing.name === entry.name; });
      if (index >= 0) {
        config[sectionKey].templates[index] = entry;
      } else {
        config[sectionKey].templates.push(entry);
      }
    });
  });
  fs.writeFileSync(CONFIG_JSON, JSON.stringify(config, null, 2) + "\n", "utf8");
}

buttonEl.addEventListener("click", function () {
  buttonEl.disabled = true;
  setStatus("Rodando... isso pode levar um tempo (clonagem + criacao de sequencias em lote).");
  logEl.textContent = "";

  evalHostScript("generateGenericItemTemplates()", function (rawResult, loadError) {
    buttonEl.disabled = false;
    if (loadError) {
      setStatus("Erro.");
      log(loadError);
      return;
    }
    if (!rawResult || String(rawResult).indexOf("EvalScript error") === 0) {
      setStatus("Erro ao executar generateGenericItemTemplates().");
      log(String(rawResult));
      return;
    }

    let parsed;
    try {
      parsed = JSON.parse(rawResult);
    } catch (parseError) {
      setStatus("Erro ao interpretar o resultado.");
      log(String(rawResult));
      return;
    }

    if (!parsed.ok) {
      setStatus("Erro.");
      log(parsed.error || "Erro desconhecido.");
      return;
    }

    const results = parsed.results || {};
    const errors = parsed.errors || [];
    const skipped = parsed.skipped || [];
    const createdCount = (results.barsAndTone || []).length + (results.blackVideo || []).length + (results.transparentVideo || []).length;

    if (createdCount > 0) {
      try {
        mergeResultsIntoConfig(results);
        log("Config atualizado: " + CONFIG_JSON);
      } catch (writeError) {
        log("Falha ao escrever o config JSON: " + String(writeError));
      }
    }

    log("Criados: " + createdCount);
    log("Pulados (ja existiam): " + skipped.length);
    if (skipped.length) log(JSON.stringify(skipped, null, 2));
    log("Erros: " + errors.length);
    if (errors.length) log(JSON.stringify(errors, null, 2));
    log("");
    log(JSON.stringify(results, null, 2));

    setStatus(errors.length > 0 ? "Concluido com erros - veja o log." : "Concluido.");
  });
});
