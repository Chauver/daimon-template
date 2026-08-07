#!/usr/bin/env python3
"""
build_web_state.py — Génère web/coach_state.json : le fichier unique que lit l'appli iPhone (PWA).

Agrège, depuis les données déjà produites par le pipeline (readiness.json, fitness.csv,
run_volume.csv, journal, .env), tout ce qu'affichent les 2 écrans de l'appli :
  • Écran 1 « Aujourd'hui » : indice de forme, jauges (TSB/FC/sommeil/ressenti/HRV),
    citation contextuelle, prochaine séance, volume course, météo.
  • Écran 2 « Plan »        : courbe de charge planifiée, dernière semaine complétée,
    seuils de référence, compteur blessure.

Champs absents = null (base de calibration encore mince) : l'appli affiche « — ».
Réseau (prochaine séance + météo) en best-effort : si hors-ligne, ces champs restent null.

Usage : python3 build_web_state.py   (à la fin de run_pipeline.sh)
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from datetime import date, timedelta

try:
    import pandas as pd
except ImportError:
    sys.exit("❌ pip install pandas")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / os.getenv("GARMIN_DATA_DIR", "data")
WEB = ROOT / "web"

def _load_dotenv(p=ROOT / ".env"):
    if not Path(p).exists(): return
    for line in Path(p).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

def envf(name, default):
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return float(default)

RACE = date(2027, 6, 27)
PLAN_START = date(2026, 6, 29)
# Plan hebdo (heures cibles/semaine) — même source que session_report.py.
# Courbe D34 (validée athlète 31/07/2026) : moyenne ~10 h, vagues 3+1 (récup S8,12,16,20,24,28,
# 32,36,40,44,48), pic 16 h (S46), pics secondaires alignés vacances scolaires (S26-27, S43),
# L de Lacanau ~mai 2027 (S45-48, micro-structure à ajuster quand la date sera connue),
# affûtage S49-52. Remplace la courbe théorique D1 (pic 17 h), incohérente avec D28/D30.
WK = [4,4,4,4, 6,7,
      7.5,5,8,8.5,9,5.5,9,9.5,10,6,
      10,10.5,11,6.5,10.5,11,11.5,7,11,12,
      12,7,11,11.5,12,7.5,12.5,
      12,13,8,12.5,13.5,14,8,13,14.5,15.5,8.5,14,16,13,9,
      10,8,6,4]

MOIS = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS = ["LUN.", "MAR.", "MER.", "JEU.", "VEN.", "SAM.", "DIM."]

# Citations : sélectionnées selon le facteur limitant du jour (couche "coach" de l'appli).
CITATIONS_FILE = WEB / "citations.json"

def charger_citations():
    try:
        return json.loads(CITATIONS_FILE.read_text(encoding="utf-8")).get("citations", [])
    except Exception:
        return []

def choisir_citation(readiness, subfn, meteo_data, ref):
    """Choisit une citation de la bibliothèque (web/citations.json) selon le contexte du jour :
    météo → forme → facteur limitant. Rotation quotidienne déterministe (toordinal, pas de hasard)
    pour que la citation change chaque jour mais reste stable au sein d'un même jour."""
    lib = charger_citations()
    if not lib:
        return {"texte": "Ce que tu fais chaque jour compte plus que ce que tu fais une fois.",
                "auteur": "Coach", "contexte": "cap tenu"}
    presents = {k: subfn(k) for k in ("sleep", "tsb", "hr", "hrv") if subfn(k) is not None}
    faible = min(presents, key=presents.get) if presents else None
    hot  = bool(meteo_data and (meteo_data.get("ajust_pct") or 0) < 0)
    cold = bool(meteo_data and meteo_data.get("temp_c") is not None and meteo_data["temp_c"] < 8)
    if hot:                                          tags, label = ["canicule", "souffrance"], "chaleur"
    elif cold:                                       tags, label = ["froid"], "grand froid"
    elif readiness is not None and readiness >= 72:  tags, label = ["green_day", "forme_haute"], "jour vert"
    elif readiness is not None and readiness < 40:   tags, label = ["recup", "patience", "doute"], "jour rouge — on lève le pied"
    elif faible == "sleep":                          tags, label = ["sommeil", "recup"], "sommeil en dette"
    elif faible == "tsb":                            tags, label = ["recup", "patience"], "charge en cours"
    else:                                            tags, label = ["regularite", "discipline", "mental", "process", "joie"], "cap tenu"
    pool = [c for c in lib if set(c.get("tags", [])) & set(tags)] or lib
    c = pool[ref.toordinal() % len(pool)]
    return {"texte": c["fr"], "auteur": c["auteur"], "contexte": label, "source": c.get("source")}

