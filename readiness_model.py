#!/usr/bin/env python3
"""
readiness_model.py — Moteur de forme & risque blessure de l'agent coach.

Lit les fichiers produits par le pipeline et calcule :
  • un INDICE DE FORME composite (0–100) → Jour Vert / Orange / Rouge
  • un COMPTEUR BLESSURE séparé (vert / orange / rouge)
Écrit data/readiness.json (consommé ensuite par la page d'accueil).

Pondération de l'indice de forme (cf. hiérarchie définie : TSS d'abord, ressenti et FC
ensuite, HRV en dernier) — chaque composante manquante est ignorée et les poids sont
renormalisés sur celles disponibles :
    TSB (forme)                0.35
    Réponse FC à l'effort      0.25
    Ressenti (RPE + feel)      0.25
    Sommeil                    0.10
    HRV                        0.05

Entrées (toutes optionnelles, dégradation gracieuse) :
    data/fitness.csv     (date, tss, ctl, atl, tsb)          ← fitness_model.py
    data/wellness.csv    (date, resting_hr, hrv_weekly_avg, sleep_hours, sleep_score)
    data/run_volume.csv  (semaine, run_km, seuil, plafond, statut)
    data/activities.csv  (pour l'efficience FC : puissance/allure vs FC)
    data/subjective.csv  (date, rpe, rpe_attendu, feel, gene_msk)   ← saisie manuelle
"""
from __future__ import annotations
import json, math, os, sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    import pandas as pd
except ImportError:
    sys.exit("❌ pip install pandas")

DATA_DIR = Path(os.getenv("GARMIN_DATA_DIR", "data"))
WEIGHTS  = {"tsb": 0.35, "hr": 0.25, "subj": 0.25, "sleep": 0.10, "hrv": 0.05}
clamp = lambda v, a=0, b=100: max(a, min(b, v))

# --- Seuils de l'athlète (mêmes valeurs que fitness_model, surchargeables via .env) ---
def _f(n, d):
    try: return float(os.getenv(n, d))
    except (TypeError, ValueError): return float(d)
FTP          = _f("FTP_WATTS", 235)
THR_PACE_RUN = _f("THR_PACE_RUN_MIN_KM", 4.333)
CSS_SEC      = _f("CSS_SEC_PER_100M", 105)
LTHR         = _f("LTHR", 174)
HR_MAX       = _f("HR_MAX", 186)
# Bandes d'intensité par facteur d'intensité (IF), avec la FC comme garde-fou (utile au tapis).
# Seuils FC recalés le 19/07/2026 sur 4 courses réelles (cf. journal/decisions.md D17) :
#   l'ancienne frontière LIT/MIIT à 0.92 (160 bpm) classait l'intensité de course IRONMAN de
#   l'athlète (154-156 bpm, reproductible sur 3 marathons) en « endurance facile ».
#   Nouvelles bornes en % du seuil : MIIT ≥ 0.87 (151) · HIIT ≥ 0.95 (165) · VO2 ≥ 1.01 (176).
IF_LIT_MAX, IF_MIIT_MAX, IF_VO2_MIN = 0.80, 0.91, 1.06
HR_MIIT_MIN, HR_HIIT_MIN, HR_VO2_MIN = 0.87, 0.95, 1.01
def band(IFv, hr_ratio):
    if IFv >= IF_VO2_MIN  or hr_ratio >= HR_VO2_MIN:  return "VO2"   # supra-seuil
    if IFv >= IF_MIIT_MAX or hr_ratio >= HR_HIIT_MIN: return "HIIT"  # seuil (≈ allure Half)
    if IFv >= IF_LIT_MAX  or hr_ratio >= HR_MIIT_MIN: return "MIIT"  # allure spé Ironman
    return "LIT"                                                     # endurance

RUN  = {"running","trail_running","treadmill_running","track_running","virtual_run"}
RIDE = {"cycling","road_biking","gravel_cycling","mountain_biking","indoor_cycling","virtual_ride"}
SWIM = {"lap_swimming","open_water_swimming","swimming"}
def _disc(s):
    return "bike" if s in RIDE else "run" if s in RUN else "swim" if s in SWIM else None

def load(name):
    p = DATA_DIR / name
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
        return df if not df.empty else None
    except Exception:
        return None

def num(s):
    return pd.to_numeric(s, errors="coerce")

