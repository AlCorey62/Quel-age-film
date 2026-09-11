#!/usr/bin/env python3
"""
Synchronise les fiches de filmspourenfants.net vers docs/data/.

Source : l'API REST WordPress publique du site (aucun scraping HTML).
  - Les fiches films sont des « pages » WordPress.
  - L'âge « à partir de » est la taxonomie gp_hubs, l'âge « déconseillé aux
    moins de » la taxonomie deconseille-aux-moins-de, et les étiquettes
    (post_tag) listent les âges auxquels le film convient.

Fonctionnement (idempotent, incrémental) :
  1. Balayage léger de toutes les pages (id + date de modification).
  2. Comparaison avec l'index local -> fiches nouvelles / modifiées / supprimées.
  3. Téléchargement complet des seules fiches à mettre à jour.
  4. Récupération des libellés de taxonomies manquants.
  5. Écriture de docs/data/films/<id>.json, index.json, terms.json, meta.json.

Usage :
  python3 scripts/sync.py            # synchro incrémentale
  python3 scripts/sync.py --full     # tout retélécharger
  python3 scripts/sync.py --limit 50 # test rapide sur 50 fiches
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.filmspourenfants.net/wp-json/wp/v2"
UA = "filmspourenfants-sync/1.0 (usage personnel; synchro quotidienne via API REST)"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
FILMS_DIR = DATA / "films"
PAUSE = 0.3  # secondes entre deux requêtes : on reste poli avec le site

# clé dans la fiche -> rest_base de la taxonomie
TAXONOMIES = {
    "age_from": "gp_hubs",
    "age_min": "deconseille-aux-moins-de",
    "age_tags": "tags",
    "format": "format",
    "annee": "annee",
    "duree": "duree",
    "studio": "studio",
    "pays": "pays",
    "createurs": "createurs",
    "univers": "univers",
    "acteurs": "acteurs",
    "technique": "technique",
    "themes": "themes",
}
# clé JSON renvoyée par l'API pour chaque taxonomie
PAGE_FIELD = {
    "age_from": "gp_hubs",
    "age_min": "deconseille-aux-moins-de",
    "age_tags": "tags",
    "format": "format",
    "annee": "annee",
    "duree": "duree",
    "studio": "studio",
    "pays": "pays",
    "createurs": "createurs",
    "univers": "univers",
    "acteurs": "acteurs",
    "technique": "technique",
    "themes": "themes",
}

PAGE_FIELDS = ",".join(
    [
        "id", "slug", "link", "date_gmt", "modified_gmt", "title", "content",
        "featured_media", "_links", "_embedded",
    ]
    + sorted(set(PAGE_FIELD.values()))
)


# --------------------------------------------------------------------------- HTTP
def get_json(path: str, params: dict | None = None, retries: int = 6):
    """GET JSON avec reprise sur erreur. Renvoie (json, headers)."""
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",:")
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                time.sleep(PAUSE)
                return json.loads(body), resp.headers
        except urllib.error.HTTPError as e:
            if e.code in (400, 404) and "page=" in url:
                # au-delà de la dernière page WordPress renvoie 400 rest_post_invalid_page_number
                return None, e.headers
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                log(f"  HTTP {e.code} sur {url} — nouvel essai dans {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if attempt < retries - 1:
                log(f"  réseau: {e} — nouvel essai dans {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_paged(path: str, params: dict) -> list:
    out = []
    page = 1
    while True:
        data, headers = get_json(path, {**params, "per_page": 100, "page": page})
        if not data:
            break
        out.extend(data)
        total_pages = int(headers.get("X-WP-TotalPages", "1") or 1)
        if page >= total_pages:
            break
        page += 1
    return out


def log(msg: str):
    print(msg, flush=True)


# --------------------------------------------------------------------------- Parsing du contenu
class ContentParser(HTMLParser):
    """
    Découpe le contenu d'une fiche en sections.
    Structure du site :  <h4>intro</h4>
                         <h2>MESSAGES</h2> <h3>Thème</h3> <p>texte</p> ...
                         <h2>SCÈNES DIFFICILES</h2> <h3>Malaise</h3> <p>…</p> ...
                         <h2>VOCABULAIRE</h2> <p>…</p>
                         <p>conclusion</p>
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[dict] = []  # {title, intro, items:[{title,text}]}
        self.intro_parts: list[str] = []
        self.cur_section: dict | None = None
        self.cur_item: dict | None = None
        self.buf: list[str] = []
        self.capture: str | None = None  # 'h2' | 'h3' | 'text'
        self.skip_depth = 0  # à l'intérieur d'un bloc légende d'image

    # -- utilitaires
    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).lstrip(" .").strip()
        self.buf = []
        kind = self.capture
        self.capture = None
        if not text:
            return
        if kind == "h2":
            self.cur_section = {"title": text, "intro": "", "items": []}
            self.cur_item = None
            self.sections.append(self.cur_section)
        elif kind == "h3":
            if self.cur_section is None:
                self.cur_section = {"title": "", "intro": "", "items": []}
                self.sections.append(self.cur_section)
            self.cur_item = {"title": text, "text": ""}
            self.cur_section["items"].append(self.cur_item)
        else:  # texte
            if self.cur_section is None:
                self.intro_parts.append(text)
            elif self.cur_item is not None:
                self.cur_item["text"] = (self.cur_item["text"] + " " + text).strip()
            else:
                self.cur_section["intro"] = (self.cur_section["intro"] + " " + text).strip()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and "wp-caption" in (a.get("class") or ""):
            self.skip_depth = 1
            return
        if self.skip_depth:
            if tag == "div":
                self.skip_depth += 1
            return
        if tag in ("h1", "h2"):
            self._flush()
            self.capture = "h2"
        elif tag == "h3":
            self._flush()
            self.capture = "h3"
        elif tag in ("p", "h4", "h5", "h6", "li"):
            self._flush()
            self.capture = "text"
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag == "div":
                self.skip_depth -= 1
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
            self._flush()

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.capture is None:
            if data.strip():
                self.capture = "text"
            else:
                return
        self.buf.append(data)

    def result(self) -> dict:
        self._flush()
        # nettoyage : titres parasites ("O", "0", vides)
        clean = []
        for s in self.sections:
            title = s["title"].strip()
            if title.upper() in ("O", "0", ""):
                # rattacher son contenu à la section précédente
                if clean:
                    clean[-1]["items"].extend(s["items"])
                    if s["intro"]:
                        clean[-1]["intro"] = (clean[-1]["intro"] + " " + s["intro"]).strip()
                continue
            s["items"] = [it for it in s["items"] if it["title"].upper() not in ("O", "0")]
            clean.append(s)

        def norm(t: str) -> str:
            return re.sub(r"[^A-Z]", "", strip_accents(t).upper())

        out = {"intro": " ".join(self.intro_parts).strip(), "messages": [], "scenes": [],
               "vocabulaire": "", "conclusion": "", "autres": []}
        for s in clean:
            key = norm(s["title"])
            if key == "MESSAGES":
                out["messages"] = s["items"]
                if s["intro"]:
                    out["messages"].insert(0, {"title": "", "text": s["intro"]})
            elif key.startswith("SCENESDIFFICILES") or key.startswith("SCENEDIFFICILE"):
                items = list(s["items"])
                # sur quelques fiches, « VOCABULAIRE » est un sous-titre (h3) de cette section : on le détache
                cut = next((k for k, it in enumerate(items) if norm(it["title"]) in ("VOCABULAIRE", "LANGAGE")), None)
                if cut is not None:
                    tail = items[cut:]
                    items = items[:cut]
                    parts = [tail[0]["text"]] + [f'{it["title"]}. {it["text"]}'.strip(". ") for it in tail[1:]]
                    parts = [q for q in parts if q]
                    if parts:
                        out["vocabulaire"] = out["vocabulaire"] or parts[0]
                        out["conclusion"] = out["conclusion"] or " ".join(parts[1:]).strip()
                out["scenes"] = [it for it in items if it["title"] or it["text"]]
                if s["intro"]:
                    out["scenes"].insert(0, {"title": "", "text": s["intro"]})
            elif key in ("VOCABULAIRE", "LANGAGE", "LANGAGES", "VOCABULAIRES"):
                # les paragraphes après le vocabulaire : le premier est le vocabulaire,
                # les suivants forment la conclusion
                parts = [s["intro"]] + [f'{it["title"]}. {it["text"]}'.strip(". ") for it in s["items"]]
                parts = [p for p in parts if p]
                if parts:
                    out["vocabulaire"] = parts[0]
                    out["conclusion"] = " ".join(parts[1:]).strip()
            else:
                out["autres"].append(s)
        return out


