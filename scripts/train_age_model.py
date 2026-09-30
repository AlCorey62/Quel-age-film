#!/usr/bin/env python3
"""
Entraîne le modèle d'estimation d'âge sur les fiches de docs/data/ et l'exporte en JSON.

    pip install numpy scikit-learn
    python3 scripts/train_age_model.py

Sorties :
  docs/model/age_model.json   modèle (poids, vocabulaire, marge de prudence) lu par agemodel.py et par le navigateur
  docs/model/report.json      mesures de précision (validation croisée, test sur les films récents…)
  docs/model/parity.json      42 films de contrôle pour vérifier que le navigateur calcule comme Python

Ce qui est appris : à partir de ce que le site relève (scènes difficiles, thèmes, format, durée, studio…),
prédire l'âge « à partir de » et l'âge « déconseillé aux moins de » qu'il attribue. Aucune classification
officielle n'est utilisée.
"""
from __future__ import annotations

import collections
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agemodel as am_lib  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
OUT = ROOT / "docs" / "model"
SCENES = am_lib.SCENES
SEED = 7
COVERAGE = 0.85          # part des films pour lesquels l'« âge prudent » est au moins égal à l'âge du site
NOISE = dict(recall=0.85, fp=0.04, level_sd=0.6)   # défauts d'extraction automatique simulés

MIN_COUNT = {"studios": 8, "creators": 4, "countries": 15, "univers": 8, "techniques": 5, "themes": 15}


def canon(s: str) -> str:
    return "Malaise" if s == "Malaises" else s


def load_films() -> list[dict]:
    index = json.load(open(DATA / "index.json", encoding="utf-8"))
    films = []
    for e in index:
        d = json.load(open(DATA / "films" / f"{e['id']}.json", encoding="utf-8"))
        films.append({"idx": e, "d": d})
    return films


def level_cuts(films) -> dict[str, list[float]]:
    """Le site n'exprime pas d'intensité : on la déduit de la longueur du texte de chaque scène (terciles par catégorie)."""
    lens = {s: [] for s in SCENES}
    for f in films:
        for it in f["d"]["scenes"]:
            t = canon(it["title"])
            if t in lens:
                lens[t].append(len(it["text"]))
    return {s: list(np.quantile(v, [1 / 3, 2 / 3])) for s, v in lens.items()}


def film_description(f: dict, cuts) -> dict:
    """Transforme une fiche du site en description au format de agemodel (niveaux de scènes inclus)."""
    e, d = f["idx"], f["d"]
    scenes = {}
    for it in d["scenes"]:
        t = canon(it["title"])
        if t in cuts:
            n = len(it["text"])
            scenes[t] = 1 + int(n > cuts[t][0]) + int(n > cuts[t][1])
    return {
        "title": e["t"], "format": e["f"], "technique": (e["tk"] or [None])[0],
        "duration_min": e["dm"], "per_episode": e["ep"], "year": e["y"],
        "countries": e["p"], "studios": e["st"], "directors": e["c"], "univers": e["u"],
        "scenes": scenes, "themes": e["th"],
    }


def build_vocab(films) -> dict[str, list[str]]:
    def counts(getter):
        c = collections.Counter()
        for f in films:
            c.update(set(getter(f["idx"])))
        return c
    src = {
        "formats": lambda e: [e["f"]] if e["f"] else [],
        "techniques": lambda e: (e["tk"] or [])[:1],
        "countries": lambda e: e["p"], "studios": lambda e: e["st"], "creators": lambda e: e["c"],
        "univers": lambda e: e["u"], "themes": lambda e: e["th"],
    }
    vocab = {}
    for kind, getter in src.items():
        c = counts(getter)
        vocab[kind] = sorted(k for k, n in c.items() if n >= MIN_COUNT.get(kind, 1))
    return vocab


def feature_names(vocab) -> list[str]:
    names = [f"f:{v}" for v in vocab["formats"]] + [f"tk:{v}" for v in vocab["techniques"]]
    names += [f"p:{v}" for v in vocab["countries"]] + [f"st:{v}" for v in vocab["studios"]]
    names += [f"c:{v}" for v in vocab["creators"]] + [f"u:{v}" for v in vocab["univers"]]
    names += ["num:log_dur", "num:per_ep", "num:year", "num:dur_missing"]
    names += [f"sc:{s}" for s in SCENES] + [f"sci:{s}" for s in SCENES]
    names += [f"th:{v}" for v in vocab["themes"]]
    return names


