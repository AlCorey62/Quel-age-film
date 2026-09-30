"""
Lecture d'un film par Claude : à partir d'un dossier (synopsis, genres, avis de spectateurs…), relève ce que
le site filmspourenfants.net relève lui-même, c'est-à-dire les scènes difficiles, leur intensité, les thèmes,
la technique et l'univers. Le résultat alimente le modèle statistique (agemodel.py), qui donne l'âge.

Claude ne décide donc pas de l'âge : il décrit le contenu, le modèle (calibré sur 3 200 fiches) convertit
cette description en âge. Les classifications officielles ne sont jamais fournies.

Variables d'environnement : ANTHROPIC_API_KEY (ou `ant auth login`), AGE_LLM_MODEL (défaut : claude-opus-5-5).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agemodel  # noqa: E402

LLM_MODEL = os.environ.get("AGE_LLM_MODEL", "claude-opus-5-5")
MAX_THEMES = 14


class SceneFlag(BaseModel):
    type: Literal[tuple(agemodel.SCENES)]  # type: ignore[valid-type]
    level: Literal[1, 2, 3]
    evidence: str


class Extraction(BaseModel):
    technique: str
    univers: list[str]
    scenes: list[SceneFlag]
    themes: list[str]
    confidence: Literal["faible", "moyenne", "élevée"]
    missing_info: str


RUBRIC = """\
Tu aides une famille à choisir des films pour ses enfants. Tu lis le dossier d'un film et tu décris son contenu
avec la même grille que le site filmspourenfants.net. Tu ne donnes PAS d'âge : un modèle statistique s'en charge
à partir de ta description. N'utilise aucune classification officielle (CNC, PEGI, MPAA, FSK, BBFC…) : ne les
cite pas et n'en tiens pas compte.

CATÉGORIES DE SCÈNES DIFFICILES (signale-en une seulement si le dossier la justifie) :
- Malaise : situations pénibles à regarder pour un enfant : conflit dur, humiliation, angoisse, suspense
  prolongé, ambiance pesante, fin difficile à comprendre.
- Mises en danger : personnages, surtout enfants ou animaux attachants, physiquement menacés : poursuites,
  chutes, combats, accidents, menace de mort.
- Tristesse : deuil, séparation, abandon, perte d'un proche ou d'un animal, maladie d'un proche, larmes appuyées.
- Visuel effrayant : monstres, créatures, ambiance sombre, apparitions soudaines, images choquantes.
- Moquerie : moqueries, insultes, dénigrement, harcèlement, comportements irrespectueux présentés comme banals.
- Santé : maladie, handicap, hôpital, alcool, tabac, drogues, troubles alimentaires, conduites à risque.
- Banalisation de la violence : violence drôle, sans conséquence, héroïque ou gratuite ; armes ; combats.
- Maltraitance : violences ou négligence subies par un enfant, un animal ou un être vulnérable ; emprise, abus.
- Sexualité : nudité, séduction appuyée, allusions, scènes ou propos sexuels.

NIVEAUX : 1 = bref, atténué ou seulement suggéré ; 2 = présent et marqué dans l'histoire ; 3 = central,
réaliste, prolongé ou très impressionnant.

RÈGLES
- Appuie chaque signalement sur le dossier (intrigue, mots-clés, avis). Cite l'indice en quelques mots dans
  « evidence ». N'invente pas de scène : si le dossier n'en dit rien, ne signale rien.
- Ne réduis pas un niveau par indulgence et ne l'augmente pas par précaution : décris fidèlement. La prudence
  est appliquée plus loin, de façon mesurée.
- Les avis de spectateurs comptent : un parent qui écrit que son enfant a eu peur est un indice de « Visuel
  effrayant » ou de « Mises en danger ».
