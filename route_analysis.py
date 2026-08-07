#!/usr/bin/env python3
"""
route_analysis.py — Analyse d'une SORTIE VÉLO ROUTE (protocole ROUTE_PEDALE_v1, cf. D35).

POURQUOI (décision athlète 02/08/2026) :
  1. Le capteur de puissance ROUTE (nouveau P5) n'est PAS celui du home-trainer → les deux
     séries ne se mélangent JAMAIS (journal/route_analyse.jsonl ≠ ht_analyse.jsonl). On les
     comparera quand chaque série aura assez de points.
  2. Sur route, les descentes en roue libre (0 W) écrasent la puissance moyenne et même la NP —
     alors qu'aux Sables d'Olonne on pédale quasi tout le temps. Les chiffres représentatifs
     de la course sont donc calculés sur le TEMPS PÉDALÉ uniquement :
       - on coupe échauffement et retour au calme (bornes déclarées par l'athlète) ;
       - on écarte tout échantillon sous SEUIL_PEDALE (défaut 80 W : roue libre, freinage) ;
       - « temps pédalé » = ce qui reste · « PMP » = moyenne de ces échantillons ·
       - « NPP » = puissance normalisée (30 s roulants, moyenne des ^4, racine 4e) calculée
         sur la série pédalée RE-CONCATÉNÉE. ⚠️ Convention maison : les 30 s roulants
         enjambent les trous (une NP officielle ne le fait pas) — cohérente d'une sortie à
         l'autre, c'est ce qui compte pour NOTRE suivi.
  3. Les CÔTES sont détectées (pente lissée ≥ 2,5 % pendant ≥ 40 s) et rendues une à une :
     c'est là que se lit le vrai travail sur un parcours vallonné.

Usage :
    python3 route_analysis.py <activity_id> --wu 679 --rac 1451   # bornes en secondes
    python3 route_analysis.py <activity_id>                       # sans coupe (wu=rac=0)
"""
from __future__ import annotations
import json, sys
from pathlib import Path

import hr_analysis as H          # api(), load_streams()

SEUIL_PEDALE = 80.0              # W : en dessous = roue libre / freinage, pas du pédalage
STORE = Path("journal") / "route_analyse.jsonl"


def _arg(name, default):
    if name in sys.argv:
        return float(sys.argv[sys.argv.index(name) + 1])
    return default


def npp(ws, win=30):
    """NP sur une série d'échantillons pédalés re-concaténée (30 échantillons roulants)."""
    if len(ws) < win:
        return None
    roll, acc = [], sum(ws[:win])
    roll.append(acc / win)
    for i in range(win, len(ws)):
        acc += ws[i] - ws[i - win]
        roll.append(acc / win)
    return (sum(r ** 4 for r in roll) / len(roll)) ** 0.25


