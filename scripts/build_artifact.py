#!/usr/bin/env python3
"""Construit une version « tout-en-un » de l'appli (données incluses) pour un aperçu hors ligne / Artifact.
Usage : python3 scripts/build_artifact.py sortie.html [--details]"""
import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs"
out = Path(sys.argv[1]); with_details = "--details" in sys.argv or "--light" in sys.argv; light = "--light" in sys.argv
index = json.load(open(D/"data/index.json", encoding="utf-8"))
facets = json.load(open(D/"data/facets.json", encoding="utf-8"))
meta = json.load(open(D/"data/meta.json", encoding="utf-8"))
details = {}
if with_details:
    for e in index:
        d = json.load(open(D/f"data/films/{e['id']}.json", encoding="utf-8"))
        keys = ("url","duration","scenes","conclusion") if light else ("url","duration","intro","scenes","messages","vocabulaire","conclusion","autres","acteurs","studio","createurs","themes","univers","technique","pays")
        details[e["id"]] = {k: d.get(k) for k in keys}
data = {"index": index, "facets": facets, "meta": meta, "details": details, "noImages": True}
html = open(D/"index.html", encoding="utf-8").read()
css = open(D/"app.css", encoding="utf-8").read()
js = open(D/"app.js", encoding="utf-8").read()
# fragment : on retire doctype/html/head/body, on garde title + fonts + style + corps
head_links = re.findall(r'<link rel="stylesheet" href="https://fonts[^>]*>', html)
body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
body = body.replace('<script src="app.js"></script>', "")
payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
frag = (f"<title>Quel âge pour ce film ?</title>\n" + "\n".join(head_links) + f"\n<style>\n{css}\n</style>\n" + body +
        f"\n<script>window.__DATA__={payload};</script>\n<script>\n{js}\n</script>\n")
out.write_text(frag, encoding="utf-8")
print(f"{out}: {out.stat().st_size/1e6:.1f} Mo, {len(index)} fiches, détails: {len(details)}")