- « themes » : au plus %d thèmes, choisis UNIQUEMENT dans la liste fournie, recopiés à l'identique.
- « technique » et « univers » : choisis dans les listes fournies, recopiés à l'identique.
- « confidence » : « élevée » si le dossier détaille l'intrigue et contient plusieurs avis ; « moyenne » pour un
  synopsis précis sans avis ; « faible » si tu n'as qu'un résumé court ou très général.
- « missing_info » : en une phrase, ce qui te manquerait pour être plus sûr (ou « rien »).
"""


def build_system(model: dict) -> str:
    v = model["vocab"]
    return (
        RUBRIC % MAX_THEMES
        + "\nTECHNIQUES : " + " | ".join(v["techniques"])
        + "\nUNIVERS : " + " | ".join(v["univers"])
        + "\nTHÈMES : " + " | ".join(v["themes"])
    )


def render_dossier(d: dict) -> str:
    def line(label, val):
        if val in (None, "", [], 0):
            return ""
        return f"{label} : {', '.join(map(str, val)) if isinstance(val, list) else val}\n"

    out = (
        line("Titre", d.get("title")) + line("Année", d.get("year")) + line("Format", d.get("format"))
        + line("Durée (min)", d.get("duration_min")) + line("Genres", d.get("genres"))
        + line("Studios", d.get("studios")) + line("Réalisation", d.get("directors")) + line("Pays", d.get("countries"))
        + line("Mots-clés", d.get("keywords"))
    )
    if d.get("synopsis"):
        out += f"\nSYNOPSIS\n{d['synopsis']}\n"
    if d.get("reviews"):
        out += "\nAVIS DE SPECTATEURS (extraits)\n" + "\n---\n".join(r[:1200] for r in d["reviews"][:6]) + "\n"
    if d.get("notes"):
        out += f"\nNOTES FOURNIES\n{d['notes']}\n"
    return out.strip()


class RefusedError(RuntimeError):
    pass


def extract(dossier: dict, model: dict | None = None, client: anthropic.Anthropic | None = None,
            llm_model: str | None = None) -> dict:
    """Renvoie {'scenes': {catégorie: niveau}, 'themes': [...], 'univers': [...], 'technique': str, …, 'evidence': {...}}."""
    model = model or agemodel.load_model()
    client = client or anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=llm_model or LLM_MODEL,
        max_tokens=8000,
        system=build_system(model),
        messages=[{"role": "user", "content": render_dossier(dossier)}],
        output_format=Extraction,
        output_config={"effort": "medium"},
        # un refus de sécurité est rejoué côté serveur sur le modèle de repli recommandé
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RefusedError(f"Requête refusée par le modèle ({getattr(response, 'stop_details', None)})")
    parsed: Extraction | None = response.parsed_output
    if parsed is None:
        raise RuntimeError("Réponse inexploitable (stop_reason=%s)" % response.stop_reason)
    return validate(parsed, model)


def validate(parsed: Extraction, model: dict) -> dict:
    """Ne garde que des valeurs connues du modèle ; une catégorie répétée garde son niveau le plus haut."""
    lk = model["_lookup"]
    scenes: dict[str, int] = {}
    evidence: dict[str, str] = {}
    for s in parsed.scenes:
        if s.level > scenes.get(s.type, 0):
            scenes[s.type] = s.level
            evidence[s.type] = s.evidence
    themes, dropped = [], []
    for t in parsed.themes:
        known = lk["themes"].get(agemodel.norm(t))
        if known and known not in themes:
            themes.append(known)
        elif not known:
            dropped.append(t)
    univers = [lk["univers"][agemodel.norm(u)] for u in parsed.univers if agemodel.norm(u) in lk["univers"]]
    technique = lk["techniques"].get(agemodel.norm(parsed.technique))
    return {
        "scenes": scenes, "themes": themes[:MAX_THEMES], "univers": univers, "technique": technique,
        "confidence": parsed.confidence, "missing_info": parsed.missing_info,
        "evidence": evidence, "themes_hors_vocabulaire": dropped,
    }
