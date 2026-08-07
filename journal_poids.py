#!/usr/bin/env python3
"""journal_poids.py — historique de poids corporel, jour par jour (suivi longitudinal).

POURQUOI. Le poids est une covariable à double lecture : (1) en TENDANCE (semaines/mois), il suit
la composition corporelle sur la prépa — pertinent pour le rapport puissance/poids au vélo et le
coût de la course à pied ; (2) en VARIATION COURTE (jour à jour), il reflète surtout l'hydratation
et le glycogène — utile pour contextualiser une dérive cardiaque ou préparer le plan hydrique des
Sables (cf. D23 : ~1 % de masse perdue ≈ +3 bpm). Ne JAMAIS interpréter un point isolé : l'eau
fait fluctuer ±1 kg d'un jour à l'autre sans aucun sens tendanciel.

PROTOCOLE (décision athlète 24/07/2026). Pesée MATINALE, NU, À JEUN, après les toilettes —
toujours dans les mêmes conditions, sinon le chiffre n'est pas comparable. Le slot existe pour
CHAQUE jour mais n'est rempli que quand l'athlète donne la valeur : les trous sont normaux et la
courbe les traverse (pas d'interpolation stockée).

STOCKAGE. journal/poids.jsonl — une ligne = un jour. Ajout append-only ; si un jour est re-loggé,
la dernière saisie fait foi (dédup à la lecture). Mémoire durable et versionnée (comme alcool et
ressenti blessure), pas dans data/ (ignoré/volatile).

USAGE.
    python3 journal_poids.py add --date 2026-07-23 --kg 70.7
    python3 journal_poids.py add --date 2026-07-23 kg=70.7 --note "..."   # forme courte
    python3 journal_poids.py recall            # derniers relevés + tendance
    python3 journal_poids.py stats             # moyennes 7 j / 30 j, tendance
    python3 journal_poids.py courbes           # export data/poids_courbes.csv (pour tracer)
    python3 journal_poids.py last              # dernier relevé (JSON)
"""
import sys, json, csv
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).parent
STORE = ROOT / "journal" / "poids.jsonl"
CURVES = ROOT / "data" / "poids_courbes.csv"

# Bornes de vraisemblance : hors de cette plage, c'est une faute de frappe, pas un poids.
KG_MIN, KG_MAX = 45.0, 110.0


def load():
    if not STORE.exists():
        return []
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]


def by_day(rows):
    """Un relevé par jour, la dernière saisie l'emporte, trié par date croissante."""
    m = {}
    for r in rows:
        if r.get("date"):
            m[r["date"]] = r
    return [m[k] for k in sorted(m)]


def _num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def add(argv):
    d = note = None
    kg = None
    # --clé=valeur  et  kg=valeur
    for a in argv:
        if a.startswith("--date="):   d = a.split("=", 1)[1]
        elif a.startswith("--kg="):   kg = a.split("=", 1)[1]
        elif a.startswith("--note="): note = a.split("=", 1)[1]
        elif a.startswith("kg="):     kg = a.split("=", 1)[1]
    # --clé valeur (séparés)
    it = iter(argv)
    for a in it:
        if a == "--date":   d = next(it, d)
        elif a == "--kg":   kg = next(it, kg)
        elif a == "--note": note = next(it, note)
    if not d:
        sys.exit("❌ --date requis (YYYY-MM-DD)")
    if kg is None:
        sys.exit("❌ --kg requis (poids matinal, nu, à jeun)")
    v = _num(kg)
    if v is None or not (KG_MIN <= v <= KG_MAX):
        sys.exit(f"❌ --kg doit être un nombre entre {KG_MIN:g} et {KG_MAX:g} (reçu {kg!r})")
    entry = {"date": d, "kg": round(v, 1), "note": note or ""}
    already = any(r.get("date") == d for r in load())
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    verb = "↺ mis à jour" if already else "✅ ajouté"
    print(f"{verb} ({d}) — {entry['kg']:g} kg ⚖️{' · ' + note if note else ''}")


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _window_mean(rows, last_day, days):
    """Moyenne des relevés présents dans la fenêtre [last_day − days + 1, last_day]."""
    lo = last_day - timedelta(days=days - 1)
    vals = [r["kg"] for r in rows
            if lo <= date.fromisoformat(r["date"]) <= last_day and r.get("kg") is not None]
    return _mean(vals), len(vals)


def recall(n=14):
    rows = by_day(load())[-n:]
    if not rows:
        print("(aucun relevé de poids)")
        return
    print(f"— poids, {len(rows)} dernier(s) relevé(s) (matin, nu, à jeun) —\n")
    prev = None
    for r in rows:
        delta = f"  ({r['kg'] - prev:+.1f})" if prev is not None else ""
        note = f"  — {r['note']}" if r.get("note") else ""
        print(f"  {r['date']}  {r['kg']:5.1f} kg{delta}{note}")
        prev = r["kg"]
    print()
    _print_stats(rows_all=load())


def _print_stats(rows_all=None):
    rows = by_day(rows_all if rows_all is not None else load())
    if not rows:
        print("(aucun relevé)")
        return
    last_day = date.fromisoformat(rows[-1]["date"])
    m7, n7 = _window_mean(rows, last_day, 7)
    m30, n30 = _window_mean(rows, last_day, 30)
    print(f"  Dernier : {rows[-1]['kg']:g} kg ({rows[-1]['date']})")
    if m7 is not None:
        print(f"  Moyenne 7 j : {m7:.1f} kg ({n7} relevé(s))")
    if m30 is not None:
        print(f"  Moyenne 30 j : {m30:.1f} kg ({n30} relevé(s))")
    # Tendance : moyenne 7 j vs moyenne des 7 j précédents — jamais point à point (l'eau bruite ±1 kg).
    prev7_vals = [r["kg"] for r in rows
                  if last_day - timedelta(days=13) <= date.fromisoformat(r["date"])
                  <= last_day - timedelta(days=7)]
    if m7 is not None and prev7_vals and n7 >= 2 and len(prev7_vals) >= 2:
        d = m7 - _mean(prev7_vals)
        print(f"  Tendance : {d:+.1f} kg vs 7 j précédents")
    elif len(rows) < 4:
        print("  (base trop mince pour une tendance — on ne conclut pas sur deux points)")


def stats():
    _print_stats()


def last():
    rows = by_day(load())
    print(json.dumps(rows[-1] if rows else {}, ensure_ascii=False))


def courbes():
    rows = by_day(load())
    CURVES.parent.mkdir(parents=True, exist_ok=True)
    with CURVES.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "kg", "note"])
        for r in rows:
            w.writerow([r["date"], r.get("kg", ""), r.get("note", "")])
    print(f"✅ {CURVES} généré ({len(rows)} relevé(s)) — prêt à tracer.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    {
        "add": lambda: add(sys.argv[2:]),
        "recall": recall,
        "stats": stats,
        "last": last,
        "courbes": courbes,
    }.get(cmd, lambda: sys.exit(__doc__))()


if __name__ == "__main__":
    main()
