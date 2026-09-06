#!/usr/bin/env python3
"""
journal.py — La mémoire persistante du coach.

Trois couches, dans journal/ :
  (a) seances.jsonl  — mémoire FACTUELLE, append-only. Une ligne = un événement.
                       Rien n'est jamais réécrit : un chiffre entré est un chiffre gravé.
                       Rempli AUTOMATIQUEMENT par le pipeline (voir `sync`).
      seances.md     — la même chose en lisible. Régénérée depuis le .jsonl.
  (b) synthese.md    — mémoire INTELLIGENTE. Réécrite par Claude à chaque analyse.
  (c) decisions.md   — fil des DÉCISIONS et de leurs raisons. Ajout seulement.

Commandes :
    python3 journal.py sync              journalise toute séance pas encore en mémoire
    python3 journal.py log <id>          journalise une séance précise
    python3 journal.py verdict <id> "…"  pose le verdict de coach sur une séance
    python3 journal.py render            régénère journal/seances.md
    python3 journal.py recall [--jours N] recharge la mémoire (ce que Claude lit avant d'analyser)

Prérequis : pip install pandas ; data/ à jour (run_pipeline.sh).
"""
from __future__ import annotations
import json, os, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit("❌ pip install pandas")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / os.getenv("GARMIN_DATA_DIR", "data")
JOURNAL = ROOT / "journal"
LOG = JOURNAL / "seances.jsonl"
VIEW = JOURNAL / "seances.md"
SYNTHESE = JOURNAL / "synthese.md"
DECISIONS = JOURNAL / "decisions.md"

PLAN_START = date(2026, 6, 29)          # S1 du plan 52 semaines
DUREE_MIN_H = 5 / 60                    # sous 5 min : ce n'est pas une séance, c'est une fausse manip
LTHR = float(os.getenv("LTHR", 174))


# ─────────────────────────── couche (a) : lecture / écriture ───────────────────────────

def read_events() -> list[dict]:
    """Toutes les lignes du journal, dans l'ordre d'écriture."""
    if not LOG.exists():
        return []
    out = []
    for i, line in enumerate(LOG.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"⚠️  journal/seances.jsonl ligne {i} illisible, ignorée.", file=sys.stderr)
    return out


def read_sessions() -> list[dict]:
    """Les séances, avec verdicts de coach et enrichissements météo repliés dessus (le dernier
    écrit gagne). Les enrichissements sont des événements postérieurs : ils ne réécrivent pas
    la ligne de séance d'origine (append-only), ils la complètent à la lecture."""
    seances, verdicts, meteos = {}, {}, {}
    for e in read_events():
        t = e.get("type")
        if t == "seance":
            seances[e["activity_id"]] = e
        elif t == "verdict":
            verdicts[e["activity_id"]] = e
        elif t == "meteo":
            meteos[e["activity_id"]] = e
    for aid, v in verdicts.items():
        if aid in seances:
            seances[aid]["verdict_coach"] = v.get("texte")
            seances[aid]["verdict_le"] = v.get("ecrit_le")
    for aid, m in meteos.items():
        if aid in seances and m.get("meteo"):
            seances[aid]["meteo"] = m["meteo"]      # météo trouvée après coup (archive)
    return sorted(seances.values(), key=lambda s: (s.get("date", ""), s.get("activity_id", "")))


def append_event(event: dict) -> None:
    JOURNAL.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ─────────────────────────── utilitaires ───────────────────────────

