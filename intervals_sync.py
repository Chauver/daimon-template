#!/usr/bin/env python3
"""
intervals_sync.py — Collecteur intervals.icu pour l'agent coach triathlète.

Lit l'API intervals.icu (données poussées depuis Garmin par le canal officiel) et remplit :
  - data/activities.csv  (même schéma que le collecteur Garmin → fitness_model.py marche tel quel)
  - data/wellness.csv     (FC repos, HRV, sommeil + CTL/ATL calculés par intervals.icu)

Avantage : pas de login fragile, pas de 429. Il suffit d'une clé API.

Prérequis :
    pip install requests
    Dans .env : INTERVALS_API_KEY et INTERVALS_ATHLETE_ID
    (Réglages intervals.icu → Developer : tu y trouves ta clé API et ton Athlete ID, ex. i123456)
"""
from __future__ import annotations
import csv, json, os, sys
from pathlib import Path
from datetime import date, datetime

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
SYNC_START = os.getenv("INTERVALS_SYNC_START", "2026-07-01")
DATA_DIR   = Path(os.getenv("GARMIN_DATA_DIR", "data"))
BASE       = "https://intervals.icu/api/v1"

CSV_FIELDS = ["id","start_date_local","sport_type","name","distance_km","moving_time_h",
              "elapsed_time_h","elevation_gain_m","avg_hr","max_hr","avg_watts","np_watts",
              "avg_speed_kmh","calories","temp_c","feel","rpe","has_fit"]
WELL_FIELDS = ["date","resting_hr","hrv_status","hrv_weekly_avg","sleep_hours","nap_hours","sleep_score",
               "training_status","vo2max_run","vo2max_bike","ctl_icu","atl_icu"]

# intervals.icu (types façon Strava) → clés attendues par fitness_model.py (façon Garmin)
TYPE_MAP = {"Ride":"cycling","VirtualRide":"virtual_ride","GravelRide":"gravel_cycling",
            "MountainBikeRide":"mountain_biking","Run":"running","TrailRun":"trail_running",
            "VirtualRun":"virtual_run","Swim":"lap_swimming","OpenWaterSwim":"open_water_swimming"}

def num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None

def api(path, params=None):
    if not (API_KEY and ATHLETE_ID):
        sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")
    r = requests.get(f"{BASE}{path}", params=params, auth=("API_KEY", API_KEY), timeout=40)
    if r.status_code == 401:
        sys.exit("❌ 401 : clé API ou Athlete ID incorrect (Réglages → Developer sur intervals.icu).")
    r.raise_for_status()
    return r.json()

def map_activity(a: dict) -> dict:
    typ = a.get("type", "")
    sport = TYPE_MAP.get(typ, typ.lower())
    if sport in ("running", "virtual_run") and a.get("trainer"):
        sport = "treadmill_running"          # tapis → fitness_model le route par la FC
    avg_ms = num(a.get("average_speed")) or num(a.get("icu_average_speed"))
    return {
        "id":               a.get("id", ""),
        "start_date_local": a.get("start_date_local", ""),
        "sport_type":       sport,
        "name":             (a.get("name") or "").replace("\n", " ").strip(),
        "distance_km":      round((num(a.get("distance")) or 0)/1000, 2),
        "moving_time_h":    round((num(a.get("moving_time")) or 0)/3600, 3),
        "elapsed_time_h":   round((num(a.get("elapsed_time")) or 0)/3600, 3),
        "elevation_gain_m": round(num(a.get("total_elevation_gain")) or 0),
        "avg_hr":           a.get("average_heartrate", ""),
        "max_hr":           a.get("max_heartrate", ""),
        "avg_watts":        a.get("average_watts") or a.get("icu_average_watts") or "",
        "np_watts":         a.get("icu_weighted_avg_watts") or a.get("normalized_watts") or "",
        "avg_speed_kmh":    round(avg_ms*3.6, 2) if avg_ms else "",
        "calories":         a.get("calories", ""),
        "temp_c":           a.get("average_temp") if a.get("average_temp") is not None else a.get("icu_avg_temp", ""),
        "feel":             a.get("feel", ""),        # ressenti 1..5 (5 = au top), saisi sur la montre
        "rpe":              a.get("icu_rpe") if a.get("icu_rpe") is not None else a.get("rpe", ""),
        "has_fit":          False,
    }

def map_wellness(w: dict) -> dict:
    secs = num(w.get("sleepSecs"))
    napsecs = num(w.get("napSecs")) or num(w.get("nap")) or num(w.get("napTime"))
    return {
        "date":            w.get("id", ""),
        "resting_hr":      w.get("restingHR", ""),
        "hrv_status":      "",
        "hrv_weekly_avg":  w.get("hrv", ""),
        "sleep_hours":     round(secs/3600, 1) if secs else "",
        "nap_hours":       round(napsecs/3600, 1) if napsecs else "",
        "sleep_score":     w.get("sleepScore", ""),
        "training_status": "",
        "vo2max_run":      "",
        "vo2max_bike":     "",
        "ctl_icu":         w.get("ctl", ""),
        "atl_icu":         w.get("atl", ""),
    }