def num1(x):
    v = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(v) else float(v)

# ------------------------------------------------------------------ sous-scores
def score_tsb(fit):
    if fit is None or "tsb" not in fit: return None, None
    tsb = num(fit["tsb"]).dropna()
    if tsb.empty: return None, None
    v = float(tsb.iloc[-1])
    return clamp(55 + v * 1.5), round(v, 1)      # +15→77 · 0→55 · -20→25

def _if_ef(row):
    """Renvoie (facteur d'intensité, efficience EF, discipline) ou (None, None, None)."""
    disc = _disc(str(row.get("sport_type", "")))
    hr = pd.to_numeric(row.get("avg_hr"), errors="coerce")
    if disc is None or pd.isna(hr) or hr <= 80:
        return None, None, None
    if disc == "bike":
        p = pd.to_numeric(row.get("np_watts"), errors="coerce")
        if pd.isna(p) or p <= 0:
            p = pd.to_numeric(row.get("avg_watts"), errors="coerce")
        if pd.isna(p) or p <= 0 or FTP <= 0:
            return None, None, None
        return p / FTP, p / hr, disc
    spd = pd.to_numeric(row.get("avg_speed_kmh"), errors="coerce")
    if pd.isna(spd) or spd <= 0:
        return None, None, None
    thr = (60.0 / THR_PACE_RUN) if disc == "run" else 3.6 * (100.0 / CSS_SEC)
    return spd / thr, spd / hr, disc

def score_hr(acts):
    """Dernière séance : efficience vs base 28 j de sa MÊME bande (LIT/MIIT/HIIT) et discipline,
    à intensité équivalente. Pour la VO2 (supra-seuil), l'EF n'a pas de sens → on juge la
    capacité à pousser via la FC max atteinte (une FC max qui plafonne bas = fatigue)."""
    if acts is None:
        return None, None
    a = acts.copy()
    a["day"] = pd.to_datetime(a["start_date_local"].astype(str).str[:10], errors="coerce")
    a = a.dropna(subset=["day"]).sort_values("day")
    recs = []
    for _, r in a.iterrows():
        IFv, ef, disc = _if_ef(r)
        if disc is None or disc == "swim":     # natation : jamais la FC (D11) → hors sous-score HR
            continue
        hr = pd.to_numeric(r.get("avg_hr"), errors="coerce")
        if pd.isna(hr) or hr <= 80:
            continue
        recs.append({"day": r["day"], "disc": disc,
                     "band": band(IFv or 0, hr / LTHR), "ef": ef,
                     "max_hr": pd.to_numeric(r.get("max_hr"), errors="coerce"),
                     "temp": pd.to_numeric(r.get("temp_c"), errors="coerce")})
    if not recs:
        return None, None
    df = pd.DataFrame(recs)
    last = df.iloc[-1]

    # --- Régime VO2 : FC max atteinte (capacité à pousser) ---
    if last["band"] == "VO2":
        mhr = last["max_hr"]
        if pd.isna(mhr) or HR_MAX <= 0:
            return None, None
        push = float(mhr) / HR_MAX
        detail = f"{last['disc']}/VO2 : FC max {int(mhr)} ({push*100:.0f}% FCmax)"
        return clamp(50 + (push - 0.95) * 800), detail   # 97%→66 · 93%→34

    # --- LIT / MIIT / HIIT : efficience vs base de la même bande ---
    if last["ef"] is None:
        return None, None
    base = df[(df["disc"] == last["disc"]) & (df["band"] == last["band"])
              & (df["day"] < last["day"]) & (df["day"] >= last["day"] - pd.Timedelta(days=28))]
    if len(base) < 3:
        return None, None
    b = base["ef"].median()
    if not b or b <= 0:
        return None, None

    # Correction thermique : normalise l'EF de la séance chaude vs température habituelle
    ef_obs, heat_note = last["ef"], ""
    temps = df["temp"].dropna()
    if pd.notna(last["temp"]) and len(temps) >= 3:
        base_temp = float(temps.median())
        dev = max(0.0, float(last["temp"]) - base_temp - 3.0)
        eff = min(0.15, 0.006 * dev)     # ~+1 bpm/°C ; coefficient calibrable
        if eff > 0:
            ef_obs = last["ef"] / (1.0 - eff)
            heat_note = f", corrigé chaleur (+{float(last['temp'])-base_temp:.0f}°C)"

    ratio = ef_obs / b
    detail = f"{last['disc']}/{last['band']} vs base 28 j (n={len(base)}) : {(ratio-1)*100:+.1f}%{heat_note}"
    return clamp(50 + (ratio - 1) * 500), detail

