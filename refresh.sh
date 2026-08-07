#!/usr/bin/env bash
# refresh.sh — met à jour Daïmon EN ARRIÈRE-PLAN (sync intervals.icu → recalcul → push → Cloudflare).
# Appelé par : (1) le hook SessionStart de Claude Code, à chaque ouverture ;
#              (2) éventuellement la règle launchd 07h50 (peut pointer ici aussi).
# Rend la main immédiatement (maj.sh tourne détaché) pour ne pas bloquer l'ouverture de session.

cd "$(dirname "$0")" 2>/dev/null || exit 0
export PATH="/Library/Frameworks/Python.framework/Versions/3.12/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$PATH"

# Anti-doublon : si un maj.sh tourne déjà, ne pas en relancer un second.
if pgrep -f "coach_triathlon/maj.sh" >/dev/null 2>&1; then
  exit 0
fi

nohup bash ./maj.sh >> data/maj_auto.log 2>&1 &
disown 2>/dev/null || true
exit 0
