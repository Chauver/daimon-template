#!/usr/bin/env python3
"""
build_dashboard.py — Génère le tableau de bord du jour (index.html) à partir de tes données.

Lit data/readiness.json (produit par readiness_model.py) et tes seuils (.env), puis injecte
les vraies valeurs dans la maquette accueil_sables_2027.html → écrit index.html.
La maquette n'est jamais modifiée : on peut régénérer autant qu'on veut.

Flux quotidien type :
    python3 intervals_sync.py      # récupère les séances + forme
    python3 fitness_model.py       # CTL / ATL / TSB + vigilance course
    python3 readiness_model.py     # indice de forme + compteur blessure → readiness.json
    python3 build_dashboard.py     # → index.html (à ouvrir dans le navigateur)
"""
from __future__ import annotations
import json, os, re, sys, shutil
from pathlib import Path

DATA     = Path(os.getenv("GARMIN_DATA_DIR", "data"))
TEMPLATE = Path("accueil_sables_2027.html")
OUT      = Path("index.html")

def _load_dotenv(p=".env"):
    f = Path(p)
    if not f.exists(): return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

def _f(n, d):
    try: return float(os.getenv(n, d))
    except (TypeError, ValueError): return float(d)

def pace_fmt(minkm):
    m = int(minkm); s = round((minkm - m) * 60)
    if s == 60: m, s = m + 1, 0
    return f"{m}:{s:02d} /km"

def css_fmt(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d} /100m"

def build_readiness_js():
    path = DATA / "readiness.json"
    if not path.exists():
        return None
    r = json.loads(path.read_text())
    comps = {}
    for k in ("tsb", "hr", "subj", "sleep", "hrv"):
        c = r.get("components", {}).get(k)
        comps[k] = {"score": c["score"], "detail": c.get("detail", "")} if c else None
    obj = {
        "readiness": r.get("readiness"),
        "color": r.get("color"),
        "components": comps,
        "injury": r.get("injury", {"level": "vert", "triggers": []}),
    }
    return "const READINESS = " + json.dumps(obj, ensure_ascii=False, indent=2) + ";", r

def build_ref_js():
    ref = [
        {"v": f"{int(_f('FTP_WATTS',235))} W",              "l": "FTP vélo (horaire)", "c": "var(--bike)"},
        {"v": pace_fmt(_f('THR_PACE_RUN_MIN_KM', 4.333)),   "l": "Allure seuil course","c": "var(--run)"},
        {"v": css_fmt(_f('CSS_SEC_PER_100M', 105)) + " (prov.)", "l": "CSS natation", "c": "var(--swim)"},
        {"v": f"{int(_f('LTHR',174))} bpm",                 "l": "FC seuil (LTHR)",    "c": "var(--gold)"},
    ]
    return "const REF = " + json.dumps(ref, ensure_ascii=False) + ";"

def main():
    if not TEMPLATE.exists():
        sys.exit(f"❌ Maquette introuvable : {TEMPLATE} (place-la dans ce dossier).")
    html = TEMPLATE.read_text()

    res = build_readiness_js()
    if res is None:
        shutil.copyfile(TEMPLATE, OUT)
        print("⚠️  data/readiness.json absent — page générée avec les valeurs de démonstration.")
        print("    Lance d'abord readiness_model.py, puis régénère.")
        return
    readiness_js, raw = res

    html = re.sub(r"/\*READINESS:START\*/.*?/\*READINESS:END\*/",
                  lambda m: "/*READINESS:START*/\n" + readiness_js + "\n/*READINESS:END*/", html, flags=re.S)
    html = re.sub(r"/\*REF:START\*/.*?/\*REF:END\*/",
                  lambda m: "/*REF:START*/\n" + build_ref_js() + "\n/*REF:END*/", html, flags=re.S)

    OUT.write_text(html)
    idx, col = raw.get("readiness"), raw.get("color")
    inj = raw.get("injury", {})
    miss = raw.get("missing", [])
    print(f"✅ index.html généré — indice {idx}/100 ({col}) · blessure {inj.get('level','?')}")
    if miss:
        print(f"   composantes en attente : {', '.join(miss)}")
    print("   Ouvre index.html dans ton navigateur.")

if __name__ == "__main__":
    main()
