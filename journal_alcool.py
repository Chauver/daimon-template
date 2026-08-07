#!/usr/bin/env python3
"""journal_alcool.py — historique de consommation d'alcool, jour par jour (contexte de récupération).

POURQUOI. L'alcool est un CONFONDANT majeur de l'indice de forme : il dégrade le sommeil profond,
abaisse la HRV et fait monter la FC de repos de la nuit qui suit. Sans le tracer, un matin « HRV
basse / FC repos haute » se lit comme une fatigue d'entraînement — et on lève le pied pour rien.
Avec l'historique, on ATTRIBUE le signal au bon coupable (la fête plutôt que la charge), et
réciproquement on ne MASQUE pas une vraie fatigue derrière « c'était l'apéro ». Cf. CLAUDE.md §7.5.

CONVENTION. On date l'alcool au SOIR de consommation ; son effet récup tombe sur le LENDEMAIN matin
(pour interpréter le wellness du jour J, on regarde l'alcool de J−1). Une « unité » ≈ un verre
standard (~10 g d'alcool pur : 25 cl bière, 10 cl vin, 3 cl spiritueux). 0 = jour sec.

STOCKAGE. journal/alcool.jsonl — une ligne = un jour. Ajout append-only ; si un jour est re-loggé,
la dernière saisie fait foi (dédup à la lecture). Mémoire durable et versionnée (comme le ressenti
blessure), pas dans data/ (ignoré/volatile).

USAGE.
    python3 journal_alcool.py add --date 2026-07-16 --unites 3 --note "apéro entre amis"
    python3 journal_alcool.py add --date 2026-07-16 unites=3            # forme courte
    python3 journal_alcool.py add --date 2026-07-17 --unites 0          # jour sec
    python3 journal_alcool.py recall            # derniers jours (barres)
    python3 journal_alcool.py stats             # cumul 7 j / 30 j, jours secs, moyenne
    python3 journal_alcool.py courbes           # export data/alcool_courbes.csv (pour tracer)
    python3 journal_alcool.py last              # dernier relevé (JSON)
    python3 journal_alcool.py veille 2026-07-17 # alcool de la VEILLE d'une date (contexte récup)
"""
import sys, json, csv
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).parent
STORE = ROOT / "journal" / "alcool.jsonl"
CURVES = ROOT / "data" / "alcool_courbes.csv"


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
        v = float(x)
        return int(v) if v == int(v) else v
    except (TypeError, ValueError):
        return None


def add(argv):
    d = note = None
    unites = None
    # --clé=valeur  et  unites=valeur
    for a in argv:
        if a.startswith("--date="):     d = a.split("=", 1)[1]
        elif a.startswith("--unites="): unites = a.split("=", 1)[1]
        elif a.startswith("--note="):   note = a.split("=", 1)[1]
        elif a.startswith("unites="):   unites = a.split("=", 1)[1]
    # --clé valeur (séparés)
    it = iter(argv)
    for a in it:
        if a == "--date":     d = next(it, d)
        elif a == "--unites": unites = next(it, unites)
        elif a == "--note":   note = next(it, note)
    if not d:
        sys.exit("❌ --date requis (YYYY-MM-DD)")
    if unites is None:
        sys.exit("❌ --unites requis (nombre de verres ; 0 = jour sec)")
    u = _num(unites)
    if u is None or u < 0:
        sys.exit(f"❌ --unites doit être un nombre ≥ 0 (reçu {unites!r})")
    entry = {"date": d, "unites": u, "note": note or ""}
    already = any(r.get("date") == d for r in load())
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    verb = "↺ mis à jour" if already else "✅ ajouté"
    label = "jour sec 🚱" if u == 0 else f"{u} verre(s) 🍷"
    print(f"{verb} ({d}) — {label}{' · ' + note if note else ''}")


def _bar(u, unit_full=6):
    if u == 0:
        return "·"
    n = int(round(u))
    return "█" * min(n, unit_full) + ("…" if n > unit_full else "")


def recall(n=14):
    rows = by_day(load())[-n:]
    if not rows:
        print("(aucun relevé alcool)")
        return
    print(f"— alcool, {len(rows)} derniers jours (unités = verres standard) —\n")
    for r in rows:
        u = r.get("unites", 0)
        note = f"  — {r['note']}" if r.get("note") else ""
        print(f"  {r['date']}  {str(u).rjust(4)}  {_bar(u):<7}{note}")
    print()
    _print_stats(rows_all=load())


def _print_stats(rows_all=None):
    rows = by_day(rows_all if rows_all is not None else load())
    if not rows:
        print("(aucun relevé)")
        return
    idx = {r["date"]: r.get("unites", 0) for r in rows}
    last_day = date.fromisoformat(rows[-1]["date"])

    def window(days):
        tot = 0.0
        secs = 0
        logged = 0
        for i in range(days):
            dd = (last_day - timedelta(days=i)).isoformat()
            v = idx.get(dd)
            if v is not None:
                logged += 1
                tot += v
                if v == 0:
                    secs += 1
        return tot, secs, logged

    t7, s7, l7 = window(7)
    t30, s30, l30 = window(30)
    print(f"  Cumul 7 j : {t7:g} verres · {s7}/{l7} jour(s) sec(s) renseigné(s)")
    print(f"  Cumul 30 j : {t30:g} verres · {s30}/{l30} jour(s) sec(s) renseigné(s)")
    # Repère santé (Santé publique France) : viser ≤ 10 verres/sem ET ≥ 2 jours secs/sem.
    flags = []
    if t7 > 10:
        flags.append(f"⚠️ {t7:g} verres/7 j (repère ≤ 10)")
    if l7 >= 5 and s7 < 2:   # on ne juge les jours secs que si la semaine est bien renseignée
        flags.append(f"⚠️ {s7} jour(s) sec(s)/7 (repère ≥ 2)")
    if flags:
        print("  " + " · ".join(flags))


def stats():
    _print_stats()


def last():
    rows = by_day(load())
    print(json.dumps(rows[-1] if rows else {}, ensure_ascii=False))


def veille(argv):
    """Alcool de la VEILLE d'une date donnée (contexte récup du matin de cette date)."""
    if not argv:
        sys.exit("❌ usage : journal_alcool.py veille YYYY-MM-DD")
    d = date.fromisoformat(argv[0])
    prev = (d - timedelta(days=1)).isoformat()
    idx = {r["date"]: r for r in by_day(load())}
    r = idx.get(prev)
    if not r:
        print(json.dumps({"date": prev, "unites": None, "note": "non renseigné"}, ensure_ascii=False))
    else:
        print(json.dumps(r, ensure_ascii=False))


def courbes():
    rows = by_day(load())
    with CURVES.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "unites", "note"])
        for r in rows:
            w.writerow([r["date"], r.get("unites", 0), r.get("note", "")])
    print(f"✅ {CURVES} généré ({len(rows)} jours) — prêt à tracer.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    {
        "add": lambda: add(sys.argv[2:]),
        "recall": recall,
        "stats": stats,
        "last": last,
        "veille": lambda: veille(sys.argv[2:]),
        "courbes": courbes,
    }.get(cmd, lambda: sys.exit(__doc__))()


if __name__ == "__main__":
    main()
