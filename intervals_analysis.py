#!/usr/bin/env python3
"""
intervals_analysis.py — Analyse fine d'une séance à intervalles (via intervals.icu).

Isole les répétitions TRAVAILLÉES (ignore les récups) et calcule :
  • la FC moyenne de chaque rep (signal propre, hors récupérations)
  • la dérive cardiaque (la FC monte-t-elle rep après rep à effort constant ?)
  • le décrochage (puissance/allure qui chute sur les dernières reps = séance non tenue)
  • la régularité (coefficient de variation entre reps)

Usage :
    python3 intervals_analysis.py            # analyse la dernière activité
    python3 intervals_analysis.py i123456    # analyse une activité précise
Prérequis : pip install requests ; INTERVALS_API_KEY + INTERVALS_ATHLETE_ID dans .env
"""
from __future__ import annotations
import os, sys, json, statistics as st
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
BASE = "https://intervals.icu/api/v1"

# Plancher d'effort (fraction de la médiane des reps) en-dessous duquel un lap est
# considéré comme échauffement / retour au calme / km partiel — pas du travail soutenu.
COOLDOWN_FLOOR = 0.6

def api(path, params=None):
    if not (API_KEY and ATHLETE_ID):
        sys.exit("❌ INTERVALS_API_KEY / INTERVALS_ATHLETE_ID manquants dans .env")
    r = requests.get(f"{BASE}{path}", params=params, auth=("API_KEY", API_KEY), timeout=40)
    if r.status_code == 401: sys.exit("❌ 401 : clé API / Athlete ID incorrect.")
    r.raise_for_status()
    return r.json()

def latest_activity_id():
    since = (date.today() - timedelta(days=30)).isoformat()
    acts = api(f"/athlete/{ATHLETE_ID}/activities", {"oldest": since, "newest": date.today().isoformat()})
    if not acts: sys.exit("ℹ️  Aucune activité récente.")
    acts.sort(key=lambda a: a.get("start_date_local", ""))
    return acts[-1]["id"]

def pace(speed_ms):
    if not speed_ms or speed_ms <= 0: return None
    s = (1000.0 / speed_ms)
    return f"{int(s//60)}:{int(s%60):02d}/km"

def cv(xs):
    xs = [x for x in xs if x]
    if len(xs) < 2: return None
    m = st.mean(xs)
    return (st.pstdev(xs) / m * 100) if m else None

