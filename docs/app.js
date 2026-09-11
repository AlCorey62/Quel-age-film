/* Quel âge pour ce film ? — interface de recherche sur les fiches de filmspourenfants.net
   Données : data/index.json (liste légère), data/films/<id>.json (fiche complète), data/facets.json, data/meta.json.
   Mode « inline » : si window.__DATA__ existe (aperçu hors ligne), on lit les données depuis cet objet. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const INLINE = window.__DATA__ || null;
  const DATA_BASE = "data/";
  const PAGE = 48;
  const AGES = Array.from({ length: 17 }, (_, i) => i + 2); // 2 … 18

  // ---------- Utilitaires ----------
  const norm = (s) =>
    (s || "")
      .toString()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[’'`´]/g, " ")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const bigrams = (s) => {
    const out = new Set();
    const t = " " + s.replace(/\s+/g, " ") + " ";
    for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
    return out;
  };
  const dice = (a, b) => {
    if (!a.size || !b.size) return 0;
    let inter = 0;
    for (const g of a) if (b.has(g)) inter++;
    return (2 * inter) / (a.size + b.size);
  };

  const plural = (n, one, many) => `${n.toLocaleString("fr-FR")} ${n > 1 ? many : one}`;
  const fmtDuration = (f) => {
    if (!f.d) return "";
    if (f.ep) return `${f.dm} min/épisode`;
    if (f.dm >= 60) return `${Math.floor(f.dm / 60)}h${String(f.dm % 60).padStart(2, "0")}`;
    return `${f.dm} min`;
  };
  const ageOf = (birth) => {
    // birth = "YYYY-MM" ; âge en années révolues, à ce jour
    if (!birth) return null;
    const [y, m] = birth.split("-").map(Number);
    const now = new Date();
    let age = now.getFullYear() - y;
    if (now.getMonth() + 1 < m) age--;
    return Math.max(0, age);
  };

  // ---------- État ----------
  const state = {
    q: "",
    age: null, // nombre ou null
    kid: null, // nom de l'enfant sélectionné (facultatif)
    limite: false,
    tooYoung: true,
    format: new Set(),
    technique: new Set(),
    univers: new Set(),
    themes: new Set(),
    themesAll: false,
    dmax: 185,
    y1: null,
    y2: null,
    pays: "",
    sort: "rel",
    limit: PAGE,
    film: null,
  };
  let films = [];
  let facets = {};
  let meta = {};
  let results = [];
  const details = new Map();
  let kids = loadKids();

  function loadKids() {
    try {
      const k = JSON.parse(localStorage.getItem("fpe.kids") || "[]");
      return Array.isArray(k) ? k.filter((x) => x && x.name && x.birth) : [];
    } catch {
      return [];
    }
  }
  function saveKids() {
    try {
      localStorage.setItem("fpe.kids", JSON.stringify(kids));
    } catch {}
  }

  // ---------- URL (hash) ----------
  const HASH_KEYS = ["q", "age", "kid", "limite", "tooYoung", "format", "technique", "univers", "themes", "themesAll", "dmax", "y1", "y2", "pays", "sort", "film"];
  let hashLock = false;
  function writeHash() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.age != null) p.set("age", state.age);
    if (state.kid) p.set("kid", state.kid);
    if (state.limite) p.set("limite", "1");
    if (!state.tooYoung) p.set("tooYoung", "0");
    for (const k of ["format", "technique", "univers", "themes"]) if (state[k].size) p.set(k, [...state[k]].join("|"));
    if (state.themesAll) p.set("themesAll", "1");
    if (state.dmax < 185) p.set("dmax", state.dmax);
    if (state.y1) p.set("y1", state.y1);
    if (state.y2) p.set("y2", state.y2);
    if (state.pays) p.set("pays", state.pays);
    if (state.sort !== "rel") p.set("sort", state.sort);
    if (state.film) p.set("film", state.film);
    const h = p.toString();
    hashLock = true;
    if (("#" + h) !== location.hash && !(h === "" && location.hash === "")) history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
    setTimeout(() => (hashLock = false), 0);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    state.q = p.get("q") || "";
    state.age = p.has("age") ? Number(p.get("age")) : null;
    state.kid = p.get("kid") || null;
    state.limite = p.get("limite") === "1";
    state.tooYoung = p.get("tooYoung") !== "0";
    for (const k of ["format", "technique", "univers", "themes"]) state[k] = new Set((p.get(k) || "").split("|").filter(Boolean));
    state.themesAll = p.get("themesAll") === "1";
    state.dmax = p.has("dmax") ? Number(p.get("dmax")) : 185;
    state.y1 = p.get("y1") ? Number(p.get("y1")) : null;
    state.y2 = p.get("y2") ? Number(p.get("y2")) : null;
    state.pays = p.get("pays") || "";
    state.sort = p.get("sort") || "rel";
    state.film = p.get("film") ? Number(p.get("film")) : null;
    state.limit = PAGE;
  }

  // ---------- Chargement ----------
  async function loadJSON(name) {
    if (INLINE) return INLINE[name.replace(".json", "")];
    const r = await fetch(DATA_BASE + name, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  }

  async function loadDetail(id) {
    if (details.has(id)) return details.get(id);
    let d;
    if (INLINE) {
      d = (INLINE.details && INLINE.details[id]) || null;
    } else {
      const r = await fetch(`${DATA_BASE}films/${id}.json`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      d = await r.json();
    }
    details.set(id, d);
    return d;
  }

  // ---------- Verdict ----------
  // Renvoie {cls, label, title} pour un film et un âge donné
  function verdict(f, age) {
    if (age == null) return null;
    if (f.af != null && age >= f.af) {
      if (f.ax != null && age > f.ax) return { cls: "mute", label: `Un peu « bébé » à ${age} ans`, title: `Conseillé de ${f.af} à ${f.ax} ans` };
      return { cls: "ok", label: `OK à ${age} ans`, title: `Conseillé à partir de ${f.af} ans` };
    }
    if (f.am != null && age >= f.am) return { cls: "warn", label: `Limite à ${age} ans`, title: `Déconseillé aux moins de ${f.am} ans, conseillé à partir de ${f.af} ans` };
    return { cls: "no", label: `Pas avant ${f.am ?? f.af} ans`, title: `Déconseillé aux moins de ${f.am ?? f.af} ans` };
  }

  // ---------- Recherche ----------
  function prepareIndex() {
    for (const f of films) {
      f._t = norm(f.t);
      f._tw = f._t.split(" ");
      f._c = norm((f.c || []).join(" "));
      f._th = norm((f.th || []).join(" "));
      f._st = norm((f.st || []).join(" "));
      f._u = norm((f.u || []).join(" "));
      f._bg = null;
    }
  }

  function scoreFilm(f, tokens, qn) {
    let score = 0;
    for (const tok of tokens) {
      let s = 0;
      if (f._t === tok) s = 120;
      else if (f._t.startsWith(tok)) s = 90;
      else if (f._tw.some((w) => w.startsWith(tok))) s = 70;
      else if (f._t.includes(tok)) s = 45;
      else if (f._c.includes(tok)) s = 30;
      else if (f._st.includes(tok)) s = 18;
      else if (f._u.includes(tok)) s = 14;
      else if (f._th.includes(tok)) s = 12;
      if (!s) return 0;
      score += s;
    }
    if (f._t === qn) score += 100;
    else if (f._t.startsWith(qn)) score += 40;
    // titres courts légèrement favorisés (« Cars » avant « Cars 3 : en route »)
    score -= Math.min(10, f._tw.length);
    return score;
  }

  function search() {
    const qn = norm(state.q);
    const tokens = qn.split(" ").filter(Boolean);
    let list = films;

    // filtres
    const age = state.age;
    list = list.filter((f) => {
      if (age != null) {
        if (f.af == null && f.am == null) return false;
        const ok = f.af != null && age >= f.af;
        const lim = !ok && f.am != null && age >= f.am;
        if (!ok && !(state.limite && lim)) return false;
        if (state.tooYoung && ok && f.ax != null && age > f.ax) return false;
      }
      if (state.format.size && !state.format.has(f.f)) return false;
      if (state.technique.size && !(f.tk || []).some((x) => state.technique.has(x))) return false;
      if (state.univers.size && !(f.u || []).some((x) => state.univers.has(x))) return false;
      if (state.themes.size) {
        const th = f.th || [];
        if (state.themesAll ? ![...state.themes].every((x) => th.includes(x)) : !th.some((x) => state.themes.has(x))) return false;
      }
      if (state.dmax < 185 && (f.dm == null || f.dm > state.dmax)) return false;
      if (state.y1 && (f.y == null || f.y < state.y1)) return false;
      if (state.y2 && (f.y == null || f.y > state.y2)) return false;
      if (state.pays && !(f.p || []).includes(state.pays)) return false;
      return true;
    });

    let scored;
    if (tokens.length) {
      scored = [];
      for (const f of list) {
        const s = scoreFilm(f, tokens, qn);
        if (s > 0) scored.push([s, f]);
      }
      // tolérance aux fautes : si peu de résultats, similarité par bigrammes sur le titre
      if (scored.length < 5 && qn.length >= 4) {
        const qb = bigrams(qn);
        const seen = new Set(scored.map(([, f]) => f.id));
        for (const f of list) {
          if (seen.has(f.id)) continue;
          f._bg = f._bg || bigrams(f._t);
          let d = dice(qb, f._bg);
          if (d < 0.45 && f._c) {
            f._cbg = f._cbg || bigrams(f._c);
            d = Math.max(d, dice(qb, f._cbg) * 0.9); // réalisateurs mal orthographiés (« miyasaki »)
          }
          if (d >= 0.45) scored.push([d * 40, f]);
        }
      }
    } else {
      scored = list.map((f) => [0, f]);
    }

    const cmp = {
      rel: (a, b) => b[0] - a[0] || a[1]._t.localeCompare(b[1]._t),
      title: (a, b) => a[1]._t.localeCompare(b[1]._t),
      year: (a, b) => (b[1].y ?? 0) - (a[1].y ?? 0) || a[1]._t.localeCompare(b[1]._t),
      pub: (a, b) => (b[1].pub || "").localeCompare(a[1].pub || ""),
      age: (a, b) => (a[1].af ?? 99) - (b[1].af ?? 99) || a[1]._t.localeCompare(b[1]._t),
      dur: (a, b) => (a[1].dm ?? 9999) - (b[1].dm ?? 9999) || a[1]._t.localeCompare(b[1]._t),
    };
    let sort = state.sort;
    if (sort === "rel" && !tokens.length) sort = age != null ? "pub" : "pub";
    scored.sort(cmp[sort] || cmp.rel);
    results = scored.map(([, f]) => f);
  }

  // ---------- Rendu : liste ----------
  const grid = $("#grid");
  const tpl = $("#card-tpl");

  function highlight(title) {
    const tokens = norm(state.q).split(" ").filter((t) => t.length >= 2);
    if (!tokens.length) return esc(title);
    // surligne les portions du titre dont la forme normalisée commence par un token
    const parts = title.split(/(\s+|[-:!?,.()]+)/);
    return parts
      .map((w) => {
        const wn = norm(w);
        return wn && tokens.some((t) => wn.startsWith(t) || t.startsWith(wn) && wn.length >= 3) ? `<mark>${esc(w)}</mark>` : esc(w);
      })
      .join("");
  }

  const HOT_SCENES = /peur|effray|violence|mort|sang|angoiss|terreur|sexual|cruaut|horreur|traumat|monstre|maltrait/i;

  function renderCard(f) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.id = f.id;
    const poster = $(".poster", node);
    const img = $("img", poster);
    if (f.img && !(INLINE && INLINE.noImages)) {
      img.src = f.img;
      img.alt = "";
      img.addEventListener("error", () => { poster.classList.add("empty"); img.remove(); poster.textContent = f.t.slice(0, 1); }, { once: true });
    } else {
      poster.classList.add("empty");
      img.remove();
      poster.textContent = f.t.slice(0, 1);
    }
    $(".card-title", node).innerHTML = highlight(f.t);

    const v = verdict(f, state.age);
    const vEl = $(".verdict", node);
    if (v) {
      vEl.hidden = false;
      vEl.className = "verdict " + v.cls;
      vEl.textContent = v.label;
      vEl.title = v.title;
    }

    const ageLine = $(".age-line", node);
    ageLine.innerHTML =
      (f.af != null ? `<span class="age-big">Dès ${f.af} ans</span>` : "") +
      (f.am != null && f.am !== f.af ? `<span class="age-min">déconseillé aux moins de <b>${f.am}</b></span>` : "") +
      (f.ax != null ? `<span class="age-min">jusqu'à <b>${f.ax}</b> ans</span>` : "");

    const metaBits = [f.f, f.y, fmtDuration(f), (f.tk || [])[0]].filter(Boolean);
    $(".meta-line", node).innerHTML = metaBits.map((b) => `<span>${esc(b)}</span>`).join("");

    const sc = $(".scenes", node);
    const labels = (f.sd || []).slice(0, 4);
    sc.innerHTML = labels.map((l) => `<span class="tag ${HOT_SCENES.test(l) ? "hot" : ""}">${esc(l)}</span>`).join("");
    if ((f.sd || []).length > 4) sc.insertAdjacentHTML("beforeend", `<span class="tag">+${f.sd.length - 4}</span>`);
    return node;
  }

  function renderList(reset = true) {
    if (reset) grid.replaceChildren();
    const start = reset ? 0 : grid.children.length;
    const slice = results.slice(start, state.limit);
    const frag = document.createDocumentFragment();
    for (const f of slice) frag.appendChild(renderCard(f));
    grid.appendChild(frag);
    $("#more").hidden = results.length <= state.limit;
    $("#empty").hidden = results.length > 0;

    const total = films.length;
    const c = $("#count");
    if (results.length === total && !state.q) c.innerHTML = `${plural(total, "fiche", "fiches")} <small>· les plus récentes d'abord</small>`;
    else c.innerHTML = `${plural(results.length, "résultat", "résultats")} <small>sur ${total.toLocaleString("fr-FR")}</small>`;
  }

  // ---------- Rendu : âges ----------
  function renderAges() {
    const box = $("#ages");
    box.replaceChildren();
    const mk = (label, sub, pressed, onClick, extraCls = "") => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "age-chip " + extraCls;
      b.setAttribute("aria-pressed", pressed ? "true" : "false");
      b.innerHTML = esc(label) + (sub ? ` <span class="sub">${esc(sub)}</span>` : "");
      b.addEventListener("click", onClick);
      box.appendChild(b);
      return b;
    };
    mk("Tous", null, state.age == null, () => { state.age = null; state.kid = null; update(); });
    for (const k of kids) {
      const a = ageOf(k.birth);
      mk(k.name, `${a} ans`, state.kid === k.name, () => {
        state.kid = k.name; state.age = a; update();
      }, "kid");
    }
    for (const a of AGES) {
      mk(`${a} ans`, null, state.age === a && !state.kid, () => { state.age = a; state.kid = null; update(); });
    }
    $("#age-options").hidden = state.age == null;
    // faire défiler jusqu'au chip actif
    const on = $('.age-chip[aria-pressed="true"]', box);
    if (on) on.scrollIntoView({ block: "nearest", inline: "center" });
  }

  // ---------- Rendu : filtres ----------
  function chipList(container, entries, set, { max = Infinity, withCount = true } = {}) {
    container.replaceChildren();
    const shown = container.dataset.expanded === "1" ? entries : entries.slice(0, max);
    for (const [name, n] of shown) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.setAttribute("aria-pressed", set.has(name) ? "true" : "false");
      b.innerHTML = esc(name) + (withCount ? ` <span class="n">${n}</span>` : "");
      b.addEventListener("click", () => { set.has(name) ? set.delete(name) : set.add(name); update(); });
      container.appendChild(b);
    }
    if (entries.length > max) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "show-more";
      more.textContent = container.dataset.expanded === "1" ? "Voir moins" : `Voir les ${entries.length - max} autres`;
      more.addEventListener("click", () => { container.dataset.expanded = container.dataset.expanded === "1" ? "0" : "1"; renderFilters(); });
      container.appendChild(more);
    }
  }

  function renderFilters() {
    chipList($("#f-format"), facets.f || [], state.format);
    chipList($("#f-technique"), facets.tk || [], state.technique);
    // univers : les sélectionnés toujours visibles en tête
    const uni = (facets.u || []).slice().sort((a, b) => (state.univers.has(b[0]) - state.univers.has(a[0])) || b[1] - a[1]);
    chipList($("#f-univers"), uni, state.univers, { max: 12 });
    // thèmes choisis
    const thBox = $("#f-themes");
    thBox.replaceChildren();
    for (const t of state.themes) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip on";
      b.innerHTML = `${esc(t)} <span class="x" aria-hidden="true">✕</span>`;
      b.setAttribute("aria-label", `Retirer le thème ${t}`);
      b.addEventListener("click", () => { state.themes.delete(t); update(); });
      thBox.appendChild(b);
    }
    $("#opt-themes-all").checked = state.themesAll;
    $("#opt-themes-all").parentElement.hidden = state.themes.size < 2;
    $("#opt-limite").checked = state.limite;
    $("#opt-toojeune").checked = state.tooYoung;
    $("#dmax").value = state.dmax;
    $("#dmax-out").textContent = state.dmax >= 185 ? "Sans limite" : `≤ ${state.dmax} min`;
    $("#y1").value = state.y1 || "";
    $("#y2").value = state.y2 || "";
    $("#f-pays").value = state.pays;
    $("#sort").value = state.sort;

    // compteur de filtres actifs + rappel
    const active = [];
    for (const k of ["format", "technique", "univers"]) for (const v of state[k]) active.push([k, v]);
    for (const v of state.themes) active.push(["themes", v]);
    if (state.dmax < 185) active.push(["dmax", `≤ ${state.dmax} min`]);
    if (state.y1 || state.y2) active.push(["years", `${state.y1 || "…"} – ${state.y2 || "…"}`]);
    if (state.pays) active.push(["pays", state.pays]);
    const badge = $("#filters-badge");
    badge.hidden = !active.length;
    badge.textContent = active.length;
    const af = $("#active-filters");
    af.replaceChildren();
    for (const [k, v] of active) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip on";
      b.innerHTML = `${esc(v)} <span class="x" aria-hidden="true">✕</span>`;
      b.setAttribute("aria-label", `Retirer le filtre ${v}`);
      b.addEventListener("click", () => {
        if (k === "dmax") state.dmax = 185;
        else if (k === "years") { state.y1 = null; state.y2 = null; }
        else if (k === "pays") state.pays = "";
        else state[k].delete(v);
        update();
      });
      af.appendChild(b);
    }
  }

  function initFilterWidgets() {
    const dl = $("#theme-list");
    for (const [name, n] of facets.th || []) {
      const o = document.createElement("option");
      o.value = name;
      o.label = `${n}`;
      dl.appendChild(o);
    }
    const themeNames = new Map((facets.th || []).map(([n]) => [norm(n), n]));
    const ti = $("#theme-input");
    const addTheme = () => {
      const v = themeNames.get(norm(ti.value));
      if (v) { state.themes.add(v); ti.value = ""; update(); }
    };
    ti.addEventListener("change", addTheme);
    ti.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTheme(); } });

    const ps = $("#f-pays");
    for (const [name, n] of facets.p || []) {
      const o = document.createElement("option");
      o.value = name;
      o.textContent = `${name} (${n})`;
      ps.appendChild(o);
    }
    ps.addEventListener("change", () => { state.pays = ps.value; update(); });

    $("#opt-limite").addEventListener("change", (e) => { state.limite = e.target.checked; update(); });
    $("#opt-toojeune").addEventListener("change", (e) => { state.tooYoung = e.target.checked; update(); });
    $("#opt-themes-all").addEventListener("change", (e) => { state.themesAll = e.target.checked; update(); });
    const dm = $("#dmax");
    dm.addEventListener("input", () => { $("#dmax-out").textContent = Number(dm.value) >= 185 ? "Sans limite" : `≤ ${dm.value} min`; });
    dm.addEventListener("change", () => { state.dmax = Number(dm.value); update(); });
    const yr = () => { state.y1 = Number($("#y1").value) || null; state.y2 = Number($("#y2").value) || null; update(); };
    $("#y1").addEventListener("change", yr);
    $("#y2").addEventListener("change", yr);
    $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; update(); });
    $("#reset").addEventListener("click", () => {
      Object.assign(state, { format: new Set(), technique: new Set(), univers: new Set(), themes: new Set(), themesAll: false, dmax: 185, y1: null, y2: null, pays: "", sort: "rel" });
      update();
    });

    // panneau filtres mobile
    const panel = $("#filters");
    const toggle = $("#filters-toggle");
    let backdrop = null;
    const close = () => { panel.classList.remove("open"); toggle.setAttribute("aria-expanded", "false"); backdrop?.remove(); backdrop = null; };
    toggle.addEventListener("click", () => {
      if (panel.classList.contains("open")) return close();
      panel.classList.add("open");
      toggle.setAttribute("aria-expanded", "true");
      backdrop = document.createElement("div");
      backdrop.className = "filters-backdrop";
      backdrop.addEventListener("click", close);
      document.body.appendChild(backdrop);
    });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel.classList.contains("open")) close(); });
  }

  // ---------- Fiche détaillée ----------
  const dlg = $("#detail");

  async function openFilm(id, push = true) {
    const f = films.find((x) => x.id === id);
    if (!f) return;
    state.film = id;
    if (push) writeHash();
    dlg.innerHTML = `<div class="detail-inner"><div class="loading">Chargement de « ${esc(f.t)} »…</div></div>`;
    if (!dlg.open) dlg.showModal();
    let d = null;
    try {
      d = await loadDetail(id);
    } catch (e) {
      d = null;
    }
    if (state.film !== id) return; // l'utilisateur a changé de fiche entre-temps
    dlg.innerHTML = renderDetail(f, d);
    dlg.scrollTop = 0;
    const pimg = $(".detail-hero .poster img", dlg);
    if (pimg) pimg.addEventListener("error", () => { const p = pimg.parentElement; pimg.remove(); p.classList.add("empty"); p.textContent = f.t.slice(0, 1); }, { once: true });
    $(".close-detail", dlg).addEventListener("click", () => dlg.close());
    $$(".tag.link", dlg).forEach((b) =>
      b.addEventListener("click", () => {
        const kind = b.dataset.kind, v = b.dataset.v;
        if (kind === "theme") state.themes.add(v);
        else if (kind === "univers") state.univers.add(v);
        else if (kind === "createur") { state.q = v; }
        state.limit = PAGE;
        dlg.close();
        update();
      })
    );
  }

  function ageCards(f) {
    const cards = [];
    if (f.af != null) cards.push(`<div class="age-card main"><div class="lbl">À partir de</div><div class="val">${f.af} <small>ans</small></div></div>`);
    if (f.am != null) cards.push(`<div class="age-card"><div class="lbl">Déconseillé aux moins de</div><div class="val">${f.am} <small>ans</small></div></div>`);
    if (f.ax != null) cards.push(`<div class="age-card"><div class="lbl">Intéressant jusqu'à</div><div class="val">${f.ax} <small>ans</small></div></div>`);
    return cards.join("");
  }

  function kidVerdicts(f) {
    const rows = [];
    const seen = new Set();
    for (const k of kids) {
      const a = ageOf(k.birth);
      const v = verdict(f, a);
      if (v) rows.push(`<span class="verdict ${v.cls}" title="${esc(v.title)}">${esc(k.name)} · ${esc(v.label.replace(/ à \d+ ans$/, ""))}</span>`);
      seen.add(a);
    }
    if (!kids.length && state.age != null) {
      const v = verdict(f, state.age);
      if (v) rows.push(`<span class="verdict ${v.cls}" title="${esc(v.title)}">${esc(v.label)}</span>`);
    }
    return rows.length ? `<div class="kid-verdicts">${rows.join("")}</div>` : "";
  }

  function itemsHTML(items) {
    return `<ul class="items">${items
      .map((it) => `<li>${it.title ? `<b>${esc(it.title)}</b>` : "<b></b>"}<p>${esc(it.text)}</p></li>`)
      .join("")}</ul>`;
  }

  function renderDetail(f, d) {
    const poster = INLINE && INLINE.noImages ? null : (d && d.image_full) || f.img;
    const sub = [f.f, f.y, d?.duration || fmtDuration(f), ...(d?.technique || f.tk || []), ...(d?.pays || f.p || [])].filter(Boolean);
    const creators = d?.createurs || f.c || [];
    const themes = d?.themes || f.th || [];
    const univers = d?.univers || f.u || [];
    let body = "";
    if (d) {
      if (d.scenes?.length)
        body += `<section class="section scenes-sec"><h3>Scènes difficiles</h3>${itemsHTML(d.scenes)}</section>`;
      else body += `<section class="section scenes-sec"><h3>Scènes difficiles</h3><p class="intro">Aucune scène difficile relevée sur la fiche.</p></section>`;
      if (d.intro) body += `<section class="section"><h3>En bref</h3><p class="intro">${esc(d.intro)}</p></section>`;
      if (d.messages?.length) body += `<section class="section"><h3>Messages</h3>${itemsHTML(d.messages)}</section>`;
      if (d.vocabulaire) body += `<section class="section"><h3>Vocabulaire</h3><p class="intro">${esc(d.vocabulaire)}</p></section>`;
      for (const s of d.autres || []) body += `<section class="section"><h3>${esc(s.title)}</h3>${s.intro ? `<p class="intro">${esc(s.intro)}</p>` : ""}${s.items?.length ? itemsHTML(s.items) : ""}</section>`;
      if (d.conclusion) body += `<section class="section"><h3>En résumé</h3><p class="intro">${esc(d.conclusion)}</p></section>`;
      const cast = d.acteurs?.length ? `<section class="section"><h3>Avec</h3><div class="tags">${d.acteurs.map((a) => `<span class="tag">${esc(a)}</span>`).join("")}</div></section>` : "";
      const studio = d.studio?.length ? `<section class="section"><h3>Studio</h3><div class="tags">${d.studio.map((a) => `<span class="tag">${esc(a)}</span>`).join("")}</div></section>` : "";
      body += cast + studio;
    } else {
      body += `<p class="intro">Le détail de la fiche n'a pas pu être chargé. Le résumé complet est disponible sur le site source.</p>`;
    }
    const tagBtn = (kind, v) => `<button type="button" class="tag link" data-kind="${kind}" data-v="${esc(v)}">${esc(v)}</button>`;
    return `
<div class="detail-inner">
  <div class="detail-hero">
    <div class="poster ${poster ? "" : "empty"}">${poster ? `<img src="${esc(poster)}" alt="Affiche de ${esc(f.t)}">` : esc(f.t.slice(0, 1))}</div>
    <div class="detail-head">
      <div class="row">
        <h2 class="detail-title" id="detail-title">${esc(f.t)}</h2>
        <button type="button" class="icon-btn close-detail" aria-label="Fermer">✕</button>
      </div>
      <div class="detail-sub">${sub.map((s) => `<span>${esc(s)}</span>`).join("")}${creators.length ? `<span>${creators.map((c) => tagBtn("createur", c)).join(", ")}</span>` : ""}</div>
      <div class="ages-block">${ageCards(f)}</div>
      ${kidVerdicts(f)}
    </div>
  </div>
  <div class="detail-body">
    ${body}
    ${univers.length ? `<section class="section"><h3>Univers</h3><div class="tags">${univers.map((u) => tagBtn("univers", u)).join("")}</div></section>` : ""}
    ${themes.length ? `<section class="section"><h3>Thèmes</h3><div class="tags">${themes.map((t) => tagBtn("theme", t)).join("")}</div></section>` : ""}
    <div class="detail-foot">
      <a class="btn-secondary" href="${esc(d?.url || `https://www.filmspourenfants.net/${f.s}/`)}" target="_blank" rel="noopener">Lire la fiche sur filmspourenfants.net ↗</a>
      <small>Fiche mise à jour le ${f.m ? new Date(f.m + "Z").toLocaleDateString("fr-FR") : "—"}</small>
    </div>
  </div>
</div>`;
  }

  dlg.addEventListener("close", () => {
    if (state.film != null) { state.film = null; writeHash(); }
  });
  dlg.addEventListener("click", (e) => {
    // clic sur le fond = fermer
    const r = dlg.getBoundingClientRect();
    if (e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dlg.close();
  });

  // ---------- Mes enfants ----------
  const kdlg = $("#kids-dialog");
  function renderKids() {
    const ul = $("#kids-list");
    ul.replaceChildren();
    kids.forEach((k, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="nm">${esc(k.name)}</span><span class="ag">${ageOf(k.birth)} ans</span><button type="button" class="icon-btn" aria-label="Retirer ${esc(k.name)}">✕</button>`;
      $("button", li).addEventListener("click", () => {
        kids.splice(i, 1);
        if (state.kid === k.name) { state.kid = null; state.age = null; }
        saveKids(); renderKids(); update();
      });
      ul.appendChild(li);
    });
  }
  $("#kids-btn").addEventListener("click", () => { renderKids(); kdlg.showModal(); });
  $("#kid-add").addEventListener("click", () => {
    const name = $("#kid-name").value.trim();
    const birth = $("#kid-birth").value;
    if (!name || !/^\d{4}-\d{2}$/.test(birth)) { $(!name ? "#kid-name" : "#kid-birth").focus(); return; }
    kids.push({ name, birth });
    kids.sort((a, b) => a.birth.localeCompare(b.birth));
    saveKids();
    $("#kid-name").value = ""; $("#kid-birth").value = "";
    renderKids(); update();
  });

  // ---------- Recherche : saisie ----------
  const qInput = $("#q");
  let qTimer = null;
  qInput.addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { state.q = qInput.value.trim(); state.limit = PAGE; update(); }, 120);
  });
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); qInput.blur(); });
  $("#clear-q").addEventListener("click", () => { qInput.value = ""; state.q = ""; update(); qInput.focus(); });
  $("#brand").addEventListener("click", (e) => {
    e.preventDefault();
    readHash.call(null); // no-op sur l'état : on remet tout à zéro ci-dessous
    Object.assign(state, { q: "", age: null, kid: null, limite: false, tooYoung: true, format: new Set(), technique: new Set(), univers: new Set(), themes: new Set(), themesAll: false, dmax: 185, y1: null, y2: null, pays: "", sort: "rel", limit: PAGE, film: null });
    qInput.value = "";
    update();
    window.scrollTo({ top: 0 });
  });

  // ---------- Liste : interactions ----------
  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (card) openFilm(Number(card.dataset.id));
  });
  grid.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("card")) { e.preventDefault(); openFilm(Number(e.target.dataset.id)); }
  });
  $("#more").addEventListener("click", () => { state.limit += PAGE; renderList(false); });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      if (entries.some((x) => x.isIntersecting) && !$("#more").hidden) { state.limit += PAGE; renderList(false); }
    }, { rootMargin: "600px" }).observe($("#more"));
  }

  window.addEventListener("hashchange", () => {
    if (hashLock) return;
    readHash();
    qInput.value = state.q;
    update(false);
    if (state.film) openFilm(state.film, false);
    else if (dlg.open) dlg.close();
  });

  // ---------- Mise à jour globale ----------
  function update(write = true) {
    if (state.kid) {
      const k = kids.find((x) => x.name === state.kid);
      if (k) state.age = ageOf(k.birth); else state.kid = null;
    }
    $("#clear-q").hidden = !state.q;
    search();
    state.limit = Math.max(PAGE, Math.min(state.limit, Math.ceil(results.length / PAGE) * PAGE || PAGE));
    renderAges();
    renderFilters();
    renderList(true);
    if (write) writeHash();
  }

  // ---------- Démarrage ----------
  async function start() {
    try {
      [films, facets, meta] = await Promise.all([loadJSON("index.json"), loadJSON("facets.json"), loadJSON("meta.json").catch(() => ({}))]);
    } catch (e) {
      $("#count").textContent = "Impossible de charger les données.";
      $("#empty").hidden = false;
      $("#empty").innerHTML = `<p><strong>Les données ne se chargent pas.</strong></p><p>${esc(e.message)}. Si tu ouvres le fichier en local, lance un petit serveur : <code>python3 -m http.server -d docs</code></p>`;
      return;
    }
    prepareIndex();
    if (meta.last_sync) {
      const d = new Date(meta.last_sync);
      $("#meta").textContent = `${films.length.toLocaleString("fr-FR")} fiches · synchronisé le ${d.toLocaleDateString("fr-FR")} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
    }
    readHash();
    qInput.value = state.q;
    initFilterWidgets();
    update(false);
    if (state.film) openFilm(state.film, false);

    if (!INLINE && "serviceWorker" in navigator && location.protocol.startsWith("http")) {
      try { navigator.serviceWorker.register("sw.js"); } catch {}
    }
  }
  start();
})();
