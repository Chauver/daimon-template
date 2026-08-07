#!/usr/bin/env python3
"""
ht_analysis.py — Analyse d'une séance HOME-TRAINER en ERG (protocole HT_ERG_CONTINU_v1).

Issu d'une recherche multi-angles avec réfutation adversariale (20/07/2026) :
73 paramètres proposés, **9 seulement** ont survécu. Ce module n'implémente que ceux-là.

CE QUI EST VOLONTAIREMENT ABSENT, et pourquoi :
  • NP, IF, VI, xPower, time-in-target, distribution de puissance → TAUTOLOGIQUES en ERG.
    Le home-trainer asservit la résistance à la consigne : ces grandeurs mesurent le trainer,
    pas l'athlète. (Le TSS reste calculé, mais comme intrant du modèle de charge.)
  • Efficiency Factor (EF = P/FC) → en ERG, EF ≡ 160/FC. C'est STRICTEMENT équivalent à
    suivre la FC moyenne, en moins lisible. On suit donc la FC directement.
  • VFC à l'effort (rMSSD, SDNN, SD1, DFA-α1) → exige les intervalles R-R battement à
    battement. Le flux est de la FC moyennée à 1 Hz : l'information est déjà détruite.
    Non approximable. Et le facteur limitant de l'athlète est périphérique, pas autonome.
  • HRR60 → validée après arrêt franc post-effort maximal. Ici le bloc est suivi d'un
    retour au calme actif : il n'y a pas d'arrêt, donc pas de HRR.
  • Couple moyen (9.549·P/rpm) → l'algèbre est juste, la lecture non : c'est le couple
    MOYEN sur le cycle, qui ne dit rien de la contrainte de pic sur le droit fémoral.
    Le pilotage de la gêne passe par la consigne de cadence et le score 0-10.

Usage : python3 ht_analysis.py <activity_id>
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

import hr_analysis as H          # réutilise api(), load_streams(), sampling_dt(), gap_threshold()

STORE = Path("journal") / "ht_analyse.jsonl"


def load_store():
    """Analyses enregistrées, LA PLUS RÉCENTE par séance. Le fichier est en ajout seulement
    (on ne réécrit jamais une mesure) : une ré-analyse ajoute une ligne qui supersède la
    précédente. Toute lecture doit donc dédupliquer par activity_id en gardant la dernière."""
    if not STORE.exists():
        return {}
    out = {}
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            out[e["activity_id"]] = e
        except Exception:
            continue
    return out

FTP = int(os.getenv("FTP_WATTS", "235"))
HR_MAX_BIKE = int(os.getenv("HR_MAX_BIKE", "180"))
PLAFOND_IM = 155                 # plafond FC de course Ironman mesuré sur 3 marathons

# --- fenêtres d'analyse, FIGÉES (protocole HT_ERG_CONTINU_v1) ---
# Offsets en secondes DEPUIS LE DÉBUT DU BLOC DÉTECTÉ, jamais en heure absolue :
# autopause, smart recording et démarrage décalé rendraient tout calcul faux.
W_REF = (8 * 60, 12 * 60)        # FC de référence : stabilisation atteinte, dérive négligeable
W_DRIFT = (10 * 60, 40 * 60)     # dérive : on rogne 10 min (athlète désentraîné du vélo)
SD_POWER_MAX = 5.0               # W : au-delà, l'ERG n'a pas tenu → analyse ERG invalide
CAD_DELTA_MAX = 3.0              # rpm entre moitiés : au-delà, la dérive n'est pas rendue
MIN_BLOC_S = 20 * 60


def ols(xs, ys):
    """Régression linéaire simple → (pente, ordonnée, R²)."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return b, a, (1 - sse / sst if sst > 0 else 0.0)


def ols2(x1, x2, ys):
    """Régression multiple FC ~ temps + cadence → pente TEMPORELLE AJUSTÉE sur la cadence.
    ⚠️ Sans cet ajustement, on attribue au temps ce qui est dû au couple : en ERG la cadence
    est le seul degré de liberté restant, et 10 rpm de glissement déplacent la FC de
    plusieurs bpm à puissance identique."""
    n = len(ys)
    if n < 10:
        return None
    m1, m2, my = sum(x1) / n, sum(x2) / n, sum(ys) / n
    a1 = [v - m1 for v in x1]
    a2 = [v - m2 for v in x2]
    ay = [v - my for v in ys]
    s11 = sum(v * v for v in a1)
    s22 = sum(v * v for v in a2)
    s12 = sum(p * q for p, q in zip(a1, a2))
    s1y = sum(p * q for p, q in zip(a1, ay))
    s2y = sum(p * q for p, q in zip(a2, ay))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-9:
        return None
    b1 = (s22 * s1y - s12 * s2y) / det          # pente temporelle ajustée
    b2 = (s11 * s2y - s12 * s1y) / det          # effet cadence (bpm/rpm)
    return b1, b2


