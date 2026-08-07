#!/usr/bin/env python3
"""journal_blessure.py — questionnaire de ressenti blessure (suivi longitudinal objectif).

Après CHAQUE séance, l'agent demande zone par zone « quelque chose à signaler ? » (score 0–10).
On stocke en AJOUT SEULEMENT dans journal/ressenti_blessure.jsonl (une ligne = un relevé).
Ces scores alimentent le compteur blessure (readiness_model.py) et permettront des COURBES dans le temps.

Zones suivies (D/G quand bilatéral) :
    pied · cheville · genou · quadri · hanche · dos (axial) · epaule · dorsaux

Usage :
    python3 journal_blessure.py add --date 2026-07-14 --session i165577684 \
        --note "..." pied_d=1 quadri_d=2 dorsaux=1
    python3 journal_blessure.py recall            # derniers relevés
    python3 journal_blessure.py courbes           # export data/ressenti_blessure_courbes.csv (large, pour tracer)
    python3 journal_blessure.py last              # dernier relevé (JSON) — utilisé par le compteur
"""
import sys, json, csv
from pathlib import Path

ROOT = Path(__file__).parent
STORE = ROOT / "journal" / "ressenti_blessure.jsonl"
CURVES = ROOT / "data" / "ressenti_blessure_courbes.csv"

# Zones canoniques (ordre stable pour les colonnes de courbes). '' = pas de côté (axial).
BILATERAL = ["pied", "cheville", "genou", "quadri", "hanche", "epaule", "dorsaux"]
AXIAL = ["dos"]
# côté d/g quand précisé, + nom nu quand le côté n'est pas donné (ex. « dorsaux 1/10 »).
ZONES = [f"{z}_{c}" for z in BILATERAL for c in ("d", "g")] + BILATERAL + AXIAL
# Zones de la chaîne postérieure / Achille à surveiller de près (antécédent tendon/voûte).
ACHILLE_CHAIN = {"pied", "pied_d", "pied_g", "cheville", "cheville_d", "cheville_g"}


def load():
    if not STORE.exists():
        return []
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]


def add(argv):
    date = session = note = None
    scores = {}
    for a in argv:
        if a.startswith("--date"):   date = a.split("=", 1)[1] if "=" in a else None
        elif a.startswith("--session"): session = a.split("=", 1)[1] if "=" in a else None
        elif a.startswith("--note"): note = a.split("=", 1)[1] if "=" in a else None
        elif "=" in a:
            k, v = a.split("=", 1)
            k = k.strip().lower()
            if k not in ZONES:
                sys.exit(f"❌ zone inconnue: {k}\n   zones valides: {', '.join(ZONES)}")
            # demi-points acceptés (ex. 0.5/10 déclaré 01/08 sur la hanche) ; entier si rond
            try:
                fv = float(v.replace(",", "."))
            except ValueError:
                sys.exit(f"❌ score invalide pour {k}: {v!r} (attendu 0-10, demi-points OK)")
            scores[k] = int(fv) if fv == int(fv) else fv
    # support --date VALUE (séparé) façon simple
    it = iter(argv)
    for a in it:
        if a == "--date": date = next(it, date)
        elif a == "--session": session = next(it, session)
        elif a == "--note": note = next(it, note)
    if not date:
        sys.exit("❌ --date requis (YYYY-MM-DD)")
    entry = {"date": date, "session_id": session, "scores": scores, "note": note or ""}
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    mx = max(scores.values()) if scores else 0
    flag = " ⚠️ chaîne Achille" if any(z in ACHILLE_CHAIN and s > 0 for z, s in scores.items()) else ""
    print(f"✅ relevé ajouté ({date}) — {scores or 'RAS'} · max {mx}/10{flag}")


def recall():
    rows = load()[-12:]
    if not rows:
        print("(aucun relevé)"); return
    print(f"— {len(rows)} derniers relevés de ressenti blessure —\n")
    for r in rows:
        sc = r.get("scores") or {}
        s = " · ".join(f"{k} {v}/10" for k, v in sc.items()) if sc else "RAS"
        n = f"  — {r['note']}" if r.get("note") else ""
        print(f"  {r['date']}  [{r.get('session_id') or '—'}]  {s}{n}")


def last():
    rows = load()
    print(json.dumps(rows[-1] if rows else {}, ensure_ascii=False))


def courbes():
    rows = load()
    header = ["date", "session_id", "max"] + ZONES
    with CURVES.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            sc = r.get("scores") or {}
            mx = max(sc.values()) if sc else 0
            w.writerow([r["date"], r.get("session_id") or "", mx] + [sc.get(z, 0) for z in ZONES])
    print(f"✅ {CURVES} généré ({len(rows)} relevés, {len(ZONES)} zones) — prêt à tracer.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    {"add": lambda: add(sys.argv[2:]), "recall": recall, "last": last, "courbes": courbes}.get(
        cmd, lambda: sys.exit(__doc__))()


if __name__ == "__main__":
    main()
