#!/usr/bin/env python3
"""
hr_analysis.py — Analyse CARDIAQUE FINE d'une séance de course (CAP uniquement).

Pourquoi ce module : `intervals_analysis.py` raisonne par laps/reps. Sur un terrain
vallonné, cela compare des portions qui n'ont rien à voir (à FC égale, l'allure varie
de >1 min/km entre montée et descente) et fabrique de faux signaux de dérive.

Ici on travaille sur les FLUX BRUTS 1 Hz d'intervals.icu et on ne compare que le
comparable :

  1. TYPE de séance     — intervalles ou continu (détecté sur le signal d'allure,
                          PAS sur l'auto-découpage d'intervals.icu qui est bruité)
  2. PATTERN            — segmentation en phases homogènes d'allure
  3. MÉTÉO RÉELLE       — Open-Meteo au point/heure de départ (la temp du capteur
                          montre est celle du poignet : elle surestime)
  4. PORTIONS PROPRES   — plates (|pente| < seuil) ET à allure stable, ≥ 500 m
  5. FC sur la 2ᵉ MOITIÉ de chaque portion (la FC met ~20-30 s à se caler après
                          un changement de pente ou d'allure : le début ment)
  6. PROFIL FC↔allure   + DÉRIVE mesurée à ALLURE APPARIÉE

Les résultats sont PERSISTÉS dans `journal/hr_analyse.jsonl` (append-only, une entrée
par séance) : c'est la base de mémoire qui permettra, en s'accumulant, de comparer
l'évolution de la FC à allure donnée et de calculer une FC ATTENDUE.

Usage :
    python3 hr_analysis.py <activity_id>   analyse une séance (+ persiste)
    python3 hr_analysis.py --all           analyse les séances CAP pas encore traitées
    python3 hr_analysis.py --all --force   re-analyse TOUT (après changement de méthode)
    python3 hr_analysis.py recall <id>     ressort une analyse enregistrée
    python3 hr_analysis.py profil          profil FC↔allure accumulé + évolution
    python3 hr_analysis.py attendu <id>    FC attendue vs constatée pour une séance
"""
from __future__ import annotations
import json, math, os, sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("❌ pip install requests")


def _load_dotenv(p=".env"):
    f = Path(p)
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
API_KEY = os.getenv("INTERVALS_API_KEY")
BASE = "https://intervals.icu/api/v1"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"

RUN_SPORTS = {"Run", "TrailRun", "VirtualRun", "Treadmill"}

# --- paramètres d'analyse (calibrables) ---
GRADE_WIN_M   = 100.0   # fenêtre de lissage de la pente (sous 100 m = bruit GPS)
FLAT_MAX_PCT  = 1.0     # |pente| <= 1 % = "plat"
MIN_SPEED_MS  = 1.5     # en-dessous : marche/arrêt
PACE_TOL      = 0.10    # tolérance d'allure dans une portion "stable" (±10 %)
MIN_SEG_M     = 400.0   # longueur mini d'une portion exploitable.
                        # 500 m était la cible, mais sur terrain vallonné les vraies lignes
                        # droites plafonnent à ~470 m (mesuré) : à 500 on ne trouve RIEN.
                        # 400 m ≈ 90-150 s, donc ≥ 45 s sur la 2ᵉ moitié — assez pour une FC
                        # calée (la FC répond en 20-30 s). Baisser le seuil de PENTE serait
                        # pire : ça mélangerait du faux plat dans la référence.
SMOOTH_S      = 15      # lissage de la vitesse (s)
PHASE_MIN_S   = 120     # durée mini d'une phase pour être rapportée
PHASE_TOL     = 0.12    # écart d'allure qui sépare deux phases (12 %)
# --- récolte par FENÊTRE DE STABILISATION (méthode principale) ---
# Sur terrain vallonné (Perros-Guirec : plat max ~240 m), exiger une portion de 400 m ne
# donne RIEN. On fonde donc la validité sur le TEMPS plutôt que sur la distance : on retient
# la FC dès que les STAB_S secondes précédentes ont été plates et à allure stable. C'est la
# même intention que « lire la FC sur la 2ᵉ moitié » (laisser la FC se caler), mais la
# stabilisation est GARANTIE au lieu d'être supposée.
STAB_S        = 60      # durée de stabilisation exigée avant de lire la FC
STAB_CV       = 0.05    # allure stable dans la fenêtre (CV ≤ 5 %)
SAMPLE_MIN_S  = 20      # durée mini d'un échantillon retenu

# --- deux exclusions physiologiques (règles athlète) ---
# 1) Les 15 premières minutes ne comptent JAMAIS : la FC n'a pas fini de monter vers son
#    plateau pour l'intensité demandée. Un point récolté là sous-estime la FC vraie et
#    pollue la relation FC↔allure.
WARMUP_S      = 900
# 2) Un échantillon dont la FC MONTE franchement n'est pas à l'état stationnaire : c'est
#    typiquement un redémarrage après arrêt (feu rouge, portail, photo) — l'allure est
#    stable mais le cœur est encore en train de rattraper. On l'écarte.
HR_SLOPE_MAX  = 5.0     # bpm/min ; au-delà (dans un sens OU dans l'autre), la FC n'est
                        # pas installée. Le filtre est BILATÉRAL : une FC qui redescend
                        # (trot après une côte, récup après une rep) est tout aussi
                        # disqualifiante qu'une FC qui monte.
HR_RANGE_MAX  = 15.0    # amplitude FC max dans un échantillon (bpm). Au-delà, ce n'est pas
                        # un plateau : c'est du bruit de capteur optique ou une transition.

# --- discontinuités du flux ---
# ⚠️ Le flux `time` d'intervals.icu n'est PAS régulier : mesuré sur i167079292, 3002
# échantillons pour 3088 s, dont un TROU DE 88 s (arrêt montre / perte de signal).
# Toute fenêtre comptée en INDICES suppose du 1 Hz parfait et peut donc enjamber un arrêt.
# On raisonne désormais en SECONDES, et on refuse tout échantillon qui traverse un trou.
MAX_GAP_S     = 3       # au-delà, on considère qu'il y a rupture de continuité