# RPE attendu (Borg CR10) — estimé par le coach : difficulté de la séance + durée + forme du jour.
# Littérature : à charge donnée, la RPE monte quand on est fatigué / sous-récupéré / privé de sommeil.
RPE_BASE = {"recup": 2.0, "LIT": 3.0, "MIIT": 5.0, "HIIT": 7.0, "VO2": 9.0}

def rpe_attendu(bande, heures, forme):
    base = RPE_BASE.get(bande, 4.0)
    duree = min(2.0, max(0.0, (heures or 0.0) - 1.0) * 0.7)          # dérive endurance, plafond +2
    fmod = 0.0 if forme is None else (-0.5 if forme >= 70 else 0.5 if forme >= 40 else 1.5)
    return max(1.0, min(10.0, base + duree + fmod))

def score_ressenti(acts, forme_prov):
    """Ressenti (0-100) = sensations (feel : 1 Fort … 5 Faible, donc INVERSÉ) + résidu RPE
    (constaté − attendu). Le RPE attendu est estimé par le coach depuis la difficulté de la
    dernière séance notée et la forme du jour. Formule : 50 + (3−feel)×10 − borne(résidu,±4)×6."""
    if acts is None or "feel" not in acts.columns: return None, None
    a = acts.copy()
    a["date"] = pd.to_datetime(a["start_date_local"].astype(str).str[:10], errors="coerce")
    a = a[pd.to_numeric(a["feel"], errors="coerce").notna()].dropna(subset=["date"]).sort_values("date")
    if a.empty: return None, None
    r = a.iloc[-1]
    feel = float(pd.to_numeric(r.get("feel"), errors="coerce"))     # 1 Fort … 5 Faible
    rpe  = pd.to_numeric(r.get("rpe"), errors="coerce")
    sc = 50.0 + (3.0 - feel) * 10.0                                 # feel bas = bien senti → monte
    detail = f"feel {int(feel)}"
    if pd.notna(rpe):
        IFv, ef, disc = _if_ef(r)
        hr = pd.to_numeric(r.get("avg_hr"), errors="coerce")
        bnd = band(IFv or 0, (hr / LTHR) if (pd.notna(hr) and hr) else 0) if disc else "LIT"
        mt = pd.to_numeric(r.get("moving_time_h"), errors="coerce")
        att = rpe_attendu(bnd, float(mt) if pd.notna(mt) else 0.0, forme_prov)
        resid = float(rpe) - att
        sc -= max(-4.0, min(4.0, resid)) * 6.0                      # plus dur que prévu → baisse
        detail += f" · RPE {int(rpe)} vs attendu {att:.0f}"
    return clamp(sc), detail

def score_sleep(well):
    """Sommeil effectif = nuit + sieste (Garmin compte les siestes à part et son score
    de nuit les ignore). La sieste remonte donc le sous-score."""
    if well is None or well.empty:
        return None, None
    # dernier jour AVEC une donnée de sommeil (la ligne du jour même est souvent encore vide,
    # Garmin ne remonte le sommeil que plus tard) — sinon la jauge reste grise pour rien.
    r = None
    for _, row in well.iloc[::-1].iterrows():
        if num1(row.get("sleep_hours")) is not None or num1(row.get("sleep_score")) is not None:
            r = row; break
    if r is None:
        return None, None
    overnight = num1(r.get("sleep_hours"))
    nap = num1(r.get("nap_hours")) or 0.0
    ss = num1(r.get("sleep_score"))
    if overnight is None and ss is None:
        return None, None
    eff = (overnight or 0) + nap
    dur = clamp(eff / 8 * 100) if eff > 0 else None
    if ss is not None:
        score = clamp(0.7 * ss + 0.3 * (dur if dur is not None else ss))
        detail = f"score {int(ss)}" + (f" + sieste {nap:.1f} h" if nap > 0 else "")
    else:
        score, detail = dur, f"{overnight:.1f} h" + (f" + sieste {nap:.1f} h" if nap > 0 else "")
    return score, detail

