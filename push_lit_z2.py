#!/usr/bin/env python3
"""
push_lit_z2.py — Crée la séance "LIT Z2" (endurance fondamentale, 50 min) pour DEMAIN
sur intervals.icu, qui se synchronise vers Garmin puis la montre.

Séance-repère "base facile" pour le suivi d'efficience FC.
Objectif d'allure : 5:15 à 5:45 /km.

Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
Lancer :   python3 push_lit_z2.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from datetime import date, timedelta

try:
    import requests
except ImportError:
    sys.exit("❌ pip install requests")

def _load_dotenv(p=".env"):
    f = Path(p)
    if not f.exists(): return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

API_KEY    = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID")
if not (API_KEY and ATHLETE_ID):
    sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")

WHEN = os.getenv("SEANCE_DATE", (date.today() + timedelta(days=1)).isoformat())
MAIN = int(os.getenv("SEANCE_MAIN", "35"))
WU = int(os.getenv("SEANCE_WU", "10"))
RAC = int(os.getenv("SEANCE_RAC", "5"))
FC_MIN = int(os.getenv("SEANCE_FC_MIN", "120"))
FC_MAX = int(os.getenv("SEANCE_FC_MAX", "145"))
FCMAX = int(os.getenv("FCMAX_MONTRE", "188"))   # FC MAX de la montre = base des % HR à l'affichage
TOTAL = WU + MAIN + RAC

# La MONTRE calcule les % HR sur la FC MAX → on convertit bornes_bpm ÷ FC MAX pour un affichage juste.
# ⚠️ Jamais de bpm absolu (units:"bpm") : Garmin y ajoute un offset FIT +100 (115 → « 216 »).
# Cf. doctrine/regles_seances §2 & §5.
PCT_MIN = round(FC_MIN / FCMAX * 100)
PCT_MAX = round(FC_MAX / FCMAX * 100)
WORKOUT = {"steps": [
    {"duration": WU * 60, "text": f"Echauffement {WU}min - libre, aucune cible"},
    {"duration": MAIN * 60, "hr": {"start": PCT_MIN, "end": PCT_MAX, "units": "%hr"},
     "text": f"LIT Z2 {MAIN}min - FC {FC_MIN}-{FC_MAX} bpm ({PCT_MIN}-{PCT_MAX}% LTHR), allure LIBRE"},
    {"duration": RAC * 60, "text": f"Retour au calme {RAC}min - libre"},
]}
DESCRIPTION = (f"Echauffement {WU}' libre · LIT Z2 {MAIN}' FC {FC_MIN}-{FC_MAX} bpm "
               f"({PCT_MIN}-{PCT_MAX}% LTHR, allure libre) · Retour au calme {RAC}' libre.")

payload = {
    "category": "WORKOUT",
    "start_date_local": f"{WHEN}T00:00:00",
    "type": "Run",
    "name": f"LIT Z2 · {TOTAL}min",
    "description": DESCRIPTION,
    "workout_doc": WORKOUT,
}

def main():
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
    r = requests.post(url, auth=("API_KEY", API_KEY), json=payload, timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    ev = r.json()
    print(f"✅ Séance '{payload['name']}' créée pour le {WHEN} (id {ev.get('id')}).")
    print("   Ouvre l'appli Garmin Connect (montre à proximité) pour la faire descendre sur la montre.")
    print(f"   {WU}' échauffement libre · {MAIN}' LIT Z2 FC {FC_MIN}-{FC_MAX} bpm (allure libre) · {RAC}' retour au calme.")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}  ({getattr(e.response,'text','')[:200]})")