MACRO_TOL     = 0.25    # regroupement en BLOCS : c'est la lecture humaine de la séance
                        # (« 22 min cool avec Juju, puis endurance »), au-dessus du
                        # découpage fin qui, lui, sert à détecter le type de séance.


def api(path, params=None):
    if not API_KEY:
        sys.exit("❌ INTERVALS_API_KEY manquant dans .env")
    r = requests.get(f"{BASE}{path}", params=params, auth=("API_KEY", API_KEY), timeout=45)
    r.raise_for_status()
    return r.json()


def pace_str(v_ms):
    """m/s → 'M:SS/km'."""
    if not v_ms or v_ms <= 0:
        return "—"
    p = 1000.0 / v_ms / 60.0
    return f"{int(p)}:{int(round((p % 1) * 60)):02d}/km"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ météo
def fetch_weather(lat, lon, when_iso):
    """Météo réelle au point et à l'heure de la séance (archive, repli prévision).
    On veut l'AMBIANTE : la temp du capteur montre est chauffée par le poignet."""
    day = when_iso[:10]
    hourly = "temperature_2m,relative_humidity_2m,wind_speed_10m,apparent_temperature"
    for url, params in (
        (ARCHIVE,  {"latitude": lat, "longitude": lon, "start_date": day, "end_date": day,
                    "hourly": hourly, "timezone": "auto"}),
        (FORECAST, {"latitude": lat, "longitude": lon, "hourly": hourly,
                    "past_days": 14, "timezone": "auto"}),
    ):
        try:
            h = requests.get(url, params=params, timeout=30).json().get("hourly", {})
            times = h.get("time") or []
            if not times:
                continue
            target = when_iso[:13]
            idx = next((i for i, t in enumerate(times) if t[:13] == target), None)
            if idx is None:
                continue
            return {
                "temp_c":     h["temperature_2m"][idx],
                "humidite":   h["relative_humidity_2m"][idx],
                "vent_kmh":   h["wind_speed_10m"][idx],
                "ressenti_c": h["apparent_temperature"][idx],
            }
        except Exception:
            continue
    return None


# ------------------------------------------------------------------ flux
def load_streams(aid):
    """Flux 1 Hz. ⚠️ Le flux `latlng` d'intervals.icu range les LATITUDES dans `data`
    et les LONGITUDES dans `data2` — ce n'est PAS une liste de paires. Lire data[1]
    comme longitude donne un point à l'autre bout du monde (et une météo absurde)."""
    js = api(f"/activity/{aid}/streams")
    out = {s["type"]: s["data"] for s in js}
    for s in js:
        if s.get("type") == "latlng" and s.get("data2") is not None:
            out["_lat"] = s["data"]
            out["_lon"] = s["data2"]
    return out


