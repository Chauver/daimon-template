#!/usr/bin/env python3
"""
test_hr_analysis.py — tests de non-régression du module d'analyse cardiaque fine.

Ces tests figent les comportements que la relecture adversariale a trouvés cassés.
Chaque test correspond à un bug RÉEL constaté : ne pas les supprimer sans comprendre
lequel. Lancer :  python3 test_hr_analysis.py
"""
import math
import sys

import hr_analysis as H

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        print(f"  ✅ {label}")
        ok += 1
    else:
        print(f"  ❌ {label}\n       attendu : {want}\n       obtenu  : {got}")
        fail += 1


def typ(v):
    t = list(range(len(v)))
    return H.detect_type(H.find_phases(v, [True] * len(v)), t)[0]


print("① TYPE DE SÉANCE")
# Bug d'origine : la fusion inconditionnelle des phases courtes fabriquait des allures
# fantômes (moyenne travail/récup) et classait toute séance d'intervalles en « continu ».
seq = []
for _ in range(6):
    seq += [4.0] * 250 + [2.4] * 90
check("6×1000 m seuil + récup 90 s → intervalles", typ(seq), "intervalles")

seq = []
for _ in range(10):
    seq += [4.6] * 30 + [2.2] * 60
check("10×30 s VO2 + récup 60 s → intervalles", typ(seq), "intervalles")

# Bug d'origine : le relief fabriquait de fausses alternances (jusqu'à 27 % d'amplitude)
# et un footing continu était classé « intervalles ».
check("footing vallonné ±20 % → continu",
      typ([2.35 + 0.25 * math.sin(i / 180.0) for i in range(3600)]), "continu")
check("EF plat régulier → continu",
      typ([2.6 + 0.03 * math.sin(i / 50.0) for i in range(2700)]), "continu")
check("2 blocs (accompagné puis endurance) → continu",
      typ([2.2] * 1300 + [3.0] * 1700), "continu")

print("\n② DISCONTINUITÉS DU FLUX (arrêt montre / perte GPS)")
# Bug d'origine : fenêtres comptées en INDICES → une fenêtre de « 60 s » pouvait
# enjamber un arrêt de 88 s (cas réel mesuré sur i167079292).
t = list(range(100)) + [i + 200 for i in range(100)]      # trou de 101 s au milieu
lg = H.gaps_before(t)
check("trou détecté", lg[100] is not None, True)
check("fenêtre traversant le trou refusée", H.spans_gap(lg, t, 100, 60), True)
check("fenêtre propre acceptée", H.spans_gap(lg, t, 190, 60), False)

sm = H.smooth([1.0] * 100 + [3.0] * 100, 30, t, lg)
check("le lissage ne franchit pas le trou", round(sm[99], 2), 1.0)

print("\n③ ÉTAT STATIONNAIRE DE LA FC")
# Bug d'origine : le filtre n'écartait que la FC MONTANTE ; une FC qui redescend
# (trot après une côte / récup) entrait dans le profil avec une FC trop haute.
t2 = list(range(60))
up = [120 + 0.2 * i for i in range(60)]        # +12 bpm/min
down = [160 - 0.2 * i for i in range(60)]      # −12 bpm/min
flat = [140 + (i % 3) for i in range(60)]
check("FC montante détectée", round(H.hr_slope_bpm_min(up, t2, 0, 59)) > H.HR_SLOPE_MAX, True)
check("FC descendante détectée (filtre bilatéral)",
      abs(H.hr_slope_bpm_min(down, t2, 0, 59)) > H.HR_SLOPE_MAX, True)
check("FC en plateau acceptée",
      abs(H.hr_slope_bpm_min(flat, t2, 0, 59)) <= H.HR_SLOPE_MAX, True)

print("\n④ DÉRIVE NON CIRCULAIRE")
# Bug d'origine : l'écart de FC était publié BRUT entre deux allures différant jusqu'à
# ±6 %, or 6 % d'allure valent ~5 bpm : on mesurait la pente FC↔allure et on l'appelait
# « dérive cardiaque » (cas réel : « +9,2 bpm » le 16/07, entièrement dû à l'allure).
mets = [
    {"t_start_s": 1000, "dur_s": 120, "v_2h": 3.00, "hr_2h": 140.0},
    {"t_start_s": 2500, "dur_s": 120, "v_2h": 3.10, "hr_2h": 143.0},
]
pairs = H.matched_drift(mets, b_ref=30.0)
check("une paire appariée trouvée", len(pairs), 1)
# brut +3.0 ; l'allure explique 30 × 0.10 = +3.0 ; donc dérive corrigée ≈ 0
check("effet allure retiré → dérive ≈ 0", pairs[0]["delta_corrige_bpm"], 0.0)
check("correction tracée", pairs[0]["correction_allure_bpm"], 3.0)

# Avec 2 points seulement, la pente intra-séance est interdite (elle forcerait 0)
b, src = H.reference_slope(mets, {"b": 27.5})
check("pente de référence prise hors séance", src, "modele_global")

print("\n⑤ HONNÊTETÉ DES SORTIES")
check("dérive non mesurable si < 2 points", H.drift_residual(mets[:1], 30.0), None)
check("modèle refusé sous 6 points", H.fit_hr_model([{"v_ms": 3, "fc": 140}] * 5), None)

print(f"\n{'─' * 50}\n{ok} test(s) OK · {fail} échec(s)")
sys.exit(1 if fail else 0)
