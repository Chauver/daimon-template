#!/usr/bin/env bash
# run_pipeline.sh — enchaîne la mise à jour quotidienne/hebdomadaire des données.
# Usage : ./run_pipeline.sh   (ou : bash run_pipeline.sh)
# Prérequis : pip install pandas requests ; .env rempli (intervals.icu + seuils).

set -euo pipefail
cd "$(dirname "$0")"

# Mode pipeline : la synchro détecte les séances nouvelles et pose le signal, mais NE recalcule
# pas elle-même l'indice (les étapes 2→3 ci-dessous le font, avec le bon ordre fitness→readiness).
export PIPELINE_RUN=1

echo "== 1/5 · Récupération intervals.icu =="
python3 intervals_sync.py

echo "== 1bis/7 · Seuils de référence (détection des dérives Zwift/Garmin) =="
python3 seuils_tracker.py

echo "== 2/5 · Modèle de charge (TSS / CTL / ATL / TSB) =="
python3 fitness_model.py

echo "== 3/5 · Indice de forme + compteur blessure =="
python3 readiness_model.py

echo "== 4/6 · Génération du tableau de bord =="
python3 build_dashboard.py

echo "== 4bis/7 · Analyse cardiaque fine des nouvelles séances CAP =="
python3 hr_analysis.py --all

echo "== 5/6 · Mémoire du coach (journal des séances) =="
python3 journal.py sync

echo "== 6/7 · Données de l'appli iPhone (web/coach_state.json) =="
python3 build_web_state.py

echo "== 7/7 · Fiche KPI (KPI.md) =="
python3 build_kpi.py

echo ""
echo "✅ Pipeline terminé. Ouvre index.html dans ton navigateur."
echo "   Mémoire du coach : journal/ — la recharger : python3 journal.py recall"
echo "   Appli iPhone : servir web/ (ex. python3 -m http.server -d web 8080)"
