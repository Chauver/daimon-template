#!/usr/bin/env python3
"""
seuils_tracker.py — surveille les SEUILS de référence et détecte toute dérive.

Pourquoi : Zwift (et Garmin) modifient la FTP automatiquement, sans prévenir. Le TSS étant
calculé en (puissance/FTP)², une FTP qui bouge de 8 % fausse le TSS de ~17 % — donc CTL, ATL,
TSB et l'indice de forme. Le même risque existe sur la LTHR et la FC max (calcul des zones).

Ce module lit les seuils déclarés dans intervals.icu à chaque passage, les historise
(journal/seuils.jsonl, append-only) et ALERTE si :
  • un seuil a changé depuis le dernier relevé  → typiquement une auto-modif Zwift/Garmin
  • un seuil diverge de la valeur du .env       → le modèle et la réalité ne coïncident plus

Usage :
    python3 seuils_tracker.py            relève, historise et alerte
    python3 seuils_tracker.py history     affiche l'historique
"""
from __future__ import annotations
import json, os, sys
from datetime import date
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
STORE = Path("journal") / "seuils.jsonl"

# Valeurs de référence du modèle (.env / CLAUDE.md §2) : ce sur quoi TOUS les calculs reposent.
REF = {
    "ftp": int(os.getenv("FTP_WATTS", "235")),
    "lthr": int(os.getenv("LTHR", "174")),
    "max_hr": int(os.getenv("HR_MAX", "186")),
}


def fetch():
    """Seuils déclarés dans intervals.icu (le sport qui porte une FTP = le vélo)."""
    r = requests.get(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/sport-settings",
                     auth=("API_KEY", API_KEY), timeout=40)
    r.raise_for_status()
    out = {"ftp": None, "indoor_ftp": None, "lthr": None, "max_hr": None}
    for s in r.json():
        if not isinstance(s, dict):
            continue
        if s.get("ftp") and out["ftp"] is None:
            out["ftp"] = s["ftp"]
            out["indoor_ftp"] = s.get("indoor_ftp")
        for k in ("lthr", "max_hr"):
            if s.get(k) and out[k] is None:
                out[k] = s[k]
    return out


def load_history():
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "history":
        h = load_history()
        if not h:
            sys.exit("Aucun relevé.")
        print(f"📈 HISTORIQUE DES SEUILS ({len(h)} relevé(s))\n")
        for e in h:
            v = e["intervals"]
            print(f"  {e['date']}  FTP {v.get('ftp')} · LTHR {v.get('lthr')} · "
                  f"FCmax {v.get('max_hr')}" + (f"   ⚠️ {'; '.join(e['alertes'])}" if e.get("alertes") else ""))
        return

    if not (API_KEY and ATHLETE_ID):
        sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")

    cur = fetch()
    hist = load_history()
    prev = hist[-1]["intervals"] if hist else None
    alertes = []

    # 1) dérive depuis le dernier relevé → auto-modification Zwift / Garmin
    if prev:
        for k, lbl in (("ftp", "FTP"), ("lthr", "LTHR"), ("max_hr", "FC max")):
            a, b = prev.get(k), cur.get(k)
            if a and b and a != b:
                pct = 100 * (b - a) / a
                alertes.append(f"{lbl} MODIFIÉE : {a} → {b} ({pct:+.1f} %)")

    # 2) divergence avec le modèle → les calculs ne reposent plus sur la réalité
    for k, lbl in (("ftp", "FTP"), ("lthr", "LTHR"), ("max_hr", "FC max")):
        ref, got = REF.get(k), cur.get(k)
        if ref and got and ref != got:
            alertes.append(f"{lbl} diverge du modèle : intervals.icu {got} vs .env {ref}")

    entry = {"date": date.today().isoformat(), "intervals": cur,
             "modele_env": REF, "alertes": alertes}
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"🎯 SEUILS au {entry['date']}")
    print(f"   intervals.icu : FTP {cur.get('ftp')} · LTHR {cur.get('lthr')} · FCmax {cur.get('max_hr')}"
          + (f" · FTP indoor {cur['indoor_ftp']}" if cur.get("indoor_ftp") else ""))
    print(f"   modèle (.env) : FTP {REF['ftp']} · LTHR {REF['lthr']} · FCmax {REF['max_hr']}")
    if not alertes:
        print("   ✅ cohérent, aucune dérive")
    else:
        for a in alertes:
            print(f"   ⚠️  {a}")
        # Une FTP qui bouge fausse le TSS au CARRÉ : on chiffre l'impact.
        f0, f1 = REF["ftp"], cur.get("ftp")
        if f1 and f0 != f1:
            print(f"   → impact TSS vélo : ×{(f0/f1)**2:.2f} "
                  f"({100*((f0/f1)**2 - 1):+.0f} % sur toute la charge vélo)")
    print(f"→ {STORE}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ API intervals.icu : {e}")