def score_hrv(well):
    if well is None or "hrv_weekly_avg" not in well: return None, None
    hrv = num(well["hrv_weekly_avg"]).dropna()
    if len(hrv) < 5: return None, None            # base courte assumée : la HRV ne pèse que 5 %
    base = hrv.iloc[:-1].tail(28).median()
    if not base or base <= 0: return None, None
    v = float(hrv.iloc[-1])
    return clamp(50 + (v / base - 1) * 200), round(v, 1)

def score_fc(well):
    """FC de repos vs base glissante — DÉTECTEUR D'ANOMALIE, pas indicateur de pic de forme.

    Refonte 22/07/2026 (cf. décision). L'ancienne formule `clamp(50 - (v-base)*8)` avait deux
    défauts relevés par l'athlète :
      - centrée à 50 pour v==base : être à sa FC de repos NORMALE n'est pas « moyen », c'est
        « rien à signaler » → doit donner un bon score, pas 50.
      - récompensait linéairement une FC très basse jusqu'à 100 : or une FC de repos anormalement
        basse peut au contraire signer un surmenage parasympathique, pas une super forme.
    Nouvelle logique :
      - v <= base  → plateau 85 (« rien à signaler » ; aucun bonus à descendre plus bas).
      - v  > base  → pénalité comptée en ÉCARTS-TYPE de la propre variabilité de l'athlète
                     (pas en bpm bruts) : 85 − 18·z, avec z=(v−base)/sd, sd planché à 2,5 bpm.
                     À +2σ ≈ 49 (vigilance), +3σ ≈ 31 (alerte).

    ⚠️ INTERIM : destiné à être remplacé par FC ATTENDUE vs CONSTATÉE (profil FC↔allure course,
    FC↔puissance vélo) dès que la base de mesures sera fiable — signal plus riche et personnalisé.
    Reste un détecteur au repos (dispo tous les jours), là où l'efficience exige une séance."""
    if well is None or "resting_hr" not in well: return None, None
    rhr = num(well["resting_hr"]).dropna()
    if len(rhr) < 4: return None, None
    prev = rhr.iloc[:-1].tail(28)
    base = prev.median()
    if not base or base <= 0: return None, None
    sdv = float(prev.std()) if len(prev) >= 3 else 2.5
    if not (sdv == sdv) or sdv < 2.5:          # NaN-safe + plancher (série trop plate = bruit)
        sdv = 2.5
    v = float(rhr.iloc[-1])
    z = (v - base) / sdv
    score = 85.0 if z <= 0 else clamp(85 - 18 * z)
    return score, round(v)

# ------------------------------------------------------------------ contexte alcool (confondant)
def alcool_veille(ref_date):
    """Alcool consommé la VEILLE de ref_date (le matin de ref_date en porte l'effet récup).
    Renvoie l'entrée {date, unites, note} ou None (None aussi si jour sec / 0 verre).
    L'alcool abaisse la HRV et monte la FC de repos de la nuit qui suit : sert à ANNOTER ces
    deux jauges (attribuer un signal du matin à la fête plutôt qu'à la fatigue, et l'inverse)
    SANS toucher au score — l'effet est déjà dans HRV/FC, on évite le double comptage.
    Alimenté par journal_alcool.py."""
    p = Path("journal") / "alcool.jsonl"
    if not p.exists():
        return None
    prev = (ref_date - timedelta(days=1)).isoformat()
    hit = None
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("date") == prev:
            hit = r                       # dernière saisie du jour l'emporte
    return hit if (hit and hit.get("unites")) else None