def write_csv(path, fields, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

# --------------------------------------------------------------------------- séances nouvelles
# Détection des séances FRAÎCHEMENT téléchargées : on compare les IDs présents à un ledger des
# IDs déjà vus. Toute séance nouvelle (≥ 5 min) déclenche une mise à jour de l'indice de forme
# (charge → readiness) et son historisation (data/readiness_history.jsonl). En mode pipeline
# (PIPELINE_RUN=1), on se contente de poser le signal : le pipeline recalcule ensuite lui-même.
LEDGER_PATH       = DATA_DIR / "sync_ledger.json"
NEW_SESSIONS_PATH = DATA_DIR / "new_sessions.json"
MIN_SESSION_H     = 5 / 60          # sous 5 min : activité fantôme, pas une séance

def _load_seen():
    """Renvoie l'ensemble des IDs déjà vus, ou None si le ledger n'existe pas encore (1er passage)."""
    if not LEDGER_PATH.exists():
        return None
    try:
        return set(str(i) for i in json.loads(LEDGER_PATH.read_text()).get("seen_ids", []))
    except Exception:
        return None

def _save_seen(ids):
    LEDGER_PATH.write_text(json.dumps(
        {"seen_ids": sorted(str(i) for i in ids),
         "updated": datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2))

def detect_new(rows):
    """Compare les activités courantes au ledger. Renvoie la liste des séances NOUVELLES (≥ 5 min).
    Au tout premier passage, amorce le ledger sans rien déclencher (évite un faux « tout est nouveau »)."""
    current_ids = {str(r["id"]) for r in rows if str(r.get("id"))}
    seen = _load_seen()
    if seen is None:
        _save_seen(current_ids)
        print(f"   ℹ️  Ledger de synchro amorcé ({len(current_ids)} séances connues) — "
              f"l'historique de forme démarrera à la prochaine séance nouvelle.")
        return []
    new = [r for r in rows
           if str(r.get("id")) and str(r["id"]) not in seen
           and (num(r.get("moving_time_h")) or 0) >= MIN_SESSION_H]
    _save_seen(current_ids)
    return new

def write_new_sessions(new):
    """Écrit le signal des séances nouvelles (liste, éventuellement vide) consommé par
    readiness_model.append_history(). Toujours réécrit pour ne pas ré-historiser d'anciennes séances."""
    summ = [{"id": str(r["id"]), "date": str(r.get("start_date_local", ""))[:10],
             "sport_type": r.get("sport_type", ""), "name": r.get("name", ""),
             "distance_km": r.get("distance_km", ""), "moving_time_h": r.get("moving_time_h", "")}
            for r in new]
    NEW_SESSIONS_PATH.write_text(json.dumps(summ, ensure_ascii=False, indent=2))
    return summ

def _recompute_form():
    """Mode autonome : après une séance nouvelle, recalcule la charge PUIS l'indice de forme
    (fitness AVANT readiness — l'indice dépend du TSB), ce qui historise l'indice au passage."""
    try:
        import fitness_model, readiness_model
        print("   ↻ Séance nouvelle → recalcul charge + indice de forme…")
        fitness_model.main()
        readiness_model.main()
    except SystemExit as e:
        print(f"   ⚠️  Recalcul interrompu : {e}")
    except Exception as e:
        print(f"   ⚠️  Recalcul impossible : {e}")

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    print(f"🔁 Lecture intervals.icu du {SYNC_START} au {today}")

    acts = api(f"/athlete/{ATHLETE_ID}/activities", {"oldest": SYNC_START, "newest": today})
    rows = [map_activity(a) for a in acts]
    rows.sort(key=lambda r: r["start_date_local"])
    write_csv(DATA_DIR / "activities.csv", CSV_FIELDS, rows)
    print(f"✅ {len(rows)} activités → {DATA_DIR/'activities.csv'}")

    well = api(f"/athlete/{ATHLETE_ID}/wellness", {"oldest": SYNC_START, "newest": today})
    if isinstance(well, dict):            # certains comptes renvoient un dict indexé par date
        well = list(well.values())
    wrows = [map_wellness(w) for w in well if w.get("id")]
    wrows.sort(key=lambda r: r["date"])
    write_csv(DATA_DIR / "wellness.csv", WELL_FIELDS, wrows)
    print(f"✅ {len(wrows)} jours de forme → {DATA_DIR/'wellness.csv'}")

    if rows:
        last = rows[-1]
        print(f"   Dernière séance : {last['start_date_local'][:10]} · {last['sport_type']} · "
              f"{last['distance_km']} km")
    else:
        print("   ℹ️  Aucune activité sur la période — vérifie que Garmin a bien poussé vers intervals.icu.")

    # --- Séances nouvelles → mise à jour de l'indice de forme ---
    new = detect_new(rows)
    summ = write_new_sessions(new)
    if summ:
        print(f"🆕 {len(summ)} séance(s) nouvelle(s) téléchargée(s) :")
        for s in summ:
            print(f"   • {s['date']} · {s['sport_type']} · {s['distance_km']} km ({s['id']})")
        if os.getenv("PIPELINE_RUN") == "1":
            print("   (mode pipeline : l'indice sera recalculé et historisé par les étapes suivantes)")
        else:
            _recompute_form()

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}")