def strip_accents(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def parse_content(raw_html: str) -> dict:
    cleaned = re.sub(r"\[/?vc_[^\]]*\]", "", raw_html)
    # le VOCABULAIRE et la conclusion sont parfois séparés par <br/> dans un même <p>
    p = ContentParser()
    p.feed(cleaned)
    return p.result()


# --------------------------------------------------------------------------- Construction des fiches
def term_int(name: str) -> int | None:
    m = re.search(r"\d+", name or "")
    return int(m.group()) if m else None


def parse_duration(name: str) -> tuple[int | None, bool]:
    """'86 minutes' -> (86, False) ; '22 minutes par épisode' -> (22, True) ; 'de 6 à 28 minutes' -> (28, False)."""
    if not name:
        return None, False
    nums = [int(n) for n in re.findall(r"\d+", name)]
    per_ep = "épisode" in name.lower() or "episode" in name.lower()
    if not nums:
        return None, per_ep
    if re.search(r"\d+\s*[hH]", name) and len(nums) >= 2:  # '1h30'
        return nums[0] * 60 + nums[1], per_ep
    return max(nums), per_ep


def names(terms: dict, tax: str, ids: list[int]) -> list[str]:
    tbl = terms.get(tax, {})
    out = []
    for i in ids:
        n = tbl.get(str(i))
        if n:
            out.append(html.unescape(n))
    return out


def build_film(page: dict, terms: dict) -> tuple[dict, dict]:
    """Renvoie (fiche complète, entrée d'index)."""
    title = html.unescape(re.sub(r"<[^>]+>", "", page["title"]["rendered"])).strip()
    g = lambda key: names(terms, TAXONOMIES[key], page.get(PAGE_FIELD[key], []) or [])

    age_from = min((term_int(n) for n in g("age_from") if term_int(n)), default=None)
    age_min = min((term_int(n) for n in g("age_min") if term_int(n)), default=None)
    age_tags = sorted({term_int(n) for n in g("age_tags") if term_int(n)})
    age_max = max(age_tags) if age_tags else None
    if age_max == 18:  # 18 = « et au-delà » : pas de limite haute
        age_max = None

    fmt = g("format")
    years = [term_int(n) for n in g("annee") if term_int(n)]
    year = min(years) if years else None
    durees = g("duree")
    dur_min, per_ep = parse_duration(durees[0]) if durees else (None, False)

    image = image_full = None
    emb = (page.get("_embedded") or {}).get("wp:featuredmedia") or []
    if emb and isinstance(emb[0], dict) and emb[0].get("source_url"):
        media = emb[0]
        image_full = media["source_url"]
        sizes = (media.get("media_details") or {}).get("sizes") or {}
        image = (sizes.get("medium") or sizes.get("post-thumbnail") or {}).get("source_url") or image_full

    content = parse_content(page["content"]["rendered"])

    detail = {
        "id": page["id"],
        "slug": page["slug"],
        "title": title,
        "url": page["link"],
        "published": page.get("date_gmt"),
        "modified": page.get("modified_gmt"),
        "age_from": age_from,
        "age_min": age_min,
        "age_max": age_max,
        "age_tags": age_tags,
        "format": fmt[0] if fmt else None,
        "formats": fmt,
        "year": year,
        "years": g("annee"),
        "duration": durees[0] if durees else None,
        "duration_min": dur_min,
        "per_episode": per_ep,
        "studio": g("studio"),
        "pays": g("pays"),
        "createurs": g("createurs"),
        "acteurs": g("acteurs"),
        "univers": g("univers"),
        "technique": g("technique"),
        "themes": g("themes"),
        "image": image,
        "image_full": image_full,
        **content,
    }
    index_entry = {
        "id": page["id"],
        "s": page["slug"],
        "t": title,
        "af": age_from,
        "am": age_min,
        "ax": age_max,
        "f": detail["format"],
        "y": year,
        "d": detail["duration"],
        "dm": dur_min,
        "ep": per_ep,
        "th": detail["themes"],
        "u": detail["univers"],
        "tk": detail["technique"],
        "p": detail["pays"],
        "st": detail["studio"],
        "c": detail["createurs"],
        "sd": [it["title"] for it in content["scenes"] if it["title"]],
        "img": image,
        "m": page.get("modified_gmt"),
        "pub": page.get("date_gmt"),
    }
    return detail, index_entry


def is_film(page: dict) -> bool:
    """Une fiche film a une année et un âge déconseillé ; les pages éditoriales (listes de Noël…) n'ont pas d'année."""
    return bool(page.get("annee")) and bool(page.get("deconseille-aux-moins-de"))


# --------------------------------------------------------------------------- Taxonomies
def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data, compact: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def ensure_terms(terms: dict, needed: dict[str, set[int]], full: bool):
    """Complète terms[tax][id] = name pour tous les ids demandés (ou tout retélécharge si full)."""
    for tax, ids in needed.items():
        tbl = terms.setdefault(tax, {})
        if full:
            log(f"  taxonomie {tax}: téléchargement complet")
            for t in fetch_paged(tax, {"_fields": "id,name,slug,count"}):
                tbl[str(t["id"])] = t["name"]
            continue
        missing = sorted(i for i in ids if str(i) not in tbl)
        if not missing:
            continue
        log(f"  taxonomie {tax}: {len(missing)} libellé(s) manquant(s)")
        for k in range(0, len(missing), 100):
            chunk = missing[k:k + 100]
            data, _ = get_json(tax, {"include": ",".join(map(str, chunk)), "per_page": 100, "_fields": "id,name"})
            for t in data or []:
                tbl[str(t["id"])] = t["name"]


# --------------------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="tout retélécharger (fiches et taxonomies)")
    ap.add_argument("--limit", type=int, default=0, help="ne traiter que N fiches (test)")
    args = ap.parse_args()

    t0 = time.time()
    FILMS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = DATA / "index.json"
    terms_path = DATA / "terms.json"
    meta_path = DATA / "meta.json"

    old_index = {str(e["id"]): e for e in load_json(index_path, [])}
    terms = load_json(terms_path, {})

    # 1. balayage léger
    log("1/5 Balayage des pages du site…")
    sweep = fetch_paged("pages", {"_fields": "id,modified_gmt", "orderby": "id", "order": "asc"})
    remote = {str(p["id"]): p["modified_gmt"] for p in sweep}
    log(f"    {len(remote)} pages côté site, {len(old_index)} fiches en local")

    # 2. diff
    if args.full:
        to_fetch = sorted(remote, key=int)
    else:
        to_fetch = sorted(
            (i for i, m in remote.items() if i not in old_index or old_index[i].get("m") != m), key=int
        )
    # les pages non-film connues (listes, pages fixes) sont mémorisées pour ne pas les retélécharger chaque jour
    meta = load_json(meta_path, {})
    non_films = set(meta.get("non_films", [])) if not args.full else set()
    to_fetch = [i for i in to_fetch if not (i in non_films and old_index.get(i) is None and not args.full)]
    # pages supprimées côté site
    deleted = [i for i in old_index if i not in remote]
    if args.limit:
        to_fetch = to_fetch[: args.limit]
    log(f"2/5 À télécharger : {len(to_fetch)} fiche(s) ; supprimées : {len(deleted)}")

    # 3. téléchargement des fiches
    pages: list[dict] = []
    for k in range(0, len(to_fetch), 50):
        chunk = to_fetch[k:k + 50]
        data, _ = get_json(
            "pages",
            {"include": ",".join(chunk), "per_page": 100, "_embed": "wp:featuredmedia", "_fields": PAGE_FIELDS},
        )
        pages.extend(data or [])
        log(f"    {min(k + 50, len(to_fetch))}/{len(to_fetch)}")

    films_pages = [p for p in pages if is_film(p)]
    new_non_films = {str(p["id"]) for p in pages if not is_film(p)}
    if new_non_films:
        log(f"    {len(new_non_films)} page(s) ignorée(s) (pas des fiches film)")

    # 4. taxonomies
    log("4/5 Libellés de taxonomies…")
    needed: dict[str, set[int]] = {tax: set() for tax in TAXONOMIES.values()}
    for p in films_pages:
        for key, field in PAGE_FIELD.items():
            needed[TAXONOMIES[key]].update(p.get(field) or [])
    ensure_terms(terms, needed, full=args.full and not args.limit)
    save_json(terms_path, terms, compact=True)

    # 5. écriture
    log("5/5 Écriture des fiches…")
    new_index = dict(old_index)
    for i in deleted:
        new_index.pop(i, None)
        f = FILMS_DIR / f"{i}.json"
        if f.exists():
            f.unlink()
    for i in new_non_films:
        new_index.pop(i, None)
    for p in films_pages:
        detail, entry = build_film(p, terms)
        save_json(FILMS_DIR / f"{p['id']}.json", detail, compact=True)
        new_index[str(p["id"])] = entry

    index_list = sorted(new_index.values(), key=lambda e: strip_accents(e["t"]).lower())
    save_json(index_path, index_list, compact=True)

    # facettes (pour l'interface : listes triées avec compteurs)
    facets = {}
    for key in ("f", "u", "tk", "p", "th"):
        cnt: dict[str, int] = {}
        for e in index_list:
            vals = e[key] if isinstance(e[key], list) else ([e[key]] if e[key] else [])
            for v in vals:
                cnt[v] = cnt.get(v, 0) + 1
        facets[key] = sorted(cnt.items(), key=lambda kv: (-kv[1], strip_accents(kv[0]).lower()))
    save_json(DATA / "facets.json", facets, compact=True)

    meta_out = {
        "source": "https://www.filmspourenfants.net",
        "last_sync": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(index_list),
        "updated": len(films_pages),
        "deleted": len(deleted),
        "non_films": sorted((non_films | new_non_films) - set(new_index), key=int),
    }
    save_json(meta_path, meta_out)
    log(
        f"Terminé en {time.time() - t0:.0f}s : {len(index_list)} fiches, "
        f"{len(films_pages)} mise(s) à jour, {len(deleted)} suppression(s)."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
