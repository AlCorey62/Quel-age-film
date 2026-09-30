"""
Estimation de l'âge conseillé d'un film, entraînée sur les fiches de filmspourenfants.net.

Ce module ne dépend que de la bibliothèque standard : il charge docs/model/age_model.json
(produit par train_age_model.py) et estime l'âge à partir d'une description du film.

Principe
--------
Le modèle est une régression linéaire régularisée (ridge). L'âge est la somme d'une base et de
contributions additives : format, technique, durée, scènes difficiles (9 catégories × 3 niveaux),
thèmes, studio, réalisateur… Chaque contribution est exprimée en années : c'est ce qui permet
d'expliquer l'estimation, et d'exécuter le même calcul dans le navigateur.

Les avis officiels (CNC, PEGI, MPAA, FSK…) n'entrent jamais dans le calcul. La référence est
uniquement l'analyse du site, plus prudente que ces classifications.

Description d'un film (tous les champs sont facultatifs sauf « format ») :
{
  "title": "…", "format": "Long-métrage", "technique": "Images de synthèse",
  "duration_min": 95, "per_episode": false, "year": 2026,
  "countries": ["États-Unis"], "studios": ["Pixar"], "directors": ["…"], "univers": ["Comique"],
  "scenes": {"Mises en danger": 2, "Visuel effrayant": 1},     # niveau 1 léger, 2 marqué, 3 intense
  "themes": ["Mort", "Amitié"]
}
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "docs" / "model" / "age_model.json"

SCENES = [
    "Malaise", "Mises en danger", "Tristesse", "Visuel effrayant", "Moquerie",
    "Santé", "Banalisation de la violence", "Maltraitance", "Sexualité",
]
LEVEL_LABELS = {0: "absent", 1: "léger", 2: "marqué", 3: "intense"}
AGE_MIN, AGE_MAX = 2, 18


def norm(s: str) -> str:
    """Minuscules, sans accents ni ponctuation : sert à rapprocher des noms écrits différemment."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[’'`´]", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_model(path: Path | str = MODEL_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        model = json.load(f)
    # tables de recherche normalisées, construites une fois
    model["_lookup"] = {
        kind: {norm(v): v for v in values} for kind, values in model["vocab"].items()
    }
    return model


# --------------------------------------------------------------------------- rapprochement des noms
def _match(kind: str, name: str, model: dict) -> str | None:
    table = model["_lookup"].get(kind, {})
    n = norm(name)
    if not n:
        return None
    if n in table:
        return table[n]
    if kind in ("studios", "countries"):
        # « Walt Disney Pictures » ↔ « Walt Disney » ; « United States of America » ↔ « États-Unis »
        for key, val in table.items():
            if len(key) >= 5 and (key in n or n in key) and len(n) >= 5:
                return val
    return None


COUNTRY_ALIASES = {
    "united states of america": "États-Unis", "usa": "États-Unis", "us": "États-Unis",
    "united kingdom": "Royaume-Uni", "uk": "Royaume-Uni", "south korea": "Corée du Sud",
    "japan": "Japon", "france": "France", "germany": "Allemagne", "spain": "Espagne",
    "italy": "Italie", "canada": "Canada", "china": "Chine", "belgium": "Belgique",
    "denmark": "Danemark", "sweden": "Suède", "norway": "Norvège", "ireland": "Irlande",
    "australia": "Australie", "netherlands": "Pays-Bas", "switzerland": "Suisse",
}


# --------------------------------------------------------------------------- variables du modèle
def featurize(film: dict, model: dict) -> tuple[dict[str, float], dict]:
    """Renvoie (variables actives, rapport de rapprochement). Seules les variables non nulles sont listées."""
    x: dict[str, float] = {}
    report = {"ignored": [], "unknown_themes": []}

    fmt = _match("formats", film.get("format", ""), model)
    if fmt:
        x[f"f:{fmt}"] = 1.0
    else:
        report["ignored"].append(f"format « {film.get('format')} »")
    tk = _match("techniques", film.get("technique", ""), model)
    if tk:
        x[f"tk:{tk}"] = 1.0
    elif film.get("technique"):
        report["ignored"].append(f"technique « {film.get('technique')} »")

    dur = film.get("duration_min")
    x["num:log_dur"] = math.log1p(dur) if dur else 0.0
    x["num:per_ep"] = 1.0 if film.get("per_episode") else 0.0
    x["num:year"] = float(min(max((film.get("year") or 2000), 1950), 2035) - 2000)
    x["num:dur_missing"] = 0.0 if dur else 1.0

    for c in film.get("countries", []) or []:
        c = COUNTRY_ALIASES.get(norm(c), c)
        m = _match("countries", c, model)
        if m:
            x[f"p:{m}"] = 1.0
    for kind, key, prefix in (("studios", "studios", "st"), ("creators", "directors", "c"), ("univers", "univers", "u")):
        for name in film.get(key, []) or []:
            m = _match(kind, name, model)
            if m:
                x[f"{prefix}:{m}"] = 1.0

    for scene, level in (film.get("scenes") or {}).items():
        scene = "Malaise" if scene == "Malaises" else scene
        if scene not in SCENES:
            report["ignored"].append(f"scène « {scene} »")
            continue
        level = int(max(0, min(3, round(float(level)))))
        if level:
            x[f"sc:{scene}"] = 1.0
            x[f"sci:{scene}"] = float(level)

    for t in film.get("themes", []) or []:
        m = _match("themes", t, model)
        if m:
            x[f"th:{m}"] = 1.0
        else:
            report["unknown_themes"].append(t)
    return x, report


# --------------------------------------------------------------------------- prédiction
def _linear(target: dict, x: dict[str, float]) -> tuple[float, list[tuple[str, float]]]:
    w = target["weights"]
    contribs = [(k, w[k] * v) for k, v in x.items() if k in w]
    return target["intercept"] + sum(c for _, c in contribs), contribs


LABELS = {"f": "Format", "tk": "Technique", "p": "Pays", "st": "Studio", "c": "Réalisation", "u": "Univers",
          "th": "Thème", "sc": "Scène difficile", "sci": "Intensité"}


def _label(key: str) -> str:
    kind, _, name = key.partition(":")
    if kind == "num":
        return {"log_dur": "Durée", "per_ep": "Format épisode", "year": "Année de sortie", "dur_missing": "Durée inconnue"}[name]
    return f"{LABELS.get(kind, kind)} : {name}"


def margin_for(target: dict, af_raw: float) -> float:
    """Marge de prudence (en années) selon l'âge estimé : l'incertitude n'est pas la même à 5 ans qu'à 11 ans."""
    for step in target["margins"]:
        if af_raw < step["below"]:
            return step["margin"]
    return target["margins"][-1]["margin"]


def estimate(film: dict, model: dict | None = None) -> dict:
    """Estime l'âge conseillé. Renvoie un dictionnaire prêt à afficher ou à sérialiser."""
    model = model or load_model()
    x, report = featurize(film, model)
    af_raw, contribs = _linear(model["targets"]["af"], x)
    am_raw, _ = _linear(model["targets"]["am"], x)
    margin = margin_for(model["targets"]["af"], af_raw)

    age = int(min(max(round(af_raw), AGE_MIN), AGE_MAX))
    prudent = int(min(max(math.ceil(af_raw + margin), age), AGE_MAX))
    avoid = int(min(max(round(am_raw), AGE_MIN), age))

    # regrouper la présence et l'intensité d'une même scène
    merged: dict[str, float] = {}
    for k, v in contribs:
        if k.startswith("sci:"):
            k = "sc:" + k[4:]
        merged[k] = merged.get(k, 0.0) + v
    top = sorted(merged.items(), key=lambda kv: -abs(kv[1]))
    reasons = [{"facteur": _label(k), "annees": round(v, 2)} for k, v in top if abs(v) >= 0.15][:8]

    scenes = film.get("scenes") or {}
    known = sum(1 for s in SCENES if s in scenes)
    confidence = "élevée" if film.get("scenes") is not None and film.get("themes") else ("moyenne" if film.get("scenes") is not None else "faible")
    return {
        "titre": film.get("title"),
        "age_estime": age,
        "age_prudent": prudent,
        "deconseille_avant": avoid,
        "age_brut": round(af_raw, 2),
        "base": round(model["targets"]["af"]["intercept"], 2),
        "raisons": reasons,
        "confiance": confidence,
        "scenes_renseignees": known,
        "couverture_prudence": model["targets"]["af"]["coverage"],
        "marge_prudence": margin,
        "ignore": report["ignored"],
        "themes_inconnus": report["unknown_themes"],
    }


def format_report(r: dict) -> str:
    lines = [f"{r['titre'] or 'Film'}",
             f"  Âge conseillé estimé : {r['age_estime']} ans   (prudent : {r['age_prudent']} ans, "
             f"couvre {int(r['couverture_prudence'] * 100)} % des cas)",
             f"  Déconseillé aux moins de : {r['deconseille_avant']} ans",
             f"  Confiance des informations : {r['confiance']}"]
    if r["raisons"]:
        lines.append(f"  Facteurs principaux (base {r['base']} ans) :")
        for x in r["raisons"]:
            lines.append(f"    {x['annees']:+.1f} an  {x['facteur']}")
    if r["themes_inconnus"]:
        lines.append(f"  Thèmes hors vocabulaire, ignorés : {', '.join(r['themes_inconnus'])}")
    return "\n".join(lines)