# ------------------------------------------------------------------ blessure
def injury_gauge(fit, runvol, subj):
    triggers, level = [], "vert"
    def bump(l):
        nonlocal level
        order = {"vert": 0, "orange": 1, "rouge": 2}
        if order[l] > order[level]: level = l
    # ACWR (charge aiguë 7 j / chronique 28 j) à partir du TSS quotidien
    if fit is not None and "tss" in fit:
        tss = num(fit["tss"]).fillna(0)
        if len(tss) >= 14:
            acute = tss.tail(7).mean(); chronic = tss.tail(28).mean()
            if chronic > 0:
                acwr = acute / chronic
                if acwr > 1.5:   triggers.append(f"ACWR {acwr:.2f} (pic de charge)"); bump("rouge")
                elif acwr > 1.3 or acwr < 0.8:
                    triggers.append(f"ACWR {acwr:.2f}"); bump("orange")
    # Ramp rate (hausse de CTL sur 7 j)
    if fit is not None and "ctl" in fit:
        ctl = num(fit["ctl"]).dropna()
        if len(ctl) >= 8:
            ramp = float(ctl.iloc[-1] - ctl.iloc[-8])
            if ramp > 8:   triggers.append(f"montée CTL +{ramp:.0f}/sem"); bump("rouge")
            elif ramp > 6: triggers.append(f"montée CTL +{ramp:.0f}/sem"); bump("orange")
    # Volume course vs plafond
    if runvol is not None and "statut" in runvol:
        st = str(runvol.iloc[-1]["statut"]).lower()
        km = runvol.iloc[-1].get("run_km"); cap = runvol.iloc[-1].get("plafond")
        if "roug" in st: triggers.append(f"course {km} km > plafond {cap}"); bump("rouge")
        elif "orange" in st: triggers.append(f"course {km} km (proche plafond {cap})"); bump("orange")
    # Ressenti blessure subjectif (questionnaire post-séance, prioritaire) — journal/ressenti_blessure.jsonl.
    # Seuils /10, plus SENSIBLES sur la chaîne Achille (pied/cheville) vu l'antécédent tendon/voûte.
    rb = Path("journal/ressenti_blessure.jsonl")
    if rb.exists():
        _lines = [l for l in rb.read_text().splitlines() if l.strip()]
        if _lines:
            sc = json.loads(_lines[-1]).get("scores") or {}
            achille = {"pied", "pied_d", "pied_g", "cheville", "cheville_d", "cheville_g"}
            red = orange = None
            for z, v in sc.items():
                thr_o, thr_r = (3, 6) if z in achille else (4, 7)
                if v >= thr_r: red = (z, v)
                elif v >= thr_o and orange is None: orange = (z, v)
            if red:
                triggers.append(f"douleur {red[0]} {red[1]}/10 (limitante)"); bump("rouge")
            elif orange:
                triggers.append(f"gêne {orange[0]} {orange[1]}/10 (à surveiller)"); bump("orange")
    return level, triggers

# ------------------------------------------------------------------ historisation par séance
def append_history(out):
    """Historise l'indice de forme quand une (ou plusieurs) séance(s) vient(nent) d'être
    téléchargée(s) — signal posé par intervals_sync.py dans data/new_sessions.json.
    Une entrée par ÉVÉNEMENT de synchro comportant ≥ 1 séance nouvelle, dans
    data/readiness_history.jsonl. Idempotent : les mêmes séances déclencheuses ne sont pas
    historisées deux fois (dédup par ID), donc rejouer readiness_model n'ajoute rien."""
    nsp = DATA_DIR / "new_sessions.json"
    if not nsp.exists():
        return None
    try:
        new_sessions = json.loads(nsp.read_text())
    except Exception:
        return None
    if not new_sessions:
        return None
    trigger_ids = [str(s.get("id")) for s in new_sessions if s.get("id")]
    # Mémoire durable et versionnée (comme journal/seances.jsonl, cf. D8) — pas dans data/ (ignoré/volatile).
    hist = Path("journal") / "readiness_history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    already = set()
    if hist.exists():
        for line in hist.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                already.update(str(i) for i in json.loads(line).get("trigger_ids", []))
            except Exception:
                continue
    if trigger_ids and all(t in already for t in trigger_ids):
        return None                       # déjà historisé (ré-exécution) → rien à faire
    newest = max(new_sessions, key=lambda s: str(s.get("date", "")))   # datée sur la séance la + récente
    comp = out.get("components", {})
    entry = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "as_of_date":  newest.get("date", ""),
        "trigger_ids": trigger_ids,
        "trigger": {"id": newest.get("id"), "sport_type": newest.get("sport_type"),
                    "name": newest.get("name"), "distance_km": newest.get("distance_km")},
        "readiness": out["readiness"], "color": out["color"],
        "components": {k: v["score"] for k, v in comp.items()},
        "tsb":  comp.get("tsb", {}).get("detail"),
        "injury_level": out["injury"]["level"],
    }
    with hist.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry

