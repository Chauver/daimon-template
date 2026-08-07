#!/usr/bin/env python3
"""
fitness_model.py — Calcul CTL / ATL / TSB à partir de data/activities.csv

- Calcule le TSS de chaque séance selon la meilleure méthode disponible :
  vélo (puissance) → TSS ; course → rTSS ; natation → sTSS ; sinon hrTSS (TRIMP).
- Agrège par jour (somme des disciplines), sur un calendrier quotidien continu.
- Calcule CTL (42 j), ATL (7 j) et TSB par lissage exponentiel.
- Écrit data/fitness.csv (date, tss, tss_bike/run/swim, ctl, atl, tsb) et affiche l'état du jour.

Prérequis : pip install pandas
Renseigne tes seuils ci-dessous (ou via .env).
"""
from __future__ import annotations
import os, sys, math
from pathlib import Path
from datetime import date, timedelta

try:
    import pandas as pd
except ImportError:
    sys.exit("❌ pip install pandas")

def _load_dotenv(p=".env"):
    f = Path(p)
    if not f.exists(): return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

def envf(name, default): 
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return float(default)

# ======================= SEUILS DE L'ATHLÈTE (à personnaliser) =======================
FTP          = envf("FTP_WATTS", 235)          # FTP horaire vélo (W) — ACTUELLE, à réactualiser (pic ~289)
THR_PACE_RUN = envf("THR_PACE_RUN_MIN_KM", 4.333) # allure seuil course (min/km) — 4:20, à réactualiser
CSS_SEC_100  = envf("CSS_SEC_PER_100M", 105)    # vitesse critique nat (s/100 m) — PROVISOIRE, test à 6 sem.
SWIM_OW_IF   = envf("SWIM_OW_IF", 0.70)          # intensité SUPPOSÉE d'une nage EAU LIBRE (aisance/Z2) :
                                                 # l'allure y est ininterprétable (courant/vent) et la FC
                                                 # non fiable (D11) → charge estimée à la durée. À relever
                                                 # dans .env si une séance eau libre est réellement dure.
LTHR         = envf("LTHR", 174)                # FC seuil course (bpm) — à affiner par CLM 30 min
HR_MAX       = envf("HR_MAX", 186)              # FC max course (bpm) — sert au hrTSS (tapis, nat)
HR_REST      = envf("HR_REST", 50)              # FC repos (bpm)
SEX          = os.getenv("SEX", "M").upper()    # "M" ou "F" (coefficients TRIMP)
DATA_DIR     = Path(os.getenv("GARMIN_DATA_DIR", "data"))
# ====================================================================================

TAU_C, TAU_A = 42.0, 7.0
ALPHA_C = 1 - math.exp(-1/TAU_C)
ALPHA_A = 1 - math.exp(-1/TAU_A)
TRIMP_A, TRIMP_B = (0.64, 1.92) if SEX == "M" else (0.86, 1.67)

# ---- Vigilance volume COURSE (protection tendon d'Achille / voûte plantaire) ----
# Plafond hebdomadaire (km) selon la semaine de prépa (début 29/06/2026).
PLAN_START = date(2026, 6, 29)
def run_cap(week: int):
    """Renvoie (seuil_vigilance, plafond_dur) en km pour la semaine de prépa donnée.
    Relevé à 40 km le 19/07/2026 (cf. decisions.md D20) : l'athlète court désormais
    beaucoup plus lentement qu'au moment où le 35 avait été fixé."""
    if week <= 25:  hard = 40     # 1re moitié : point faible, risque blessure élevé
    elif week <= 33: hard = 45    # remise en charge progressive
    elif week <= 41: hard = 48
    else:            hard = 50    # jamais au-delà de 50
    return hard - 5, hard


# ---- Second plafond : TEMPS DE COURSE hebdomadaire ----
# Le km seul est un mauvais proxy de la charge tendineuse : à 6:16/km, 35 km font 3h39
# contre 2h43 à 4:40/km — 34 % de temps sous contrainte en plus pour la même distance.
# Le plafond en TEMPS suit automatiquement l'allure ; celui en km, non. Le premier des
# deux qui bloque, bloque.
def run_cap_time_h(week: int):
    if week <= 25:   return 4.0
    elif week <= 33: return 4.5
    elif week <= 41: return 5.0
    else:            return 5.5

