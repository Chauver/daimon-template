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
ETALONS = Path("journal") / "etalons_velo_route.jsonl"   # tableau DÉDIÉ des étalons plats (15/08)


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


def detect_portions_plates(t, w, hr, alt, dist, core, lats=None, lons=None, t0_iso=None):
    """Portions PLATES et STABLES en puissance — l'étalon officiel FC×puissance sur route
    (décision athlète 14/08/2026, renforcée le 15/08) : fenêtres ≥ 90 s avec |pente| < 0,6 %
    ET SD de la puissance lissée 30 s < 8 W. **UNE portion par TRANCHE DE 30 MIN** (la meilleure,
    c.-à-d. la plus longue qualifiée) : c'est ce maillage régulier qui permettra les régressions
    linéaires de dérive cardiaque et d'adaptation thermique. Chaque portion embarque la
    TEMPÉRATURE au point GPS/heure exacts (archive Open-Meteo). FC retenue = 2e moitié."""
    if not (alt and dist):
        return []
    alt_s = smooth(alt, 15)
    pm = smooth(w, 30)
    a0, b0 = core
    n = len(t)
    W = 90                                   # fenêtre minimale (échantillons ≈ s)

    # RÈGLE DES 60 s (D42, mesurée le 06/09/2026 sur 15 roues libres) : après toute coupure de
    # pédalage ≥ 4 s (croisement, relance), la FC est FAUSSEMENT BASSE pendant la reconvergence
    # (médiane 46 s, p75 57 s). On exclut donc 60 s après chaque reprise : aucun étalon ne peut
    # chevaucher cette fenêtre de redémarrage.
    redem = [False] * n
    _cs = None
    for _i in range(n):
        _cold = (w[_i] is None) or (w[_i] < 30)
        if _cold and _cs is None: _cs = _i
        elif not _cold and _cs is not None:
            if t[_i] - t[_cs] >= 4:
                for _k in range(_i, n):
                    if t[_k] > t[_i] + 60: break
                    redem[_k] = True
            _cs = None

    def ok(a, b):
        dd = (dist[b] - dist[a]) if dist[b] and dist[a] else 0
        if dd < 50: return False
        if alt_s[b] is None or alt_s[a] is None: return False
        pente = abs((alt_s[b] - alt_s[a]) / dd * 100)
        seg = [pm[k] for k in range(a, b + 1) if pm[k] is not None]
        if len(seg) < (b - a) * 0.9: return False
        m = sum(seg) / len(seg)
        if m < 100: return False              # roue libre / trop léger : pas un étalon
        sdv = (sum((x - m) ** 2 for x in seg) / len(seg)) ** 0.5
        if any(redem[k] for k in range(a, b + 1)): return False   # règle des 60 s (D42)
        return pente < 0.6 and sdv < 8.0

    def scan(lo, hi):
        """Meilleure portion qualifiée (la plus longue) dans [lo, hi]."""
        best = None
        i = lo
        while i + W <= hi:
            j = i + W
            if ok(i, j):
                while j + 15 <= hi and ok(i, j + 15):
                    j += 15
                if best is None or (t[j] - t[i]) > (t[best[1]] - t[best[0]]):
                    best = (i, j)
                i = j + 30
            else:
                i += 15
        return best

    def temp_at(i):
        if not (lats and lons and t0_iso): return None
        try:
            from datetime import datetime, timedelta
            import requests
            dt = datetime.fromisoformat(t0_iso[:19]) + timedelta(seconds=t[i])
            la, lo_ = lats[i], lons[i]
            if not (la and lo_): return None
            r = requests.get("https://archive-api.open-meteo.com/v1/archive",
                             params={"latitude": la, "longitude": lo_, "hourly": "temperature_2m",
                                     "start_date": dt.date().isoformat(), "end_date": dt.date().isoformat(),
                                     "timezone": "auto"}, timeout=15).json().get("hourly", {})
            vals = r.get("temperature_2m") or []
            if not vals: return None
            h = dt.hour + dt.minute / 60
            i0 = min(int(h), len(vals) - 1)
            v0, v1 = vals[i0], vals[min(i0 + 1, len(vals) - 1)]
            if v0 is None: return None
            return round(v0 + (h - i0) * ((v1 - v0) if v1 is not None else 0), 1)
        except Exception:
            return None

    out = []
    fen = 0
    lo = a0
    while lo < b0:
        hi = min(b0, next((k for k in range(lo, n) if t[k] >= t[lo] + 30 * 60), b0))
        fen += 1
        seg = scan(lo, hi)
        if seg:
            a, b = seg
            idx = range(a, b + 1)
            ws = [w[k] for k in idx if w[k] is not None]
            hs = [hr[k] for k in idx if hr[k] is not None]
            h2 = [hr[k] for k in range(a + (b - a) // 2, b + 1) if hr[k] is not None]
            dd = (dist[b] - dist[a])
            dur = t[b] - t[a]
            mid = a + (b - a) // 2
            out.append({
                "fenetre_30min": fen, "min_sortie": round(t[a] / 60),
                "km": round(dist[a] / 1000, 1), "duree_s": round(dur),
                "pente_pct": round((alt_s[b] - alt_s[a]) / dd * 100, 2),
                "watts_moy": round(sum(ws) / len(ws)), "vitesse_kmh": round(dd / dur * 3.6, 1),
                "fc_moy": round(sum(hs) / len(hs), 1) if hs else None,
                "fc_2e_moitie": round(sum(h2) / len(h2), 1) if h2 else None,
                "temp_c": temp_at(mid),
            })
        else:
            out.append({"fenetre_30min": fen, "min_sortie": round(t[lo] / 60),
                        "km": round((dist[lo] or 0) / 1000, 1), "duree_s": None,
                        "note": "aucune portion qualifiée dans cette tranche (relief/instabilité)"})
        lo = hi
    return out


def detect_echantillons(t, w, hr, alt, dist, core, lats=None, lons=None, t0_iso=None, cad=None):
    """ÉCHANTILLONS À DURÉE FIXE (D43, 06/09/2026) — remplacent les portions variables comme
    étalons officiels : la durée normalisée rend les points comparables entre sorties.
      • bande Z2      : 3 min à 160-200 W  → fenetre « 3min-k »
      • bande allure  : 5 min à 200-235 W  → fenetre « 5min-k »
    Critères : 100 % pédalé (aucune coupure), |pente| < 0,6 %, SD puissance lissée < 8 W,
    aucun chevauchement d'une fenêtre de redémarrage (règle des 60 s, D42). FC = 2e moitié."""
    if not (alt and dist):
        return []
    alt_s = smooth(alt, 15)
    pm = smooth(w, 30)
    a0, b0 = core
    n = len(t)
    redem = [False] * n
    _cs = None
    for _i in range(n):
        _cold = (w[_i] is None) or (w[_i] < 30)
        if _cold and _cs is None: _cs = _i
        elif not _cold and _cs is not None:
            if t[_i] - t[_cs] >= 4:
                for _k in range(_i, n):
                    if t[_k] > t[_i] + 60: break
                    redem[_k] = True
            _cs = None

    def temp_at(i):
        if not (lats and lons and t0_iso): return None
        try:
            from datetime import datetime, timedelta
            import requests
            dt = datetime.fromisoformat(t0_iso[:19]) + timedelta(seconds=t[i])
            la, lo_ = lats[i], lons[i]
            if not (la and lo_): return None
            r = requests.get("https://archive-api.open-meteo.com/v1/archive",
                             params={"latitude": la, "longitude": lo_, "hourly": "temperature_2m",
                                     "start_date": dt.date().isoformat(), "end_date": dt.date().isoformat(),
                                     "timezone": "auto"}, timeout=15).json().get("hourly", {})
            vals = r.get("temperature_2m") or []
            if not vals: return None
            h = dt.hour + dt.minute / 60
            i0 = min(int(h), len(vals) - 1)
            v0, v1 = vals[i0], vals[min(i0 + 1, len(vals) - 1)]
            if v0 is None: return None
            return round(v0 + (h - i0) * ((v1 - v0) if v1 is not None else 0), 1)
        except Exception:
            return None

    def ok(a, b, wlo, whi):
        if any(redem[k] or w[k] is None or w[k] < 30 for k in range(a, b + 1)): return False
        dd = (dist[b] - dist[a]) if dist[b] and dist[a] else 0
        if dd < 50 or alt_s[b] is None or alt_s[a] is None: return False
        if abs((alt_s[b] - alt_s[a]) / dd * 100) >= 0.6: return False
        seg = [pm[k] for k in range(a, b + 1) if pm[k] is not None]
        if len(seg) < (b - a) * 0.9: return False
        m = sum(seg) / len(seg)
        sd = (sum((x - m) ** 2 for x in seg) / len(seg)) ** 0.5
        return sd < 8.0 and wlo <= m <= whi

    out = []
    for bande, dur, wlo, whi in (("3min", 180, 160, 200), ("5min", 300, 200, 235)):
        k, tcur = 0, t[a0]
        while tcur + dur <= t[b0]:
            a = next((j for j in range(n) if t[j] >= tcur), None)
            b = next((j for j in range(n) if t[j] >= tcur + dur), None)
            if a is not None and b is not None and b <= b0 and ok(a, b, wlo, whi):
                k += 1
                idx = range(a, b + 1)
                ws = [w[j] for j in idx if w[j] is not None]
                h2 = [hr[j] for j in range(a + (b - a) // 2, b + 1) if hr[j] is not None]
                out.append({
                    "fenetre": f"{bande}-{k}", "min_sortie": round(t[a] / 60),
                    "km": round(dist[a] / 1000, 1), "duree_s": dur,
                    "watts": round(sum(ws) / len(ws)),
                    "vitesse_kmh": round((dist[b] - dist[a]) / dur * 3.6, 1),
                    "fc_moy": round(sum(h2) / len(h2), 1) if h2 else None,
                    "temp_c": temp_at(a + (b - a) // 2),
                    "cadence": round(sum(cs) / len(cs)) if (cs := [cad[j] for j in idx if cad and j < len(cad) and cad[j]]) else None,
                })
                tcur = t[b] + 60
            else:
                tcur += 20
    return out


def derive_par_bande(echs):
    """Pente FC↔temps (bpm/h) par bande d'échantillons, quand ≥ 2 points — la dérive
    intra-sortie à puissance normalisée (le chiffre que la série d'étalons permet)."""
    out = {}
    for bande in ("3min", "5min"):
        pts = [(e["min_sortie"], e["fc_moy"]) for e in echs
               if e["fenetre"].startswith(bande) and e.get("fc_moy")]
        if len(pts) >= 2:
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
            den = sum((p[0] - mx) ** 2 for p in pts)
            if den > 0:
                out[bande] = round(sum((p[0] - mx) * (p[1] - my) for p in pts) / den * 60, 1)
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
        res["portions_plates"] = detect_portions_plates(
            t, w, hr, alt, dist, (a, b),
            lats=S.get("_lat"), lons=S.get("_lon"),
            t0_iso=act.get("start_date_local"))
        res["echantillons"] = detect_echantillons(
            t, w, hr, alt, dist, (a, b),
            lats=S.get("_lat"), lons=S.get("_lon"),
            t0_iso=act.get("start_date_local"), cad=S.get("cadence"))
        res["derive_bpm_h"] = derive_par_bande(res["echantillons"])
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
    if r.get("portions_plates"):
        print(f"\n   PORTIONS PLATES — 1 étalon / tranche de 30 min (|pente|<0,6 %, SD<8 W)")
        for p in r["portions_plates"]:
            if p.get("duree_s"):
                tc = f" · {p['temp_c']} °C" if p.get("temp_c") is not None else ""
                print(f"   F{p['fenetre_30min']} · min {p['min_sortie']:3d} · km {p['km']:5.1f} · {p['duree_s']:3d} s · "
                      f"{p['watts_moy']} W · {p['vitesse_kmh']} km/h · FC 2e moitié {p['fc_2e_moitie']}{tc}")
            else:
                print(f"   F{p['fenetre_30min']} · min {p['min_sortie']:3d} · — {p.get('note','')}")
    if r.get("echantillons"):
        print(f"\n   ÉCHANTILLONS À DURÉE FIXE (D43) — 3' Z2 160-200 W · 5' allure 200-235 W · règle 60 s")
        for e in r["echantillons"]:
            tc = f" · {e['temp_c']} °C" if e.get("temp_c") is not None else ""
            print(f"   {e['fenetre']:>7} · min {e['min_sortie']:3d} · km {e['km']:5.1f} · "
                  f"{e['watts']} W · {e['vitesse_kmh']} km/h · FC 2e moitié {e['fc_moy']}{tc}")
        if r.get("derive_bpm_h"):
            d = " · ".join(f"{k} : {v:+.1f} bpm/h" for k, v in r["derive_bpm_h"].items())
            print(f"   dérive intra-sortie à puissance normalisée → {d}")
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
    # tableau dédié des étalons — depuis le 06/09 (D43) ce sont les ÉCHANTILLONS À DURÉE FIXE
    # qui y entrent (3' Z2 / 5' allure), plus les portions variables (durées non comparables).
    # Dédup à la lecture par (activity_id, fenetre), la dernière analyse fait foi.
    with ETALONS.open("a") as f:
        for x in r.get("echantillons") or []:
            f.write(json.dumps({
                "activity_id": r["activity_id"], "date": r["date"],
                "watts": x["watts"], "min_sortie": x.get("min_sortie"),
                "temp_c": x.get("temp_c"), "fc_moy": x.get("fc_moy"),
                "duree_s": x["duree_s"], "km": x.get("km"),
                "vitesse_kmh": x.get("vitesse_kmh"), "fenetre": x.get("fenetre"),
                "cadence": x.get("cadence"),
            }, ensure_ascii=False) + "\n")
    print(f"\n💾 → {STORE} + {ETALONS} ({len(r.get('echantillons') or [])} échantillons D43)")