def analyze(intervals):
    """Analyse pure (testable hors réseau). `intervals` = liste de laps intervals.icu."""
    work = [iv for iv in intervals if str(iv.get("type", "")).upper() == "WORK"]
    if len(work) < 2:
        return {"is_interval": False, "n_work": len(work)}

    reps = []
    for i, iv in enumerate(work, 1):
        reps.append({
            "n": i,
            "hr":  iv.get("average_heartrate"),
            "max_hr": iv.get("max_heartrate"),
            "watts": iv.get("average_watts"),
            "speed": iv.get("average_speed"),   # m/s
            "dist": iv.get("distance"),
            "time": iv.get("moving_time") or iv.get("elapsed_time"),
        })

    # métrique primaire d'effort : puissance si dispo, sinon vitesse
    has_power = sum(1 for r in reps if r["watts"]) >= len(reps) - 1
    prim = "watts" if has_power else "speed"

    # Écarte les laps qui ne sont PAS du travail soutenu : échauffement, retour au
    # calme, km partiel. Sur une séance CONTINUE auto-découpée en laps par intervals.icu
    # (ex. sortie Z2), le retour au calme au pas (~14:00/km) était compté comme une "rep"
    # et effondrait la 2e moitié → faux décrochage. Plancher : effort < 60 % de la
    # médiane = marche/récup. On NE masque PAS la fatigue (§5) : une rep simplement cuite
    # reste au-dessus du plancher (un athlète fatigué ralentit, il ne divise pas son
    # allure par deux), et les laps écartés restent affichés dans le détail.
    prim_vals = [r[prim] for r in reps if r[prim]]
    floor = (st.median(prim_vals) * COOLDOWN_FLOOR) if prim_vals else None
    for r in reps:
        r["sustained"] = bool(r[prim]) and (floor is None or r[prim] >= floor)
    sustained = [r for r in reps if r["sustained"]]
    if len(sustained) < 2:               # garde-fou : trim trop agressif → on garde tout
        for r in reps: r["sustained"] = True
        sustained = reps
    n_trimmed = len(reps) - len(sustained)

    vals = [r[prim] for r in sustained if r[prim]]
    hrs  = [r["hr"] for r in sustained if r["hr"]]

    half = max(1, len(sustained) // 2)
    def mean_of(key, sl):
        xs = [r[key] for r in sl if r[key]]
        return st.mean(xs) if xs else None
    first_p, last_p = mean_of(prim, sustained[:half]), mean_of(prim, sustained[-half:])
    first_hr, last_hr = mean_of("hr", sustained[:half]), mean_of("hr", sustained[-half:])

    fade = None      # % de chute de l'effort sur la 2e moitié (positif = décrochage)
    if first_p and last_p:
        fade = (first_p - last_p) / first_p * 100
    hr_drift = (last_hr - first_hr) if (first_hr and last_hr) else None

    return {
        "is_interval": True, "n_work": len(sustained),
        "n_reps_raw": len(reps), "n_trimmed": n_trimmed,
        "primary": prim, "reps": reps,
        "avg_work_hr": round(st.mean(hrs), 1) if hrs else None,
        "cv_effort_pct": round(cv(vals), 1) if cv(vals) is not None else None,
        "fade_pct": round(fade, 1) if fade is not None else None,
        "hr_drift_bpm": round(hr_drift, 1) if hr_drift is not None else None,
    }

def verdict(a):
    """Petit verdict fondé sur des règles (la couche IA viendra enrichir plus tard)."""
    if not a.get("is_interval"):
        return "Pas une séance à intervalles (ou reps non détectées)."
    msgs = []
    if a["fade_pct"] is not None:
        if a["fade_pct"] > 4:   msgs.append(f"décrochage de {a['fade_pct']:.0f}% en 2e moitié — séance trop dure ou fatigue")
        elif a["fade_pct"] < -2: msgs.append("negative split — reps montées en puissance, bon signe")
        else:                    msgs.append("effort bien tenu d'un bout à l'autre")
    # ⚠️ PAS de verdict de « dérive cardiaque » ici. Cette valeur est l'écart de FC entre la
    # 1ʳᵉ et la 2ᵉ moitié des reps, SANS correction de l'allure ni du relief : dès que
    # l'allure change en cours de séance (negative split, terrain vallonné), elle mesure
    # l'effet allure et l'étiquette « fatigue ». Cas réel : « +24 bpm » annoncé le 19/07/2026
    # sur un simple negative split, et « +18,6 bpm » le 05/07 — deux faux signaux de fatigue.
    # La dérive honnête (effet allure retiré, allures appariées) est calculée par
    # hr_analysis.py. On garde le chiffre brut en sortie chiffrée, mais on n'en tire plus
    # de conclusion physiologique. Cf. journal/decisions.md D21.
    if a["hr_drift_bpm"] is not None and a["hr_drift_bpm"] > 5:
        msgs.append(f"FC +{a['hr_drift_bpm']:.0f} bpm entre 1ʳᵉ et 2ᵉ moitié "
                    f"(brut, NON corrigé de l'allure — voir hr_analysis.py pour la vraie dérive)")
    if a["cv_effort_pct"] is not None and a["cv_effort_pct"] < 3:
        msgs.append("reps très régulières")
    return " ; ".join(msgs) if msgs else "reps analysées."

def main():
    aid = sys.argv[1] if len(sys.argv) > 1 else latest_activity_id()
    data = api(f"/activity/{aid}/intervals")
    intervals = data.get("icu_intervals", data if isinstance(data, list) else [])
    a = analyze(intervals)
    print(f"🔎 Activité {aid}")
    if not a["is_interval"]:
        print("   Pas de répétitions travaillées détectées (séance continue ?)."); return
    note = (f"  ({a['n_trimmed']} lap échauffement/retour au calme écarté"
            f"{'s' if a['n_trimmed'] > 1 else ''})") if a.get("n_trimmed") else ""
    print(f"   {a['n_work']} reps de travail{note} · FC moyenne travail {a['avg_work_hr']} bpm")
    for r in a["reps"]:
        eff = f"{round(r['watts'])} W" if r["watts"] else (pace(r["speed"]) or "—")
        flag = "" if r.get("sustained", True) else "   ⤷ écartée (échauff./retour calme)"
        print(f"     rep {r['n']:>2} : {eff:<10} FC {r['hr'] or '—'}  (max {r['max_hr'] or '—'}){flag}")
    print(f"   Régularité (CV) : {a['cv_effort_pct']}%   Décrochage : {a['fade_pct']}%   "
          f"Dérive FC : {a['hr_drift_bpm']} bpm")
    print(f"   → {verdict(a)}")
    Path("data").mkdir(exist_ok=True)
    Path(f"data/intervals_{aid}.json").write_text(json.dumps({**a, "verdict": verdict(a)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ Erreur API intervals.icu : {e}")