def median(xs):
    s = sorted(xs)
    n = len(s)
    return None if not s else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def sd(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def smooth_w(t, w, win_s=60):
    """Puissance lissée sur win_s secondes. Indispensable : la puissance BRUTE à 1 Hz
    oscille de 7-10 W même en ERG parfait (coup de pédale). C'est la moyenne qui est
    asservie, pas l'instantané."""
    n = len(t)
    out = [None] * n
    a = b = 0
    for i in range(n):
        while a < i and t[i] - t[a] > win_s / 2:
            a += 1
        while b < n - 1 and t[b + 1] - t[i] <= win_s / 2:
            b += 1
        seg = [w[k] for k in range(a, b + 1) if w[k] is not None]
        out[i] = sum(seg) / len(seg) if seg else None
    return out


def detect_bloc(t, w):
    """Bloc principal = la plus longue fenêtre où la puissance LISSÉE tient dans une
    fourchette étroite (max − min ≤ 2×SD_POWER_MAX). Fenêtre glissante MAXIMALE
    (deux pointeurs + déques monotones), et non gloutonne : l'ancienne version
    ancrait sa fenêtre dans la fin de la rampe d'échauffement, cassait en plein
    plateau quand la fourchette débordait, et fragmentait un plateau de 25 min en
    deux morceaux < 20 min (bug vu sur i168567463, 23/07/2026 → « aucun bloc »).
    On juge la stabilité sur la moyenne glissante (ce que l'ERG asservit), pas sur
    l'instantané. Jamais de bornes en dur : autopause et démarrage décalé rendraient
    tout calcul faux."""
    from collections import deque
    pm = smooth_w(t, w, 60)
    n = len(t)
    best = None
    maxq, minq = deque(), deque()          # indices ; pm décroissante / croissante
    a = 0
    for b in range(n):
        if pm[b] is None:                  # trou de flux : la fenêtre ne le traverse pas
            maxq.clear(); minq.clear()
            a = b + 1
            continue
        while maxq and pm[maxq[-1]] <= pm[b]:
            maxq.pop()
        maxq.append(b)
        while minq and pm[minq[-1]] >= pm[b]:
            minq.pop()
        minq.append(b)
        while pm[maxq[0]] - pm[minq[0]] > 2 * SD_POWER_MAX:
            a += 1                         # rétrécir par la gauche jusqu'à retenir la fourchette
            if maxq[0] < a:
                maxq.popleft()
            if minq[0] < a:
                minq.popleft()
        dur = t[b] - t[a]
        if dur >= MIN_BLOC_S and (not best or dur > best[2]):
            best = (a, b, dur)
    return best


def analyse(aid):
    act = H.api(f"/activity/{aid}")
    S = H.load_streams(aid)
    t = S["time"]
    w = S.get("watts")
    hr = S.get("heartrate")
    cad = S.get("cadence")
    if not (w and hr):
        sys.exit("⛔ flux puissance ou FC absent")

    res = {"activity_id": aid, "date": act.get("start_date_local", "")[:10],
           "nom": (act.get("name") or "").strip(), "protocole": "HT_ERG_CONTINU_v1",
           "gates": {}, "alertes": []}

    # ---------------- portillons bloquants ----------------
    # Si l'un échoue, le paramètre concerné n'est PAS rendu : mieux vaut ne rien dire
    # qu'afficher un chiffre ininterprétable.
    dt = H.sampling_dt(t)
    res["gates"]["echantillonnage_s"] = dt
    res["gates"]["trainer"] = bool(act.get("trainer"))
    res["gates"]["ftp_utilisee"] = act.get("icu_ftp")
    res["gates"]["lthr_declaree"] = act.get("lthr")

    bloc = detect_bloc(t, w)
    if not bloc:
        sys.exit("⛔ aucun bloc principal stable ≥ 20 min détecté")
    i0, i1, dur = bloc
    t0 = t[i0]
    seg_w = [w[i] for i in range(i0, i1 + 1) if w[i] is not None]
    p_cible = median(seg_w)
    sd_p = sd(seg_w)                                   # bruit du coup de pédale (~8 W, normal)
    pm = smooth_w(t, w, 30)
    sd_lisse = sd([pm[i] for i in range(i0, i1 + 1) if pm[i] is not None])
    res["bloc"] = {"debut_min": round(t0 / 60, 1), "duree_min": round(dur / 60, 1),
                   "puissance_W": round(p_cible), "sd_brute_W": round(sd_p, 1),
                   "sd_lissee_W": round(sd_lisse, 1)}

    # G2 — l'ERG a-t-il tenu ? Jugé sur la puissance LISSÉE 30 s : c'est la moyenne que
    # le home-trainer asservit. La SD brute (~8 W) est le coup de pédale, pas un défaut d'ERG.
    erg_ok = sd_lisse <= SD_POWER_MAX
    res["gates"]["erg_tenu"] = erg_ok
    if not erg_ok:
        res["alertes"].append(f"ERG non tenu (SD lissée {sd_lisse:.1f} W > {SD_POWER_MAX}) — "
                              f"les métriques à puissance fixe ne s'appliquent pas")

    # G3 — complétude FC sur le bloc
    hr_bloc = [hr[i] for i in range(i0, i1 + 1)]
    compl = sum(1 for x in hr_bloc if x is not None) / max(1, len(hr_bloc))
    res["gates"]["completude_fc"] = round(100 * compl)
    if compl < 0.95:
        res["alertes"].append(f"flux FC incomplet ({100*compl:.0f} %)")

    def win(a_s, b_s):
        return [i for i in range(i0, i1 + 1)
                if a_s <= t[i] - t0 <= b_s and hr[i] is not None]

    # ---------------- ① FC de référence iso-puissance ----------------
    idx = win(*W_REF)
    fc_ref = median([hr[i] for i in idx]) if idx else None
    res["fc_ref"] = None
    if fc_ref:
        res["fc_ref"] = {
            "bpm": round(fc_ref, 1),
            "fenetre": "min 8→12 du bloc",
            "bpm_par_W": round(fc_ref / p_cible, 4),
            "pct_fcmax_velo": round(100 * fc_ref / HR_MAX_BIKE, 1),
            "delta_plafond_IM": round(fc_ref - PLAFOND_IM, 1),
        }
        # Contrôle de validité : le bloc était-il vraiment du LIT ?
        if fc_ref < PLAFOND_IM:
            res["fc_ref"]["verdict"] = "LIT confirmé — la suite est interprétable"
        elif fc_ref < (act.get("lthr") or 171) - 10:
            res["fc_ref"]["verdict"] = "ZONE GRISE — interpréter avec réserve"
        else:
            res["fc_ref"]["verdict"] = "NON-LIT — découplage non interprétable ; FTP à retester"
            res["alertes"].append("FC trop haute pour 68 % de FTP → FTP probablement périmée")

    # ---------------- ② dérive de FC, AJUSTÉE SUR LA CADENCE ----------------
    idx = win(*W_DRIFT)
    res["derive"] = {"mesurable": False}
    if len(idx) >= 60:
        ts = [(t[i] - t0) / 60.0 for i in idx]          # minutes
        hs = [float(hr[i]) for i in idx]
        cs = [float(cad[i]) for i in idx] if cad and all(cad[i] is not None for i in idx) else None

        # invalidation : glissement de cadence entre les deux moitiés
        cad_ok, dcad = True, None
        if cs:
            h = len(cs) // 2
            dcad = (sum(cs[h:]) / len(cs[h:])) - (sum(cs[:h]) / len(cs[:h]))
            cad_ok = abs(dcad) <= CAD_DELTA_MAX

        brut = ols(ts, hs)
        d = {"mesurable": True, "fenetre": "min 10→40 du bloc",
             "n_points": len(idx), "duree_min": round((ts[-1] - ts[0]), 1),
             "pente_brute_bpm_min": round(brut[0], 3) if brut else None,
             "r2": round(brut[2], 2) if brut else None,
             "delta_cadence_moities_rpm": round(dcad, 1) if dcad is not None else None}

        if cs and cad_ok:
            aj = ols2(ts, cs, hs)
            if aj:
                d["pente_ajustee_bpm_min"] = round(aj[0], 3)
                d["effet_cadence_bpm_par_rpm"] = round(aj[1], 3)
        elif cs and not cad_ok:
            d["mesurable"] = False
            d["raison"] = (f"cadence glissée de {dcad:+.1f} rpm entre les moitiés "
                           f"(> {CAD_DELTA_MAX}) — la dérive n'est pas attribuable au temps")
            res["alertes"].append(d["raison"])

        pente = d.get("pente_ajustee_bpm_min", d.get("pente_brute_bpm_min"))
        if d["mesurable"] and pente is not None:
            tot = pente * d["duree_min"]
            d["derive_totale_bpm"] = round(tot, 1)
            d["bpm_par_10min"] = round(pente * 10, 2)
            d["bpm_par_h"] = round(pente * 60, 1)
            # R² faible → dérive non monotone : on rend une amplitude, pas une pente
            if brut and brut[2] < 0.3:
                a = [hr[i] for i in idx if (t[i] - t0) <= W_DRIFT[0] + 300]
                b = [hr[i] for i in idx if (t[i] - t0) >= W_DRIFT[1] - 300]
                if a and b:
                    d["amplitude_observee_bpm"] = round(median(b) - median(a), 1)
                d["note"] = "R² < 0,3 : dérive non monotone, lire l'amplitude plutôt que la pente"
            if abs(tot) < 3:
                d["lecture"] = "dans le bruit (< 3 bpm sur la fenêtre)"
            elif abs(tot) <= 8:
                d["lecture"] = "à noter — chercher le contexte thermique / hydrique"
            else:
                d["lecture"] = "marquée — pièce chaude, ventilation, hydratation, ou intensité réelle > LT1"
        res["derive"] = d

    # ---------------- ③ cadence ----------------
    cs_all = [cad[i] for i in range(i0, i1 + 1) if cad and cad[i]]
    if cs_all:
        res["cadence"] = {"moyenne_rpm": round(sum(cs_all) / len(cs_all), 1),
                          "mediane_rpm": round(median(cs_all), 1),
                          "sd_rpm": round(sd(cs_all), 1),
                          "consigne": "90-100 rpm, couple léger (gêne droit fémoral)"}
        m = res["cadence"]["moyenne_rpm"]
        res["cadence"]["consigne_respectee"] = 88 <= m <= 102
        if not res["cadence"]["consigne_respectee"]:
            res["alertes"].append(f"cadence moyenne {m} rpm hors consigne 90-100 "
                                  f"→ couple plus élevé sur le droit fémoral")

    # ---------------- ④ TSS et rapport au CTL ----------------
    np_w = act.get("icu_weighted_avg_watts") or act.get("np_watts")
    dur_tot = act.get("moving_time") or (t[-1] - t[0])
    ftp_used = act.get("icu_ftp") or FTP
    if np_w and dur_tot:
        tss = dur_tot * (np_w ** 2) / ((ftp_used ** 2) * 36)
        res["charge"] = {"tss": round(tss, 1), "np_W": np_w, "ftp_utilisee": ftp_used}
        try:
            import csv as _csv
            rows = list(_csv.DictReader(open("data/fitness.csv")))
            ctl = float(rows[-1]["ctl"]) if rows else None
            if ctl and ctl > 0:
                res["charge"]["ctl"] = ctl
                res["charge"]["tss_sur_ctl"] = round(tss / ctl, 1)
                # Le chiffre utile est RELATIF : la grille absolue TrainingPeaks est
                # calibrée sur des athlètes chargés, pas sur un CTL de 9.
                if tss / ctl >= 3:
                    res["charge"]["lecture"] = (f"choc de reprise réel : {tss/ctl:.1f}× le CTL "
                                                f"malgré une valeur absolue faible")
        except Exception:
            pass

    # ---------------- contexte (covariables de la dérive) ----------------
    # Sans hydratation ni température, une dérive n'est pas ATTRIBUABLE : fatigue,
    # chaleur et déshydratation produisent le même signal.
    try:
        import journal_contexte as JC
        ctx = next((e for e in JC.load() if e.get("session_id") == aid), None)
    except Exception:
        ctx = None
    res["contexte"] = ctx
    if ctx is None:
        res["alertes"].append("contexte absent (température, ventilateur, hydratation, RPE) "
                              "→ la dérive n'est pas attribuable ; séance HORS série comparative")
    else:
        d = ctx.get("derive", {})
        for k, v in d.items():
            if k.startswith("alerte"):
                res["alertes"].append(v)

    # ---------------- travail total ----------------
    kj = 0.0
    for i in range(1, len(t)):
        d_t = t[i] - t[i - 1]
        if w[i] is not None and 0 < d_t <= H.gap_threshold(t):
            kj += w[i] * d_t
    res["travail_kJ"] = round(kj / 1000)

    return res


def report(r):
    print(f"🚴 {r['nom']}")
    print(f"   {r['date']} · protocole {r['protocole']}")
    g = r["gates"]
    print(f"\n⚙️  CONTRÔLES  échantillonnage {g['echantillonnage_s']:.0f} s · "
          f"trainer {g['trainer']} · FTP utilisée {g['ftp_utilisee']} W · LTHR {g['lthr_declaree']}")
    b = r["bloc"]
    print(f"   bloc principal : {b['duree_min']} min à {b['puissance_W']} W "
          f"(SD lissée {b['sd_lissee_W']} W, brute {b['sd_brute_W']} W) · "
          f"ERG tenu : {'✅' if g['erg_tenu'] else '❌'} · "
          f"FC complète à {g['completude_fc']} %")

    f = r.get("fc_ref")
    print(f"\n① FC DE RÉFÉRENCE ISO-PUISSANCE ({b['puissance_W']} W)")
    if f:
        print(f"   {f['bpm']} bpm  ({f['fenetre']})")
        print(f"   {f['bpm_par_W']} bpm/W · {f['pct_fcmax_velo']} % FCmax vélo · "
              f"{f['delta_plafond_IM']:+.1f} bpm vs plafond IM ({PLAFOND_IM})")
        print(f"   → {f['verdict']}")
    else:
        print("   non calculable")

    d = r.get("derive", {})
    print(f"\n② DÉRIVE DE FC (ajustée sur la cadence)")
    if not d.get("mesurable"):
        print(f"   non mesurable — {d.get('raison', 'fenêtre insuffisante')}")
    else:
        p = d.get("pente_ajustee_bpm_min")
        lbl = "ajustée cadence" if p is not None else "brute"
        p = p if p is not None else d.get("pente_brute_bpm_min")
        print(f"   {d['derive_totale_bpm']:+.1f} bpm sur {d['duree_min']:.0f} min "
              f"({d['bpm_par_10min']:+.2f} bpm/10min · {d['bpm_par_h']:+.1f} bpm/h) [{lbl}]")
        print(f"   R² {d['r2']} · cadence : {d.get('delta_cadence_moities_rpm'):+.1f} rpm entre moitiés"
              if d.get("delta_cadence_moities_rpm") is not None else f"   R² {d['r2']}")
        if d.get("effet_cadence_bpm_par_rpm") is not None:
            print(f"   effet cadence mesuré : {d['effet_cadence_bpm_par_rpm']:+.2f} bpm/rpm")
        if d.get("note"):
            print(f"   ⚠️  {d['note']}")
        if d.get("amplitude_observee_bpm") is not None:
            print(f"   amplitude observée : {d['amplitude_observee_bpm']:+.1f} bpm")
        print(f"   → {d.get('lecture')}")

    c = r.get("cadence")
    if c:
        print(f"\n③ CADENCE  {c['moyenne_rpm']} rpm (médiane {c['mediane_rpm']}, SD {c['sd_rpm']})"
              f"  {'✅' if c['consigne_respectee'] else '⚠️'} consigne {c['consigne']}")

    ch = r.get("charge")
    if ch:
        print(f"\n④ CHARGE  TSS {ch['tss']} (NP {ch['np_W']} W / FTP {ch['ftp_utilisee']} W)"
              + (f" · CTL {ch['ctl']} → {ch['tss_sur_ctl']}× le CTL" if ch.get("tss_sur_ctl") else ""))
        if ch.get("lecture"):
            print(f"   → {ch['lecture']}")
    print(f"\n   Travail total : {r['travail_kJ']} kJ")

    ctx = r.get("contexte")
    if ctx:
        d = ctx.get("derive", {})
        print("\n⑤ CONTEXTE")
        bits = []
        if ctx.get("temp_piece_c") is not None: bits.append(f"{ctx['temp_piece_c']} °C")
        if ctx.get("ventilateur"): bits.append(f"ventilo {ctx['ventilateur']}")
        if ctx.get("rpe"): bits.append(f"RPE {ctx['rpe']}")
        if bits: print("   " + " · ".join(bits))
        if d.get("sudation_l_h"): print(f"   sudation {d['sudation_l_h']} L/h · "
                                        f"perte {d.get('perte_masse_pct')} % de masse")
        if d.get("boisson_ml_h") is not None: print(f"   boisson {d['boisson_ml_h']} mL/h")
        if d.get("glucides_g_h") is not None: print(f"   glucides {d['glucides_g_h']} g/h")

    if r["alertes"]:
        print("\n⚠️  ALERTES")
        for a in r["alertes"]:
            print(f"   • {a}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    r = analyse(sys.argv[1])
    report(r)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n💾 → {STORE}")
