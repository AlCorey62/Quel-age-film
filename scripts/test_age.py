#!/usr/bin/env python3
"""
Tests de la chaîne d'estimation d'âge :  python3 scripts/test_age.py

Aucun réseau ni clé d'API n'est nécessaire : les appels à Claude et à TMDB sont simulés.
Ces tests vérifient la mécanique (requêtes, lecture des réponses, calcul, publication), pas la qualité
des réponses réelles de Claude, qui se mesure avec scripts/evaluate_extractor.py.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import anthropic
import httpx2 as httpx  # le SDK anthropic 1.x s’appuie sur httpx2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agemodel  # noqa: E402
import estimate_age  # noqa: E402
import extract_descriptors as ed  # noqa: E402
import tmdb_source  # noqa: E402
import evaluate_extractor as ev  # noqa: E402

MODEL = agemodel.load_model()

GENTLE = {"title": "Petit film doux", "format": "Long-métrage", "technique": "Dessin animé", "duration_min": 80,
          "year": 2026, "countries": ["France"], "scenes": {"Malaise": 1}, "themes": ["Amitié"]}
HARSH = dict(GENTLE, title="Film dur", technique="Prise de vue réelle",
             scenes={"Mises en danger": 3, "Visuel effrayant": 3, "Maltraitance": 2, "Banalisation de la violence": 2, "Sexualité": 1},
             themes=["Mort"])


def fake_claude(payload: dict, captured: list):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        msg = {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
               "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
               "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 50}}
        return httpx.Response(200, json=msg)
    return anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


PAYLOAD = {
    "technique": "Images de synthèse", "univers": ["Comique", "Univers inventé"],
    "scenes": [
        {"type": "Mises en danger", "level": 2, "evidence": "poursuite en voiture"},
        {"type": "Mises en danger", "level": 3, "evidence": "chute dans le vide"},
        {"type": "Visuel effrayant", "level": 1, "evidence": "créature dans la nuit"},
    ],
    "themes": ["Amitié", "Thème qui n'existe pas", "Mort"],
    "confidence": "moyenne", "missing_info": "avis de parents",
}
DOSSIER = {"title": "Film test", "year": 2026, "format": "Long-métrage", "duration_min": 95,
           "synopsis": "Un renard et un lapin fuient un méchant.", "studios": ["Pixar"], "countries": ["États-Unis"],
           "directors": ["Quelqu'un"], "genres": ["Animation"], "keywords": ["fuite"], "reviews": ["Mon fils a eu peur."]}


class ModelTests(unittest.TestCase):
    def test_monotone_with_content(self):
        a, b = agemodel.estimate(GENTLE, MODEL), agemodel.estimate(HARSH, MODEL)
        self.assertGreaterEqual(b["age_estime"], a["age_estime"] + 2)
        for r in (a, b):
            self.assertGreaterEqual(r["age_prudent"], r["age_estime"])
            self.assertGreaterEqual(r["age_estime"], r["deconseille_avant"])
            self.assertTrue(2 <= r["age_estime"] <= 18)

    def test_each_scene_adds_years(self):
        base = agemodel.estimate(dict(GENTLE, scenes={}), MODEL)["age_brut"]
        for s in agemodel.SCENES:
            with self.subTest(scene=s):
                high = agemodel.estimate(dict(GENTLE, scenes={s: 3}), MODEL)["age_brut"]
                self.assertGreater(high, base - 0.5)   # une scène ne doit pas faire baisser l'âge de façon notable

    def test_name_matching(self):
        r = agemodel.estimate(dict(GENTLE, countries=["United States of America"], studios=["Pixar Animation Studios"]), MODEL)
        labels = " ".join(x["facteur"] for x in r["raisons"])
        self.assertIsInstance(r["age_estime"], int)
        self.assertNotIn("inconnu", labels)

    def test_unknown_inputs_are_reported_not_fatal(self):
        r = agemodel.estimate(dict(GENTLE, format="Hologramme", scenes={"Bizarre": 2}, themes=["Zzz"]), MODEL)
        self.assertTrue(r["ignore"])
        self.assertEqual(r["themes_inconnus"], ["Zzz"])

    def test_served_values_match_browser_fixtures(self):
        parity = json.loads((agemodel.ROOT / "docs" / "model" / "parity.json").read_text(encoding="utf-8"))
        for p in parity:
            got = agemodel.estimate(p["film"], MODEL)
            for k, v in p["attendu"].items():
                self.assertEqual(got[k], v, p["film"]["title"])


class ExtractorTests(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        cap: list = []
        out = ed.extract(DOSSIER, MODEL, client=fake_claude(PAYLOAD, cap))
        body = cap[0]["body"]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", cap[0]["headers"]["anthropic-beta"])
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
            self.assertNotIn(forbidden, json.dumps(body))
        self.assertNotIn({"type": "disabled"}, [body.get("thinking")])
        self.assertIn("Mon fils a eu peur.", body["messages"][0]["content"])
        self.assertNotIn("CNC", body["messages"][0]["content"])
        # résultat validé : niveau le plus haut gardé, thème inconnu écarté, univers inconnu écarté
        self.assertEqual(out["scenes"], {"Mises en danger": 3, "Visuel effrayant": 1})
        self.assertEqual(out["evidence"]["Mises en danger"], "chute dans le vide")
        self.assertEqual(out["themes"], ["Amitié", "Mort"])
        self.assertEqual(out["themes_hors_vocabulaire"], ["Thème qui n'existe pas"])
        self.assertEqual(out["univers"], ["Comique"])
        self.assertEqual(out["technique"], "Images de synthèse")

    def test_refusal_is_surfaced(self):
        def handler(request):
            return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                             "content": [], "stop_reason": "refusal", "stop_sequence": None,
                                             "stop_details": {"type": "refusal", "category": "general_harms", "explanation": "x"},
                                             "usage": {"input_tokens": 1, "output_tokens": 0}})
        client = anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with self.assertRaises(ed.RefusedError):
            ed.extract(DOSSIER, MODEL, client=client)

    def test_end_to_end_estimate(self):
        r = estimate_age.estimate_dossier(DOSSIER, MODEL, client=fake_claude(PAYLOAD, []))
        self.assertEqual(r["confiance"], "moyenne")
        self.assertEqual(r["age_annonce"], r["age_estime"])
        low = dict(PAYLOAD, confidence="faible")
        r2 = estimate_age.estimate_dossier(DOSSIER, MODEL, client=fake_claude(low, []))
        self.assertEqual(r2["age_annonce"], r2["age_prudent"])     # information maigre : on annonce l'âge prudent


TMDB_MOVIE = {
    "id": 4242, "title": "Film neuf", "release_date": "2026-09-16", "runtime": 92, "overview": "Un ourson cherche sa mère.",
    "genres": [{"id": 16, "name": "Animation"}, {"id": 10751, "name": "Familial"}],
    "production_companies": [{"name": "Walt Disney Pictures"}], "production_countries": [{"name": "United States of America"}],
    "keywords": {"keywords": [{"name": "ourson"}]}, "credits": {"crew": [{"job": "Director", "name": "A. Réal"}, {"job": "Writer", "name": "B"}]},
    "reviews": {"results": [{"content": "Mes enfants de 4 ans ont pleuré."}]}, "poster_path": "/p.jpg",
}


class EvaluatorTests(unittest.TestCase):
    def test_compare_counts(self):
        r = ev.compare({"Malaise", "Tristesse"}, {"Malaise": 2, "Sexualité": 1})
        self.assertEqual(r["Malaise"], (1, 0, 0))
        self.assertEqual(r["Tristesse"], (0, 0, 1))      # oubli
        self.assertEqual(r["Sexualité"], (0, 1, 0))      # fausse alerte
        self.assertEqual(r["Santé"], (0, 0, 0))

    def test_site_scenes_reads_fiche(self):
        self.assertEqual(ev.site_scenes({"scenes": [{"title": "Malaises"}, {"title": "Inconnue"}, {"title": "Santé"}]}), {"Malaise", "Santé"})


class TmdbTests(unittest.TestCase):
    def test_dossier_mapping(self):
        d = tmdb_source.dossier_from_tmdb("movie", TMDB_MOVIE)
        self.assertEqual((d["title"], d["year"], d["format"], d["duration_min"]), ("Film neuf", 2026, "Long-métrage", 92))
        self.assertEqual(d["directors"], ["A. Réal"])
        self.assertEqual(d["keywords"], ["ourson"])
        self.assertTrue(d["poster"].endswith("/p.jpg"))
        short = tmdb_source.dossier_from_tmdb("movie", dict(TMDB_MOVIE, runtime=12))
        self.assertEqual(short["format"], "Court-métrage")
        tv = tmdb_source.dossier_from_tmdb("tv", {"id": 9, "name": "Série", "first_air_date": "2025-01-02", "episode_run_time": [11],
                                                  "overview": "x", "genres": [], "keywords": {"results": []}, "created_by": [{"name": "C"}]})
        self.assertEqual((tv["format"], tv["per_episode"], tv["duration_min"], tv["directors"]), ("Série", True, 11, ["C"]))

    def test_no_official_rating_is_read(self):
        src = Path(tmdb_source.__file__).read_text(encoding="utf-8")
        self.assertNotIn("release_dates", src)
        self.assertNotIn("certification", src.replace("classifications", ""))   # seule la mention en commentaire est tolérée


class BatchTests(unittest.TestCase):
    def test_batch_publishes_and_retires(self):
        tmp = Path(tempfile.mkdtemp())
        today = dt.date.today().isoformat()
        listing = [{"id": 1, "title": "Film neuf", "release_date": today}, {"id": 2, "title": "Déjà au catalogue", "release_date": today}]
        fetch = lambda i, kind="movie", opener=None: tmdb_source.dossier_from_tmdb(
            "movie", dict(TMDB_MOVIE, id=i, title="Film neuf" if i == 1 else "Déjà au catalogue"))
        fake_extract = lambda dossier, model=None, client=None, llm_model=None: ed.validate(ed.Extraction(**PAYLOAD), MODEL)
        est, idx = tmp / "estimates.json", tmp / "index.json"
        with mock.patch.object(estimate_age, "ESTIMATES", est), mock.patch.object(estimate_age, "INDEX", idx), \
                mock.patch.object(tmdb_source, "recent_releases", lambda *a, **k: listing), \
                mock.patch.object(tmdb_source, "fetch_dossier", fetch), \
                mock.patch.object(ed, "extract", fake_extract):
            idx.write_text(json.dumps([{"t": "Déjà au catalogue", "y": 2026}]), encoding="utf-8")
            estimate_age.run_new_releases(21, "FR", None)
            out = json.loads(est.read_text(encoding="utf-8"))
            self.assertEqual([e["t"] for e in out], ["Film neuf"])            # le film déjà au catalogue est ignoré
            e = out[0]
            self.assertEqual((e["id"], e["est"]), ("est-1", 1))
            self.assertGreaterEqual(e["af"], e["estimate"])                    # l'âge publié est l'âge prudent
            self.assertEqual(e["af"], estimate_age.agemodel.estimate(estimate_age.to_film(fetch(1), ed.validate(ed.Extraction(**PAYLOAD), MODEL)), MODEL)["age_prudent"])
            # le site publie ensuite la fiche : l'estimation disparaît au passage suivant
            idx.write_text(json.dumps([{"t": "Déjà au catalogue", "y": 2026}, {"t": "Film neuf", "y": 2026}]), encoding="utf-8")
            estimate_age.run_new_releases(21, "FR", None)
            self.assertEqual(json.loads(est.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
