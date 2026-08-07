#!/usr/bin/env python3
"""
move_event.py — Déplace une séance planifiée du calendrier intervals.icu à une autre date.

POURQUOI (incident 23/07/2026, cf. regles_seances.md §7.1) : Zwift ne propose que le workout
planifié DU JOUR via la connexion intervals.icu ↔ Zwift. Si une séance glisse (prévue mercredi,
faite jeudi), l'événement resté à l'ancienne date devient invisible dans Zwift — et l'athlète
doit recréer la séance à la main. Déplacer l'événement à la date réelle règle le problème.

Usage :
    python3 move_event.py "ref + varie" 2026-07-23        # séances dont le nom contient "ref + varie" → 23/07
    python3 move_event.py --id 124434578 2026-07-23       # par identifiant exact
    python3 move_event.py "HT" 2026-07-23 --from 2026-07-22   # chercher à une date précise (défaut : ±7 jours)

Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
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
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

API_KEY    = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID")
if not (API_KEY and ATHLETE_ID):
    sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")
BASE = "https://intervals.icu/api/v1"
AUTH = ("API_KEY", API_KEY)


def move_by_id(eid: str, new_date: str):
    r = requests.put(f"{BASE}/athlete/{ATHLETE_ID}/events/{eid}", auth=AUTH,
                     json={"start_date_local": f"{new_date}T00:00:00"}, timeout=40)
    r.raise_for_status()
    ev = r.json()
    print(f"✅ '{ev.get('name')}' (id {eid}) déplacée au {new_date}")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        sys.exit(__doc__)

    # --id <id> <date>
    if args[0] == "--id":
        if len(args) < 3:
            sys.exit("Usage : move_event.py --id <id> <YYYY-MM-DD>")
        return move_by_id(args[1], args[2])

    # <motif> <date> [--from <date>]
    if len(args) < 2:
        sys.exit(__doc__)
    motif, new_date = args[0].lower(), args[1]
    if "--from" in args:
        d = args[args.index("--from") + 1]
        oldest = newest = d
    else:
        today = date.today()
        oldest = (today - timedelta(days=7)).isoformat()
        newest = (today + timedelta(days=7)).isoformat()

    r = requests.get(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH,
                     params={"oldest": oldest, "newest": newest}, timeout=40)
    r.raise_for_status()
    hits = [e for e in r.json()
            if e.get("category") == "WORKOUT" and motif in (e.get("name") or "").lower()]
    if not hits:
        sys.exit(f"❌ aucune séance planifiée contenant « {motif} » entre {oldest} et {newest}")
    if len(hits) > 1:
        print("⚠️ plusieurs séances trouvées — préciser avec --id :")
        for e in hits:
            print(f"   id {e['id']} · {e.get('start_date_local','')[:10]} · {e.get('name')}")
        sys.exit(1)
    e = hits[0]
    print(f"→ '{e.get('name')}' du {e.get('start_date_local','')[:10]} vers {new_date}")
    move_by_id(e["id"], new_date)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as err:
        sys.exit(f"❌ Erreur API : {err}  ({getattr(err.response, 'text', '')[:250]})")
