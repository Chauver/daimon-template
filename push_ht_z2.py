#!/usr/bin/env python3
"""
push_ht_z2.py — Crée une séance HOME-TRAINER en Z2 (puissance) dans le calendrier intervals.icu.

Une fois la connexion Zwift ↔ intervals.icu activée (Settings → Zwift), la séance remonte
AUTOMATIQUEMENT dans Zwift (Entraînements → Custom → filtre « Intervals.icu »), et l'activité
réalisée revient dans intervals.icu — donc dans le pipeline d'analyse. Plus besoin de copier
des .zwo à la main.

Pilotage : bloc principal en PUISSANCE ABSOLUE (→ mode ERG sur le home-trainer).
Échauffement et retour au calme en rampe douce, sans cible stricte (regles_seances.md §1).

Variables d'environnement :
  SEANCE_DATE     date ISO (défaut : aujourd'hui)
  SEANCE_MAIN     minutes du bloc principal (défaut 40)
  SEANCE_WU       minutes d'échauffement (défaut 12)
  SEANCE_RAC      minutes de retour au calme (défaut 8)
  SEANCE_WATTS    puissance du bloc principal en W (défaut 160)
  SEANCE_CADENCE  cadence cible (défaut 90) — ⚠️ à 160 W, passer de 95 à 70 rpm augmente
                  le couple d'environ 35 % : c'est le couple qui charge le quadriceps,
                  pas les watts.
  SEANCE_NOM      nom affiché

Lancer :  SEANCE_DATE=2026-07-20 SEANCE_WATTS=160 python3 push_ht_z2.py
"""
from __future__ import annotations
import os, sys
from datetime import date, timedelta
from pathlib import Path

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
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
API_KEY = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID")
FTP = int(os.getenv("FTP_WATTS", "235"))

WHEN = os.getenv("SEANCE_DATE", date.today().isoformat())
MAIN = int(os.getenv("SEANCE_MAIN", "40"))
WU = int(os.getenv("SEANCE_WU", "12"))
RAC = int(os.getenv("SEANCE_RAC", "8"))
WATTS = int(os.getenv("SEANCE_WATTS", "160"))
CAD = int(os.getenv("SEANCE_CADENCE", "90"))
NOM = os.getenv("SEANCE_NOM", "HT Z2")
NOTE = os.getenv("SEANCE_NOTE", "")
TOTAL = WU + MAIN + RAC
PCT = round(100 * WATTS / FTP)

# ⚠️ TOUTE la séance en ERG (demande athlète 20/07) : échauffement et retour au calme sont
# découpés en PALIERS à puissance FIXE plutôt qu'en rampes. Une rampe (power start→end) peut
# être interprétée différemment selon le home-trainer / l'appli ; un palier à watts constants
# déclenche l'ERG sans ambiguïté. Le confort de montée est conservé via des paliers courts.
WU_STEPS = int(os.getenv("SEANCE_WU_STEPS", "4"))
RAC_STEPS = int(os.getenv("SEANCE_RAC_STEPS", "2"))
WU_START = float(os.getenv("SEANCE_WU_START", "0.62"))   # fraction de WATTS au 1er palier
RAC_END = float(os.getenv("SEANCE_RAC_END", "0.62"))     # fraction de WATTS au dernier palier


def paliers(n, w_from, w_to, total_min, label, cad=None):
    """n paliers à puissance fixe, répartis linéairement de w_from à w_to."""
    out = []
    dur = int(round(total_min * 60 / n))
    for i in range(n):
        w = int(round(w_from + (w_to - w_from) * (i / max(1, n - 1))))
        step = {"duration": dur, "power": {"value": w, "units": "w"},
                "text": f"{label} {i+1}/{n} - {w} W (ERG)"}
        if cad:
            step["cadence"] = cad
        out.append(step)
    return out


WORKOUT = {"steps":
    paliers(WU_STEPS, int(WATTS * WU_START), int(WATTS * 0.94), WU, "Echauffement", CAD)
    + [{"duration": MAIN * 60,
        "power": {"value": WATTS, "units": "w"},
        "cadence": CAD,
        "text": f"Z2 {MAIN}min - {WATTS} W ({PCT}% FTP) ERG - CADENCE {CAD}-100 rpm, couple leger"}]
    + paliers(RAC_STEPS, int(WATTS * 0.81), int(WATTS * RAC_END * 0.72), RAC, "Retour au calme")
}

DESCRIPTION = (
    f"SEANCE ENTIEREMENT EN ERG. Echauffement {WU}' en {WU_STEPS} paliers · "
    f"Z2 {MAIN}' a {WATTS} W ({PCT}% FTP) · Retour au calme {RAC}' en {RAC_STEPS} paliers. "
    f"Tous les blocs sont a puissance fixe : le home-trainer tient la puissance du debut a la fin, "
    f"tu n'as qu'a pedaler. CONSIGNE : cadence {CAD}-100 rpm, couple leger."
    + (f" {NOTE}" if NOTE else "")
)

payload = {
    "category": "WORKOUT",
    "start_date_local": f"{WHEN}T00:00:00",
    "type": "Ride",
    "name": f"{NOM} · {TOTAL}min",
    "description": DESCRIPTION,
    "workout_doc": WORKOUT,
}


def main():
    if not (API_KEY and ATHLETE_ID):
        sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
    r = requests.post(url, auth=("API_KEY", API_KEY), json=payload, timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    ev = r.json()
    print(f"✅ '{payload['name']}' créée le {WHEN} (id {ev.get('id')})")
    print(f"   {WU}' echauffement · {MAIN}' a {WATTS} W ({PCT}% FTP) ERG cadence {CAD}+ · {RAC}' RAC")
    print(f"   → visible dans Zwift (Custom / Intervals.icu) si la connexion Zwift est activee")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API : {e}  ({getattr(e.response, 'text', '')[:250]})")
