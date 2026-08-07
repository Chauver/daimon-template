#!/usr/bin/env python3
"""build_kpi.py — génère KPI.md, la fiche d'état actualisée de l'athlète.

Source unique : web/coach_state.json (régénéré à chaque pipeline → la fiche reste à jour).
Les CIBLES (objectif course) sont dans CIBLES ci-dessous (miroir de CLAUDE.md §1-2).
Lancer :  python3 build_kpi.py   (inclus dans run_pipeline.sh)
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "web" / "coach_state.json"
OUT = ROOT / "KPI.md"

# Cibles course (miroir CLAUDE.md §1-2 ; à réactualiser si l'objectif bouge).
CIBLES = {
    "course": "IRONMAN Les Sables d'Olonne — dimanche 27/06/2027",
    "objectif": "sub-9h50 (repère Cascais 2024 = 9h50)",
    "splits": "Nat ~1h00 · Vélo ~230 W NP · CAP ~4:40/km",
    "ftp_cible": 289, "allure_cible": "4:10/km",
    "fc_max_course": 186, "fc_max_velo": 180, "fc_repos_base": 41,
    "base_2026": "≈350 km CAP + 1500 km vélo depuis janvier 2026",
}


def f(x, d="—"):
    return x if x not in (None, "") else d


def main():
    if not STATE.exists():
        sys.exit(f"❌ {STATE} introuvable — lance run_pipeline.sh d'abord.")
    d = json.loads(STATE.read_text())
    prepa = d.get("prepa", {}); forme = d.get("forme", {}); charge = forme.get("charge", {})
    vol = d.get("volume_course", {}); seuils = d.get("seuils", {}); bl = d.get("blessure", {})
    dsem = d.get("derniere_semaine", {}); cp = d.get("charge_planifiee", {})
    fc_seuil = (seuils.get("fc_seuil") or {}).get("valeur", 174)
    z2 = round(fc_seuil * 0.83)  # repère haut de zone EF

    def seuil_line(k, emoji):
        s = seuils.get(k, {})
        return f"| {emoji} {f(s.get('label'))} | **{f(s.get('valeur'))} {f(s.get('unite'),'')}** | {f(s.get('statut'))} |"

    jauges = " · ".join(f"{g['label']} {g['valeur']}" for g in d.get("jauges", []))
    L = []
    L.append("# FICHE KPI — état actualisé de l'athlète")
    L.append("")
    L.append(f"> Généré automatiquement par `build_kpi.py` depuis `web/coach_state.json`. "
             f"**Ne pas éditer à la main** (régénéré à chaque `run_pipeline.sh`). Généré le **{f(d.get('genere_le'))}**.")
    L.append("")
    L.append(f"**Prépa :** semaine **S{f(prepa.get('semaine'))}/{f(prepa.get('total'))}** · "
             f"bloc base {f(prepa.get('base'))} · {f(prepa.get('pct'))} % du plan · "
             f"**J-{f(prepa.get('j_moins'))}** ({f(prepa.get('date_label'))})")
    L.append("")
    L.append("## 🎯 Objectif & cibles")
    L.append(f"- **Course :** {CIBLES['course']}")
    L.append(f"- **Objectif :** {CIBLES['objectif']}")
    L.append(f"- **Splits visés :** {CIBLES['splits']}")
    L.append(f"- **Base 2026 :** {CIBLES['base_2026']}")
    L.append("")
    L.append("## 📊 Seuils de référence (actuel → cible)")
    L.append("| Seuil | Valeur actuelle | Statut |")
    L.append("|---|---|---|")
    L.append(seuil_line("ftp", "🚴"))
    L.append(seuil_line("allure_run", "🏃"))
    L.append(seuil_line("css", "🏊"))
    L.append(seuil_line("fc_seuil", "❤️"))
    L.append(f"\n**Cibles :** FTP **{CIBLES['ftp_cible']} W** (actuel {f((seuils.get('ftp') or {}).get('valeur'))}) · "
             f"allure seuil **{CIBLES['allure_cible']}**. "
             f"FC max {CIBLES['fc_max_course']} (course) / {CIBLES['fc_max_velo']} (vélo) · "
             f"FC repos base ~{CIBLES['fc_repos_base']}.")
    L.append(f"\n**Repère zone EF/LIT :** FC ≤ ~**{z2}** bpm (≈83 % du seuil {fc_seuil}) — cible du travail facile (allure libre).")
    L.append("")
    L.append("## 🩺 Forme du jour")
    L.append(f"- **Indice : {f(forme.get('indice'))}/100 ({f(forme.get('couleur'))})** — {f(forme.get('guide'))}")
    L.append(f"- **Charge :** CTL {f(charge.get('ctl'))} · ATL {f(charge.get('atl'))} · **TSB {f(charge.get('tsb'))}**")
    L.append(f"- **Jauges :** {f(jauges)}")
    L.append("")
    L.append("## 🩹 Blessure / vigilance")
    trig = "; ".join(bl.get("triggers", [])) or "aucun"
    L.append(f"- **Compteur : {f(bl.get('level')).upper()}** — {trig}")
    L.append(f"- **Volume course semaine :** {f(vol.get('semaine_km'))} / {f(vol.get('plafond'))} km "
             f"(plafond) — statut {f(vol.get('statut'))}")
    L.append("")
    L.append("## 📅 Charge")
    L.append(f"- **Dernière semaine (S{f(dsem.get('semaine'))}) :** course {f(dsem.get('course_km'))} km · "
             f"vélo {f(dsem.get('velo_h'))} h · nat {f(dsem.get('nat_km'))} km")
    L.append(f"- **Pic de charge planifié :** {f(cp.get('pic_h'))} h/sem (semaine S{f(cp.get('pic_s'))})")
    L.append("")
    L.append("---")
    L.append("*Fiche générée — pour les règles de programmation voir `doctrine/regles_programmation.md`, "
             "pour la méthode `doctrine/methodologie.md`, pour l'historique `journal/`.*")
    OUT.write_text("\n".join(L) + "\n")
    print(f"✅ {OUT.name} généré (S{prepa.get('semaine')}, forme {forme.get('indice')} {forme.get('couleur')}).")


if __name__ == "__main__":
    main()