def sampling_dt(t):
    """Pas d'échantillonnage médian du flux (s). Les .fit anciens ne sont pas à 1 Hz :
    Frankfurt 2022 échantillonne toutes les ~5 s, Barcelone toutes les ~2 s."""
    if len(t) < 3:
        return 1.0
    dts = sorted(t[i] - t[i - 1] for i in range(1, len(t)))
    return max(1.0, float(dts[len(dts) // 2]))


def gap_threshold(t):
    """Seuil de discontinuité ADAPTATIF. Un seuil fixe à 3 s traite chaque intervalle
    normal d'un fichier échantillonné à 5 s comme un arrêt — et rejette alors tout."""
    return max(MAX_GAP_S, 3.0 * sampling_dt(t))


def gaps_before(t, max_gap=None):
    """Pour chaque i, l'instant du dernier saut de continuité qui le précède.
    Permet de refuser en O(1) toute fenêtre qui enjamberait un arrêt."""
    if max_gap is None:
        max_gap = gap_threshold(t)
    n = len(t)
    last = [None] * n
    cur = None
    for i in range(1, n):
        if t[i] - t[i - 1] > max_gap:
            cur = t[i]
        last[i] = cur
    return last


def spans_gap(last_gap, t, i, back_s):
    """True si la fenêtre [t[i]-back_s, t[i]] traverse une discontinuité."""
    g = last_gap[i]
    return g is not None and g > t[i] - back_s


def smooth(xs, win_s, t=None, last_gap=None):
    """Moyenne glissante centrée sur une fenêtre de win_s SECONDES (pas d'indices).
    Ne franchit jamais une discontinuité : sans ça, le lissage « invente » de la vitesse
    de part et d'autre d'un arrêt et fait croire que l'athlète courait encore."""
    n = len(xs)
    if t is None:
        t = list(range(n))
    out = [None] * n
    half = win_s / 2.0
    a = b = 0
    for i in range(n):
        while a < i and t[i] - t[a] > half:
            a += 1
        while b < n - 1 and t[b + 1] - t[i] <= half:
            b += 1
        lo, hi = a, b
        if last_gap is not None:
            while lo < i and last_gap[i] is not None and t[lo] < last_gap[i]:
                lo += 1
            thr = gap_threshold(t)
            j = i
            while j < hi:
                if t[j + 1] - t[j] > thr:
                    hi = j
                    break
                j += 1
        seg = [xs[k] for k in range(lo, hi + 1) if xs[k] is not None]
        out[i] = sum(seg) / len(seg) if seg else None
    return out


def grades(dist, alt):
    """Pente (%) lissée sur GRADE_WIN_M — la pente point-à-point est du bruit GPS pur."""
    n = len(dist)
    g = [None] * n
    lo = 0
    hi = 0
    for i in range(n):
        while lo < i and dist[i] - dist[lo] > GRADE_WIN_M / 2:
            lo += 1
        while hi < n - 1 and dist[hi] - dist[i] < GRADE_WIN_M / 2:
            hi += 1
        dd = dist[hi] - dist[lo]
        if dd > 20 and alt[hi] is not None and alt[lo] is not None:
            g[i] = 100.0 * (alt[hi] - alt[lo]) / dd
    return g


# ------------------------------------------------------------------ 1. type & 2. pattern
def find_phases(v, moving):
    """Découpe la séance en phases homogènes d'allure (greedy sur la vitesse lissée).
    Sert à la fois au PATTERN et à la détection du TYPE de séance."""
    phases = []
    cur = None
    for i, ok in enumerate(moving):
        if not ok or v[i] is None:
            continue
        if cur is None:
            cur = {"i0": i, "i1": i, "vals": [v[i]]}
            continue
        med = sorted(cur["vals"])[len(cur["vals"]) // 2]
        if med > 0 and abs(v[i] - med) / med > PHASE_TOL and len(cur["vals"]) > 45:
            phases.append(cur)
            cur = {"i0": i, "i1": i, "vals": [v[i]]}
        else:
            cur["i1"] = i
            cur["vals"].append(v[i])
    if cur:
        phases.append(cur)
    # Fusion des phases courtes — CONDITIONNELLE à l'allure.
    # ⚠️ Fusionner sans regarder l'allure fabrique des allures FANTÔMES : sur un 6×1000 m
    # avec récup de 90 s, ça produit des « phases homogènes » à 4:40/km, moyenne du travail
    # (4:10) et de la récup (6:57) — une allure jamais courue. La fusion doit rester un
    # filtre anti-bruit, pas un lissage structurel.
    merged = []
    for p in phases:
        if merged and len(p["vals"]) < PHASE_MIN_S:
            prev = mean(merged[-1]["vals"])
            cur = mean(p["vals"])
            if prev and cur and abs(cur - prev) / prev <= PHASE_TOL:
                merged[-1]["i1"] = p["i1"]
                merged[-1]["vals"] += p["vals"]
                continue
        merged.append(p)
    return merged


def macro_phases(phases):
    """Regroupe les phases fines en BLOCS lisibles : deux phases voisines dont l'allure
    ne diffère pas de plus de MACRO_TOL appartiennent au même bloc. C'est ce qui fait
    ressortir la structure réelle (« partie cool » puis « partie endurance »)."""
    if not phases:
        return []
    blocks = [dict(phases[0])]
    for p in phases[1:]:
        prev = mean(blocks[-1]["vals"])
        cur = mean(p["vals"])
        if prev and cur and abs(cur - prev) / prev <= MACRO_TOL:
            blocks[-1]["i1"] = p["i1"]
            blocks[-1]["vals"] = blocks[-1]["vals"] + p["vals"]
        else:
            blocks.append(dict(p))
    return blocks


# Amplitude minimale d'une alternance pour parler d'INTERVALLES.
# Calibré sur données réelles : un footing continu en terrain vallonné produit des
# alternances allant jusqu'à 27 % (les bosses ralentissent 60-70 s puis ça repart),
# alors qu'un vrai 6×1000 m au seuil (4:10/km) avec récup (6:57/km) fait ~67 %.
# 35 % sépare proprement les deux sans coller à l'un ni à l'autre.
ALT_AMP_MIN = 0.35


def detect_type(phases, t):
    """Intervalles = alternance RÉPÉTÉE et AMPLE rapide/lent. Sinon : continu.
    On ne se fie pas à l'auto-découpage d'intervals.icu (bruité, cf. D6), ni à un simple
    comptage de phases : le relief en fabrique tout seul."""
    real = [p for p in phases if t[p["i1"]] - t[p["i0"]] >= 5]     # < 5 s = bruit
    if len(real) < 6:
        return "continu", f"{len(real)} phase(s) d'allure homogène — pas d'alternance répétée"
    speeds = [mean(p["vals"]) for p in real]
    # ⚠️ Ne PAS tester le franchissement d'une médiane : sur un signal bimodal (travail /
    # récup), la médiane tombe pile sur l'une des deux allures, (a−med)×(b−med) vaut 0 et
    # aucune alternance n'est jamais détectée. On compte directement les TRANSITIONS amples,
    # et on exige qu'elles changent de sens (haut, bas, haut… = format d'intervalles).
    dirs = []
    for a, b in zip(speeds, speeds[1:]):
        if not a or not b:
            continue
        if abs(a - b) / max(a, b) > ALT_AMP_MIN:
            dirs.append(1 if b > a else -1)
    alt = sum(1 for x, y in zip(dirs, dirs[1:]) if x != y) + (1 if dirs else 0)
    short = sum(1 for p in real if t[p["i1"]] - t[p["i0"]] < 300)
    if alt >= 4 and short >= 4:
        return "intervalles", (f"{alt} alternance(s) d'amplitude > {ALT_AMP_MIN:.0%} "
                               f"sur {len(real)} phases")
    return "continu", (f"{len(real)} phases, {alt} alternance(s) ample(s) "
                       f"— pas un format d'intervalles")


# ------------------------------------------------------------------ 4. portions propres
def clean_segments(v, g, hr, dist, moving):
    """Portions PLATES + à ALLURE STABLE + >= MIN_SEG_M.
    On coupe dès que l'allure sort de ±PACE_TOL autour de la médiane courante."""
    n = len(v)
    ok = [moving[i] and g[i] is not None and abs(g[i]) <= FLAT_MAX_PCT
          and v[i] is not None and hr[i] is not None for i in range(n)]
    segs = []
    cur = None
    for i in range(n):
        if not ok[i]:
            if cur:
                segs.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {"i0": i, "i1": i, "vals": [v[i]]}
            continue
        # ⚠️ Référence ANCRÉE sur le début du segment, pas médiane courante : une médiane
        # qui suit le segment dérive avec lui et ne borne jamais l'étendue réelle (mesuré :
        # un segment « stable à ±10 % » passait en fait de 5:33 à 4:33/km, soit 22 %).
        anchor = sorted(cur["vals"][:20])[min(len(cur["vals"]), 20) // 2]
        if anchor > 0 and abs(v[i] - anchor) / anchor > PACE_TOL:
            segs.append(cur)
            cur = {"i0": i, "i1": i, "vals": [v[i]]}
        else:
            cur["i1"] = i
            cur["vals"].append(v[i])
    if cur:
        segs.append(cur)
    out = []
    for s in segs:
        d = dist[s["i1"]] - dist[s["i0"]]
        if d >= MIN_SEG_M:
            s["dist_m"] = d
            out.append(s)
    return out


def hr_slope_bpm_min(hr, t, a, b):
    """Pente de la FC sur [a,b] en bpm/min (régression linéaire). Sert à écarter les
    échantillons où la FC est encore en train de monter (arrêt-redémarrage)."""
    pts = [(t[i], hr[i]) for i in range(a, b + 1) if hr[i] is not None]
    n = len(pts)
    if n < 5:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx    # bpm/s
    return slope * 60.0


def segment_metrics(s, v, hr, dist, t):
    """FC lue sur la 2ᵉ MOITIÉ de la portion : le début est encore en transition
    (la FC met ~20-30 s à répondre à un changement d'allure ou de pente)."""
    i0, i1 = s["i0"], s["i1"]
    mid = i0 + (i1 - i0) // 2
    hr2 = [hr[i] for i in range(mid, i1 + 1) if hr[i] is not None]
    v2 = [v[i] for i in range(mid, i1 + 1) if v[i] is not None]
    return {
        "t_start_s": t[i0], "dur_s": t[i1] - t[i0], "dist_m": s["dist_m"],
        "hr_2h": mean(hr2), "v_2h": mean(v2),
        "hr_full": mean([hr[i] for i in range(i0, i1 + 1)]),
        "n_2h": len(hr2),
    }


def stabilized_samples(v, g, hr, dist, t, moving, exclude_ranges=()):
    """Récolte par fenêtre glissante : la FC de l'instant i est retenue si les STAB_S
    secondes qui précèdent ont été plates, en mouvement et à allure stable.
    `exclude_ranges` : intervalles déjà couverts par une portion longue (qualité
    supérieure) — on ne compte pas deux fois la même mesure physiologique."""
    n = len(hr)
    ex = [False] * n
    for a, b in exclude_ranges:
        for i in range(max(0, a), min(n, b + 1)):
            ex[i] = True

    last_gap = gaps_before(t)
    dt = sampling_dt(t)
    ok = [False] * n
    a = 0
    for i in range(n):
        if ex[i] or hr[i] is None:
            continue
        if t[i] < WARMUP_S:              # échauffement : la FC n'a pas fini de monter
            continue
        # fenêtre de STAB_S SECONDES (et non 60 indices)
        while a < i and t[i] - t[a] > STAB_S:
            a += 1
        if t[i] - t[a] < STAB_S * 0.9:   # pas assez d'historique réel
            continue
        if spans_gap(last_gap, t, i, STAB_S):   # la fenêtre enjambe un arrêt → refusée
            continue
        w = range(a, i + 1)
        if any((not moving[j]) or v[j] is None for j in w):
            continue
        if any(g[j] is None or abs(g[j]) > FLAT_MAX_PCT for j in w):
            continue
        vs = [v[j] for j in w]
        m = sum(vs) / len(vs)
        if m <= 0:
            continue
        cv = ((sum((x - m) ** 2 for x in vs) / len(vs)) ** 0.5) / m
        if cv <= STAB_CV:
            ok[i] = True

    out, cur = [], None
    for i in range(n):
        if ok[i]:
            cur = [i, i] if cur is None else [cur[0], i]
        elif cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)

    res = []
    for a, b in out:
        if t[b] - t[a] + 1 < SAMPLE_MIN_S:
            continue
        sl = hr_slope_bpm_min(hr, t, a, b)
        # BILATÉRAL : FC qui monte (redémarrage) OU qui descend (récup après côte/rep)
        if sl is not None and abs(sl) > HR_SLOPE_MAX:
            continue
        vs = [v[i] for i in range(a, b + 1) if v[i] is not None]
        hs = [hr[i] for i in range(a, b + 1) if hr[i] is not None]
        gs = [abs(g[i]) for i in range(a, b + 1) if g[i] is not None]
        if not vs or not hs:
            continue
        if max(hs) - min(hs) > HR_RANGE_MAX:   # pas un plateau : bruit ou transition
            continue
        mv = sum(vs) / len(vs)
        res.append({
            "t_start_s": t[a], "dur_s": t[b] - t[a] + 1,
            "dist_m": dist[b] - dist[a],
            "v_2h": mv, "hr_2h": sum(hs) / len(hs),
            "hr_full": sum(hs) / len(hs), "n_2h": len(hs),
            "pente_moy_pct": round(sum(gs) / len(gs), 2) if gs else None,
            "cv_allure_pct": round(100 * ((sum((x - mv) ** 2 for x in vs) / len(vs)) ** 0.5 / mv), 1),
            "fc_pente_bpm_min": round(sl, 1) if sl is not None else None,
            "methode": "fenetre_60s", "qualite": "standard",
        })
    return res


# ------------------------------------------------------------------ 6. dérive à allure appariée
def reference_slope(mets, global_model):
    """Pente FC↔vitesse servant à RETIRER l'effet allure avant de parler de dérive.
    Priorité : pente intra-séance si elle est estimable sans ambiguïté (≥ 4 points ET
    plage de vitesse ≥ 0,3 m/s) ; sinon pente du modèle global ajusté SANS cette séance ;
    sinon rien — et on dit qu'on ne sait pas.
    ⚠️ Avec 2 points, la pente intra-séance force le résidu à 0 par construction : on
    l'interdit explicitement."""
    pts = [(m["v_2h"], m["hr_2h"]) for m in mets if m.get("v_2h") and m.get("hr_2h")]
    if len(pts) >= 4:
        vs = [p[0] for p in pts]
        if max(vs) - min(vs) >= 0.30:
            n = len(pts)
            mx = sum(p[0] for p in pts) / n
            my = sum(p[1] for p in pts) / n
            sxx = sum((p[0] - mx) ** 2 for p in pts)
            if sxx > 0:
                return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx, "intra_seance"
    if global_model:
        return global_model["b"], "modele_global"
    return None, None


def drift_residual(mets, b_ref):
    """Dérive = pente temporelle de la FC RÉSIDUELLE (FC − b_ref·vitesse), en bpm/h.
    On régresse sur TOUS les points propres : pas d'appariement, donc pas de pondération
    arbitraire par le nombre de partenaires, et le résultat est normalisé par le temps —
    donc comparable d'une séance à l'autre."""
    pts = [(m["t_start_s"] + m["dur_s"] / 2.0, m["hr_2h"] - b_ref * m["v_2h"])
           for m in mets if m.get("v_2h") and m.get("hr_2h")]
    n = len(pts)
    if n < 2:
        return None
    span = max(p[0] for p in pts) - min(p[0] for p in pts)
    if span < 300:                       # moins de 5 min d'amplitude : rien à dire
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx      # bpm/s
    # On publie AUSSI l'écart réellement observé sur la durée mesurée : le taux horaire
    # est comparable entre séances, mais l'extrapoler depuis 12 min gonfle l'impression
    # (−4,7 bpm mesurés deviennent « −23 bpm/h »). Les deux, ou rien.
    return {"bpm_par_h": round(slope * 3600, 1),
            "delta_observe_bpm": round(slope * span, 1),
            "n_points": n, "amplitude_min": round(span / 60, 1)}


def matched_drift(mets, b_ref, tol=0.04):
    """Paires d'allure appariée, à titre ILLUSTRATIF (le chiffre qui fait foi est la
    dérive résiduelle ci-dessus). L'écart est corrigé de l'effet allure résiduel :
    sans cette correction, ±4 % d'allure valent déjà plusieurs bpm et on mesurerait
    la pente FC↔allure en croyant mesurer une dérive."""
    pairs = []
    for a in mets:
        for b in mets:
            if b["t_start_s"] - a["t_start_s"] < 300:
                continue
            va, vb = a.get("v_2h"), b.get("v_2h")
            if not va or not vb:
                continue
            if abs(va - vb) / ((va + vb) / 2) > tol:      # dénominateur symétrique
                continue
            brut = b["hr_2h"] - a["hr_2h"]
            corr = b_ref * (vb - va) if b_ref is not None else None
            pairs.append({
                "t1_min": round(a["t_start_s"] / 60), "allure1": pace_str(va),
                "fc1": round(a["hr_2h"], 1),
                "t2_min": round(b["t_start_s"] / 60), "allure2": pace_str(vb),
                "fc2": round(b["hr_2h"], 1),
                "delta_brut_bpm": round(brut, 1),
                "correction_allure_bpm": round(corr, 1) if corr is not None else None,
                "delta_corrige_bpm": round(brut - corr, 1) if corr is not None else None,
                "ecart_allure_pct": round(100 * abs(va - vb) / ((va + vb) / 2), 1),
                "domine_par_correction": (corr is not None and abs(corr) > abs(brut - corr)),
            })
    return pairs


# ------------------------------------------------------------------ persistance
STORE = Path("journal") / "hr_analyse.jsonl"


def load_store():
    """Toutes les analyses déjà enregistrées, la plus récente par activité."""
    if not STORE.exists():
        return {}
    out = {}
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            out[e["activity_id"]] = e          # une ré-analyse écrase la précédente
        except Exception:
            continue
    return out


def save_entry(entry):
    """Append-only : on n'efface jamais une analyse, on en ajoute une plus récente."""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def all_points(store, exclude=None):
    """Tous les points (allure, FC) propres accumulés, hors séance exclue."""
    pts = []
    for aid, e in store.items():
        if exclude and aid == exclude:
            continue
        for p in e.get("profil_fc_allure", []):
            pts.append({"date": e.get("date"), "activity_id": aid, "v_ms": p["v_ms"],
                        "fc": p["fc"], "temp_c": (e.get("meteo") or {}).get("temp_c"),
                        "n_s": p.get("n_s")})
    return pts


def fit_hr_model(pts):
    """Régression linéaire FC = a + b·vitesse sur les points accumulés.
    Volontairement simple : avec peu de points, un modèle riche sur-apprend.
    Renvoie None tant que la base est trop mince — mieux vaut ne rien dire que mentir."""
    pts = [p for p in pts if p.get("v_ms") and p.get("fc")]
    n = len(pts)
    if n < 6:
        return None
    xs = [p["v_ms"] for p in pts]
    ys = [float(p["fc"]) for p in pts]
    # Pondération par la DURÉE de l'échantillon : une portion de 211 s vaut plus qu'une
    # fenêtre de 26 s. Sans ça, le bruit court pèse autant que la mesure solide.
    ws = [max(1.0, float(p.get("n_s") or 1)) for p in pts]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if sxx <= 0:
        return None
    b = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / sxx
    a = my - b * mx
    ss_tot = sum(w * (y - my) ** 2 for w, y in zip(ws, ys))
    ss_res = sum(w * (y - (a + b * x)) ** 2 for w, x, y in zip(ws, xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    sd = (ss_res / sw * n / max(1, n - 2)) ** 0.5      # n-2 ddl, pas n

    # ---- diagnostic PARADOXE DE SIMPSON ----
    # Chaque séance ne couvre qu'une bande d'allure étroite. Si la pente GLOBALE ne
    # ressemble pas aux pentes INTRA-séance, le R² flatteur reflète surtout les écarts
    # ENTRE séances (météo, forme, parcours) et non la physiologie FC↔allure.
    by_sess = {}
    for p in pts:
        by_sess.setdefault(p.get("activity_id"), []).append(p)
    intra = []
    for sid, ps in by_sess.items():
        if len(ps) < 2:
            continue
        vx = [q["v_ms"] for q in ps]
        if max(vx) - min(vx) < 0.15:
            continue
        vy = [float(q["fc"]) for q in ps]
        k = len(ps)
        ax, ay = sum(vx) / k, sum(vy) / k
        s2 = sum((x - ax) ** 2 for x in vx)
        if s2 > 0:
            intra.append(sum((x - ax) * (y - ay) for x, y in zip(vx, vy)) / s2)
    coherent = None
    if intra:
        med_intra = sorted(intra)[len(intra) // 2]
        coherent = abs(med_intra - b) <= max(15.0, 0.5 * abs(b))
    return {"a": a, "b": b, "n": n, "r2": r2, "sd": sd,
            "v_min": min(xs), "v_max": max(xs),
            "pentes_intra": [round(s, 1) for s in intra],
            "coherent_intra_inter": coherent}


# ------------------------------------------------------------------ analyse d'une séance
def analyze(aid, verbose=True, global_model=None):
    """Analyse complète d'une séance CAP → dict structuré (ou None si hors périmètre).
    `global_model` : modèle FC↔vitesse ajusté SANS cette séance, utilisé pour retirer
    l'effet allure de la dérive (évite le raisonnement circulaire)."""
    if global_model is None:
        global_model = fit_hr_model(all_points(load_store(), exclude=aid))
    act = api(f"/activity/{aid}")
    sport = act.get("type", "")
    name = (act.get("name") or "").strip()
    start = act.get("start_date_local", "")
    if verbose:
        print(f"🔎 {aid} · {name}")
        print(f"   {sport} · {start[:16].replace('T', ' ')}")
    if sport not in RUN_SPORTS:
        if verbose:
            print(f"   ⛔ hors périmètre (sport = {sport}) — module réservé à la CAP")
        return None

    S = load_streams(aid)
    need = ("heartrate", "distance", "velocity_smooth")
    miss = [k for k in need if k not in S]
    if miss:
        if verbose:
            print(f"   ⛔ flux manquants : {', '.join(miss)}")
        return None

    hr = S["heartrate"]
    dist = S["distance"]
    t = S.get("time") or list(range(len(hr)))
    alt = S.get("fixed_altitude") or S.get("altitude")
    last_gap = gaps_before(t)
    v = smooth(S["velocity_smooth"], SMOOTH_S, t, last_gap)
    n = len(hr)

    # --- 3. météo réelle ---
    lats, lons = S.get("_lat"), S.get("_lon")
    w = None
    coords = None
    if lats and lons and start:
        coords = (lats[0], lons[0])
        w = fetch_weather(coords[0], coords[1], start)
    dev_t = mean(S.get("temp") or [])

    moving = [v[i] is not None and v[i] >= MIN_SPEED_MS for i in range(n)]
    g = grades(dist, alt) if alt else [None] * n

    # --- 1. type + 2. pattern ---
    phases = find_phases(v, moving)
    typ, why = detect_type(phases, t)
    blocks = macro_phases(phases)

    # --- 4/5. récolte des mesures propres ---
    # Deux niveaux de qualité, complémentaires :
    #  A) PORTIONS LONGUES (≥ MIN_SEG_M, plates, allure stable) — FC lue sur la 2ᵉ moitié.
    #     Les plus fiables. Rares en terrain vallonné, fréquentes dès qu'il y a du plat.
    #  B) FENÊTRES DE STABILISATION (60 s plats et stables avant lecture) — marchent partout.
    # B exclut ce que A couvre déjà, pour ne pas compter deux fois la même mesure.
    segs_raw = clean_segments(v, g, hr, dist, moving)
    segs, mets = [], []
    for s in segs_raw:
        mid = s["i0"] + (s["i1"] - s["i0"]) // 2
        if t[mid] < WARMUP_S:                        # lecture dans l'échauffement → écartée
            continue
        sl = hr_slope_bpm_min(hr, t, mid, s["i1"])   # pente sur la 2ᵉ moitié (la zone lue)
        if sl is not None and sl > HR_SLOPE_MAX:     # FC encore ascendante → écartée
            continue
        segs.append(s)
        m = segment_metrics(s, v, hr, dist, t)
        m["fc_pente_bpm_min"] = round(sl, 1) if sl is not None else None
        gm = [abs(g[i]) for i in range(s["i0"], s["i1"] + 1) if g[i] is not None]
        vs = [v[i] for i in range(s["i0"], s["i1"] + 1) if v[i] is not None]
        mv = mean(vs) or 0
        m["pente_moy_pct"] = round(mean(gm), 2) if gm else None
        m["cv_allure_pct"] = round(100 * ((sum((x - mv) ** 2 for x in vs) / len(vs)) ** 0.5 / mv), 1) \
            if vs and mv else None
        m["methode"] = f"portion_{int(MIN_SEG_M)}m"
        m["qualite"] = "haute"
        mets.append(m)

    covered = [(s["i0"], s["i1"]) for s in segs]
    mets += stabilized_samples(v, g, hr, dist, t, moving, exclude_ranges=covered)
    mets.sort(key=lambda m: m["t_start_s"])

    usable = sum(m["dur_s"] for m in mets)
    total_moving = sum(1 for x in moving if x)

    # --- dérive : effet allure RETIRÉ, sinon on mesure la pente FC↔allure ---
    b_ref, src_b = reference_slope(mets, global_model)
    res = drift_residual(mets, b_ref) if b_ref is not None else None
    pairs = matched_drift(mets, b_ref) if b_ref is not None else []

    # On distingue les CAUSES : « je n'ai rien mesuré » ≠ « j'ai mesuré mais rien à comparer »
    if not mets:
        raison = "aucune mesure propre (terrain trop vallonné, allure instable, ou séance trop courte)"
    elif len(mets) < 2:
        raison = f"une seule mesure propre — il en faut ≥ 2 séparées de 5 min"
    elif b_ref is None:
        raison = "pas de pente FC↔vitesse de référence (base < 6 points et séance trop homogène)"
    elif res is None:
        raison = "mesures trop rapprochées dans le temps (< 5 min d'amplitude)"
    else:
        raison = None

    return {
        "activity_id": aid,
        "date": start[:10],
        "debut": start[:16].replace("T", " "),
        "nom": name,
        "sport": sport,
        "type_seance": typ,
        "type_detail": why,
        "meteo": w,
        "coords": {"lat": round(coords[0], 5), "lon": round(coords[1], 5)} if coords else None,
        "capteur_temp_c": round(dev_t, 1) if dev_t else None,
        "blocs": [{
            "t0_min": round(t[p["i0"]] / 60, 1), "t1_min": round(t[p["i1"]] / 60, 1),
            "duree_min": round((t[p["i1"]] - t[p["i0"]]) / 60, 1),
            "dist_km": round((dist[p["i1"]] - dist[p["i0"]]) / 1000, 2),
            "v_ms": round(mean(p["vals"]), 3), "allure": pace_str(mean(p["vals"])),
            "fc": round(mean([hr[i] for i in range(p["i0"], p["i1"] + 1)]) or 0, 1),
        } for p in blocks],
        "portions": [{
            "t_start_min": round(m["t_start_s"] / 60, 1), "dist_m": round(m["dist_m"]),
            "duree_s": m["dur_s"], "pente_moy_pct": m["pente_moy_pct"],
            "cv_allure_pct": m["cv_allure_pct"],
            "v_2h_ms": round(m["v_2h"], 3), "allure_2h": pace_str(m["v_2h"]),
            "fc_2h": round(m["hr_2h"], 1), "fc_portion_entiere": round(m["hr_full"], 1),
            "n_2h_s": m["n_2h"], "methode": m["methode"], "qualite": m["qualite"],
        } for m in mets],
        "profil_fc_allure": [{
            "v_ms": round(m["v_2h"], 3), "allure": pace_str(m["v_2h"]),
            "fc": round(m["hr_2h"], 1), "n_s": m["n_2h"], "dist_m": round(m["dist_m"]),
            "methode": m["methode"], "qualite": m["qualite"],
        } for m in sorted(mets, key=lambda x: -x["v_2h"])],
        "derive": {
            "mesurable": res is not None,
            "raison_non_mesurable": raison,
            "bpm_par_h": res["bpm_par_h"] if res else None,
            "delta_observe_bpm": res["delta_observe_bpm"] if res else None,
            "n_points": res["n_points"] if res else None,
            "amplitude_min": res["amplitude_min"] if res else None,
            "b_ref_bpm_ms": round(b_ref, 1) if b_ref is not None else None,
            "source_b_ref": src_b,
            "fiabilite": ("indicatif" if (res and res["n_points"] < 3) else
                          "standard" if res else None),
            "paires_illustratives": pairs,
        },
        "rendement": {
            "min_exploitables": round(usable / 60, 1),
            "min_mouvement": round(total_moving / 60, 1),
            "pct": round(100 * usable / total_moving) if total_moving else 0,
        },
        "params": {"pente_max_pct": FLAT_MAX_PCT, "seg_min_m": MIN_SEG_M,
                   "tol_allure": PACE_TOL, "lissage_s": SMOOTH_S},
    }


# ------------------------------------------------------------------ rapport lisible
def report(e, model=None):
    print(f"🔎 {e['activity_id']} · {e['nom']}")
    print(f"   {e['sport']} · {e['debut']}")
    w = e.get("meteo")
    if w:
        print(f"\n🌡️  Météo réelle : {w['temp_c']:.1f} °C (ressenti {w['ressenti_c']:.1f}) · "
              f"humidité {w['humidite']:.0f} % · vent {w['vent_kmh']:.0f} km/h")
        if e.get("capteur_temp_c"):
            print(f"    (capteur montre : {e['capteur_temp_c']:.0f} °C — poignet, surestime)")
    else:
        print("\n🌡️  Météo indisponible")

    print(f"\n① TYPE : {e['type_seance'].upper()}  ({e['type_detail']})")
    print(f"\n② PATTERN — {len(e['blocs'])} bloc(s)")
    for k, b in enumerate(e["blocs"], 1):
        print(f"   bloc {k} : t+{b['t0_min']:.0f}→{b['t1_min']:.0f} min · {b['duree_min']:5.1f} min · "
              f"{b['dist_km']:4.2f} km · {b['allure']} · FC {b['fc']:.0f}")

    r = e["rendement"]
    nh = sum(1 for p in e["portions"] if p.get("qualite") == "haute")
    print(f"\n③ MESURES PROPRES (plat + allure stable + FC installée) : {len(e['portions'])} "
          f"· dont {nh} portion(s) longue(s) ≥ {e['params']['seg_min_m']:.0f} m · "
          f"{r['min_exploitables']} min exploitables ({r['pct']} % du temps en mouvement)")
    for k, p in enumerate(e["portions"], 1):
        tag = "portion" if p.get("qualite") == "haute" else "fenêtre"
        print(f"   #{k} [{tag}] t+{p['t_start_min']:.0f} min · {p['dist_m']} m · {p['duree_s']}s · "
              f"pente {p['pente_moy_pct']}% · CV {p['cv_allure_pct']}% "
              f"→ {p['allure_2h']} @ FC {p['fc_2h']}")

    if e["profil_fc_allure"]:
        print("\n④ PROFIL FC ↔ ALLURE (plat, stable, 2ᵉ moitié)")
        for p in e["profil_fc_allure"]:
            line = f"   {p['allure']:>10s}  →  FC {p['fc']:5.1f}   ({p['n_s']:3d} s · {p['dist_m']} m)"
            if model:
                pred = model["a"] + model["b"] * p["v_ms"]
                if model["v_min"] * 0.95 <= p["v_ms"] <= model["v_max"] * 1.05:
                    line += f"   | attendue {pred:5.1f}  écart {p['fc'] - pred:+5.1f}"
                else:
                    line += "   | hors plage du modèle"
            print(line)

    d = e["derive"]
    print("\n⑤ DÉRIVE CARDIAQUE (effet allure retiré, régression sur tous les points)")
    if not d["mesurable"]:
        print(f"   Non mesurable — {d.get('raison_non_mesurable')}")
    else:
        print(f"   → {d['delta_observe_bpm']:+.1f} bpm mesurés sur {d['amplitude_min']} min "
              f"(soit {d['bpm_par_h']:+.1f} bpm/h)   [{d['fiabilite']}, {d['n_points']} points]")
        print(f"   pente de référence retirée : {d['b_ref_bpm_ms']} bpm/(m/s) "
              f"({d['source_b_ref']})")
        for p in d.get("paires_illustratives") or []:
            flag = "  ⚠️ dominée par la correction" if p.get("domine_par_correction") else ""
            print(f"     illustration : t+{p['t1_min']}→{p['t2_min']}min · "
                  f"{p['allure1']}→{p['allure2']} ({p['ecart_allure_pct']}%) · "
                  f"brut {p['delta_brut_bpm']:+.1f} − allure {p['correction_allure_bpm']:+.1f} "
                  f"= {p['delta_corrige_bpm']:+.1f} bpm{flag}")


# ------------------------------------------------------------------ profil accumulé
def cmd_profil():
    store = load_store()
    pts = all_points(store)
    if not pts:
        sys.exit("Aucune analyse enregistrée. Lance d'abord : python3 hr_analysis.py --all")
    print(f"📚 BASE DE MÉMOIRE FC↔ALLURE — {len(pts)} point(s) sur {len(store)} séance(s)\n")
    buckets = {}
    for p in pts:
        key = round((1000.0 / p["v_ms"] / 60.0) * 4) / 4     # bucket de 15 s/km
        buckets.setdefault(key, []).append(p)
    print("Par tranche d'allure (évolution dans le temps) :")
    for k in sorted(buckets):
        ps = sorted(buckets[k], key=lambda x: x["date"] or "")
        lbl = f"{int(k)}:{int(round((k % 1) * 60)):02d}/km"
        fcs = [p["fc"] for p in ps]
        print(f"\n  {lbl}  ({len(ps)} mesure(s), FC {min(fcs):.0f}–{max(fcs):.0f})")
        for p in ps:
            tp = f" · {p['temp_c']:.0f}°C" if p.get("temp_c") is not None else ""
            print(f"     {p['date']}  FC {p['fc']:5.1f}{tp}   [{p['activity_id']}]")
    m = fit_hr_model(pts)
    print("\n" + "─" * 62)
    if not m:
        print(f"Modèle FC attendue : PAS ENCORE — {len(pts)} point(s), il en faut ≥ 6.")
    else:
        print(f"Modèle FC attendue :  FC = {m['a']:.1f} + {m['b']:.1f} × vitesse(m/s)")
        print(f"  n={m['n']} · R²={m['r2']:.2f} · écart-type résiduel {m['sd']:.1f} bpm")
        print(f"  valable de {pace_str(m['v_min'])} à {pace_str(m['v_max'])}")
        if m["r2"] < 0.7:
            print("  ⚠️ R² faible : lien FC↔allure encore bruité (conditions variables, base mince).")
        # Diagnostic PARADOXE DE SIMPSON — le R² peut être flatteur pour de mauvaises raisons
        if m.get("pentes_intra"):
            print(f"  pentes INTRA-séance observées : {m['pentes_intra']} bpm/(m/s)")
            if m.get("coherent_intra_inter") is False:
                print("  🔴 INCOHÉRENT : la pente globale ne ressemble pas aux pentes intra-séance.")
                print("     Le R² reflète surtout les écarts ENTRE séances (météo, forme, parcours),")
                print("     pas la physiologie FC↔allure. NE PAS utiliser ce modèle pour juger une séance.")
            elif m.get("coherent_intra_inter"):
                print("  ✅ pente globale cohérente avec les pentes intra-séance")
        else:
            print("  ⚠️ aucune séance ne couvre une plage d'allure suffisante pour vérifier")
            print("     que la pente globale est physiologique et non un artefact entre séances.")


def cmd_attendu(aid):
    store = load_store()
    e = store.get(aid)
    if not e:
        print(f"Séance {aid} pas encore analysée — analyse en cours…")
        e = analyze(aid)
        if not e:
            sys.exit("⛔ analyse impossible")
        save_entry(e)
    model = fit_hr_model(all_points(store, exclude=aid))   # modèle SANS la séance jugée
    if not model:
        print("⚠️ Base trop mince pour une FC attendue (≥ 6 points hors séance requis).")
    report(e, model)


def cmd_all(force=False):
    import csv as _csv
    p = Path("data/activities.csv")
    if not p.exists():
        sys.exit("❌ data/activities.csv introuvable (lance intervals_sync.py)")
    runs = [r for r in _csv.DictReader(p.open()) if "run" in r["sport_type"]]
    store = load_store()
    done = skipped = failed = 0
    for r in runs:
        aid = r["id"]
        if aid in store and not force:
            print(f"⏭️  {aid} ({r['start_date_local'][:10]}) déjà analysée")
            skipped += 1
            continue
        try:
            e = analyze(aid, verbose=False)
        except Exception as ex:
            print(f"❌ {aid} : {ex}")
            failed += 1
            continue
        if not e:
            print(f"⏭️  {aid} ({r['start_date_local'][:10]}) écartée (hors périmètre / flux absents)")
            skipped += 1
            continue
        save_entry(e)
        done += 1
        haute = sum(1 for p in e["portions"] if p["qualite"] == "haute")
        print(f"✅ {e['date']} {aid} · {e['type_seance']:8s} · "
              f"{len(e['portions'])} mesure(s) (dont {haute} haute qualité) · "
              f"{e['rendement']['min_exploitables']:4.1f} min · "
              f"dérive {str(e['derive']['delta_observe_bpm']) + ' bpm' if e['derive']['mesurable'] else '—'}")
    print(f"\n{done} analysée(s) · {skipped} écartée(s) · {failed} échec(s) → {STORE}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "--all":
        cmd_all(force="--force" in sys.argv)
    elif cmd == "profil":
        cmd_profil()
    elif cmd == "recall":
        if len(sys.argv) < 3:
            sys.exit("Usage : python3 hr_analysis.py recall <activity_id>")
        e = load_store().get(sys.argv[2])
        if not e:
            sys.exit(f"Aucune analyse enregistrée pour {sys.argv[2]}")
        report(e, fit_hr_model(all_points(load_store(), exclude=sys.argv[2])))
    elif cmd == "attendu":
        if len(sys.argv) < 3:
            sys.exit("Usage : python3 hr_analysis.py attendu <activity_id>")
        cmd_attendu(sys.argv[2])
    else:
        e = analyze(cmd)
        if not e:
            sys.exit(1)
        save_entry(e)
        model = fit_hr_model(all_points(load_store(), exclude=cmd))
        report(e, model)
        print(f"\n💾 enregistré → {STORE}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.exit(f"❌ API intervals.icu : {e}")
