#!/usr/bin/env python3
"""
Estime l'âge conseillé d'un film, y compris juste après sa sortie.

  # 1. À la main, sans réseau : tu décris le film (voir agemodel.py pour le format)
  python3 scripts/estimate_age.py --descriptors film.json

  # 2. À partir d'un synopsis : Claude relève les scènes difficiles, le modèle donne l'âge
  python3 scripts/estimate_age.py --title "Titre" --year 2026 --runtime 95 --synopsis "…" [--notes-file avis.txt]

  # 3. À partir de TMDB (film ou série)
  python3 scripts/estimate_age.py --tmdb 123456 [--tv]

  # 4. Lot : les sorties des 21 derniers jours → docs/data/estimates.json (affiché dans l'appli)
  python3 scripts/estimate_age.py --new-releases 21

Les modes 2 à 4 demandent ANTHROPIC_API_KEY ; 3 et 4 demandent aussi TMDB_TOKEN.
Règle de prudence : si les informations sont maigres (confiance « faible »), l'âge annoncé est l'âge prudent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agemodel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ESTIMATES = ROOT / "docs" / "data" / "estimates.json"
INDEX = ROOT / "docs" / "data" / "index.json"
STALE_DAYS = 120   # une estimation plus ancienne est supprimée (la fiche du site a dû paraître entre-temps)


def to_film(dossier: dict, extraction: dict | None) -> dict:
    """Assemble les métadonnées (TMDB ou saisies) et la description du contenu (Claude) en une description de film."""
    film = {k: dossier.get(k) for k in ("title", "format", "duration_min", "per_episode", "year", "countries", "studios", "directors")}
    film["format"] = film["format"] or ("Court-métrage" if (film["duration_min"] or 90) < 40 else "Long-métrage")
    if extraction:
        film.update({"technique": extraction["technique"], "univers": extraction["univers"],
                     "scenes": extraction["scenes"], "themes": extraction["themes"]})
    return film


def headline(result: dict, confidence: str) -> int:
    """Âge annoncé : l'estimation, ou l'âge prudent quand l'information est maigre."""
    return result["age_prudent"] if confidence == "faible" else result["age_estime"]


def estimate_dossier(dossier: dict, model: dict | None = None, client=None) -> dict:
    from extract_descriptors import extract
    model = model or agemodel.load_model()
    extraction = extract(dossier, model, client)
    film = to_film(dossier, extraction)
    result = agemodel.estimate(film, model)
    result["confiance"] = extraction["confidence"]
    result["age_annonce"] = headline(result, extraction["confidence"])
    result["extraction"] = extraction
    result["film"] = film
    return result


def print_result(r: dict):
    print(agemodel.format_report(r))
    if "age_annonce" in r:
        note = " (âge prudent retenu : information maigre)" if r["age_annonce"] == r["age_prudent"] and r["age_estime"] != r["age_prudent"] else ""
        print(f"  ► Âge annoncé : {r['age_annonce']} ans{note}")
    ex = r.get("extraction")
    if ex:
        for s, lvl in sorted(ex["scenes"].items(), key=lambda kv: -kv[1]):
            print(f"    {s} ({agemodel.LEVEL_LABELS[lvl]}) — {ex['evidence'].get(s, '')}")
        if ex["missing_info"] and ex["missing_info"].lower() != "rien":
            print(f"  Informations manquantes : {ex['missing_info']}")


# --------------------------------------------------------------------------- lot de sorties récentes
def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", agemodel.norm(title)).strip("-")


def already_on_site(dossier: dict, index: list[dict]) -> bool:
    n = agemodel.norm(dossier["title"])
    return any(agemodel.norm(e["t"]) == n and abs((e["y"] or 0) - (dossier["year"] or 0)) <= 1 for e in index)


