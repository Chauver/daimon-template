#!/usr/bin/env bash
# maj.sh — met à jour tes données ET les publie sur l'appli hébergée.
# Enchaîne : pipeline (recalcul) → commit → push GitHub → Cloudflare Pages redéploie tout seul.
# Usage quotidien : ./maj.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== Recalcul (pipeline) =="
./run_pipeline.sh

echo ""
echo "== Publication sur l'appli =="
git add -A
if git diff --cached --quiet; then
  echo "Pas de nouvelles données ; je pousse quand même les commits en attente."
else
  git commit -m "maj données $(date +%F_%H%M)"
fi
git push && echo "✅ Poussé sur GitHub — Cloudflare redéploie l'appli dans ~1 min." \
         || echo "⚠️  Rien à pousser, ou push impossible (vérifie ta connexion)."
