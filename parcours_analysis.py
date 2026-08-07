#!/usr/bin/env python3
"""
parcours_analysis.py — Analyse d'un tracé GPX de COURSE (reconnaissance de parcours).

Détecte et caractérise chaque bosse d'un GPX (sans données de capteurs — c'est le TERRAIN
qu'on analyse, en amont de la course). Créé le 02/08/2026 pour le vélo des Sables 2025
(`parcours/sables_2025_velo.gpx`) ; resservira pour le tracé 2027 quand il sera publié,
pour Lacanau, et pour la CAP.

Méthode : distance haversine cumulée → rééchantillonnage tous les 10 m → altitude lissée
(fenêtre ~90 m) → pente sur fenêtres de 100 m → bosse = pente ≥ 2,5 % soutenue, trous
≤ 150 m fusionnés, D+ ≥ 8 m. « % max » = pente max sur 100 m à l'intérieur de la bosse.

Usage : python3 parcours_analysis.py parcours/sables_2025_velo.gpx [--seuil 2.5]
"""
from __future__ import annotations
import math, sys
import xml.etree.ElementTree as ET


def load_gpx(path):
    t = ET.parse(path)
    root = t.getroot()
    ns = {"g": root.tag.split("}")[0].strip("{")}
    pts = []
    for p in root.findall(".//g:trkpt", ns):
        e = p.find("g:ele", ns)
        if e is not None:
            pts.append((float(p.get("lat")), float(p.get("lon")), float(e.text)))
    return pts


def haversine(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def resample(pts, step=10.0):
    """(distance_m, ele) tous les `step` mètres."""
    d = [0.0]
    for i in range(1, len(pts)):
        d.append(d[-1] + haversine(pts[i - 1], pts[i]))
    out_d, out_e = [0.0], [pts[0][2]]
    target, i = step, 1
    while target < d[-1] and i < len(pts):
        while i < len(pts) and d[i] < target:
            i += 1
        if i >= len(pts):
            break
        f = (target - d[i - 1]) / max(1e-9, d[i] - d[i - 1])
        out_d.append(target)
        out_e.append(pts[i - 1][2] + f * (pts[i][2] - pts[i - 1][2]))
        target += step
    return out_d, out_e


def smooth(xs, n=9):
    out, m = [], len(xs)
    for i in range(m):
        a, b = max(0, i - n // 2), min(m, i + n // 2 + 1)
        out.append(sum(xs[a:b]) / (b - a))
    return out


def detect_bosses(dist, ele, seuil=2.5, fenetre_m=100, gap_m=150, dplus_min=8.0):
    step = dist[1] - dist[0]
    W, G = int(fenetre_m / step), int(gap_m / step)
    n = len(dist)
    grade = [0.0] * n
    for i in range(W, n):
        grade[i] = (ele[i] - ele[i - W]) / (fenetre_m) * 100
    up = [g >= seuil for g in grade]
    bosses, i = [], 0
    while i < n:
        if up[i]:
            j, k, gap = i, i, 0
            while k < n and gap <= G:
                if up[k]:
                    j, gap = k, 0
                else:
                    gap += 1
                k += 1
            a = max(0, i - W)                     # la fenêtre regarde derrière : la bosse commence avant
            dplus = ele[j] - ele[a]
            L = dist[j] - dist[a]
            if dplus >= dplus_min and L > 0:
                gmax = max(grade[a + W:j + 1]) if j >= a + W else grade[j]
                bosses.append({
                    "km": round(dist[a] / 1000, 1), "longueur_m": round(L),
                    "d_plus_m": round(dplus, 1), "pente_pct": round(dplus / L * 100, 1),
                    "pente_max_pct": round(gmax, 1),
                    "alt_pied": round(ele[a]), "alt_sommet": round(ele[j]),
                })
            i = k
        else:
            i += 1
    return bosses


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    seuil = float(sys.argv[sys.argv.index("--seuil") + 1]) if "--seuil" in sys.argv else 2.5
    pts = load_gpx(sys.argv[1])
    dist, ele_raw = resample(pts)
    ele = smooth(ele_raw, 9)
    dplus_total = sum(max(0.0, ele[i] - ele[i - 1]) for i in range(1, len(ele)))
    bosses = detect_bosses(dist, ele, seuil)
    print(f"📍 {sys.argv[1]} — {dist[-1]/1000:.1f} km · D+ recalculé {dplus_total:.0f} m "
          f"(lissage 90 m) · {len(pts)} points GPX")
    print(f"   altitude min {min(ele):.0f} m · max {max(ele):.0f} m")
    print(f"\n   BOSSES (pente ≥ {seuil} % sur ≥ 100 m, D+ ≥ 8 m) : {len(bosses)}\n")
    print(f"   {'#':>2} {'km':>6} {'long':>6} {'D+':>6} {'%moy':>5} {'%max':>5} {'alt':>9}")
    for i, b in enumerate(bosses, 1):
        print(f"   {i:>2} {b['km']:>5.1f} {b['longueur_m']:>5d}m {b['d_plus_m']:>5.1f}m "
              f"{b['pente_pct']:>5.1f} {b['pente_max_pct']:>5.1f} {b['alt_pied']:>3d}→{b['alt_sommet']:<3d}m")
    cumul = sum(b["d_plus_m"] for b in bosses)
    print(f"\n   D+ total dans les bosses : {cumul:.0f} m ({100*cumul/max(1,dplus_total):.0f} % du D+ du parcours)")


if __name__ == "__main__":
    main()