RUN  = {"running","trail_running","treadmill_running","track_running","virtual_run"}
RIDE = {"cycling","road_biking","gravel_cycling","mountain_biking","indoor_cycling","virtual_ride"}
SWIM = {"lap_swimming","open_water_swimming","swimming"}
OPEN_WATER = {"open_water_swimming"}
def is_open_water(s): return s in OPEN_WATER

def num(x):
    try:
        v = float(x); return v if not math.isnan(v) else None
    except (TypeError, ValueError): return None

def trimp(hr_avg, minutes):
    if not hr_avg or HR_MAX <= HR_REST: return None
    x = max(0.0, min(1.0, (hr_avg - HR_REST) / (HR_MAX - HR_REST)))
    return minutes * x * TRIMP_A * math.exp(TRIMP_B * x)

def hr_tss(hr_avg, hours):
    t = trimp(hr_avg, hours * 60)
    ref = trimp(LTHR, 60)  # 1 h au seuil ≈ 100
    if not t or not ref: return None
    return 100 * t / ref

def session_tss(row):
    sport = str(row.get("sport_type", "")).strip()
    hours = num(row.get("moving_time_h")) or 0
    if hours <= 0: return None, None
    hr    = num(row.get("avg_hr"))
    disc  = "bike" if sport in RIDE else "run" if sport in RUN else "swim" if sport in SWIM else "autre"

    # 1) vélo par puissance
    if disc == "bike":
        npw = num(row.get("np_watts")) or num(row.get("avg_watts"))
        if npw and FTP > 0:
            return 100 * hours * (npw / FTP) ** 2, disc
    # 2) course : tapis (côtes) → FC ; extérieur (plat) → allure
    if disc == "run":
        if sport == "treadmill_running":
            val = hr_tss(hr, hours)
            if val is not None:
                return val, disc
        spd = num(row.get("avg_speed_kmh"))
        if spd and THR_PACE_RUN > 0:
            thr_spd = 60.0 / THR_PACE_RUN          # km/h au seuil
            return 100 * hours * (spd / thr_spd) ** 2, disc
    # 3) natation
    if disc == "swim":
        if is_open_water(sport):
            # Eau libre : allure ininterprétable (courant/vent) + FC non fiable (D11) → on
            # n'estime PAS l'intensité par la vitesse (les palmes et un CSS provisoire la
            # gonflent). Charge d'endurance forfaitaire à la durée, à l'intensité supposée.
            return 100 * hours * SWIM_OW_IF ** 2, disc
        spd = num(row.get("avg_speed_kmh"))          # piscine : splits/allure vs CSS (D11)
        if spd and CSS_SEC_100 > 0:
            css_spd = 3.6 * (100.0 / CSS_SEC_100)     # km/h à CSS
            return 100 * hours * (spd / css_spd) ** 2, disc
    # 4) repli FC (hrTSS)
    val = hr_tss(hr, hours)
    return (val, disc) if val is not None else (None, disc)