def smooth(xs, n=15):
    out, m = [], len(xs)
    for i in range(m):
        a, b = max(0, i - n // 2), min(m, i + n // 2 + 1)
        seg = [x for x in xs[a:b] if x is not None]
        out.append(sum(seg) / len(seg) if seg else None)
    return out


def detect_cotes(t, alt, dist, w, hr, v_kmh, core):
    """Côtes = pente lissée ≥ 2,5 % soutenue ≥ 40 s (trous ≤ 10 s fusionnés, D+ ≥ 8 m)."""
    n = len(t)
    alt_s = smooth(alt, 15)
    G = 10  # fenêtre de pente (échantillons ≈ 10 s)
    climbing = [False] * n
    for i in range(G, n):
        dd = (dist[i] - dist[i - G]) if dist[i] is not None and dist[i - G] is not None else 0
        if dd and dd > 3 and alt_s[i] is not None and alt_s[i - G] is not None:
            grade = (alt_s[i] - alt_s[i - G]) / dd * 100
            climbing[i] = grade >= 2.5
    # fusion des trous courts
    segs, i = [], core[0]
    while i <= core[1]:
        if climbing[i]:
            j = i
            gap = 0
            k = i
            while k <= core[1] and gap <= 10:
                if climbing[k]:
                    j = k
                    gap = 0
                else:
                    gap += 1
                k += 1
            segs.append((i, j))
            i = k
        else:
            i += 1
    out = []
    for a, b in segs:
        dur = t[b] - t[a]
        dplus = (alt_s[b] or 0) - (alt_s[a] or 0)
        dd = (dist[b] - dist[a]) if dist[b] and dist[a] else 0
        if dur < 40 or dplus < 8 or dd <= 0:
            continue
        idx = range(a, b + 1)
        ws = [w[i] for i in idx if w[i] is not None]
        hs = [hr[i] for i in idx if hr[i] is not None]
        out.append({
            "debut_min": round(t[a] / 60, 1), "duree_s": round(dur),
            "d_plus_m": round(dplus, 1), "longueur_m": round(dd),
            "pente_pct": round(dplus / dd * 100, 1),
            "w_moy": round(sum(ws) / len(ws)) if ws else None,
            "vitesse_kmh": round(dd / dur * 3.6, 1),
            "fc_moy": round(sum(hs) / len(hs)) if hs else None,
            "fc_max": max(hs) if hs else None,
        })
    return out


def analyse(aid, wu_s, rac_s, seuil):
    act = H.api(f"/activity/{aid}")
    S = H.load_streams(aid)
    t, w, hr = S["time"], S.get("watts"), S.get("heartrate")
    alt, dist = S.get("altitude"), S.get("distance")
    if not w:
        sys.exit("⛔ pas de flux puissance")
    n = len(t)
    end = t[-1]
    a = next(i for i in range(n) if t[i] >= wu_s)
    b = max(i for i in range(n) if t[i] <= end - rac_s)
    core_dur = t[b] - t[a]

    idx_ped = [i for i in range(a, b + 1) if w[i] is not None and w[i] >= seuil]
    ws_ped = [w[i] for i in idx_ped]
    pmp = sum(ws_ped) / len(ws_ped) if ws_ped else None
    v_npp = npp(ws_ped)
    hs_ped = [hr[i] for i in idx_ped if hr[i] is not None]

    res = {
        "activity_id": aid, "date": act.get("start_date_local", "")[:10],
        "nom": (act.get("name") or "").strip(), "protocole": "ROUTE_PEDALE_v1",
        "capteur": "route (nouveau P5) — série DISTINCTE du HT",
        "coupes": {"echauffement_s": round(wu_s), "retour_calme_s": round(rac_s),
                   "seuil_pedale_W": seuil},
        "coeur_min": round(core_dur / 60, 1),
        "temps_pedale_min": round(len(idx_ped) / 60, 1),
        "pct_pedale": round(100 * len(idx_ped) / max(1, (b - a + 1)), 1),
        "PMP_W": round(pmp) if pmp else None,
        "NPP_W": round(v_npp) if v_npp else None,
        "fc_moy_pedale": round(sum(hs_ped) / len(hs_ped)) if hs_ped else None,
        "np_officielle_W": act.get("icu_weighted_avg_watts"),
        "distance_km": round((act.get("distance") or 0) / 1000, 1),
        "d_plus_m": act.get("total_elevation_gain"),
    }
    if alt and dist:
        res["cotes"] = detect_cotes(t, alt, dist, w, hr, None, (a, b))
    return res


def report(r):
    print(f"🚴 {r['nom']} · {r['date']} · protocole {r['protocole']}")
    print(f"   {r['distance_km']} km · D+ {r['d_plus_m']} m · capteur : {r['capteur']}")
    c = r["coupes"]
    print(f"   coupes : échauffement {c['echauffement_s']/60:.0f} min · RAC {c['retour_calme_s']/60:.0f} min · seuil pédalage {c['seuil_pedale_W']:.0f} W")
    print(f"\n   CŒUR DE SÉANCE  {r['coeur_min']} min")
    print(f"   → temps pédalé   : {r['temps_pedale_min']} min ({r['pct_pedale']} %)")
    print(f"   → PMP  (moyenne pédalée)    : {r['PMP_W']} W")
    print(f"   → NPP  (normalisée pédalée) : {r['NPP_W']} W   (NP officielle toute sortie : {r['np_officielle_W']} W)")
    print(f"   → FC moyenne en pédalant    : {r['fc_moy_pedale']} bpm")
    if r.get("cotes"):
        print(f"\n   CÔTES DÉTECTÉES ({len(r['cotes'])}) — pente ≥ 2,5 % soutenue ≥ 40 s")
        for i, k in enumerate(r["cotes"], 1):
            print(f"   {i:2d}. min {k['debut_min']:5.1f} · {k['duree_s']:3d} s · {k['longueur_m']:4d} m à {k['pente_pct']:.1f} % · "
                  f"D+ {k['d_plus_m']:4.1f} m · {k['w_moy']} W · {k['vitesse_kmh']} km/h · FC {k['fc_moy']} (max {k['fc_max']})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    aid = sys.argv[1]
    r = analyse(aid, _arg("--wu", 0), _arg("--rac", 0), _arg("--seuil", SEUIL_PEDALE))
    report(r)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n💾 → {STORE}")
