#!/usr/bin/env python3
"""profil_fc.py — vue consolidée du profil FC↔allure (course) et FC↔puissance (vélo).

POURQUOI. Les mesures vivent en append-only dans trois fichiers du journal
(`hr_analyse.jsonl` : portions plates stabilisées par séance · `profil_fc_allure.jsonl` :
paliers de calibration · `profil_fc_puissance.jsonl` : références vélo par position).
Cette vue les AGRÈGE sans jamais les modifier — source de vérité unique, outil réutilisable
(avant : reconstruit à la main à chaque demande, code jetable — défaut corrigé le 14/08/2026).

USAGE.
    python3 profil_fc.py cap             # table datée allure × FC × T°C (course)
    python3 profil_fc.py velo            # table datée puissance × FC × position (vélo)
    python3 profil_fc.py courbes         # data/profil_cap.csv + data/profil_velo.csv (à tracer)
"""
import sys, json, csv
from pathlib import Path

ROOT = Path(__file__).parent
J = ROOT / "journal"


def points_cap():
    rows = []
    p = J / "hr_analyse.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            e = json.loads(line)
            temp = (e.get("meteo") or {}).get("temp_c")
            portions = {round(p.get("v_2h_ms", 0), 3): p for p in e.get("portions") or []}
            for x in e.get("profil_fc_allure") or []:
                if x.get("fc") and x.get("v_ms"):
                    q = x.get("qualite", "")
                    # FC montante DANS la portion (2e moitié >> entière) = étalon contaminé
                    # (post-descente, dérive thermique…) — repéré par l'athlète le 14/08 sur
                    # un 148,9 gonflé de +2,7 bpm. On garde le point, on le marque.
                    p = portions.get(round(x["v_ms"], 3))
                    if p and p.get("fc_portion_entiere") and p.get("fc_2h"):
                        if p["fc_2h"] - p["fc_portion_entiere"] > 2.0:
                            q = (q + " ⚠️FC montante").strip()
                    rows.append({"date": e.get("date"), "s_km": 1000 / x["v_ms"],
                                 "allure": x.get("allure"), "fc": x["fc"], "temp_c": temp,
                                 "min_sortie": round(p["t_start_min"]) if p and p.get("t_start_min") is not None else None,
                                 "source": x.get("methode", "portion"), "qualite": q})
    p = J / "profil_fc_allure.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            e = json.loads(line)
            if e.get("allure_s_km") and e.get("fc_moy"):
                bloc = str(e.get("bloc") or "")
                m0 = int(bloc.split(":")[0]) if ":" in bloc else None
                rows.append({"date": e.get("date"), "s_km": e["allure_s_km"],
                             "allure": e.get("allure_min_km"), "fc": e["fc_moy"],
                             "temp_c": e.get("temp_c"), "min_sortie": m0,
                             "source": e.get("source") or "palier calibration",
                             "qualite": "basse (GPS parc)" if "parc" in str(e.get("source") or "") else "haute"})
    # dédup (date, allure arrondie) — la 1re occurrence gagne, tri par allure
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x["s_km"]):
        k = (r["date"], round(r["s_km"]))
        if k in seen: continue
        seen.add(k); out.append(r)
    return out


def points_velo():
    """HT uniquement (capteur trainer). La route est une série DISTINCTE (D35) : points_route()."""
    rows = []
    p = J / "profil_fc_puissance.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            e = json.loads(line)
            w = e.get("watts") or e.get("watts_moy")
            fc = e.get("fc_moy")
            if w and fc:
                rows.append({"date": e.get("date"), "watts": round(w), "fc": fc,
                             "position": e.get("position", "?"), "temp_c": e.get("temp_c"),
                             "cadence": e.get("cadence") or e.get("cadence_moy"),
                             "source": e.get("segmentation") or e.get("structure") or ""})
    return sorted(rows, key=lambda x: (x["watts"], x["date"]))


def points_route():
    """Étalons plats des sorties ROUTE — depuis le TABLEAU DÉDIÉ journal/etalons_velo_route.jsonl
    (alimenté par route_analysis.py à chaque analyse ; dédup (activity_id, fenetre), dernière
    analyse fait foi). Colonnes : watts, date, min de sortie, temp_c, fc_moy, duree_s, km, km/h."""
    p = J / "etalons_velo_route.jsonl"
    if not p.exists(): return []
    dedup = {}
    for line in p.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            dedup[(e.get("activity_id"), e.get("fenetre"))] = e
    rows = [{"date": e.get("date"), "watts": e["watts"], "fc": e.get("fc_moy"),
             "min_sortie": e.get("min_sortie"), "temp_c": e.get("temp_c"),
             "km": e.get("km"), "duree_s": e.get("duree_s"),
             "vitesse_kmh": e.get("vitesse_kmh")} for e in dedup.values()]
    return sorted(rows, key=lambda x: (x["date"], x["min_sortie"] or 0))


