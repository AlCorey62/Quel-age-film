#!/usr/bin/env python3
"""
Mesure la qualité réelle de la chaîne « Claude lit le dossier → le modèle donne l'âge » sur des films que le site
a déjà analysés : on cache l'analyse du site, on ne donne à Claude que ce qui est disponible à la sortie d'un film
(synopsis, mots-clés, avis TMDB), puis on compare avec ce que le site a relevé.

    ANTHROPIC_API_KEY=… TMDB_TOKEN=… python3 scripts/evaluate_extractor.py --n 60 --min-year 2025

Utilise de préférence des films récents (--min-year) : pour un film ancien, Claude peut se souvenir de l'œuvre et
le résultat serait trop optimiste. Coût indicatif : quelques centimes par film. Le rapport donne :
  - rappel et précision de chaque catégorie de scènes difficiles,
  - l'erreur sur l'âge et la part des films où l'âge prudent est au moins égal à celui du site,
  - la marge de prudence qu'il faudrait pour couvrir 85 % des cas avec ces vraies erreurs d'extraction.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agemodel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def site_scenes(film_json: dict) -> set[str]:
    out = set()
    for it in film_json.get("scenes", []):
        t = "Malaise" if it["title"] == "Malaises" else it["title"]
        if t in agemodel.SCENES:
            out.add(t)
    return out


def compare(site: set[str], extracted: dict[str, int]) -> dict[str, tuple[int, int, int]]:
    """Par catégorie : (vrais positifs, faux positifs, oublis)."""
    got = set(extracted)
    return {s: (int(s in site and s in got), int(s not in site and s in got), int(s in site and s not in got)) for s in agemodel.SCENES}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--min-year", type=int, default=2025)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    import estimate_age
    import tmdb_source

    index = [e for e in json.load(open(ROOT / "docs" / "data" / "index.json", encoding="utf-8")) if (e["y"] or 0) >= a.min_year and e["f"] == "Long-métrage"]
    random.Random(a.seed).shuffle(index)
    model = agemodel.load_model()
    tp = {s: [0, 0, 0] for s in agemodel.SCENES}
    errs, raw_gap, done = [], [], 0
    for e in index:
        if done >= a.n:
            break
        try:
            tid = tmdb_source.search_movie(e["t"], e["y"])
            if not tid:
                continue
            dossier = tmdb_source.fetch_dossier(tid)
            if not dossier["synopsis"]:
                continue
            res = estimate_age.estimate_dossier(dossier, model)
            site = site_scenes(json.load(open(ROOT / "docs" / "data" / "films" / f"{e['id']}.json", encoding="utf-8")))
            for s, (t, f, m) in compare(site, res["extraction"]["scenes"]).items():
                tp[s][0] += t; tp[s][1] += f; tp[s][2] += m
            errs.append((res["age_brut"], res["age_prudent"], e["af"], res["confiance"]))
            done += 1
            print(f"{e['t'][:40]:40s} site {e['af']:2d} | estimé {res['age_estime']:2d} | prudent {res['age_prudent']:2d} | confiance {res['confiance']}")
        except Exception as ex:
            print(f"! {e['t']} : {ex}", file=sys.stderr)
    if not errs:
        sys.exit("Aucun film évalué.")
    print(f"\n{len(errs)} films évalués (sortis en {a.min_year} ou après)")
    print("Scènes difficiles : rappel / précision par catégorie")
    for s, (t, f, m) in tp.items():
        r = t / (t + m) if t + m else float("nan"); p = t / (t + f) if t + f else float("nan")
        print(f"  {s:30s} rappel {r*100:4.0f}%  précision {p*100:4.0f}%  (site : {t + m} films)")
    mae = sum(abs(r - s) for r, _, s, _ in errs) / len(errs)
    cov = sum(p >= s for _, p, s, _ in errs) / len(errs)
    under2 = sum(round(r) - s <= -2 for r, _, s, _ in errs) / len(errs)
    print(f"Erreur moyenne sur l'âge : {mae:.2f} an | sous-estimation de 2 ans ou plus : {under2*100:.0f}% | âge prudent ≥ âge du site : {cov*100:.0f}%")
    d = 0.0
    while sum(math.ceil(r + d) >= s for r, _, s, _ in errs) / len(errs) < 0.85 and d < 5:
        d = round(d + 0.05, 2)
    print(f"Marge unique nécessaire pour couvrir 85 % avec ces vraies erreurs : +{d} an (le modèle suppose des marges de 0,35 à 1,85 an selon l'âge)")


if __name__ == "__main__":
    main()