def num(x):
    try:
        v = float(x)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def semaine_plan(d: date) -> int:
    return max(1, min(52, (d - PLAN_START).days // 7 + 1))


def allure(kmh):
    """km/h → 'm:ss/km' (lisible par un humain, pas par un tableur)."""
    if not kmh or kmh <= 0:
        return None
    total = 60.0 / kmh
    return f"{int(total)}:{round((total - int(total)) * 60):02d}/km"


def aujourdhui() -> str:
    return date.today().isoformat()


# ─────────────────────────── journalisation d'une séance ───────────────────────────

def build_entry(aid: str) -> dict | None:
    """Construit l'entrée de journal d'une séance à partir de son dossier session_report."""
    rep = DATA / f"session_report_{aid}.json"
    if not rep.exists():
        print(f"❌ {rep} absent — lance d'abord : python3 session_report.py {aid}", file=sys.stderr)
        return None
    d = json.loads(rep.read_text(encoding="utf-8"))
    d.pop("prompt_ia", None)          # le prompt est jetable, pas de la mémoire

    reel = d.get("realise") or {}
    d_date = date.fromisoformat(d["date"])
    hr = num(reel.get("avg_hr"))
    is_swim = d.get("discipline") == "swim"          # nat : jamais d'interprétation FC (D11)
    eau_libre = bool(d.get("eau_libre"))             # eau libre : pas non plus d'allure

    # TSS de la séance : on réutilise le modèle de charge, pas une formule bis
    tss = None
    try:
        from fitness_model import session_tss
        acts = pd.read_csv(DATA / "activities.csv")
        row = acts[acts["id"].astype(str) == aid]
        if not row.empty:
            t, _ = session_tss(row.iloc[-1])
            tss = round(t, 1) if t is not None else None
    except Exception as e:
        print(f"⚠️  TSS non calculé pour {aid} ({e})", file=sys.stderr)

    # sommeil des 3 nuits précédant la séance : c'est ce qui permettra de voir
    # « 2e nuit courte avant une séance clé ce mois-ci »
    sommeil_avant = []
    wf = DATA / "wellness.csv"
    if wf.exists():
        w = pd.read_csv(wf)
        w["d"] = pd.to_datetime(w["date"], errors="coerce").dt.date
        for delta in (0, 1, 2):
            j = d_date - timedelta(days=delta)
            r = w[w["d"] == j]
            if not r.empty:
                h = num(r.iloc[-1].get("sleep_hours"))
                if h is not None:
                    sommeil_avant.append({"date": j.isoformat(), "h": h})

    return {
        "type": "seance",
        "activity_id": aid,
        "date": d["date"],
        "semaine_plan": semaine_plan(d_date),
        "discipline": d.get("discipline"),
        "bande": d.get("bande"),
        "nom": d.get("nom"),
        "tss": tss,
        "duree_h": num(reel.get("duree_h")),
        "distance_km": num(reel.get("distance_km")),
        "allure": None if eau_libre else allure(num(reel.get("avg_speed_kmh"))),
        "avg_hr": hr,                                          # fait brut conservé
        "max_hr": num(reel.get("max_hr")),
        "pct_lthr": None if is_swim else (round(hr / LTHR * 100) if hr else None),
        "eau_libre": eau_libre,
        "np_watts": num(reel.get("np_watts")),
        "deniv_m": num(reel.get("deniv_m")),
        "prevu": d.get("prevu"),
        "efficience": d.get("efficience"),
        "vo2": d.get("vo2"),
        "intervalles": d.get("intervalles"),
        "charge": d.get("charge"),
        "forme": d.get("forme"),
        "sommeil_avant": sommeil_avant,
        "meteo": d.get("meteo"),
        "ressenti": d.get("ressenti"),
        "verdict_regles": d.get("verdict_regles"),
        "verdict_coach": None,
        "journalise_le": aujourdhui(),
    }


def cmd_log(aid: str, force=False) -> bool:
    deja = {s["activity_id"] for s in read_sessions()}
    if aid in deja and not force:
        print(f"↩️  {aid} déjà en mémoire — rien à faire.")
        return False
    entry = build_entry(aid)
    if not entry:
        return False
    append_event(entry)
    print(f"📖 Journalisé : {entry['date']} · {entry['discipline']}/{entry['bande']} · "
          f"{entry['distance_km']} km · TSS {entry['tss']}")
    return True


def cmd_verdict(aid: str, texte: str) -> None:
    if aid not in {s["activity_id"] for s in read_sessions()}:
        sys.exit(f"❌ {aid} n'est pas encore au journal. Lance d'abord : python3 journal.py log {aid}")
    append_event({"type": "verdict", "activity_id": aid, "texte": texte.strip(), "ecrit_le": aujourdhui()})
    print(f"🗣️  Verdict enregistré sur {aid}.")
    cmd_render()


def cmd_meteo(aid: str) -> None:
    """Enrichit une séance déjà journalisée avec sa météo, lue depuis son session_report
    (utile pour rattraper une séance loggée avant que la météo ne soit branchée). Les
    nouvelles séances, elles, arrivent déjà avec leur température via le pipeline."""
    if aid not in {s["activity_id"] for s in read_sessions()}:
        sys.exit(f"❌ {aid} n'est pas au journal. Lance d'abord : python3 journal.py log {aid}")
    rep = DATA / f"session_report_{aid}.json"
    if not rep.exists():
        sys.exit(f"❌ {rep} absent — régénère-le : python3 session_report.py {aid}")
    meteo = (json.loads(rep.read_text(encoding="utf-8")).get("meteo") or {})
    if meteo.get("temp_c") is None:
        print(f"↩️  Aucune température trouvée pour {aid} — rien à enrichir.")
        return
    append_event({"type": "meteo", "activity_id": aid, "meteo": meteo, "ecrit_le": aujourdhui()})
    print(f"🌡️  Météo enregistrée sur {aid} : {meteo['temp_c']} °C ({meteo.get('source', '?')}).")
    cmd_render()


# ─────────────────────────── sync : le filet qui ne rate aucune séance ───────────────────────────

def cmd_sync() -> None:
    af = DATA / "activities.csv"
    if not af.exists():
        sys.exit("❌ data/activities.csv absent — lance d'abord ./run_pipeline.sh")
    acts = pd.read_csv(af)
    deja = {s["activity_id"] for s in read_sessions()}

    nouvelles, ignorees = [], []
    for _, r in acts.iterrows():
        aid = str(r["id"])
        if aid in deja:
            continue
        h = num(r.get("moving_time_h")) or 0
        if h < DUREE_MIN_H:
            ignorees.append((aid, h))       # jamais en silence : on l'affiche plus bas
            continue
        nouvelles.append(aid)

    if ignorees:
        detail = ", ".join(f"{a} ({round(h*60)} min)" for a, h in ignorees)
        print(f"⏭️  {len(ignorees)} activité(s) écartée(s), durée < 5 min : {detail}")

    if not nouvelles:
        print("✅ Journal à jour — aucune nouvelle séance.")
        cmd_render()
        return

    print(f"🔎 {len(nouvelles)} séance(s) à journaliser…")
    for aid in nouvelles:
        # on (re)fabrique le dossier de la séance, analyse d'intervalles comprise
        subprocess.run([sys.executable, "intervals_analysis.py", aid],
                       cwd=ROOT, capture_output=True)          # best effort : pas toutes les séances ont des laps
        p = subprocess.run([sys.executable, "session_report.py", aid],
                           cwd=ROOT, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"⚠️  session_report a échoué sur {aid} : {p.stderr.strip()[:120]}", file=sys.stderr)
            continue
        cmd_log(aid)
    cmd_render()

    manquants = [s["activity_id"] for s in read_sessions() if not s.get("verdict_coach")]
    if manquants:
        print(f"\n✍️  {len(manquants)} séance(s) sans verdict de coach : {', '.join(manquants)}")
        print("   → à Claude de les rédiger : python3 journal.py verdict <id> \"…\"")


# ─────────────────────────── render : la vue lisible ───────────────────────────

def _ligne_chiffres(s: dict) -> str:
    bits = []
    if s.get("duree_h"):
        bits.append(f"{round(s['duree_h'] * 60)} min")
    if s.get("distance_km"):
        bits.append(f"{s['distance_km']} km")
    if s.get("allure"):
        bits.append(s["allure"])
    if s.get("np_watts"):
        bits.append(f"NP {round(s['np_watts'])} W")
    swim = s.get("discipline") == "swim"              # nat : on n'affiche jamais la FC (D11)
    if s.get("avg_hr") and not swim:
        pct = f" ({s['pct_lthr']}% FC seuil)" if s.get("pct_lthr") else ""
        bits.append(f"FC moy {round(s['avg_hr'])}{pct}")
    if s.get("max_hr") and not swim:
        bits.append(f"max {round(s['max_hr'])}")
    if s.get("deniv_m"):
        bits.append(f"D+ {round(s['deniv_m'])} m")
    if s.get("tss") is not None:
        bits.append(f"**TSS {s['tss']}**")
    return " · ".join(bits)


def _ligne_contexte(s: dict) -> list[str]:
    out = []
    iv = s.get("intervalles") or {}
    if iv.get("fade_pct") is not None or iv.get("hr_drift_bpm") is not None:
        m = []
        if iv.get("n_work"):
            m.append(f"{iv['n_work']} reps")
        if iv.get("fade_pct") is not None:
            m.append(f"décrochage {iv['fade_pct']}%")
        if iv.get("hr_drift_bpm") is not None:
            # Chiffre BRUT (1ʳᵉ vs 2ᵉ moitié des reps), non corrigé de l'allure ni du relief.
            # La dérive cardiaque qui fait foi est celle d'« Analyse FC fine » plus bas.
            m.append(f"ΔFC brut {iv['hr_drift_bpm']:+} bpm (non corrigé)")
        out.append(f"- **Intervalles** : {' · '.join(m)}")

    ef = s.get("efficience")
    if ef:
        signe = "+" if ef["delta_pct"] >= 0 else ""
        chaud = " (corrigé chaleur)" if ef.get("corrige_chaleur") else ""
        out.append(f"- **Efficience** : {signe}{ef['delta_pct']}% vs base {s['bande']} "
                   f"(médiane sur {ef['n_base']} séances){chaud}")
    elif s.get("bande") and s.get("bande") != "VO2":
        out.append(f"- **Efficience** : pas encore de base {s['bande']} (< 3 séances comparables sur 28 j)")

    ch = s.get("charge") or {}
    if ch.get("tsb") is not None:
        out.append(f"- **Charge** : CTL {ch.get('ctl')} · ATL {ch.get('atl')} · TSB {ch['tsb']:+.1f}")

    fo = s.get("forme") or {}
    dodo = s.get("sommeil_avant") or []
    m = []
    if fo.get("fc_repos"):
        m.append(f"FC repos {round(fo['fc_repos'])}")
    if fo.get("hrv"):
        m.append(f"HRV {fo['hrv']}")
    for d in dodo:
        alerte = " ⚠️" if d["h"] < 6 else ""
        m.append(f"sommeil {d['date'][-5:]} {d['h']} h{alerte}")
    if m:
        out.append(f"- **Forme** : {' · '.join(m)}")

    me = s.get("meteo") or {}
    if me.get("temp_c") is not None:
        src = " (archive)" if me.get("source") == "archive" else ""
        diag = f" — {me['diagnostic']}" if me.get("diagnostic") else ""
        out.append(f"- **Météo** : {me['temp_c']} °C{src}{diag}")

    re_ = s.get("ressenti") or {}
    if re_.get("rpe") or re_.get("feel"):
        out.append(f"- **Ressenti** : RPE {re_.get('rpe')} · feel {re_.get('feel')}")

    pr = s.get("prevu")
    if pr:
        out.append(f"- **Prévu** : {pr['type']} {pr['duree_prevue_h']} h")

    out += _ligne_hr_fine(s.get("activity_id"))
    return out


def _hr_store() -> dict:
    """Analyses cardiaques fines (hr_analysis.py) — la plus récente par séance."""
    p = JOURNAL / "hr_analyse.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            out[e["activity_id"]] = e
        except Exception:
            continue
    return out


def _ligne_hr_fine(aid) -> list[str]:
    """Résultat de l'analyse cardiaque fine, pour pouvoir le ressortir à tout moment
    depuis le journal sans relancer d'API (cf. hr_analysis.py)."""
    e = _hr_store().get(aid)
    if not e:
        return []
    out = ["- **Analyse FC fine** :"]
    out.append(f"    - type **{e['type_seance']}** · {len(e.get('blocs') or [])} bloc(s) d'allure")
    for i, b in enumerate(e.get("blocs") or [], 1):
        out.append(f"        - bloc {i} : {b['duree_min']} min · {b['dist_km']} km · "
                   f"{b['allure']} · FC {b['fc']}")
    w = e.get("meteo")
    if w:
        out.append(f"    - météo réelle : {w['temp_c']} °C · {w['humidite']} % HR · "
                   f"vent {w['vent_kmh']} km/h")
    r = e.get("rendement") or {}
    prof = e.get("profil_fc_allure") or []
    if prof:
        pts = " · ".join(f"{p['allure']} → FC {p['fc']}" for p in prof)
        out.append(f"    - profil FC↔allure (plat, stabilisé) : {pts}")
        out.append(f"    - {len(prof)} mesure(s) propres sur {r.get('min_mouvement', '?')} min "
                   f"({r.get('pct', '?')} % exploitable)")
    else:
        out.append(f"    - aucune mesure exploitable (terrain trop vallonné ou allure instable)")
    d = e.get("derive") or {}
    # « mesurable » sans 'moyenne_bpm' arrive (ex. 14/08 : strides en fin de séance → paires
    # d'allures atypiques) : ne jamais laisser un KeyError casser tout le rendu du journal.
    if d.get("mesurable") and d.get("moyenne_bpm") is not None:
        out.append(f"    - dérive cardiaque à allure appariée : **{d['moyenne_bpm']:+.1f} bpm**")
    else:
        out.append("    - dérive non mesurable (aucune allure répétée)")
    return out


def cmd_render() -> None:
    seances = read_sessions()
    JOURNAL.mkdir(parents=True, exist_ok=True)

    L = [
        "# Journal des séances — mémoire factuelle",
        "",
        "> **Couche (a).** Vue lisible, **générée automatiquement** depuis `seances.jsonl`.",
        "> Ne l'édite pas à la main : elle est réécrite à chaque `journal.py render`.",
        "> La source de vérité est le `.jsonl`, en ajout seulement — aucun chiffre n'y est jamais modifié.",
        "",
        f"*{len(seances)} séance(s) en mémoire · dernière mise à jour {aujourdhui()}*",
        "",
    ]
    if not seances:
        L += ["_Journal vide. Lance `python3 journal.py sync`._", ""]

    mois_courant = None
    for s in seances:
        mois = s["date"][:7]
        if mois != mois_courant:
            mois_courant = mois
            L += [f"## {mois}", ""]
        titre = f"### {s['date']} · {s.get('nom') or 'séance'}"
        tags = " · ".join(x for x in (s.get("discipline"), s.get("bande"),
                                      f"S{s.get('semaine_plan')}") if x)
        L += [titre, "", f"`{tags}`", "", _ligne_chiffres(s), ""]
        L += _ligne_contexte(s)
        L += [""]
        if s.get("verdict_coach"):
            L += [f"> 🗣️ **Verdict coach** — {s['verdict_coach']}", ""]
        else:
            L += [f"> _Verdict coach à écrire._ (règles : {s.get('verdict_regles')})", ""]
        L += ["---", ""]

    VIEW.write_text("\n".join(L), encoding="utf-8")
    print(f"📄 journal/seances.md régénéré ({len(seances)} séance(s)).")


# ─────────────────────────── recall : recharger la mémoire ───────────────────────────

def _tendances(seances: list[dict]) -> list[str]:
    """Ce que les chiffres disent dans la durée. Silencieux quand la base est trop mince —
    on ne conclut pas sur deux points."""
    out = []
    if not seances:
        return ["- (aucune séance en mémoire)"]

    # volume course par semaine de plan vs plafond (garde-fou n°1)
    runs = [s for s in seances if s.get("discipline") == "run" and s.get("distance_km")]
    if runs:
        par_sem: dict[int, float] = {}
        for s in runs:
            par_sem[s["semaine_plan"]] = par_sem.get(s["semaine_plan"], 0) + s["distance_km"]
        recent = sorted(par_sem.items())[-4:]
        detail = " · ".join(f"S{w} {km:.1f} km" for w, km in recent)
        out.append(f"- **Volume course** (plafond 35 km/sem jusqu'à S25) : {detail}")
        depasse = [f"S{w}" for w, km in par_sem.items() if km > 35]
        if depasse:
            out.append(f"  - 🔴 **Plafond course dépassé** : {', '.join(depasse)}")

    # dérive cardiaque : 4 dernières vs 4 précédentes, par discipline
    for disc in ("run", "bike", "swim"):
        d = [s for s in seances if s.get("discipline") == disc
             and (s.get("intervalles") or {}).get("hr_drift_bpm") is not None]
        if len(d) >= 6:
            recents = [s["intervalles"]["hr_drift_bpm"] for s in d[-4:]]
            avant = [s["intervalles"]["hr_drift_bpm"] for s in d[-8:-4]]
            m_r, m_a = sum(recents) / len(recents), sum(avant) / len(avant)
            sens = "s'améliore" if m_r < m_a - 0.5 else "se dégrade" if m_r > m_a + 0.5 else "stable"
            out.append(f"- **Dérive cardiaque {disc}** : {m_a:.1f} → {m_r:.1f} bpm — {sens}")
        elif d:
            vals = " · ".join(f"{s['date'][5:]} +{s['intervalles']['hr_drift_bpm']}" for s in d[-4:])
            out.append(f"- **Dérive cardiaque {disc}** : {vals} bpm _(base trop mince pour une tendance)_")

    # efficience par bande
    for disc in ("run", "bike", "swim"):
        e = [s for s in seances if s.get("discipline") == disc and s.get("efficience")]
        if e:
            vals = " · ".join(f"{s['date'][5:]} {s['bande']} {s['efficience']['delta_pct']:+.1f}%" for s in e[-4:])
            out.append(f"- **Efficience {disc}** : {vals}")

    # sommeil court avant séance (30 derniers jours)
    limite = (date.today() - timedelta(days=30)).isoformat()
    courtes = [s for s in seances if s["date"] >= limite
               and any(n["h"] < 6 for n in (s.get("sommeil_avant") or [])[:1])]
    if courtes:
        out.append(f"- ⚠️ **Nuits < 6 h la veille d'une séance** (30 j) : {len(courtes)} — "
                   + ", ".join(s["date"] for s in courtes))

    # charge
    ch = [s for s in seances if (s.get("charge") or {}).get("ctl") is not None]
    if ch:
        d = ch[-1]["charge"]
        out.append(f"- **Charge au dernier point** : CTL {d.get('ctl')} · ATL {d.get('atl')} · TSB {d.get('tsb'):+.1f}")
    return out or ["- (pas encore de tendance exploitable)"]


def cmd_recall(jours: int = 90) -> None:
    seances = read_sessions()
    limite = (date.today() - timedelta(days=jours)).isoformat()
    fenetre = [s for s in seances if s["date"] >= limite]

    print("=" * 78)
    print("  MÉMOIRE DU COACH — à relire avant toute analyse ou proposition")
    print("=" * 78)

    for titre, f in (("(c) FIL DES DÉCISIONS", DECISIONS), ("(b) SYNTHÈSE ÉVOLUTIVE", SYNTHESE)):
        print(f"\n\n### {titre}  —  {f.relative_to(ROOT) if f.exists() else '(absent)'}\n")
        print(f.read_text(encoding="utf-8") if f.exists() else "  (fichier absent)")

    print("\n\n### (a) SÉANCES — %d sur les %d derniers jours (%d en mémoire au total)\n"
          % (len(fenetre), jours, len(seances)))
    for s in fenetre:
        v = s.get("verdict_coach") or f"[sans verdict] {s.get('verdict_regles')}"
        print(f"  {s['date']} · S{s['semaine_plan']} · {s.get('discipline')}/{s.get('bande')} · "
              f"{_ligne_chiffres(s).replace('**', '')}")
        print(f"      🗣️ {v}")
    if not fenetre:
        print("  (aucune séance sur la fenêtre)")

    print("\n\n### TENDANCES CALCULÉES (lecture longitudinale)\n")
    for l in _tendances(seances):
        print(l)
    print("\n" + "=" * 78)
    print("Rappel : raisonne en comparant au passé (§ CLAUDE.md — mémoire & lecture longitudinale).")
    print("=" * 78)


# ─────────────────────────── entrée ───────────────────────────

def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "recall"

    if cmd == "sync":
        cmd_sync()
    elif cmd == "log":
        if len(args) < 2:
            sys.exit("Usage : python3 journal.py log <activity_id>")
        cmd_log(args[1], force="--force" in args)
        cmd_render()
    elif cmd == "verdict":
        if len(args) < 3:
            sys.exit('Usage : python3 journal.py verdict <activity_id> "ton verdict"')
        cmd_verdict(args[1], args[2])
    elif cmd == "meteo":
        if len(args) < 2:
            sys.exit("Usage : python3 journal.py meteo <activity_id>")
        cmd_meteo(args[1])
    elif cmd == "render":
        cmd_render()
    elif cmd == "recall":
        j = 90
        if "--jours" in args:
            j = int(args[args.index("--jours") + 1])
        cmd_recall(j)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