def estimate_entry(dossier: dict, result: dict) -> dict:
    """Entrée au même format que index.json, marquée « est » ; l'âge affiché dans l'appli est l'âge prudent."""
    f, ex = result["film"], result["extraction"]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "id": f"est-{dossier['tmdb_id']}", "s": slug(dossier["title"]), "t": dossier["title"],
        "af": result["age_prudent"], "am": result["deconseille_avant"], "ax": None,
        "f": f["format"], "y": f["year"], "d": f"{f['duration_min']} minutes" if f["duration_min"] else None,
        "dm": f["duration_min"], "ep": bool(f["per_episode"]), "th": ex["themes"], "u": ex["univers"],
        "tk": [ex["technique"]] if ex["technique"] else [], "p": f["countries"], "st": f["studios"], "c": f["directors"],
        "sd": [s for s, _ in sorted(ex["scenes"].items(), key=lambda kv: -kv[1])],
        "img": dossier.get("poster"), "m": now, "pub": dossier.get("release_date"),
        "est": 1, "estimate": result["age_estime"], "announced": result["age_annonce"], "conf": result["confiance"],
        "detail": {
            "url": dossier["url"], "intro": dossier["synopsis"], "duration": f"{f['duration_min']} minutes" if f["duration_min"] else None,
            "scenes": [{"title": f"{s} ({agemodel.LEVEL_LABELS[lvl]})", "text": ex["evidence"].get(s, "")}
                       for s, lvl in sorted(ex["scenes"].items(), key=lambda kv: -kv[1])],
            "reasons": result["raisons"], "missing": ex["missing_info"],
            "conclusion": f"Estimation automatique : {result['age_estime']} ans, âge prudent {result['age_prudent']} ans. "
                          f"Le site n'a pas encore analysé ce film.",
        },
    }


def run_new_releases(days: int, region: str, genres: str | None):
    import tmdb_source
    model = agemodel.load_model()
    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
    known = json.loads(ESTIMATES.read_text(encoding="utf-8")) if ESTIMATES.exists() else []
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    keep = {e["id"]: e for e in known if e["m"] >= cutoff}
    added = 0
    for m in tmdb_source.recent_releases(days, region=region, genres=genres or tmdb_source.FAMILY_GENRES):
        eid = f"est-{m['id']}"
        if eid in keep:
            continue
        try:
            dossier = tmdb_source.fetch_dossier(m["id"])
            if already_on_site(dossier, index) or not dossier["synopsis"]:
                continue
            result = estimate_dossier(dossier, model)
            keep[eid] = estimate_entry(dossier, result)
            added += 1
            print(f"+ {dossier['title']} ({dossier['year']}) → {result['age_annonce']} ans (estimé {result['age_estime']}, confiance {result['confiance']})")
        except Exception as e:  # un film en échec ne doit pas bloquer les autres
            print(f"! {m.get('title')} : {e}", file=sys.stderr)
    # retirer les estimations dont la fiche du site est parue
    for eid in [k for k, e in keep.items() if any(agemodel.norm(i["t"]) == agemodel.norm(e["t"]) and abs((i["y"] or 0) - (e["y"] or 0)) <= 1 for i in index)]:
        del keep[eid]
        print(f"- {eid} : la fiche du site est parue, estimation retirée")
    out = sorted(keep.values(), key=lambda e: e.get("pub") or "", reverse=True)
    ESTIMATES.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)} estimation(s) publiée(s), {added} nouvelle(s).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--descriptors", help="fichier JSON décrivant le film (mode manuel, sans réseau)")
    ap.add_argument("--title"); ap.add_argument("--year", type=int); ap.add_argument("--runtime", type=int)
    ap.add_argument("--synopsis"); ap.add_argument("--notes-file")
    ap.add_argument("--format", default=None, help="Long-métrage, Court-métrage, Série…")
    ap.add_argument("--tmdb", type=int); ap.add_argument("--tv", action="store_true")
    ap.add_argument("--new-releases", type=int, metavar="JOURS")
    ap.add_argument("--region", default="FR"); ap.add_argument("--genres", default=None, help="ids TMDB, « | » = OU (défaut 16|10751)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.new_releases:
        return run_new_releases(a.new_releases, a.region, a.genres)
    model = agemodel.load_model()
    if a.descriptors:
        film = json.loads(Path(a.descriptors).read_text(encoding="utf-8"))
        result = agemodel.estimate(film, model)
    else:
        if a.tmdb:
            import tmdb_source
            dossier = tmdb_source.fetch_dossier(a.tmdb, "tv" if a.tv else "movie")
        elif a.title and a.synopsis:
            notes = Path(a.notes_file).read_text(encoding="utf-8") if a.notes_file else None
            dossier = {"title": a.title, "year": a.year, "duration_min": a.runtime, "synopsis": a.synopsis,
                       "format": a.format, "notes": notes, "per_episode": a.format == "Série"}
        else:
            ap.error("donne --descriptors, --tmdb, --new-releases, ou --title et --synopsis")
        result = estimate_dossier(dossier, model)
    if a.json:
        print(json.dumps({k: v for k, v in result.items() if k != "film"}, ensure_ascii=False, indent=1))
    else:
        print_result(result)


if __name__ == "__main__":
    main()