def matrix(descs, model_like) -> np.ndarray:
    names = model_like["features"]
    pos = {n: i for i, n in enumerate(names)}
    X = np.zeros((len(descs), len(names)), np.float32)
    for r, desc in enumerate(descs):
        x, _ = am_lib.featurize(desc, model_like)
        for k, v in x.items():
            if k in pos:
                X[r, pos[k]] = v
    return X


def add_noise(X, names, rng):
    """Simule une extraction automatique imparfaite des scènes difficiles (oublis, fausses alertes, niveaux flous)."""
    Xn = X.copy()
    pos = {n: i for i, n in enumerate(names)}
    for s in SCENES:
        p, l = pos[f"sc:{s}"], pos[f"sci:{s}"]
        lvl = X[:, l]
        present = lvl > 0
        drop = rng.random(len(X)) > NOISE["recall"]
        add = (~present) & (rng.random(len(X)) < NOISE["fp"])
        new = np.where(present & ~drop, np.clip(np.round(lvl + rng.normal(0, NOISE["level_sd"], len(X))), 1, 3), np.where(add, 2, 0))
        Xn[:, p] = new > 0
        Xn[:, l] = new
    return Xn


def stats(pred, y):
    r = np.round(pred)
    e = pred - y
    return {
        "n": int(len(y)), "mae": round(float(np.mean(np.abs(e))), 3), "rmse": round(float(np.sqrt(np.mean(e ** 2))), 3),
        "within1": round(float(np.mean(np.abs(r - y) <= 1)), 3), "within2": round(float(np.mean(np.abs(r - y) <= 2)), 3),
        "under2": round(float(np.mean(r - y <= -2)), 3), "bias": round(float(np.mean(e)), 3),
    }


def cross_val(X, y, alpha, seed, transform=None, folds=5):
    kf = KFold(folds, shuffle=True, random_state=seed)
    oof = np.zeros(len(y))
    for tr, te in kf.split(X):
        m = Ridge(alpha=alpha).fit(X[tr], y[tr])
        oof[te] = m.predict(transform(X[te]) if transform else X[te])
    return oof


def sparse_target(ridge: Ridge, names, margin, coverage) -> dict:
    w = {n: round(float(c), 5) for n, c in zip(names, ridge.coef_) if abs(c) > 1e-4}
    return {"intercept": round(float(ridge.intercept_), 5), "weights": w, "margin": margin, "coverage": coverage}