# ------------------------------------------------------------------ main
def main():
    fit    = load("fitness.csv")
    well   = load("wellness.csv")
    runvol = load("run_volume.csv")
    acts   = load("activities.csv")
    # (le ressenti est calculé plus bas par score_ressenti, depuis feel + RPE de la dernière séance)

    # Siestes saisies manuellement (naps.csv : date, nap_hours) → complètent/écrasent la valeur
    naps = load("naps.csv")
    if naps is not None and "date" in naps and "nap_hours" in naps:
        if well is None:
            well = naps
        else:
            well = well.merge(naps[["date", "nap_hours"]].rename(columns={"nap_hours": "_nap_m"}),
                              on="date", how="left")
            if "nap_hours" in well:
                well["nap_hours"] = well["_nap_m"].where(well["_nap_m"].notna(), well["nap_hours"])
            else:
                well["nap_hours"] = well["_nap_m"]
            well = well.drop(columns=["_nap_m"])

    subs = {
        "tsb":   score_tsb(fit),
        "hr":    score_fc(well),          # jauge FC = FC de repos vs base (dispo tous les jours)
        "sleep": score_sleep(well),
        "hrv":   score_hrv(well),
    }
    # forme provisoire (sans le ressenti) → sert à estimer le RPE attendu, sans circularité
    prov = {k: v[0] for k, v in subs.items() if v[0] is not None}
    forme_prov = (round(sum(prov[k] * WEIGHTS[k] for k in prov) / sum(WEIGHTS[k] for k in prov))
                  if prov else None)
    subs["subj"] = score_ressenti(acts, forme_prov)   # jauge Ressenti
    avail = {k: v[0] for k, v in subs.items() if v[0] is not None}
    if not avail:
        print("ℹ️  Pas encore assez de données pour calculer la forme (il faut au moins une séance analysée).")
        return
    wsum = sum(WEIGHTS[k] for k in avail)
    index = round(sum(avail[k] * WEIGHTS[k] for k in avail) / wsum)
    color = "vert" if index >= 70 else "orange" if index >= 40 else "rouge"
    guide = {"vert": "Frais — bon jour pour une séance clé.",
             "orange": "Vigilance — garde l'intensité mesurée, soigne la récup.",
             "rouge": "Fatigue — allège ou repose-toi aujourd'hui."}[color]

    inj_level, inj_triggers = injury_gauge(fit, runvol, None)

    ref_date = datetime.now().date()
    components = {k: {"score": round(v[0]), "detail": v[1]}
                 for k, v in subs.items() if v[0] is not None}
    # Confondant alcool : on ANNOTE les jauges HRV/FC de la veille sans modifier le score.
    alc = alcool_veille(ref_date)
    if alc:
        note = f"{alc['unites']} verre(s) la veille ({(ref_date - timedelta(days=1)).isoformat()})"
        for k in ("hrv", "hr"):
            if k in components:
                components[k]["note"] = note

    out = {
        "date": ref_date.isoformat(),
        "readiness": index, "color": color, "guide": guide,
        "components": components,
        "missing": [k for k, v in subs.items() if v[0] is None],
        "injury": {"level": inj_level, "triggers": inj_triggers},
        "alcool_veille": alc,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "readiness.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"🧭 Indice de forme : {index}/100  → JOUR {color.upper()}")
    print(f"   {guide}")
    comp = "  ".join(f"{k}:{round(v[0])}" for k, v in subs.items() if v[0] is not None)
    print(f"   Composantes : {comp}")
    if out["missing"]:
        print(f"   (en attente : {', '.join(out['missing'])})")
    flag = {"vert": "🟢", "orange": "🟠", "rouge": "🔴"}[inj_level]
    print(f"🩹 Compteur blessure : {flag} {inj_level.upper()}"
          + (f" — {'; '.join(inj_triggers)}" if inj_triggers else " — RAS"))
    if alc:
        print(f"🍷 Contexte : {alc['unites']} verre(s) la veille — jauges HRV/FC à lire avec ce "
              f"filtre (artefact possible, pas forcément fatigue) ; score inchangé.")
    print(f"→ {DATA_DIR/'readiness.json'}")

    # Historisation longitudinale : un point d'indice à chaque séance nouvelle téléchargée
    hist_entry = append_history(out)
    if hist_entry:
        trig = hist_entry["trigger"]
        print(f"📈 Indice historisé (séance {trig.get('sport_type')} {trig.get('id')}, "
              f"{hist_entry['as_of_date']}) : forme {hist_entry['readiness']}/100 "
              f"→ journal/readiness_history.jsonl")
    return out

if __name__ == "__main__":
    main()
