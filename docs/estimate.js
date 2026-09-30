/* Estimateur d'âge : applique dans le navigateur le même calcul que scripts/agemodel.py.
   Le modèle (model/age_model.json) est chargé à la première ouverture. Rien n'est envoyé à un serveur. */
(() => {
  "use strict";
  const SCENES = ["Malaise", "Mises en danger", "Tristesse", "Visuel effrayant", "Moquerie", "Santé", "Banalisation de la violence", "Maltraitance", "Sexualité"];
  const SCENE_HINT = {
    "Malaise": "conflit dur, humiliation, angoisse, fin difficile",
    "Mises en danger": "poursuites, chutes, combats, menace de mort",
    "Tristesse": "deuil, séparation, abandon, maladie d'un proche",
    "Visuel effrayant": "monstres, ambiance sombre, images choquantes",
    "Moquerie": "insultes, dénigrement, harcèlement",
    "Santé": "maladie, handicap, alcool, tabac, drogues",
    "Banalisation de la violence": "violence drôle, gratuite ou héroïque",
    "Maltraitance": "violence subie par un enfant ou un être vulnérable",
    "Sexualité": "nudité, séduction appuyée, allusions",
  };
  const LEVELS = [["0", "Aucune"], ["1", "Léger"], ["2", "Marqué"], ["3", "Intense"]];
  const COUNTRY_ALIASES = {
    "united states of america": "États-Unis", "usa": "États-Unis", "us": "États-Unis", "united kingdom": "Royaume-Uni", "uk": "Royaume-Uni",
    "south korea": "Corée du Sud", "japan": "Japon", "france": "France", "germany": "Allemagne", "spain": "Espagne", "italy": "Italie",
    "canada": "Canada", "china": "Chine", "belgium": "Belgique", "denmark": "Danemark", "sweden": "Suède", "norway": "Norvège",
    "ireland": "Irlande", "australia": "Australie", "netherlands": "Pays-Bas", "switzerland": "Suisse",
  };
  const LABELS = { f: "Format", tk: "Technique", p: "Pays", st: "Studio", c: "Réalisation", u: "Univers", th: "Thème", sc: "Scène difficile", sci: "Intensité" };
  const NUM_LABELS = { log_dur: "Durée", per_ep: "Format épisode", year: "Année de sortie", dur_missing: "Durée inconnue" };

  const norm = (s) =>
    String(s ?? "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[’'`´]/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
  // Python arrondit la moitié au pair (round(2.5) == 2) : on fait pareil pour obtenir des résultats identiques
  const pyRound = (x) => { const r = Math.round(x); return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

  let model = null, loading = null;
  async function load() {
    if (model) return model;
    loading = loading || fetch("model/age_model.json").then((r) => { if (!r.ok) throw new Error("modèle introuvable (HTTP " + r.status + ")"); return r.json(); }).then((m) => {
      m._lookup = {};
      for (const [kind, values] of Object.entries(m.vocab)) m._lookup[kind] = new Map(values.map((v) => [norm(v), v]));
      model = m;
      return m;
    });
    return loading;
  }

  function match(kind, name, m) {
    const table = m._lookup[kind];
    const n = norm(name);
    if (!n || !table) return null;
    if (table.has(n)) return table.get(n);
    if ((kind === "studios" || kind === "countries") && n.length >= 5) {
      for (const [key, val] of table) if (key.length >= 5 && (key.includes(n) || n.includes(key))) return val;
    }
    return null;
  }

  function featurize(film, m) {
    const x = {}, ignored = [], unknownThemes = [];
    const fmt = match("formats", film.format || "", m);
    if (fmt) x["f:" + fmt] = 1; else ignored.push(`format « ${film.format} »`);
    const tk = match("techniques", film.technique || "", m);
    if (tk) x["tk:" + tk] = 1; else if (film.technique) ignored.push(`technique « ${film.technique} »`);
    const dur = film.duration_min;
    x["num:log_dur"] = dur ? Math.log1p(dur) : 0;
    x["num:per_ep"] = film.per_episode ? 1 : 0;
    x["num:year"] = clamp(film.year || 2000, 1950, 2035) - 2000;
    x["num:dur_missing"] = dur ? 0 : 1;
    for (let c of film.countries || []) {
      c = COUNTRY_ALIASES[norm(c)] || c;
      const v = match("countries", c, m); if (v) x["p:" + v] = 1;
    }
    for (const [kind, key, prefix] of [["studios", "studios", "st"], ["creators", "directors", "c"], ["univers", "univers", "u"]]) {
      for (const name of film[key] || []) { const v = match(kind, name, m); if (v) x[prefix + ":" + v] = 1; }
    }
    for (let [scene, level] of Object.entries(film.scenes || {})) {
      if (scene === "Malaises") scene = "Malaise";
      if (!SCENES.includes(scene)) { ignored.push(`scène « ${scene} »`); continue; }
      level = clamp(Math.round(Number(level)), 0, 3);
      if (level) { x["sc:" + scene] = 1; x["sci:" + scene] = level; }
    }
    for (const t of film.themes || []) {
      const v = match("themes", t, m);
      if (v) x["th:" + v] = 1; else unknownThemes.push(t);
    }
    return { x, ignored, unknownThemes };
  }

  function linear(target, x) {
    const w = target.weights, contribs = [];
    let sum = target.intercept;
    for (const [k, v] of Object.entries(x)) if (k in w) { const c = w[k] * v; contribs.push([k, c]); sum += c; }
    return [sum, contribs];
  }
  const marginFor = (target, raw) => (target.margins.find((s) => raw < s.below) || target.margins[target.margins.length - 1]).margin;
  function labelOf(key) {
    const i = key.indexOf(":"), kind = key.slice(0, i), name = key.slice(i + 1);
    return kind === "num" ? NUM_LABELS[name] : `${LABELS[kind] || kind} : ${name}`;
  }

  function estimate(film, m = model) {
    const { x, ignored, unknownThemes } = featurize(film, m);
    const [afRaw, contribs] = linear(m.targets.af, x);
    const [amRaw] = linear(m.targets.am, x);
    const margin = marginFor(m.targets.af, afRaw);
    const age = clamp(pyRound(afRaw), 2, 18);
    const prudent = clamp(Math.ceil(afRaw + margin), age, 18);
    const avoid = clamp(pyRound(amRaw), 2, age);
    const merged = new Map();
    for (let [k, v] of contribs) { if (k.startsWith("sci:")) k = "sc:" + k.slice(4); merged.set(k, (merged.get(k) || 0) + v); }
    const reasons = [...merged].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).filter(([, v]) => Math.abs(v) >= 0.15).slice(0, 8)
      .map(([k, v]) => ({ facteur: labelOf(k), annees: Math.round(v * 100) / 100 }));
    return { age_estime: age, age_prudent: prudent, deconseille_avant: avoid, age_brut: Math.round(afRaw * 100) / 100,
             base: Math.round(m.targets.af.intercept * 100) / 100, raisons: reasons, marge: margin,
             couverture: m.targets.af.coverage, ignore: ignored, themes_inconnus: unknownThemes };
  }

  // ------------------------------------------------------------------ interface
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ui = { themes: new Set() };
  let formBuilt = false;

  function readForm() {
    const scenes = {};
    for (const s of SCENES) {
      const v = document.querySelector(`input[name="sc-${CSS.escape(s)}"]:checked`);
      if (v && v.value !== "0") scenes[s] = Number(v.value);
    }
    const studio = $("#est-studio").value.trim();
    const isSeries = $("#est-format").value === "Série";
    return {
      title: $("#est-title").value.trim() || null, format: $("#est-format").value, technique: $("#est-tech").value || null,
      duration_min: Number($("#est-dur").value) || null, per_episode: isSeries, year: Number($("#est-year").value) || null,
      studios: studio ? [studio] : [], countries: $("#est-country").value ? [$("#est-country").value] : [], scenes, themes: [...ui.themes],
    };
  }

  function render() {
    if (!model) return;
    const film = readForm();
    const r = estimate(film);
    const out = $("#est-result");
    const maxAbs = Math.max(1, ...r.raisons.map((x) => Math.abs(x.annees)));
    const nScenes = Object.keys(film.scenes).length;
    out.innerHTML = `
      <div class="est-ages">
        <div class="est-main"><span class="est-lbl">Âge conseillé estimé</span><span class="est-num">${r.age_estime}<small> ans</small></span></div>
        <div class="est-side">
          <div><span class="est-lbl">Âge prudent</span><b>${r.age_prudent} ans</b></div>
          <div><span class="est-lbl">Déconseillé avant</span><b>${r.deconseille_avant} ans</b></div>
        </div>
      </div>
      <p class="est-note">L'âge prudent couvre ${Math.round(r.couverture * 100)} % des cas observés. ${nScenes === 0 ? "<strong>Aucune scène difficile n'est cochée : l'estimation ne repose que sur le format et la durée.</strong>" : ""}</p>
      <h4>Ce qui pèse dans l'estimation <small>(base ${r.base} ans)</small></h4>
      <ul class="est-why">${r.raisons.map((x) => `<li><span class="est-bar ${x.annees < 0 ? "neg" : "pos"}" style="--w:${(Math.abs(x.annees) / maxAbs * 100).toFixed(0)}%"></span><span class="est-y">${x.annees > 0 ? "+" : ""}${x.annees.toFixed(1)}</span><span>${esc(x.facteur)}</span></li>`).join("") || "<li>Pas de facteur marquant.</li>"}</ul>`;
  }

  function buildForm() {
    const scenes = SCENES.map((s) => `
      <fieldset class="est-scene"><legend><b>${s}</b> <small>${SCENE_HINT[s]}</small></legend>
        <div class="seg">${LEVELS.map(([v, l]) => `<label><input type="radio" name="sc-${esc(s)}" value="${v}" ${v === "0" ? "checked" : ""}><span>${l}</span></label>`).join("")}</div>
      </fieldset>`).join("");
    const opts = (vals, all) => (all ? `<option value="">${all}</option>` : "") + vals.map((v) => `<option>${esc(v)}</option>`).join("");
    $("#est-form").innerHTML = `
      <div class="est-row">
        <label class="est-field wide"><span>Titre <small>(facultatif)</small></span><input id="est-title" type="text" maxlength="80" placeholder="ex. un film qui vient de sortir"></label>
        <label class="est-field"><span>Format</span><select id="est-format">${opts(model.vocab.formats)}</select></label>
        <label class="est-field"><span>Technique</span><select id="est-tech">${opts(model.vocab.techniques, "Je ne sais pas")}</select></label>
        <label class="est-field"><span>Durée (min)</span><input id="est-dur" type="number" min="1" max="400" inputmode="numeric" placeholder="95"></label>
        <label class="est-field"><span>Année</span><input id="est-year" type="number" min="1900" max="2100" inputmode="numeric" placeholder="${new Date().getFullYear()}"></label>
        <label class="est-field"><span>Pays</span><select id="est-country">${opts(model.vocab.countries, "Autre / inconnu")}</select></label>
        <label class="est-field"><span>Studio</span><input id="est-studio" type="text" list="est-studio-list" placeholder="ex. Pixar"><datalist id="est-studio-list">${opts(model.vocab.studios)}</datalist></label>
      </div>
      <h4>Scènes difficiles <small>Coche ce que tu sais du film (avis, bande-annonce, synopsis)</small></h4>
      <div class="est-scenes">${scenes}</div>
      <h4>Thèmes <small>facultatif</small></h4>
      <input id="est-theme" type="text" list="est-theme-list" placeholder="Ajouter un thème (ex. Mort, Harcèlement…)" aria-label="Ajouter un thème">
      <datalist id="est-theme-list">${opts(model.vocab.themes)}</datalist>
      <div class="chips" id="est-themes"></div>`;
    const themeMap = new Map(model.vocab.themes.map((t) => [norm(t), t]));
    const addTheme = () => { const v = themeMap.get(norm($("#est-theme").value)); if (v) { ui.themes.add(v); $("#est-theme").value = ""; renderThemes(); render(); } };
    $("#est-theme").addEventListener("change", addTheme);
    $("#est-theme").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTheme(); } });
    $("#est-form").addEventListener("input", render);
    $("#est-form").addEventListener("change", render);
  }
  function renderThemes() {
    const box = $("#est-themes"); box.replaceChildren();
    for (const t of ui.themes) {
      const b = document.createElement("button"); b.type = "button"; b.className = "chip on";
      b.innerHTML = `${esc(t)} <span class="x" aria-hidden="true">✕</span>`; b.setAttribute("aria-label", `Retirer le thème ${t}`);
      b.addEventListener("click", () => { ui.themes.delete(t); renderThemes(); render(); });
      box.appendChild(b);
    }
  }

  async function open() {
    const dlg = $("#estimator");
    dlg.showModal();
    if (!formBuilt) {
      $("#est-result").textContent = "Chargement du modèle…";
      try { await load(); } catch (e) { $("#est-result").innerHTML = `<p class="est-note">Le modèle ne se charge pas : ${esc(e.message)}</p>`; return; }
      buildForm();
      formBuilt = true;
    }
    render();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const btn = $("#est-btn");
    if (btn) btn.addEventListener("click", open);
    const close = $("#est-close");
    if (close) close.addEventListener("click", () => $("#estimator").close());
  });
  window.__estimate = { load, estimate, featurize, norm };
})();
