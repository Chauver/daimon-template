#!/usr/bin/env python3
"""push_cap_ef.py — Crée une séance de COURSE EN EF (endurance facile) sur intervals.icu.

Conforme à doctrine/regles_seances.md :
  - TOUJOURS échauffement + retour au calme, SANS cible d'allure (pilotés à la FC).
  - Bloc principal EF piloté par la FC (bas Z2), allure LIBRE — aucune cible de vitesse.
  - Durée (pas distance) : l'allure flotte, c'est voulu.

Paramètres (variables d'environnement) :
  SEANCE_DATE  date ISO (défaut : demain)
  SEANCE_MAIN  minutes du bloc EF principal (défaut 30)
  SEANCE_WU    minutes d'échauffement (défaut 10)
  SEANCE_RAC   minutes de retour au calme (défaut 8)
  SEANCE_NOM   nom affiché (défaut « CAP EF Z2 »)

Lancer :  SEANCE_DATE=2026-07-14 SEANCE_MAIN=22 python3 push_cap_ef.py
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
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

API_KEY = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID")
if not (API_KEY and ATHLETE_ID):
    sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")

WHEN = os.getenv("SEANCE_DATE", (date.today() + timedelta(days=1)).isoformat())
MAIN = int(os.getenv("SEANCE_MAIN", "30"))
WU = int(os.getenv("SEANCE_WU", "10"))
RAC = int(os.getenv("SEANCE_RAC", "8"))
NOM = os.getenv("SEANCE_NOM", "CAP EF Z2")
# Bornes FC du bloc EF (bpm) : plancher volontairement BAS (jamais de sonnerie « FC trop basse »,
# ex. segment couru accompagné) + plafond = la vraie contrainte EF (bas Z2). Réglables.
FC_MIN = int(os.getenv("SEANCE_FC_MIN", "125"))   # D17 : cible du volume facile
FC_MAX = int(os.getenv("SEANCE_FC_MAX", "140"))   # D17 : plafond LIT = 144
FCMAX = int(os.getenv("FCMAX_MONTRE", "188"))   # FC MAX de la montre = base des % HR à l'affichage
TOTAL = WU + MAIN + RAC

# La MONTRE Garmin calcule les cibles % HR sur la FC MAX (constaté 16/07 : elle affichait 129-156 pour
# 69-83 %, soit ces % appliqués à 188). On calcule donc le % depuis bornes_bpm ÷ FC MAX → la montre
# reconvertit en bpm JUSTE. (Garmin Connect affiche, lui, un % de LTHR → un chiffre plus bas : c'est la
# MONTRE qui fait foi en course.) ⚠️ Jamais de bpm absolu (units:"bpm") : Garmin y ajoute un offset +100.
PCT_MIN = round(FC_MIN / FCMAX * 100)
PCT_MAX = round(FC_MAX / FCMAX * 100)

# workout_doc structuré : échauffement + retour au calme = AUCUNE cible (durée seule) ;
# bloc EF = cible FC en % de FC MAX (regles_seances.md §5 bis), allure LIBRE.
# SEANCE_WU_TEXT permet de personnaliser le libellé de l'échauffement (ex. segment accompagné).
WU_TEXT = os.getenv("SEANCE_WU_TEXT", f"Echauffement {WU}min - libre, aucune cible")
NOTE = os.getenv("SEANCE_NOTE", "")
WORKOUT = {"steps": [
    {"duration": WU * 60, "text": WU_TEXT},
    {"duration": MAIN * 60, "hr": {"start": PCT_MIN, "end": PCT_MAX, "units": "%hr"},
     "text": f"EF {MAIN}min - FC {FC_MIN}-{FC_MAX} bpm ({PCT_MIN}-{PCT_MAX}% FCmax), allure LIBRE"},
    {"duration": RAC * 60, "text": f"Retour au calme {RAC}min - libre"},
]}
DESCRIPTION = (f"{WU_TEXT} · EF {MAIN}' FC {FC_MIN}-{FC_MAX} bpm "
               f"({PCT_MIN}-{PCT_MAX}% FCmax, allure libre) · Retour au calme {RAC}' libre."
               + (f" {NOTE}" if NOTE else ""))

payload = {
    "category": "WORKOUT",
    "start_date_local": f"{WHEN}T00:00:00",
    "type": "Run",
    "name": f"{NOM} · {TOTAL}min",
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
    print(f"✅ '{payload['name']}' créée le {WHEN} (id {ev.get('id')}) — {WU}'+{MAIN}'+{RAC}' · "
          f"WU/RAC libres (sans cible) · EF {FC_MIN}-{FC_MAX}bpm.")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}  ({getattr(e.response,'text','')[:200]})")
