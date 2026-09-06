#!/usr/bin/env python3
"""
journal_contexte.py — contexte physiologique et environnemental d'une séance.

POURQUOI. Ces données ne sont pas décoratives : ce sont les COVARIABLES des mesures.
  • Hydratation — confondant PRINCIPAL de la dérive cardiaque. La perte de volume
    plasmatique fait monter la FC à puissance constante : ~1 % de masse corporelle
    perdue ≈ +3 bpm, dégradation de la performance au-delà de 2 %. Sans elle, une
    dérive mesurée n'est pas attribuable (fatigue ? chaleur ? déshydratation ?).
  • Température & ventilateur — en intérieur, sans vent relatif, la sudation grimpe à
    1-1,5 L/h et la dérive thermique domine. Deux séances à températures différentes ne
    sont pas comparables.
  • Glucides — inutiles pour lire 40 min de Z2 (le glycogène n'est pas limitant), mais
    (a) à glycogène bas la même puissance coûte plus cher en FC et en RPE, (b) c'est le
    levier direct du facteur limitant PÉRIPHÉRIQUE de l'athlète (cf. D18 : effondrement
    d'allure avec FC qui CHUTE en fin de marathon), et (c) la tolérance intestinale
    (60 → 90-120 g/h) ne se construit que par pratique systématique. On trace dès
    maintenant pour que la série existe le jour où elle comptera.
  • RPE — le seul canal qui capte ce qu'aucun capteur ne voit.

Stockage : journal/contexte_seance.jsonl, en AJOUT SEULEMENT.

Usage :
  python3 journal_contexte.py add --session i167535510 [--date 2026-07-20] \
      --temp 24 --ventilo oui --masse-avant 72.0 --masse-apres 71.2 \
      --boisson 500 --glucides 0 --rpe 3 --note "..."
  python3 journal_contexte.py recall [n]
  python3 journal_contexte.py get <session_id>
"""
from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path

STORE = Path("journal") / "contexte_seance.jsonl"


def load():
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def derive(e):
    """Métriques dérivées — c'est là qu'est la valeur coach."""
    d = {}
    ma, mb = e.get("masse_avant_kg"), e.get("masse_apres_kg")
    boisson = e.get("boisson_ml")
    duree_h = e.get("duree_h")

    if ma and mb:
        perte_kg = ma - mb
        d["perte_masse_kg"] = round(perte_kg, 2)
        d["perte_masse_pct"] = round(100 * perte_kg / ma, 2)
        # Sudation = perte de masse + liquide ingéré (1 mL ≈ 1 g)
        if boisson is not None:
            sueur_l = perte_kg + boisson / 1000.0
            d["sudation_l"] = round(sueur_l, 2)
            if duree_h:
                d["sudation_l_h"] = round(sueur_l / duree_h, 2)
        # Seuil classique : au-delà de 2 % de masse perdue, la performance se dégrade
        # et la FC est mécaniquement surélevée → la dérive n'est plus attribuable.
        if d["perte_masse_pct"] >= 2.0:
            d["alerte_hydratation"] = (f"perte {d['perte_masse_pct']} % de masse (> 2 %) : "
                                       f"la dérive cardiaque de cette séance est CONFONDUE "
                                       f"par la déshydratation, ne pas l'interpréter comme de la fatigue")
        elif d["perte_masse_pct"] >= 1.0:
            d["alerte_hydratation"] = (f"perte {d['perte_masse_pct']} % : compter environ "
                                       f"+{round(3 * d['perte_masse_pct'])} bpm d'effet hydratation "
                                       f"sur la dérive observée")

    if boisson is not None and duree_h:
        d["boisson_ml_h"] = round(boisson / duree_h)

    g = e.get("glucides_g")
    if g is not None and duree_h:
        d["glucides_g_h"] = round(g / duree_h)
        if duree_h >= 2.5 and d["glucides_g_h"] < 60:
            d["alerte_glucides"] = (f"{d['glucides_g_h']} g/h sur {duree_h} h — sous les 60 g/h. "
                                    f"L'entraînement intestinal se construit sur les sorties longues.")
    return d


def cmd_add(args):
    def val(flag, cast=float):
        if flag in args:
            v = args[args.index(flag) + 1]
            return None if v in ("", "-") else cast(v)
        return None

    sid = args[args.index("--session") + 1] if "--session" in args else None
    e = {
        "date": (args[args.index("--date") + 1] if "--date" in args else date.today().isoformat()),
        "session_id": sid,
        "temp_piece_c": val("--temp"),
        "ventilateur": (args[args.index("--ventilo") + 1] if "--ventilo" in args else None),
        "masse_avant_kg": val("--masse-avant"),
        "masse_apres_kg": val("--masse-apres"),
        "boisson_ml": val("--boisson"),
        "glucides_g": val("--glucides"),
        "rpe": val("--rpe", float),  # demi-points OK (ex. 9.5) — même logique que journal_blessure
        "duree_h": val("--duree-h"),
        # Contexte CAP (demande athlète 27/07) : les chaussures changent la charge pied/Achille
        # (plaque carbone ≠ chaussure souple) et la musique cale la cadence — deux covariables
        # à corréler avec les scores de ressenti blessure.
        "chaussures": (args[args.index("--chaussures") + 1] if "--chaussures" in args else None),
        "musique_bpm": val("--musique", int),
        "note": (args[args.index("--note") + 1] if "--note" in args else None),
    }
    e = {k: v for k, v in e.items() if v is not None}
    e["derive"] = derive(e)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"✅ contexte enregistré ({e['date']}" + (f" · {sid}" if sid else "") + ")")
    for k, v in e["derive"].items():
        icon = "⚠️ " if k.startswith("alerte") else "   "
        print(f"{icon}{k} : {v}")


def cmd_recall(n=8):
    h = load()
    if not h:
        sys.exit("Aucun contexte enregistré.")
    print(f"— contexte des {min(n, len(h))} dernière(s) séance(s) —\n")
    for e in h[-n:]:
        d = e.get("derive", {})
        bits = []
        if e.get("temp_piece_c") is not None:
            bits.append(f"{e['temp_piece_c']}°C")
        if e.get("ventilateur"):
            bits.append(f"ventilo {e['ventilateur']}")
        if d.get("sudation_l_h"):
            bits.append(f"sudation {d['sudation_l_h']} L/h")
        if d.get("perte_masse_pct") is not None:
            bits.append(f"perte {d['perte_masse_pct']} %")
        if d.get("boisson_ml_h"):
            bits.append(f"boisson {d['boisson_ml_h']} mL/h")
        if d.get("glucides_g_h") is not None:
            bits.append(f"glucides {d['glucides_g_h']} g/h")
        if e.get("rpe"):
            bits.append(f"RPE {e['rpe']}")
        print(f"  {e['date']}  {' · '.join(bits)}")
        for k, v in d.items():
            if k.startswith("alerte"):
                print(f"      ⚠️  {v}")


def cmd_get(sid):
    for e in load():
        if e.get("session_id") == sid:
            print(json.dumps(e, ensure_ascii=False, indent=2))
            return
    sys.exit(f"Aucun contexte pour {sid}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = sys.argv[1]
    if c == "add":
        cmd_add(sys.argv[2:])
    elif c == "recall":
        cmd_recall(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    elif c == "get":
        cmd_get(sys.argv[2])
    else:
        sys.exit(__doc__)