def num(x):
    try:
        v = float(x); return None if pd.isna(v) else v
    except (TypeError, ValueError): return None

def load_json(name):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def load_csv(name):
    p = DATA / name
    if not p.exists(): return None
    try:
        df = pd.read_csv(p); return df if not df.empty else None
    except Exception:
        return None

def semaine_plan(d: date) -> int:
    return max(1, min(52, (d - PLAN_START).days // 7 + 1))

def date_label(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month]}"

# ─────────────────────────── prochaine séance + météo (réseau, best-effort) ───────────────────────────

def next_workout(ref: date):
    """Prochaine séance planifiée sur intervals.icu (>= ref). None si hors-ligne / rien."""
    try:
        import requests
        key, ath = os.getenv("INTERVALS_API_KEY"), os.getenv("INTERVALS_ATHLETE_ID")
        if not (key and ath): return None
        # séances À VENIR : strictement après le dernier jour de données (celles du jour même
        # sont déjà faites/en cours — sinon on afficherait une séance passée comme "prochaine").
        r = requests.get(f"https://intervals.icu/api/v1/athlete/{ath}/events",
                         params={"oldest": (ref + timedelta(days=1)).isoformat(), "newest": (ref + timedelta(days=21)).isoformat()},
                         auth=("API_KEY", key), timeout=20)
        r.raise_for_status()
        evs = sorted((e for e in r.json() if e.get("category") == "WORKOUT"),
                     key=lambda x: x.get("start_date_local", ""))
        if not evs: return None
        e = evs[0]
        typ = str(e.get("type", ""))
        disc = "bike" if "Ride" in typ else "swim" if "Swim" in typ else "run"
        full = e.get("description") or ""
        desc = full.splitlines()
        import re
        # durée : lignes de workout texte ("- 12m") d'abord, sinon dernier "45min" du nom
        # (un \d+m global gobait les distances de la prose — "400 m chrono" → 400 min, bug 04/08)
        mins = sum(int(m) for m in re.findall(r"^\s*-\s*(\d+)\s*m\b", full, re.M))
        if not mins:
            m_nom = re.findall(r"(\d+)\s*min\b", e.get("name") or "")
            mins = int(m_nom[-1]) if m_nom else 0
        low = full.lower()
        bande = ("VO2" if "vo2" in low else "HIIT" if ("seuil" in low or "hiit" in low) else
                 "MIIT" if any(w in low for w in ("sweet", "tempo", "miit", "spé")) else
                 "LIT" if any(w in low for w in ("z1", "z2", "endurance", "lit", "footing")) else None)
        return {"date": str(e.get("start_date_local", ""))[:10], "nom": e.get("name", ""),
                "discipline": disc, "resume": desc[0] if desc else None,
                "duree_min": mins or None, "bande": bande}
    except Exception:
        return None

def derniere_position():
    """Position de la DERNIÈRE séance extérieure avec GPS — la météo doit suivre l'athlète
    (Bordeaux, vacances…), pas rester figée sur le lieu de base. Les activités VIRTUELLES
    sont exclues (le « GPS » Zwift pointe dans Watopia). Cache data/last_position.json :
    les flux ne sont re-téléchargés que si une activité plus récente est apparue.
    Repli : HOME_LAT/HOME_LON, puis dernière position connue."""
    import json as _json
    cache_p = DATA / "last_position.json"
    try:
        cache = _json.loads(cache_p.read_text()) if cache_p.exists() else {}
    except Exception:
        cache = {}
    fallback = (envf("HOME_LAT", None), envf("HOME_LON", None), None)
    acts = load_csv("activities.csv")
    if acts is None or len(acts) == 0:
        return (cache.get("lat"), cache.get("lon"), cache.get("lieu")) if cache.get("lat") else fallback
    a = acts.sort_values("start_date_local", ascending=False)
    newest = str(a.iloc[0].get("id"))
    if cache.get("last_seen_id") == newest and cache.get("lat"):
        return cache["lat"], cache["lon"], cache.get("lieu")
    VIRTUEL = {"virtual_ride", "virtual_run"}
    found = None
    for _, r in a.head(8).iterrows():
        st = str(r.get("sport_type", "")).lower()
        if st in VIRTUEL or str(r.get("name", "")).lower().startswith("zwift"):
            continue
        try:
            import hr_analysis as HRA
            S = HRA.load_streams(str(r.get("id")))
            la, lo = S.get("_lat"), S.get("_lon")
            lat = next((x for x in (la or []) if x), None)
            lon = next((x for x in (lo or []) if x), None)
            if lat and lon:
                lieu = (str(r.get("name") or "").split(" - ")[0]).strip() or None
                found = {"activity_id": str(r.get("id")), "lat": lat, "lon": lon, "lieu": lieu}
                break
        except Exception:
            continue
    out = found or ({"lat": cache.get("lat"), "lon": cache.get("lon"), "lieu": cache.get("lieu")}
                    if cache.get("lat") else None)
    try:
        cache_p.write_text(_json.dumps(dict(out or {}, last_seen_id=newest), ensure_ascii=False))
    except Exception:
        pass
    if out and out.get("lat"):
        return out["lat"], out["lon"], out.get("lieu")
    return fallback


def meteo(ref: date):
    """Météo prévue au lieu de la DERNIÈRE séance GPS (repli lieu de base). None si hors-ligne."""
    try:
        from weather import fetch_temp_jour, plan_heat_advice
        lat, lon, lieu = derniere_position()
        if not lat or not lon: return None
        t08, tmax = fetch_temp_jour(lat, lon, ref.isoformat())
        if t08 is None and tmax is None: return None
        adv = plan_heat_advice(tmax if tmax is not None else t08)   # le conseil se juge au pire de la journée
        return {"temp_c": round(t08, 1) if t08 is not None else None,
                "temp_max_c": round(tmax, 1) if tmax is not None else None,
                "niveau": adv["level"] if adv else None,
                "ajust_pct": adv["intensity_adjust_pct"] if adv else 0,
                "conseil": adv["advice"] if adv else None,
                "lieu": lieu}
    except Exception:
        return None

# ─────────────────────────── nouvelles couches (historique / explications / semaine) ───────────────────────────

def readiness_history(ref: date, value):
    """Ajoute la readiness du jour à data/readiness_history.csv (dédup date) → 20 derniers points."""
    p = DATA / "readiness_history.csv"
    hist = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            if "," in line:
                dd, vv = line.split(",", 1)
                try: hist[dd.strip()] = int(float(vv))
                except ValueError: pass
    if value is not None:
        hist[ref.isoformat()] = int(round(value))
    keys = sorted(hist)
    p.write_text("date,readiness\n" + "\n".join(f"{d},{hist[d]}" for d in keys) + "\n", encoding="utf-8")
    return [{"d": d[5:], "v": hist[d]} for d in keys[-20:]]   # d = "MM-DD"

def explications(sub, detail):
    """Phrase explicative par jauge (couche coach) à partir des sous-scores + détails."""
    def band(sc, hi, mid, lo):
        return hi if (sc or 0) >= 70 else mid if (sc or 0) >= 40 else lo
    tsb, hr, hv = detail("tsb"), detail("hr"), detail("hrv")
    sj = detail("subj")
    e = {}
    e["tsb"] = (f"TSB {tsb:+.0f} : bien reposé, prêt à charger." if isinstance(tsb,(int,float)) and tsb >= 5 else
                f"TSB {tsb:+.0f} : fatigue accumulée — la fraîcheur reviendra après récup." if isinstance(tsb,(int,float)) and tsb <= -10 else
                f"TSB {tsb:+.0f} : charge équilibrée." if isinstance(tsb,(int,float)) else "Charge : données à venir.")
    e["fc"] = band(sub("hr"), "FC de repos au plancher : récupération nerveuse au top.",
                   f"FC de repos {('à '+str(round(hr))+' bpm, ') if isinstance(hr,(int,float)) else ''}un cran au-dessus de ta base — récup correcte.",
                   "FC de repos élevée vs ta base : signe de fatigue ou de début d'infection, prudence.")
    e["sommeil"] = band(sub("sleep"), "Sommeil solide : bon socle de récupération.",
                        "Sommeil correct sans plus — à surveiller si ça se répète.",
                        "Nuit courte : récup entamée, allège si ça s'accumule.")
    e["hrv"] = band(sub("hrv"), "HRV haute : bonne balance parasympathique.",
                    f"HRV {('à '+str(round(hv))+' ms, ') if isinstance(hv,(int,float)) else ''}dans ta moyenne.",
                    "HRV basse : système nerveux sous charge, favorise la récup.")
    e["ressenti"] = band(sub("subj"), f"Ressenti excellent{(' ('+str(sj)+')') if sj else ''} : effort perçu plus facile que prévu.",
                        "Ressenti dans la norme du jour.",
                        "Ressenti dur : l'effort a coûté plus que prévu, à recouper avec la fatigue.")
    return e

def _disc_from_type(typ: str) -> str:
    typ = str(typ)
    return "bike" if "Ride" in typ else "swim" if "Swim" in typ else "run"

def planned_week(monday: date, sunday: date):
    """Séances PLANIFIÉES (intervals.icu) de la semaine courante. [] si hors-ligne."""
    try:
        import requests, re
        key, ath = os.getenv("INTERVALS_API_KEY"), os.getenv("INTERVALS_ATHLETE_ID")
        if not (key and ath): return []
        r = requests.get(f"https://intervals.icu/api/v1/athlete/{ath}/events",
                         params={"oldest": monday.isoformat(), "newest": sunday.isoformat()},
                         auth=("API_KEY", key), timeout=20)
        r.raise_for_status()
        out = []
        for e in r.json():
            if e.get("category") != "WORKOUT": continue
            full = e.get("description") or ""
            wd = e.get("workout_doc")
            mins = None
            if isinstance(wd, dict) and wd.get("steps"):        # durée = somme des steps du workout structuré
                secs = sum((s.get("duration") or 0) for s in wd["steps"])
                if secs: mins = round(secs / 60)
            if mins is None:
                # fallback 1 : lignes de workout texte ("- 12m") UNIQUEMENT — un \d+m global
                # gobait les distances de la prose ("400 m chrono" → 400 min ! bug vu 04/08)
                mins = sum(int(m) for m in re.findall(r"^\s*-\s*(\d+)\s*m\b", full, re.M)) or None
            if mins is None:                                    # fallback 2 : "· 45min" dans le NOM
                m_nom = re.findall(r"(\d+)\s*min\b", e.get("name") or "")
                mins = int(m_nom[-1]) if m_nom else None        # le DERNIER (= durée totale en fin de nom)
            out.append({"date": str(e.get("start_date_local", ""))[:10], "nom": e.get("name", ""),
                        "discipline": _disc_from_type(e.get("type", "")), "duree_min": mins})
        return out
    except Exception:
        return []

def semaine_view(ref: date, acts, verdicts_by_id, seances_by_id):
    """Toutes les séances de la semaine calendaire (faites + validées + à venir) + volumes par discipline."""
    monday = ref - timedelta(days=ref.weekday()); sunday = monday + timedelta(days=6)
    RUN = {"running","trail_running","treadmill_running","track_running","virtual_run"}
    RIDE = {"cycling","road_biking","gravel_cycling","mountain_biking","indoor_cycling","virtual_ride"}
    SWIM = {"lap_swimming","open_water_swimming","swimming"}
    STRENGTH = {"weighttraining","strength_training","weight_training","indoor_strength_training"}
    def dsc(s): return ("bike" if s in RIDE else "run" if s in RUN else "swim" if s in SWIM
                        else "muscu" if s in STRENGTH else "run")
    KMEST = {"run": lambda m: m/6.0, "swim": lambda m: m*0.05, "bike": lambda m: m/60*28.0}  # km estimés depuis la durée

    seances, jours_faits = [], set()
    # 1) séances RÉALISÉES cette semaine (activities.csv)
    if acts is not None:
        a = acts.copy()
        a["day"] = pd.to_datetime(a["start_date_local"].astype(str).str[:10], errors="coerce").dt.date
        a = a.dropna(subset=["day"])
        wk_a = a[(a["day"] >= monday) & (a["day"] <= sunday)]
        for _, r in wk_a.iterrows():
            disc = dsc(str(r.get("sport_type", "")))
            aid = str(r.get("id", "")); km = num(r.get("distance_km"))
            valide = aid in verdicts_by_id
            h_seance = num(r.get("moving_time_h"))
            seances.append({"date": str(r["day"]), "jour": JOURS[r["day"].weekday()][:3],
                            "discipline": disc, "nom": (seances_by_id.get(aid, {}) or {}).get("nom") or str(r.get("name", "")),
                            "statut": "valide" if valide else "fait",
                            "km": round(km, 1) if km else None,
                            "duree_min": round(h_seance * 60) if h_seance else None,
                            "verdict": verdicts_by_id.get(aid)})
            jours_faits.add((str(r["day"]), disc))
    # 2) séances PLANIFIÉES à venir (pas déjà faites le même jour/discipline)
    for p in planned_week(monday, sunday):
        if p["date"] < ref.isoformat(): continue                    # passé non fait → on n'invente pas
        if (p["date"], p["discipline"]) in jours_faits: continue    # déjà réalisée
        try: wd = date.fromisoformat(p["date"]).weekday()
        except ValueError: wd = 0
        seances.append({"date": p["date"], "jour": JOURS[wd][:3], "discipline": p["discipline"],
                        "nom": p["nom"], "statut": "prevu", "duree_min": p["duree_min"], "verdict": None})
    seances.sort(key=lambda x: (x["date"], x["discipline"]))

    # volumes par discipline : fait (km réalisés) / cible (plan de la semaine, km estimés)
    # muscu : en MINUTES (pas de km), cible hebdo ~50 min (séance type D31, mardis soirs)
    vol = {k: {"fait": 0.0, "cible": 0.0} for k in ("cap", "nat", "velo")}
    vol["muscu"] = {"fait": 0, "cible": 50, "unite": "min"}
    total_min = 0
    MAP = {"run": "cap", "swim": "nat", "bike": "velo"}
    for s in seances:
        if s["statut"] in ("fait", "valide"):
            total_min += s.get("duree_min") or 0
        if s["discipline"] == "muscu":
            if s["statut"] in ("fait", "valide"):
                vol["muscu"]["fait"] += s.get("duree_min") or 0
            continue
        k = MAP[s["discipline"]]
        km_fait = s.get("km") or 0
        km_est = s.get("km") or (KMEST[s["discipline"]](s["duree_min"]) if s.get("duree_min") else 0)
        if s["statut"] in ("fait", "valide"): vol[k]["fait"] += km_fait
        vol[k]["cible"] += km_est
    for k in ("cap", "nat", "velo"):
        vol[k] = {"fait": round(vol[k]["fait"], 1), "cible": round(vol[k]["cible"], 1)}
    vol["muscu"]["cible"] = max(vol["muscu"]["cible"], vol["muscu"]["fait"])
    vol["total_min"] = total_min                      # durée totale FAITE cette semaine (tous sports)
    return seances, vol

# ─────────────────────────── construction ───────────────────────────

def build():
    readiness = load_json("readiness.json") or {}
    ref = date.fromisoformat(readiness.get("date", date.today().isoformat()))
    wk = semaine_plan(ref)

    comp = readiness.get("components", {})
    def sub(key):   # sous-score 0-100 ou None
        c = comp.get(key)
        return c.get("score") if isinstance(c, dict) else None
    def detail(key):
        c = comp.get(key)
        return c.get("detail") if isinstance(c, dict) else None

    # jauges : (clé readiness, libellé, valeur affichée)
    tsb_val = detail("tsb")
    def gval(key):   # valeur affichée = le sous-score lui-même (0-100), None si absent
        return str(sub(key)) if sub(key) is not None else None
    gauges = [
        {"cle": "tsb",      "label": "TSB",      "score": sub("tsb"),
         "valeur": (f"{tsb_val:+.0f}" if isinstance(tsb_val, (int, float)) else None)},
        {"cle": "fc",       "label": "FC",       "score": sub("hr"),    "valeur": gval("hr")},
        {"cle": "sommeil",  "label": "SOMMEIL",  "score": sub("sleep"), "valeur": gval("sleep")},
        {"cle": "ressenti", "label": "RESSENTI", "score": sub("subj"),  "valeur": gval("subj")},
        {"cle": "hrv",      "label": "HRV",      "score": sub("hrv"),   "valeur": gval("hrv")},
    ]
    _expl = explications(sub, detail)
    for _g in gauges: _g["explication"] = _expl.get(_g["cle"])

    # citation selon le facteur limitant (plus bas sous-score présent)
    meteo_data = meteo(ref)
    citation = choisir_citation(readiness.get("readiness"), sub, meteo_data, ref)

    # volume course
    rv = load_csv("run_volume.csv")
    vol = None
    if rv is not None:
        cur = rv.sort_values("semaine").iloc[-1]
        vol = {"semaine_km": round(num(cur["run_km"]) or 0, 1), "plafond": int(cur["plafond"]),
               "statut": str(cur["statut"]).lower()}

    # charge planifiée (52 semaines) + pic
    pic_h = max(WK); pic_s = WK.index(pic_h) + 1

    # agrégats RÉELS par semaine de plan (heures + volumes par discipline)
    acts = load_csv("activities.csv")
    reel = {}
    if acts is not None:
        a = acts.copy()
        a["day"] = pd.to_datetime(a["start_date_local"].astype(str).str[:10], errors="coerce").dt.date
        a = a.dropna(subset=["day"])
        a["wk"] = a["day"].apply(semaine_plan)
        RUN = {"running","trail_running","treadmill_running","track_running","virtual_run"}
        RIDE = {"cycling","road_biking","gravel_cycling","mountain_biking","indoor_cycling","virtual_ride"}
        SWIM = {"lap_swimming","open_water_swimming","swimming"}
        STRENGTH = {"weighttraining","strength_training","weight_training","indoor_strength_training"}
        def d(s): return ("bike" if s in RIDE else "run" if s in RUN else "swim" if s in SWIM
                          else "muscu" if s in STRENGTH else None)
        for _, r in a.iterrows():
            disc = d(str(r.get("sport_type", "")))
            if disc is None: continue
            w_ = int(r["wk"])
            e = reel.setdefault(w_, {"h_total": 0.0, "velo_h": 0.0, "velo_km": 0.0,
                                     "course_km": 0.0, "course_h": 0.0,
                                     "nat_km": 0.0, "nat_h": 0.0,
                                     "muscu_h": 0.0, "muscu_n": 0, "n": 0})
            h_ = num(r.get("moving_time_h")) or 0
            km_ = num(r.get("distance_km")) or 0
            e["h_total"] += h_; e["n"] += 1
            if disc == "bike":  e["velo_h"] += h_; e["velo_km"] += km_
            elif disc == "run": e["course_km"] += km_; e["course_h"] += h_
            elif disc == "swim": e["nat_km"] += km_; e["nat_h"] += h_
            elif disc == "muscu": e["muscu_h"] += h_; e["muscu_n"] += 1

    # dernière semaine de plan COMPLÉTÉE (entièrement passée) : totaux par discipline
    derniere_sem = None
    completes = sorted(w_ for w_ in reel if w_ < wk)
    if completes:
        w = completes[-1]; e = reel[w]
        derniere_sem = {"semaine": int(w), "course_km": round(e["course_km"], 1),
                        "velo_h": round(e["velo_h"], 1), "nat_km": round(e["nat_km"], 1)}

    # lectures hebdo du coach (journal/bilans_semaine.jsonl, la dernière saisie fait foi)
    lectures = {}
    bj = ROOT / "journal" / "bilans_semaine.jsonl"
    if bj.exists():
        for line in bj.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line); lectures[int(o["semaine"])] = o

    # squelette des phases du MACRO (D30/D34) — affiché sur l'écran Saison, grisé si à venir
    PHASES = [
        (1, 4,  "Reprise",             "sortie du creux · pipeline & journal en place"),
        (5, 6,  "Calibration",         "tests CSS · FTP · profil FC↔allure"),
        (7, 16, "Squelette & vagues",  "3 sports installés · muscu lourde · 7,5→10 h"),
        (17, 26, "Base aérobie 1",     "constance vélo « façon Cascais » · 10→12 h"),
        (27, 33, "Trimestre fondateur","l'assurance anti-Nice · ≥ 83 h sur Jan-Mars"),
        (34, 48, "Spécifique IM",      "MIIT vélo · longues · bricks · pic 16 h · L de Lacanau (mai)"),
        (49, 52, "Affûtage",           "réduction stricte → LES SABLES · 27 juin 2027"),
    ]
    phases = []
    for s_from, s_to, nom, det in PHASES:
        deb = PLAN_START + timedelta(days=(s_from - 1) * 7)
        fin = PLAN_START + timedelta(days=(s_to - 1) * 7 + 6)
        statut = "fait" if s_to < wk else ("en_cours" if s_from <= wk else "a_venir")
        phases.append({"de": s_from, "a": s_to, "nom": nom, "detail": det, "statut": statut,
                       "periode": f"{deb.day} {MOIS[deb.month]} {deb.year % 100} → {fin.day} {MOIS[fin.month]} {fin.year % 100}"})

    # semaines du graphe : plan + réel + fiche bilan (semaines écoulées et en cours)
    plan = []
    for i, h in enumerate(WK):
        s_ = i + 1
        entry = {"s": s_, "h": h}
        if s_ <= wk and s_ in reel:
            e = reel[s_]
            deb = PLAN_START + timedelta(days=(s_ - 1) * 7); fin = deb + timedelta(days=6)
            entry["reel_h"] = round(e["h_total"], 1)
            entry["bilan"] = {
                "periode": f"{deb.day} {MOIS[deb.month]} → {fin.day} {MOIS[fin.month]}",
                "h_total": round(e["h_total"], 2), "n": e["n"],
                "course_km": round(e["course_km"], 1), "course_h": round(e["course_h"], 2),
                "velo_h": round(e["velo_h"], 2), "velo_km": round(e["velo_km"], 1),
                "nat_km": round(e["nat_km"], 1), "nat_h": round(e["nat_h"], 2),
                "muscu_h": round(e["muscu_h"], 2), "muscu_n": e["muscu_n"],
                "en_cours": s_ == wk,
                "lecture": (lectures.get(s_) or {}).get("lecture"),
            }
        plan.append(entry)

    # seuils de référence
    css_s = envf("CSS_SEC_PER_100M", 105)
    seuils = {
        "ftp":        {"valeur": round(envf("FTP_WATTS", 245)), "unite": "W", "label": "FTP vélo (horaire)", "statut": "testée 05/08 (20 min)"},
        "allure_run": {"valeur": "4:20", "unite": "/km", "label": "Allure seuil course", "statut": "à revérifier"},
        "css":        {"valeur": f"{int(css_s // 60)}:{int(css_s % 60):02d}", "unite": "/100m", "label": "CSS natation", "statut": "provisoire"},
        "fc_seuil":   {"valeur": round(envf("LTHR", 174)), "unite": "bpm", "label": "FC seuil (LTHR)", "statut": "plausible"},
    }

    # dernières séances (depuis le journal factuel)
    dernieres = []
    seances, verdicts = {}, {}
    jl = ROOT / "journal" / "seances.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            o = json.loads(line)
            if o.get("type") == "seance": seances[o["activity_id"]] = o
            elif o.get("type") == "verdict": verdicts[o["activity_id"]] = o.get("texte")
        for s in sorted(seances.values(), key=lambda x: x.get("date", ""))[-5:]:
            dernieres.append({"date": s.get("date"), "discipline": s.get("discipline"),
                              "nom": s.get("nom"), "bande": s.get("bande"),
                              "distance_km": s.get("distance_km"), "duree_h": s.get("duree_h"),
                              "tss": s.get("tss"), "verdict": verdicts.get(s["activity_id"])})

    hist20 = readiness_history(ref, readiness.get("readiness"))
    sem_seances, sem_volumes = semaine_view(ref, acts, verdicts, seances)

    state = {
        "genere_le": ref.isoformat(),
        "titre": "Cap sur Les Sables 2027",
        "prepa": {
            "semaine": wk, "total": 52, "base": (wk - 1) // 4 + 1,
            "pct": round((ref - PLAN_START).days / (RACE - PLAN_START).days * 100),
            "date_label": date_label(ref), "j_moins": (RACE - ref).days,
        },
        "forme": {
            "indice": readiness.get("readiness"), "couleur": readiness.get("color"),
            "guide": readiness.get("guide"),
            "charge": {"ctl": None, "atl": None, "tsb": tsb_val},
            "historique": hist20,
        },
        "citation": citation,
        "jauges": gauges,
        "prochaine_seance": next_workout(ref),
        "volume_course": vol,
        "semaine": {"seances": sem_seances},
        "volumes": sem_volumes,
        "meteo": meteo_data,
        "charge_planifiee": {"pic_h": pic_h, "pic_s": pic_s, "semaines": plan},
        "phases": phases,
        "derniere_semaine": derniere_sem,
        "seuils": seuils,
        "blessure": readiness.get("injury", {"level": "vert", "triggers": []}),
        "dernieres_seances": dernieres,
        "alertes": [],
    }

    # charge CTL/ATL au dernier point
    fit = load_csv("fitness.csv")
    if fit is not None:
        last = fit.sort_values("date").iloc[-1]
        state["forme"]["charge"] = {"ctl": round(num(last["ctl"]) or 0, 1),
                                    "atl": round(num(last["atl"]) or 0, 1),
                                    "tsb": round(num(last["tsb"]) or 0, 1)}

    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "coach_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ web/coach_state.json généré · S{wk} · forme {state['forme']['indice']} ({state['forme']['couleur']})")
    online = "oui" if state["prochaine_seance"] or state["meteo"] else "hors-ligne (séance/météo à null)"
    print(f"   Prochaine séance / météo : {online}")

if __name__ == "__main__":
    build()
