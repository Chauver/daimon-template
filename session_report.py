#!/usr/bin/env python3
"""
session_report.py — Assemble le dossier chiffré d'une séance et prépare le verdict IA.

Croise, pour une séance donnée :
  • prévu (squelette hebdo) vs réalisé
  • bande d'intensité (LIT/MIIT/HIIT/VO2) et efficience FC vs base 28 j de la bande
  • décrochage / dérive cardiaque (si l'analyse d'intervalles existe)
  • contexte de charge (CTL/ATL/TSB), sommeil/HRV, et météo (correction chaleur)
  • ressenti (RPE / feel) si saisi

Sort : un résumé + un verdict « règles » (repli hors-ligne) + un PROMPT prêt à envoyer à
Claude pour rédiger la ou les phrases clés. Écrit data/session_report_<id>.json.

Usage : python3 session_report.py [activity_id]   (défaut : dernière séance de activities.csv)
Prérequis : pip install pandas ; seuils dans .env ; weather.py dans le dossier.
"""
from __future__ import annotations
import json, os, sys, math
from pathlib import Path
from datetime import date, datetime, timedelta

try:
    import pandas as pd
except ImportError:
    sys.exit("❌ pip install pandas")
try:
    from weather import heat_diagnosis, thermal_ef_factor, temp_baseline, fetch_temp
except ImportError:
    heat_diagnosis = lambda t, b: None
    thermal_ef_factor = lambda t, b: 1.0
    temp_baseline = lambda ts: None
    fetch_temp = lambda lat, lon, when: None

DATA = Path(os.getenv("GARMIN_DATA_DIR", "data"))
def _load_dotenv(p=".env"):
    f = Path(p)
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()
def _f(n, d):
    try: return float(os.getenv(n, d))
    except (TypeError, ValueError): return float(d)
def _fopt(n):
    v = os.getenv(n)
    try: return float(v) if v not in (None, "") else None
    except (TypeError, ValueError): return None

# Séances sans météo pertinente : indoor (piloté en salle, la température ambiante ne dit rien
# de l'effort de la façon dont la chaleur extérieure le fait).
INDOOR = {"treadmill_running","indoor_cycling","virtual_ride","virtual_run"}

FTP, THR_PACE_RUN, CSS_SEC = _f("FTP_WATTS",235), _f("THR_PACE_RUN_MIN_KM",4.333), _f("CSS_SEC_PER_100M",105)
LTHR, HR_MAX = _f("LTHR",174), _f("HR_MAX",186)
RUN  = {"running","trail_running","treadmill_running","track_running","virtual_run"}
RIDE = {"cycling","road_biking","gravel_cycling","mountain_biking","indoor_cycling","virtual_ride"}
SWIM = {"lap_swimming","open_water_swimming","swimming"}
OPEN_WATER = {"open_water_swimming"}      # eau libre : ni allure ni FC (courant, vent, houle)
def disc(s): return "bike" if s in RIDE else "run" if s in RUN else "swim" if s in SWIM else None
def is_open_water(s): return s in OPEN_WATER

# Plan (mêmes valeurs que l'afficheur) — courbe D34 validée 31/07/2026 (moy ~10 h, pic 16 h S46)
WK=[4,4,4,4, 6,7,
    7.5,5,8,8.5,9,5.5,9,9.5,10,6,
    10,10.5,11,6.5,10.5,11,11.5,7,11,12,
    12,7,11,11.5,12,7.5,12.5,
    12,13,8,12.5,13.5,14,8,13,14.5,15.5,8.5,14,16,13,9,
    10,8,6,4]
PLAN_START = date(2026,6,29)

def num(x):
    try:
        v=float(x); return None if math.isnan(v) else v
    except (TypeError, ValueError): return None

def if_ef(row):
    """(IF, EF, discipline). Règle natation (définitive, cf. D11) : JAMAIS la FC en nage
    (capteur poignet non fiable sous l'eau) → pas d'EF. Eau libre : pas non plus d'allure
    (courant, vent, houle faussent la vitesse) → aucune intensité. Piscine : IF sur l'allure
    vs CSS uniquement."""
    sport = str(row.get("sport_type",""))
    d = disc(sport)
    if d is None:
        return None, None, d
    if d == "swim":
        if is_open_water(sport):
            return None, None, d                      # eau libre : rien à analyser
        spd = num(row.get("avg_speed_kmh"))
        if not spd or CSS_SEC <= 0:
            return None, None, d
        return spd / (3.6*(100.0/CSS_SEC)), None, d    # piscine : allure seule, jamais la FC
    hr = num(row.get("avg_hr"))
    if hr is None or hr <= 80: return None, None, d
    if d == "bike":
        p = num(row.get("np_watts")) or num(row.get("avg_watts"))
        if not p or FTP <= 0: return None, None, d
        return p/FTP, p/hr, d
    spd = num(row.get("avg_speed_kmh"))
    if not spd: return None, None, d
    return spd/(60.0/THR_PACE_RUN), spd/hr, d

