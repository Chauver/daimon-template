#!/usr/bin/env python3
"""journal_budget.py — suivi du BUDGET TRIATHLON de la prépa Les Sables 2027.

POURQUOI. Une prépa Ironman coûte cher (matériel, nutrition, inscriptions, abonnements) et le
coût réel est toujours sous-estimé quand il n'est pas tracé. Le suivi donne : le cumul de la
prépa, le rythme mensuel, et la répartition par poste — utile pour arbitrer (et pour l'honnêteté
du projet vis-à-vis du foyer 😄).

STOCKAGE. journal/budget.jsonl — une ligne = une dépense (append-only). Les dépenses RÉCURRENTES
(abonnements) sont stockées une fois avec `recurrent: "mensuel"` + date de début (et de fin si
résiliation) : les stats les développent mois par mois automatiquement.

CATÉGORIES conseillées : nutrition · matos-velo · matos-cap · matos-nat · abonnement ·
inscription · sante (kiné/podologue) · voyage · autre.

USAGE.
    python3 journal_budget.py add --date 2026-08-01 --montant 180 --cat matos-velo --libelle "pneus + chambre a air"
    python3 journal_budget.py add --date 2026-07-01 --montant 24 --cat abonnement --libelle "piscine" --recurrent mensuel
    python3 journal_budget.py recall           # dernières dépenses
    python3 journal_budget.py stats            # cumul prépa, par mois, par catégorie
    python3 journal_budget.py courbes          # export data/budget_courbes.csv
"""
import sys, json, csv
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent
STORE = ROOT / "journal" / "budget.jsonl"
CURVES = ROOT / "data" / "budget_courbes.csv"


def load():
    if not STORE.exists():
        return []
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]


def _num(x):
    try:
        return float(str(x).replace(",", ".").replace("€", "").strip())
    except (TypeError, ValueError):
        return None


def _months_between(d0: date, d1: date):
    """Liste de premiers-du-mois de d0 à d1 inclus."""
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def develop(rows, today=None):
    """Développe les récurrents en dépenses mensuelles virtuelles (jusqu'à aujourd'hui)."""
    today = today or date.today()
    out = []
    for r in rows:
        if r.get("recurrent") == "mensuel":
            d0 = date.fromisoformat(r["date"])
            fin = date.fromisoformat(r["fin"]) if r.get("fin") else today
            for m in _months_between(d0, min(fin, today)):
                out.append(dict(r, date=m.isoformat(), montant_eur=r["montant_eur"],
                                libelle=f"{r.get('libelle','')} (mensuel)", _virtuel=True))
        else:
            out.append(r)
    return sorted(out, key=lambda x: x["date"])


def add(argv):
    d = montant = cat = lib = rec = fin = None
    it = iter(argv)
    for a in it:
        if a == "--date":      d = next(it, None)
        elif a == "--montant": montant = next(it, None)
        elif a == "--cat":     cat = next(it, None)
        elif a == "--libelle": lib = next(it, None)
        elif a == "--recurrent": rec = next(it, None)
        elif a == "--fin":     fin = next(it, None)
    if not d:
        sys.exit("❌ --date requis (YYYY-MM-DD)")
    v = _num(montant)
    if v is None or v < 0:
        sys.exit(f"❌ --montant invalide : {montant!r}")
    if not cat:
        sys.exit("❌ --cat requis (nutrition, matos-velo, abonnement, inscription…)")
    entry = {"date": d, "montant_eur": round(v, 2), "categorie": cat.lower(),
             "libelle": lib or ""}
    if rec:
        entry["recurrent"] = rec
    if fin:
        entry["fin"] = fin
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    label = f"{v:.2f} € · {cat}" + (f" · récurrent {rec}" if rec else "")
    print(f"✅ ajouté ({d}) — {label}{' · ' + lib if lib else ''}")


def recall(n=15):
    rows = develop(load())[-n:]
    if not rows:
        print("(aucune dépense)")
        return
    print(f"— budget triathlon, {len(rows)} dernières lignes (récurrents développés) —\n")
    for r in rows:
        v = "~" if r.get("_virtuel") else " "
        print(f" {v}{r['date']}  {r['montant_eur']:>8.2f} €  {r['categorie']:<12} {r.get('libelle','')}")
    print()
    stats()


def stats():
    rows = develop(load())
    if not rows:
        print("(aucune dépense)")
        return
    total = sum(r["montant_eur"] for r in rows)
    print(f"  CUMUL PRÉPA : {total:,.2f} €".replace(",", " "))
    par_cat = {}
    for r in rows:
        par_cat[r["categorie"]] = par_cat.get(r["categorie"], 0) + r["montant_eur"]
    for c, v in sorted(par_cat.items(), key=lambda kv: -kv[1]):
        print(f"    {c:<14} {v:>9.2f} €  ({100*v/total:.0f} %)")
    par_mois = {}
    for r in rows:
        par_mois[r["date"][:7]] = par_mois.get(r["date"][:7], 0) + r["montant_eur"]
    print("  Par mois : " + " · ".join(f"{m} {v:.0f}€" for m, v in sorted(par_mois.items())))


def courbes():
    rows = develop(load())
    CURVES.parent.mkdir(parents=True, exist_ok=True)
    cum = 0.0
    with CURVES.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "montant_eur", "categorie", "libelle", "cumul_eur"])
        for r in rows:
            cum += r["montant_eur"]
            w.writerow([r["date"], r["montant_eur"], r["categorie"], r.get("libelle", ""), round(cum, 2)])
    print(f"✅ {CURVES} généré ({len(rows)} lignes, cumul {cum:.2f} €).")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    {
        "add": lambda: add(sys.argv[2:]),
        "recall": recall,
        "stats": stats,
        "courbes": courbes,
    }.get(sys.argv[1], lambda: sys.exit(__doc__))()


if __name__ == "__main__":
    main()
