#!/usr/bin/env python3
"""
delete_event.py — Supprime une séance planifiée sur intervals.icu (source de vérité).

Comme intervals.icu repousse vers Garmin, il faut supprimer ICI (pas seulement sur Garmin).

Usage :
    python3 delete_event.py "6x1000"                 # aujourd'hui, séances dont le nom contient "6x1000"
    python3 delete_event.py "footing" 2026-07-14     # à une date précise
    python3 delete_event.py --id 123456789           # par identifiant exact
Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
"""
from __future__ import annotations
import os, sys
from datetime import date
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

def delete_by_id(eid):
    r = requests.delete(f"{BASE}/athlete/{ATHLETE_ID}/events/{eid}", auth=AUTH, timeout=40)
    r.raise_for_status()
    print(f"🗑️  Supprimé (id {eid}).")

def main():
    args = sys.argv[1:]
    if not args:
        sys.exit('Usage : python3 delete_event.py "mot du nom" [AAAA-MM-JJ]   ou   --id <id>')

    if args[0] == "--id" and len(args) > 1:
        delete_by_id(args[1]); return

    needle = args[0].lower()
    day = args[1] if len(args) > 1 else date.today().isoformat()

    r = requests.get(f"{BASE}/athlete/{ATHLETE_ID}/events",
                     params={"oldest": day, "newest": day, "category": "WORKOUT"}, auth=AUTH, timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    matches = [e for e in r.json() if needle in (e.get("name", "") or "").lower()]

    if not matches:
        print(f"Aucune séance contenant « {args[0]} » le {day}.")
        return
    print(f"{len(matches)} séance(s) trouvée(s) le {day} :")
    for e in matches:
        print(f"   • {e.get('name')} (id {e.get('id')})")
    if input("Supprimer ? (o/n) ").strip().lower() not in ("o", "oui", "y", "yes"):
        print("Annulé."); return
    for e in matches:
        delete_by_id(e["id"])
    print("Ouvre l'appli Garmin Connect (montre à proximité) pour synchroniser la suppression.")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}")