def main():
    path = DATA_DIR / "activities.csv"
    if not path.exists(): sys.exit(f"❌ Fichier introuvable : {path} (lance d'abord la synchro)")
    df = pd.read_csv(path)
    df["day"] = pd.to_datetime(df["start_date_local"].astype(str).str[:10], errors="coerce").dt.date
    df = df.dropna(subset=["day"])

    # TSS par séance + discipline
    recs = []
    for _, r in df.iterrows():
        tss, disc = session_tss(r)
        if tss is not None:
            recs.append({"day": r["day"], "disc": disc, "tss": tss})
    if not recs: sys.exit("❌ Aucun TSS calculable (vérifie tes seuils et tes données).")
    s = pd.DataFrame(recs)

    # agrégation quotidienne, calendrier continu
    daily = s.pivot_table(index="day", columns="disc", values="tss", aggfunc="sum").fillna(0)
    for c in ("bike", "run", "swim"):
        if c not in daily.columns: daily[c] = 0.0
    daily["tss"] = daily[["bike", "run", "swim"] + [c for c in daily.columns if c == "autre"]].sum(axis=1)
    full = pd.date_range(min(daily.index), date.today(), freq="D").date
    daily = daily.reindex(full, fill_value=0.0)

    # CTL / ATL / TSB
    ctl = atl = 0.0
    rows = []
    for d, row in daily.iterrows():
        tsb = ctl - atl                       # veille
        ctl += ALPHA_C * (row["tss"] - ctl)
        atl += ALPHA_A * (row["tss"] - atl)
        rows.append({"date": d, "tss": round(row["tss"], 1),
                     "tss_bike": round(row["bike"], 1), "tss_run": round(row["run"], 1),
                     "tss_swim": round(row["swim"], 1),
                     "ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(tsb, 1)})
    out = pd.DataFrame(rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(DATA_DIR / "fitness.csv", index=False)

    # ---- Vigilance volume course : km hebdo réels vs plafond ----
    runs = df[df["sport_type"].isin(RUN)].copy()
    vol_rows = []
    if not runs.empty:
        runs["wk"] = runs["day"].apply(lambda d: (d - PLAN_START).days // 7 + 1)
        wk_km = runs.groupby("wk")["distance_km"].sum()
        wk_h = runs.groupby("wk")["moving_time_h"].sum()
        for wk, km in wk_km.items():
            if wk < 1:
                continue
            soft, hard = run_cap(wk)
            h = float(wk_h.get(wk, 0.0))
            cap_h = run_cap_time_h(wk)
            # DEUX plafonds : distance ET temps sous contrainte. Le plus contraignant l'emporte.
            st_km = "vert" if km <= soft else "orange" if km <= hard else "ROUGE"
            st_h = "vert" if h <= cap_h * 0.87 else "orange" if h <= cap_h else "ROUGE"
            rank = {"vert": 0, "orange": 1, "ROUGE": 2}
            status = max((st_km, st_h), key=lambda s: rank[s])
            limitant = "temps" if rank[st_h] > rank[st_km] else \
                       "km" if rank[st_km] > rank[st_h] else "les deux"
            vol_rows.append({"semaine": int(wk), "run_km": round(km, 1),
                             "seuil": soft, "plafond": hard,
                             "run_h": round(h, 2), "plafond_h": cap_h,
                             "statut": status, "limitant": limitant})
    if vol_rows:
        vol = pd.DataFrame(vol_rows).sort_values("semaine")
        vol.to_csv(DATA_DIR / "run_volume.csv", index=False)
        cur = vol.iloc[-1]
        flag = {"vert": "🟢", "orange": "🟠", "ROUGE": "🔴"}[cur["statut"]]
        print(f"🦵 Course S{cur['semaine']} : {cur['run_km']:.0f} km "
              f"(vigilance {cur['seuil']} / plafond {cur['plafond']} km) {flag}")
        if cur["statut"] == "ROUGE":
            print("   ⚠️  Plafond course dépassé — risque tendon d'Achille / voûte plantaire. Alléger la course.")

    last = out.iloc[-1]
    tsb = last["tsb"]
    verdict = ("frais / affûté" if tsb > 5 else "en forme, charge équilibrée" if tsb > -10
               else "fatigue de charge (bloc en cours)" if tsb > -30 else "fatigue marquée — vigilance")
    print(f"✅ {len(out)} jours écrits → {DATA_DIR/'fitness.csv'}")
    print(f"   Aujourd'hui  CTL {last['ctl']:.0f} (fitness) · ATL {last['atl']:.0f} (fatigue) · "
          f"TSB {tsb:+.0f} → {verdict}")
    if len(out) < 42:
        print("   ⚠️  Moins de 6 semaines de données : CTL encore en phase de chauffe, interpréter avec prudence.")

if __name__ == "__main__":
    main()