def band(IFv, hr_ratio):
    if IFv >= 1.06 or hr_ratio >= 1.02: return "VO2"
    if IFv >= 0.91 or hr_ratio >= 0.98: return "HIIT"
    if IFv >= 0.80 or hr_ratio >= 0.92: return "MIIT"
    return "LIT"

def prescribed_for(d_date, sport_disc, factor):
    wd = d_date.weekday()  # 0=lundi
    plan = {
        1: [("bike",4*factor,"sortie longue"), ("run",1*factor,"footing")],
        3: [("run",0.75,"tapis côtes / footing"), ("bike",0.75,"home-trainer HIIT/récup")],
        5: [("swim",1,"nage"), ("bike",3*factor,"sortie"), ("run",1*factor,"footing")],
        6: [("bike",2*factor,"sortie"), ("run",2*factor,"sortie longue")],
    }.get(wd, [])
    for disc_p, h, label in plan:
        if disc_p == sport_disc:
            return {"discipline": disc_p, "duree_prevue_h": round(h,2), "type": label}
    return None

def load(name):
    p = DATA/name
    if not p.exists(): return None
    try:
        df = pd.read_csv(p); return df if not df.empty else None
    except Exception: return None

def main():
    acts = load("activities.csv")
    if acts is None: sys.exit("❌ data/activities.csv introuvable/vide.")
    acts["day"] = pd.to_datetime(acts["start_date_local"].astype(str).str[:10], errors="coerce")
    acts = acts.dropna(subset=["day"]).sort_values("day").reset_index(drop=True)

    aid = sys.argv[1] if len(sys.argv) > 1 else str(acts.iloc[-1]["id"])
    row = acts[acts["id"].astype(str) == aid]
    if row.empty: sys.exit(f"❌ Séance {aid} absente de activities.csv.")
    row = row.iloc[-1]
    d_date = row["day"].date()
    IFv, ef, d = if_ef(row)
    hr = num(row.get("avg_hr"))
    sport = str(row.get("sport_type",""))
    if d == "swim":
        # Natation : bande jamais fondée sur la FC. Eau libre : pas de bande du tout.
        bnd = band(IFv, 0) if (IFv is not None and not is_open_water(sport)) else None
    elif d:
        bnd = band(IFv or 0, (hr/LTHR) if hr else 0)
    else:
        bnd = None

    # prévu vs réalisé
    wk = max(1, min(52, (d_date - PLAN_START).days//7 + 1))
    factor = max(0.4, min(1.1, WK[wk-1]/16))
    prescribed = prescribed_for(d_date, d, factor) if d else None
    realized = {"duree_h": num(row.get("moving_time_h")), "distance_km": num(row.get("distance_km")),
                "avg_hr": hr, "max_hr": num(row.get("max_hr")), "np_watts": num(row.get("np_watts")),
                "avg_watts": num(row.get("avg_watts")), "avg_speed_kmh": num(row.get("avg_speed_kmh")),
                "deniv_m": num(row.get("elevation_gain_m"))}

    # efficience vs base 28 j de la même bande (hors VO2)
    eff = None
    if d and bnd and bnd != "VO2" and ef:
        recs = []
        for _, r in acts.iterrows():
            i2, e2, d2 = if_ef(r)
            h2 = num(r.get("avg_hr"))
            if e2 and d2:
                recs.append({"day": r["day"], "disc": d2, "band": band(i2 or 0, (h2/LTHR) if h2 else 0),
                             "ef": e2, "temp": num(r.get("temp_c"))})
        df = pd.DataFrame(recs)
        base = df[(df.disc==d)&(df.band==bnd)&(df.day<row["day"])&(df.day>=row["day"]-pd.Timedelta(days=28))]
        if len(base) >= 3:
            b = base["ef"].median()
            temps = df["temp"].dropna()
            ef_obs, heat = ef, False
            t = num(row.get("temp_c"))
            if t is not None and len(temps) >= 3:
                bt = float(temps.median()); f = thermal_ef_factor(t, bt)
                if f > 1: ef_obs, heat = ef*f, True
            eff = {"ef": round(ef,4), "base_bande": round(b,4),
                   "delta_pct": round((ef_obs/b-1)*100,1), "corrige_chaleur": heat, "n_base": len(base)}
    vo2 = None
    if bnd == "VO2" and d != "swim" and realized["max_hr"]:      # pas de jugement FC en nat
        vo2 = {"fc_max": realized["max_hr"], "pct_fcmax": round(realized["max_hr"]/HR_MAX*100)}

    # analyse d'intervalles si disponible
    ivf = DATA/f"intervals_{aid}.json"
    intervals = json.loads(ivf.read_text()) if ivf.exists() else None
    if intervals:
        intervals = {k: intervals.get(k) for k in ("n_work","avg_work_hr","fade_pct","hr_drift_bpm","verdict")}
        if d == "swim":
            intervals["avg_work_hr"] = None            # jamais la FC en nage
            intervals["hr_drift_bpm"] = None
            if is_open_water(sport):                   # eau libre : pas non plus d'allure
                intervals["fade_pct"] = None
                intervals["verdict"] = ("eau libre — loguée en volume/technique ; ni allure ni FC "
                                        "analysées (bruit courant/vent, FC poignet peu fiable)")

    # contexte charge + forme
    fit = load("fitness.csv"); well = load("wellness.csv")
    ctx = {}
    if fit is not None:
        fr = fit[pd.to_datetime(fit["date"]).dt.date == d_date]
        if not fr.empty:
            ctx = {k: num(fr.iloc[-1].get(k)) for k in ("ctl","atl","tsb")}
    welld = {}
    if well is not None:
        wr = well[pd.to_datetime(well["date"]).dt.date == d_date]
        if not wr.empty:
            welld = {"sommeil_h": num(wr.iloc[-1].get("sleep_hours")), "hrv": num(wr.iloc[-1].get("hrv_weekly_avg")),
                     "fc_repos": num(wr.iloc[-1].get("resting_hr"))}

    # météo — capteur montre si dispo ; sinon on va chercher l'archive Open-Meteo à la position
    # de la séance (GPS si présent, sinon repli HOME_LAT/HOME_LON dans .env), sauf en indoor.
    t = num(row.get("temp_c")); source = "capteur" if t is not None else None
    if t is None and str(row.get("sport_type","")).lower() not in INDOOR:
        lat = num(row.get("lat")) if "lat" in acts else None
        lon = num(row.get("lon")) if "lon" in acts else None
        lat = lat if lat is not None else _fopt("HOME_LAT")
        lon = lon if lon is not None else _fopt("HOME_LON")
        when = str(row.get("start_date_local") or "")
        if lat is not None and lon is not None and when:
            ft = fetch_temp(lat, lon, when)          # best effort : None si hors-ligne
            if ft is not None:
                t, source = round(ft, 1), "archive"
    baseT = None
    if "temp_c" in acts:
        baseT = temp_baseline(acts["temp_c"].apply(num).dropna().tolist())
    weather = {"temp_c": t, "source": source, "habituelle_c": baseT, "diagnostic": heat_diagnosis(t, baseT)}

    # ressenti
    subj = load("subjective.csv"); ress = None
    if subj is not None and "date" in subj:
        sr = subj[pd.to_datetime(subj["date"]).dt.date == d_date]
        if not sr.empty:
            ress = {k: num(sr.iloc[-1].get(k)) for k in ("rpe","rpe_attendu","feel")}

    # verdict "règles" (repli)
    bits = []
    if eff: bits.append(("efficience " + (f"+{eff['delta_pct']}%" if eff['delta_pct']>=0 else f"{eff['delta_pct']}%") +
                         f" vs base {bnd}" + (" (chaleur corrigée)" if eff['corrige_chaleur'] else "")))
    if vo2: bits.append(f"VO2 : FC max {vo2['pct_fcmax']}% FCmax")
    if intervals and intervals.get("fade_pct") is not None:
        if intervals["fade_pct"] > 4: bits.append(f"décrochage {intervals['fade_pct']}%")
    if weather["diagnostic"]: bits.append(weather["diagnostic"])
    if ctx.get("tsb") is not None: bits.append(f"TSB {ctx['tsb']:+.0f}")
    rule_verdict = " · ".join(bits) if bits else "séance enregistrée, contexte limité."

    dossier = {
        "activity_id": aid, "date": d_date.isoformat(), "discipline": d, "bande": bnd,
        "eau_libre": is_open_water(sport),
        "nom": str(row.get("name","")), "prevu": prescribed, "realise": realized,
        "efficience": eff, "vo2": vo2, "intervalles": intervals,
        "charge": ctx, "forme": welld, "meteo": weather, "ressenti": ress,
        "verdict_regles": rule_verdict,
    }

    prompt = (
        "Tu es un coach de triathlon expérimenté. Voici le dossier chiffré d'une séance de ton athlète "
        "(préparation Ironman). Rédige 1 à 2 phrases clés, ton d'entraîneur : précis, utile, sans baratin. "
        "Explique comment la séance s'est passée et ce qu'elle dit de la forme, en tenant compte de la météo, "
        "du prévu/réalisé et de la fatigue. Si une donnée est absente, ne l'invente pas.\n\n"
        + json.dumps(dossier, ensure_ascii=False, indent=2)
    )
    dossier["prompt_ia"] = prompt

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA/f"session_report_{aid}.json").write_text(json.dumps(dossier, ensure_ascii=False, indent=2))

    print(f"📋 Dossier séance {aid} — {d_date} · {d or '?'}/{bnd or '?'}")
    if prescribed: print(f"   Prévu : {prescribed['type']} {prescribed['duree_prevue_h']} h ({d})")
    print(f"   Réalisé : {realized['duree_h']} h · {realized['distance_km']} km · FC moy {realized['avg_hr']}")
    print(f"   Verdict (règles) : {rule_verdict}")
    print(f"   → data/session_report_{aid}.json (contient le prompt IA prêt à envoyer)")

    # Mémoire : toute séance analysée entre au journal. Idempotent (ne double jamais une entrée).
    try:
        import journal
        journal.cmd_log(aid)
        journal.cmd_render()
    except Exception as e:
        print(f"⚠️  Journalisation impossible : {e}. Rattrapage : python3 journal.py sync", file=sys.stderr)

if __name__ == "__main__":
    main()