def main():
    t0 = time.time()
    films = load_films()
    print(f"{len(films)} fiches chargées")
    cuts = level_cuts(films)
    descs = [film_description(f, cuts) for f in films]
    y_af = np.array([f["idx"]["af"] for f in films], float)
    y_am = np.array([f["idx"]["am"] for f in films], float)
    years = np.array([f["idx"]["y"] or 2000 for f in films])

    vocab = build_vocab(films)
    names = feature_names(vocab)
    skeleton = {"features": names, "vocab": vocab}
    skeleton["_lookup"] = {k: {am_lib.norm(v): v for v in vs} for k, vs in vocab.items()}
    X = matrix(descs, skeleton)
    print(f"{X.shape[1]} variables ({', '.join(f'{k}: {len(v)}' for k, v in vocab.items())})")

    # --- réglage de la régularisation
    best = None
    for alpha in (10, 20, 30, 50, 80):
        mae = np.mean([np.mean(np.abs(cross_val(X, y_af, alpha, s) - y_af)) for s in (1, 2, 3)])
        print(f"  alpha={alpha:3d}  MAE (3 répétitions) = {mae:.3f}")
        if best is None or mae < best[0]:
            best = (mae, alpha)
    alpha = best[1]

    rng = np.random.default_rng(SEED)
    report = {"trained_at": time.strftime("%Y-%m-%d"), "n_films": len(films), "alpha": alpha, "noise_assumption": NOISE,
              "baseline_mae": round(float(np.mean(np.abs(y_af - np.median(y_af)))), 3)}

    # --- précision : descripteurs exacts, puis bruités (cas d'une extraction automatique)
    oof_clean = cross_val(X, y_af, alpha, SEED)
    oof_noisy = cross_val(X, y_af, alpha, SEED, transform=lambda Z: add_noise(Z, names, rng))
    report["cv_clean"] = stats(oof_clean, y_af)
    report["cv_noisy"] = stats(oof_noisy, y_af)
    # métadonnées seules : ce qu'on sait à la sortie d'un film sans rien avoir analysé de son contenu
    meta_cols = [i for i, n in enumerate(names) if not n.startswith(("sc:", "sci:", "th:"))]
    oof_meta = cross_val(X[:, meta_cols], y_af, alpha, SEED)
    report["cv_metadata_only"] = stats(oof_meta, y_af)

    # --- film tout juste sorti : entraînement sur ≤2023, test sur ≥2024
    tr, te = np.where(years <= 2023)[0], np.where(years >= 2024)[0]
    m = Ridge(alpha=alpha).fit(X[tr], y_af[tr])
    report["temporal_2024_plus"] = stats(m.predict(add_noise(X[te], names, rng)), y_af[te])
    report["temporal_2024_plus"]["bias_note"] = "biais négatif = sous-estimation des films récents"

    # --- par format
    fmts = np.array([f["idx"]["f"] for f in films])
    report["by_format"] = {k: stats(oof_noisy[fmts == k], y_af[fmts == k]) for k in sorted(set(fmts))}

    # --- marge de prudence, par tranche d'âge estimé : plus petit décalage δ tel que ceil(âge_brut + δ) ≥ âge du site
    #     dans COVERAGE des cas de la tranche (l'incertitude croît avec l'âge : une marge unique couvre mal 10-12 ans)
    EDGES = [6, 8, 10, 12, 14]

    def fit_margin(p, yy):
        d = 0.0
        while np.mean(np.ceil(p + d) >= yy) < COVERAGE and d < 5:
            d = round(d + 0.05, 2)
        return d
    bounds = [(-99, EDGES[0])] + [(EDGES[i], EDGES[i + 1]) for i in range(len(EDGES) - 1)] + [(EDGES[-1], 99)]
    margins = [{"below": hi if hi < 99 else 99, "margin": fit_margin(oof_noisy[(oof_noisy >= lo) & (oof_noisy < hi)], y_af[(oof_noisy >= lo) & (oof_noisy < hi)])}
               for lo, hi in bounds]
    def margin_of(v):
        return next(m["margin"] for m in margins if v < m["below"])
    up = np.ceil(oof_noisy + np.array([margin_of(v) for v in oof_noisy]))
    margin = fit_margin(oof_noisy, y_af)       # marge unique équivalente, pour information
    sites_old = y_af >= 13
    report["prudence"] = {
        "target_coverage": COVERAGE, "margins": margins, "coverage": round(float(np.mean(up >= y_af)), 3),
        "coverage_by_estimated_age": {f"{lo if lo > -99 else 2}-{hi if hi < 99 else 18}": round(float(np.mean(up[(oof_noisy >= lo) & (oof_noisy < hi)] >= y_af[(oof_noisy >= lo) & (oof_noisy < hi)])), 3) for lo, hi in bounds},
        "coverage_when_site_age_ge13": round(float(np.mean(up[sites_old] >= y_af[sites_old])), 3),
        "coverage_when_site_age_le12": round(float(np.mean(up[~sites_old] >= y_af[~sites_old])), 3),
        "estimate_when_site_age_ge13_mean": round(float(np.mean(oof_noisy[sites_old])), 2),
        "overestimate_mean": round(float(np.mean(up - y_af)), 2),
        "overestimate_ge2": round(float(np.mean(up - y_af >= 2)), 3),
        "single_margin_equivalent": margin,
    }

    # --- « déconseillé aux moins de »
    oof_am = cross_val(X, y_am, alpha, SEED, transform=lambda Z: add_noise(Z, names, rng))
    report["cv_noisy_deconseille"] = stats(oof_am, y_am)

    # --- ajustement final sur tous les films
    r_af = Ridge(alpha=alpha).fit(X, y_af)
    r_am = Ridge(alpha=alpha).fit(X, y_am)
    model = {
        "version": 1,
        "trained_at": report["trained_at"], "n_films": len(films),
        "note": "Modèle linéaire entraîné sur les fiches filmspourenfants.net. Aucune classification officielle n'est utilisée.",
        "features": names, "vocab": vocab,
        "targets": {"af": {**sparse_target(r_af, names, margin, COVERAGE), "margins": margins},
                    "am": {**sparse_target(r_am, names, 0.0, COVERAGE), "margins": [{"below": 99, "margin": 0.0}]}},
        "metrics": {k: report[k] for k in ("cv_clean", "cv_noisy", "cv_metadata_only", "temporal_2024_plus", "prudence")},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(model, open(OUT / "age_model.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # --- contrôle de cohérence : le calcul exporté (agemodel.py) doit redonner exactement celui de scikit-learn
    served = am_lib.load_model(OUT / "age_model.json")
    sample = np.random.default_rng(3).choice(len(films), 300, replace=False)
    worst = 0.0
    for i in sample:
        est = am_lib.estimate(descs[i], served)
        worst = max(worst, abs(est["age_brut"] - float(r_af.predict(X[i:i + 1])[0])))
    report["parity_max_abs_diff_years"] = round(worst, 4)
    print(f"cohérence exporté/scikit-learn sur 300 films : écart max = {worst:.4f} an")
    assert worst < 0.02, "le modèle exporté ne reproduit pas l'entraînement"

    # --- films de contrôle pour le navigateur (description → résultat attendu)
    parity = []
    for i in np.random.default_rng(11).choice(len(films), 42, replace=False):
        est = am_lib.estimate(descs[i], served)
        parity.append({"film": descs[i], "attendu": {k: est[k] for k in ("age_estime", "age_prudent", "deconseille_avant", "age_brut")}})
    json.dump(parity, open(OUT / "parity.json", "w", encoding="utf-8"), ensure_ascii=False)

    # --- exemples lisibles : prédiction hors échantillon (validation croisée) face à l'âge du site
    order = np.random.default_rng(5).choice(len(films), 14, replace=False)
    report["examples"] = [{"titre": films[i]["idx"]["t"], "annee": films[i]["idx"]["y"], "site": int(y_af[i]),
                           "estime": int(round(oof_noisy[i])), "prudent": int(math.ceil(oof_noisy[i] + margin))} for i in order]

    # --- pistes les plus fortes (poids du modèle)
    coef = sorted(((n, float(c)) for n, c in zip(names, r_af.coef_) if n.startswith(("sc:", "sci:", "th:", "tk:", "f:"))), key=lambda t: -abs(t[1]))
    report["top_weights"] = [{"variable": n, "annees": round(c, 2)} for n, c in coef[:20]]
    json.dump(report, open(OUT / "report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def line(k, label):
        s = report[k]
        print(f"  {label:38s} MAE={s['mae']:.2f} an  ±1 an={s['within1']*100:.0f}%  ±2 ans={s['within2']*100:.0f}%  sous-estime≥2 ans={s['under2']*100:.0f}%")
    print("\nPrécision (validation croisée 5 blocs, films jamais vus) :")
    print(f"  {'réponse fixe (médiane)':38s} MAE={report['baseline_mae']:.2f} an")
    line("cv_metadata_only", "métadonnées seules")
    line("cv_clean", "descripteurs exacts")
    line("cv_noisy", "descripteurs extraits avec erreurs")
    line("temporal_2024_plus", "films 2024+ (entraînés ≤ 2023)")
    p = report["prudence"]
    print(f"\nÂge prudent (marge par tranche, arrondi au-dessus) : ≥ âge du site dans {p['coverage']*100:.0f}% des cas ; surestimation moyenne {p['overestimate_mean']:+.1f} an")
    print("  couverture par âge estimé :", ", ".join(f"{k} ans : {v*100:.0f}%" for k, v in p["coverage_by_estimated_age"].items()))
    print(f"  films que le site juge ≤ 12 ans : couverts à {p['coverage_when_site_age_le12']*100:.0f}% ; ≥ 13 ans : couverts à seulement {p['coverage_when_site_age_ge13']*100:.0f}% (estimation moyenne {p['estimate_when_site_age_ge13_mean']} ans)")
    print(f"Terminé en {time.time() - t0:.0f} s — {OUT}")


if __name__ == "__main__":
    main()
