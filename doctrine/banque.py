#!/usr/bin/env python3
"""banque.py — requêter la banque d'évidence (doctrine/banque_evidence.jsonl).

Usage :
    python3 doctrine/banque.py stats
    python3 doctrine/banque.py theme <n> [tag]
    python3 doctrine/banque.py tag <verifie|a_confirmer|refute>

La banque est en AJOUT SEULEMENT : pour enrichir, on ajoute des lignes (voir doctrine/README.md).
"""
import json, sys
from pathlib import Path
from collections import Counter

BANQUE = Path(__file__).with_name("banque_evidence.jsonl")
TAGS = {"verifie": "✅", "a_confirmer": "🟡", "refute": "❌"}
THEMES = {1: "distribution-intensité", 2: "durabilité", 4: "natation", 5: "course-plafonnée",
          6: "force-Achille", 7: "nutrition", 8: "chaleur", 9: "périodisation-affûtage",
          10: "monitoring", 11: "jour-J", 0: "non-classé"}


def load():
    if not BANQUE.exists():
        sys.exit(f"❌ Banque introuvable : {BANQUE}")
    return [json.loads(l) for l in BANQUE.read_text().splitlines() if l.strip()]


def show(e):
    tag = TAGS.get(e["tag"], e["tag"])
    vote = f" [{e['vote']}]" if e.get("vote") else ""
    note = f"  ⟨{e['note']}⟩" if e.get("note") else ""
    print(f"{tag} {e['id']} · T{e['theme']} {e.get('theme_nom','')}{vote}{note}")
    print(f"   {e['claim']}")
    if e.get("source"):
        print(f"   ↳ {e['source']} ({e.get('source_qualite','')})")
    print()


def main():
    a = sys.argv[1:]
    ev = load()
    if not a or a[0] == "stats":
        print(f"{len(ev)} entrées\n")
        print("Par tag :", {k: v for k, v in Counter(e["tag"] for e in ev).items()})
        print("Par thème :")
        for t, n in sorted(Counter(e["theme"] for e in ev).items()):
            byt = Counter(e["tag"] for e in ev if e["theme"] == t)
            print(f"  T{t:<2} {THEMES.get(t,'?'):24s} {n:3d}  {dict(byt)}")
        return
    if a[0] == "theme":
        n = int(a[1]); sub = [e for e in ev if e["theme"] == n]
        if len(a) > 2:
            sub = [e for e in sub if e["tag"] == a[2]]
        print(f"— Thème {n} ({THEMES.get(n,'?')}) : {len(sub)} entrée(s) —\n")
        for e in sub:
            show(e)
        return
    if a[0] == "tag":
        sub = [e for e in ev if e["tag"] == a[1]]
        print(f"— tag={a[1]} : {len(sub)} entrée(s) —\n")
        for e in sub:
            show(e)
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
