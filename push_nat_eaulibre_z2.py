#!/usr/bin/env python3
"""
push_nat_eaulibre_z2.py — Crée une séance de natation EAU LIBRE, Z2 souple, avec PALMES
(40 min) sur intervals.icu, qui se synchronise ensuite vers Garmin Connect puis la montre.

Séance d'aisance / technique. Pilotée au TEMPS et au RESSENTI, pas à l'allure : le CSS
n'est pas encore mesuré (cf. D5), donc aucune cible de vitesse fiable. Avec palmes on va plus
vite à effort égal — rester vraiment souple. En eau libre, Garmin ne gère pas les cibles
structurées ; la séance est donc décrite en blocs de temps, l'essentiel tient dans le nom.

Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
Date : SEANCE_DATE=YYYY-MM-DD en variable d'env (défaut : aujourd'hui).
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from datetime import date

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

WHEN = os.getenv("SEANCE_DATE", date.today().isoformat())

# Blocs de temps (40 min au total). Les % HR sont indicatifs : en eau libre la FC au poignet
# est peu fiable, la vraie cible est le RESSENTI facile (respiration nasale possible).
DESCRIPTION = """Nat eau libre - Z2 souple, PALMES - aisance & technique, pas de chrono
Mise en route ~8min (sans palmes, tres souple)
- 8m 60-72% HR
Corps ~27min PALMES - continu Z2, gainage/roulis, reperage tous les 8-10 coups
- 27m 62-75% HR
Retour au calme ~5min (souple, respiration nasale)
- 5m 60-70% HR"""

payload = {
    "category": "WORKOUT",
    "start_date_local": f"{WHEN}T00:00:00",
    "type": "OpenWaterSwim",
    "name": "Nat eau libre · Z2 palmes · 40min",
    "description": DESCRIPTION,
}

def main():
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
    r = requests.post(url, auth=("API_KEY", API_KEY), json=payload, timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    ev = r.json()
    print(f"✅ Séance nat eau libre créée pour le {WHEN} sur intervals.icu (id {ev.get('id')}).")
    print("   Sync vers Garmin si « Upload planned workouts » est coché ; ouvre Garmin Connect")
    print("   (montre à proximité) pour la faire descendre sur la montre.")
    print("   Pilotage : 40 min souple, PALMES, au ressenti facile — pas de chrono (CSS non mesuré).")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}  ({getattr(e.response,'text','')[:200]})")