def points_ht_steady():
    """Références STEADY-STATE du home-trainer : FC réf iso-puissance des séances ERG
    (ht_analyse.jsonl, dédup par activité, température jointe depuis contexte_seance.jsonl).
    Le plat importe peu en HT : c'est la stabilité ERG qui fait l'étalon."""
    import json as _j
    temps = {}
    pc = J / "contexte_seance.jsonl"
    if pc.exists():
        for line in pc.read_text().splitlines():
            if line.strip():
                e = _j.loads(line)
                if e.get("session_id") and e.get("temp_piece_c") is not None:
                    temps[e["session_id"]] = e["temp_piece_c"]
    p = J / "ht_analyse.jsonl"
    if not p.exists(): return []
    par_act = {}
    for line in p.read_text().splitlines():
        if line.strip():
            e = _j.loads(line)
            par_act[e.get("activity_id")] = e
    rows = []
    for e in par_act.values():
        b, f = e.get("bloc") or {}, e.get("fc_ref") or {}
        if b.get("puissance_W") and f.get("bpm"):
            rows.append({"date": e.get("date"), "watts": b["puissance_W"], "fc": f["bpm"],
                         "duree_min": b.get("duree_min"), "temp_c": temps.get(e.get("activity_id")),
                         "derive": (e.get("derive") or {}).get("derive_totale_bpm")})
    return sorted(rows, key=lambda x: x["date"])


def fmt_allure(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


def cap():
    rows = points_cap()
    print(f"{'allure':>8} {'FC':>6} {'date':>11} {'min':>5} {'T°C':>5}  source")
    print("-" * 64)
    for r in rows:
        t = f"{r['temp_c']:.0f}" if r.get("temp_c") is not None else "?"
        m = f"{r['min_sortie']}" if r.get("min_sortie") is not None else "?"
        print(f"{fmt_allure(r['s_km']):>8} {r['fc']:>6.1f} {r['date']:>11} {m:>5} {t:>5}  "
              f"{r['source']}{' ·' + r['qualite'] if r['qualite'] else ''}")
    print(f"\n{len(rows)} points — bruts dans journal/ (append-only), vue recalculée à la demande.")


def velo():
    rows = points_velo()
    print("── HT (capteur trainer) " + "─" * 30)
    print(f"{'W':>5} {'FC':>6} {'date':>11} {'pos':>6} {'T°C':>5} {'cad':>5}")
    print("-" * 48)
    for r in rows:
        t = f"{r['temp_c']:.0f}" if r.get("temp_c") is not None else "?"
        c = f"{r['cadence']:.0f}" if r.get("cadence") else "?"
        print(f"{r['watts']:>5} {r['fc']:>6.1f} {r['date']:>11} {r['position']:>6} {t:>5} {c:>5}")
    hs = points_ht_steady()
    print(f"\n── HT · réf steady-state ERG (fc_ref min 8→12 du bloc) " + "─" * 4)
    print(f"{'W':>5} {'FC':>6} {'date':>11} {'bloc':>6} {'T°C':>5} {'dérive':>7}")
    print("-" * 48)
    for r in hs:
        tc = f"{r['temp_c']:.0f}" if r.get("temp_c") is not None else "?"
        dv = f"{r['derive']:+.1f}" if r.get("derive") is not None else "?"
        print(f"{r['watts']:>5} {r['fc']:>6.1f} {r['date']:>11} {r['duree_min']:>5}' {tc:>5} {dv:>7}")
    rr = points_route()
    print(f"\n── ROUTE · étalons plats /30 min (capteur P5 — série distincte, D35) " + "─" * 2)
    print(f"{'W':>5} {'FC':>6} {'date':>11} {'min':>5} {'T°C':>5} {'km':>7} {'durée':>6} {'km/h':>6}")
    print("-" * 62)
    for r in rr:
        tc = f"{r['temp_c']:.0f}" if r.get("temp_c") is not None else "?"
        m = r.get("min_sortie") if r.get("min_sortie") is not None else "?"
        print(f"{r['watts']:>5} {r['fc']:>6.1f} {r['date']:>11} {m:>5} {tc:>5} {r['km']:>7} {r['duree_s']:>5}s {r['vitesse_kmh']:>6}")
    print(f"\n{len(rows)} points HT segments + {len(hs)} réf steady + {len(rr)} étalons route — séries jamais mélangées.")


def courbes():
    d = ROOT / "data"
    d.mkdir(exist_ok=True)
    with (d / "profil_cap.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "allure", "s_km", "fc", "temp_c", "min_sortie", "source", "qualite"])
        w.writeheader()
        for r in points_cap():
            r = dict(r); r["s_km"] = round(r["s_km"], 1); w.writerow(r)
    with (d / "profil_velo.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "watts", "fc", "position", "temp_c", "cadence", "source"])
        w.writeheader()
        for r in points_velo():
            w.writerow(r)
    with (d / "etalons_velo_route.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["watts", "date", "min_sortie", "temp_c", "fc", "duree_s", "km", "vitesse_kmh"])
        w.writeheader()
        for r in points_route():
            w.writerow(r)
    print("✅ data/profil_cap.csv + data/profil_velo.csv + data/etalons_velo_route.csv générés.")


if __name__ == "__main__":
    {"cap": cap, "velo": velo, "courbes": courbes}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
