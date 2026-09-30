"""
Source des sorties récentes : The Movie Database (TMDB), API gratuite (clé personnelle sur themoviedb.org).

Variables d'environnement : TMDB_TOKEN (jeton « API Read Access Token », recommandé) ou TMDB_API_KEY.

Ce module ne sert qu'à récupérer titre, synopsis, durée, studios, réalisation, mots-clés et avis de spectateurs.
TMDB publie aussi des classifications par pays : elles ne sont volontairement jamais lues.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
UA = "filmspourenfants-age-estimator/1.0"
FAMILY_GENRES = "16|10751"   # Animation ou Famille (le « | » signifie OU)


def _get(path: str, params: dict | None = None, opener=urllib.request.urlopen) -> dict:
    params = dict(params or {})
    headers = {"User-Agent": UA, "Accept": "application/json"}
    token, key = os.environ.get("TMDB_TOKEN"), os.environ.get("TMDB_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        params["api_key"] = key
    else:
        raise RuntimeError("Définis TMDB_TOKEN ou TMDB_API_KEY (compte gratuit sur themoviedb.org).")
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(4):
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=30) as r:
                body = json.loads(r.read())
            time.sleep(0.15)
            return body
        except Exception as e:  # réseau, 429, 5xx
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TMDB injoignable : {last}")


def dossier_from_tmdb(kind: str, data: dict) -> dict:
    """Convertit une réponse TMDB (film ou série, avec credits/keywords/reviews) en dossier pour Claude et pour le modèle."""
    is_tv = kind == "tv"
    date = data.get("first_air_date") if is_tv else data.get("release_date")
    runtime = (data.get("episode_run_time") or [None])[0] if is_tv else data.get("runtime")
    kw = data.get("keywords", {})
    kw = kw.get("results") if is_tv else kw.get("keywords")
    crew = data.get("credits", {}).get("crew", [])
    directors = [c["name"] for c in crew if c.get("job") == "Director"][:3]
    if is_tv:
        directors = directors or [c["name"] for c in data.get("created_by", [])][:3]
    if is_tv:
        fmt = "Série"
    else:
        fmt = "Court-métrage" if runtime and runtime < 40 else "Long-métrage"
    year = int(date[:4]) if date else None
    return {
        "source": "tmdb", "tmdb_id": data["id"], "kind": kind,
        "title": data.get("name") if is_tv else data.get("title"),
        "year": year, "release_date": date, "format": fmt, "per_episode": is_tv,
        "duration_min": runtime,
        "genres": [g["name"] for g in data.get("genres", [])],
        "studios": [c["name"] for c in data.get("production_companies", [])][:4],
        "countries": [c["name"] for c in data.get("production_countries", [])][:3],
        "directors": directors,
        "keywords": [k["name"] for k in (kw or [])][:25],
        "synopsis": data.get("overview") or "",
        "reviews": [r["content"] for r in data.get("reviews", {}).get("results", []) if r.get("content")][:6],
        "poster": IMG + data["poster_path"] if data.get("poster_path") else None,
        "url": f"https://www.themoviedb.org/{kind}/{data['id']}",
    }


def fetch_dossier(tmdb_id: int, kind: str = "movie", opener=urllib.request.urlopen) -> dict:
    # on essaie d'abord en français, puis on complète le synopsis manquant en anglais
    data = _get(f"/{kind}/{tmdb_id}", {"language": "fr-FR", "append_to_response": "credits,keywords,reviews"}, opener)
    if not data.get("overview"):
        en = _get(f"/{kind}/{tmdb_id}", {"language": "en-US"}, opener)
        data["overview"] = en.get("overview", "")
    return dossier_from_tmdb(kind, data)


def recent_releases(days: int = 21, language: str = "fr-FR", region: str = "FR", pages: int = 3,
                    genres: str = FAMILY_GENRES, opener=urllib.request.urlopen) -> list[dict]:
    """Films sortis ces derniers jours (Animation ou Famille). Renvoie des résumés {id, title, release_date}."""
    today = dt.date.today()
    out, seen = [], set()
    for page in range(1, pages + 1):
        res = _get("/discover/movie", {
            "language": language, "region": region, "with_release_type": "2|3|4|5|6", "page": page,
            "with_genres": genres, "sort_by": "primary_release_date.desc",
            "primary_release_date.gte": (today - dt.timedelta(days=days)).isoformat(),
            "primary_release_date.lte": today.isoformat(),
        }, opener)
        for m in res.get("results", []):
            if m["id"] not in seen:
                seen.add(m["id"])
                out.append({"id": m["id"], "title": m.get("title"), "release_date": m.get("release_date")})
        if page >= res.get("total_pages", 1):
            break
    return out


def search_movie(title: str, year: int | None = None, opener=urllib.request.urlopen) -> int | None:
    """Identifiant TMDB du film qui correspond le mieux à un titre (et une année), ou None."""
    params = {"query": title, "language": "fr-FR"}
    if year:
        params["year"] = year
    res = _get("/search/movie", params, opener).get("results", [])
    return res[0]["id"] if res else None
