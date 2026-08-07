#!/usr/bin/env python3
"""
push_seance_run.py — Crée une séance structurée sur intervals.icu, qui se synchronise
ensuite automatiquement vers Garmin Connect (coche « Upload planned workouts » dans les
réglages de connexion Garmin d'intervals.icu).

Séance test : 6 x 1000 m au seuil (séance-repère pour le suivi d'efficience FC).

Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
"""
from __future__ import annotations
import os, sys, json
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

# Date de la séance : demain par défaut (modifiable)
WHEN = os.getenv("SEANCE_DATE", (date.today() + timedelta(days=1)).isoformat())

# Description structurée (format intervals.icu). Bloc principal en % d'allure seuil (100% = seuil).
# WU / récup / RAC = AUCUNE cible (durée nue) → pas de sonnerie « FC trop basse » (cf. regles_seances §1).
DESCRIPTION = """Warm-up 12min - libre, aucune cible
- 12m

Bloc principal - 6x1000m au seuil
- 6x
- 1000m 98-102% Pace
- 90s

Retour au calme 8min - libre
- 8m"""

payload = {
    "category": "WORKOUT",
    "start_date_local": f"{WHEN}T00:00:00",
    "type": "Run",
    "name": "Seuil · 6x1000m (repère)",
    "description": DESCRIPTION,
}

def main():
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
    r = requests.post(url, auth=("API_KEY", API_KEY), json=payload, timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    ev = r.json()
    print(f"✅ Séance créée le {WHEN} sur intervals.icu (id {ev.get('id')}).")
    print("   Elle se synchronisera vers Garmin si « Upload planned workouts » est coché.")
    print("   Si le découpage des étapes n'est pas parfait, ouvre-la dans le Workout Builder")
    print("   d'intervals.icu pour l'ajuster — on affinera la syntaxe après ce premier test.")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}  ({getattr(e.response,'text','')[:200]})")
