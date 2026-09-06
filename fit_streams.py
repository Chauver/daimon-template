#!/usr/bin/env python3
"""
fit_streams.py — lit un fichier .FIT local et produit les MÊMES flux que l'API intervals.icu.

Pourquoi : l'export Strava contient l'historique complet en .fit (1421 activités, dont les
courses de 2022 et 2024 qu'intervals.icu ne connaît pas — il ne remonte qu'à 2025). Cet
adaptateur permet de faire tourner `hr_analysis.py` dessus SANS le modifier : il renvoie le
dictionnaire de flux attendu par `analyze()`.

Tout se fait en local : aucun fichier n'est envoyé nulle part.

Usage :
    python3 fit_streams.py <fichier.fit>          # inspection rapide
    from fit_streams import load_fit              # → (streams, meta)
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    import fitdecode
except ImportError:
    sys.exit("❌ pip3 install --user fitdecode")

SEMI = 180.0 / 2 ** 31          # semicercles → degrés


def _get(frame, *names):
    for n in names:
        if frame.has_field(n):
            v = frame.get_value(n)
            if v is not None:
                return v
    return None


def sessions(path):
    """Découpage multisport d'un .fit de triathlon : [(sport, t_debut, t_fin, km, h), …].
    Un .fit d'Ironman contient TOUT (nat + T1 + vélo + T2 + course) : sans ce découpage,
    on analyserait 10 h et 229 km comme une seule « course à pied »."""
    out = []
    with fitdecode.FitReader(str(path)) as fr:
        for frame in fr:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name != "session":
                continue
            sp = _get(frame, "sport")
            st = _get(frame, "start_time")
            el = _get(frame, "total_elapsed_time") or 0
            di = _get(frame, "total_distance") or 0
            if st is None:
                continue
            out.append({"sport": str(sp), "start": st,
                        "end": st + __import__("datetime").timedelta(seconds=float(el)),
                        "km": di / 1000.0, "h": float(el) / 3600.0})
    return out


def load_fit(path, sport_filter=None):
    """Renvoie (streams, meta) — streams au format attendu par hr_analysis.analyze().
    `sport_filter` (ex. 'running') : ne garde que les points de la session de ce sport —
    indispensable sur un .fit de triathlon."""
    window = None
    if sport_filter:
        sess = [s for s in sessions(path) if s["sport"] == sport_filter]
        if not sess:
            raise ValueError(f"aucune session '{sport_filter}' dans {Path(path).name}")
        s = max(sess, key=lambda x: x["km"])          # la plus longue (le marathon)
        window = (s["start"], s["end"])

    recs = []
    sport = None
    start = None
    with fitdecode.FitReader(str(path)) as fr:
        for frame in fr:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "sport" and sport is None:
                sport = _get(frame, "sport")
            if frame.name == "session":
                sport = _get(frame, "sport") or sport
                start = _get(frame, "start_time") or start
            if frame.name != "record":
                continue
            ts = _get(frame, "timestamp")
            if ts is None:
                continue
            if window and not (window[0] <= ts <= window[1]):
                continue          # hors de la session demandée (nat / vélo / transitions)
            lat = _get(frame, "position_lat")
            lon = _get(frame, "position_long")
            recs.append({
                "ts": ts,
                "hr": _get(frame, "heart_rate"),
                "dist": _get(frame, "distance"),
                "spd": _get(frame, "enhanced_speed", "speed"),
                "alt": _get(frame, "enhanced_altitude", "altitude"),
                "lat": lat * SEMI if isinstance(lat, (int, float)) else None,
                "lon": lon * SEMI if isinstance(lon, (int, float)) else None,
                "temp": _get(frame, "temperature"),
                # ⚠️ puissance et cadence : indispensables pour analyser un parcours vélo.
                # Leur absence rendait la voie .fit inutilisable sur les épreuves cyclistes.
                "watts": _get(frame, "power"),
                "cad": _get(frame, "cadence"),
            })
    if not recs:
        raise ValueError(f"aucun enregistrement dans {path}")

    recs.sort(key=lambda r: r["ts"])
    t0 = recs[0]["ts"]
    # Le .fit peut ne pas être à 1 Hz strict : on garde le temps RÉEL en secondes, ce que
    # hr_analysis gère depuis la correction sur les discontinuités.
    time = [int((r["ts"] - t0).total_seconds()) for r in recs]

    # Vitesse : si absente, on la dérive de la distance (dérivée locale, jamais point à point
    # sur 1 s — trop bruité : on prend une fenêtre de 5 s).
    spd = [r["spd"] for r in recs]
    if sum(1 for s in spd if s is not None) < len(spd) * 0.5:
        dist = [r["dist"] for r in recs]
        spd = [None] * len(recs)
        for i in range(len(recs)):
            a = max(0, i - 2)
            b = min(len(recs) - 1, i + 2)
            if dist[a] is not None and dist[b] is not None and time[b] > time[a]:
                spd[i] = (dist[b] - dist[a]) / (time[b] - time[a])

    d0 = next((r["dist"] for r in recs if r["dist"] is not None), 0) or 0
    for r in recs:
        if r["dist"] is not None:
            r["dist"] -= d0        # distance repartant de 0 au début de la session

    streams = {
        "time": time,
        "heartrate": [r["hr"] for r in recs],
        "distance": [r["dist"] for r in recs],
        "velocity_smooth": spd,
        "altitude": [r["alt"] for r in recs],
        "fixed_altitude": [r["alt"] for r in recs],
        "temp": [r["temp"] for r in recs],
        "watts": [r["watts"] for r in recs],
        "cadence": [r["cad"] for r in recs],
    }
    lats = [r["lat"] for r in recs if r["lat"] is not None]
    lons = [r["lon"] for r in recs if r["lon"] is not None]
    if lats and lons:
        streams["_lat"] = lats
        streams["_lon"] = lons

    meta = {
        "sport": {"running": "Run", "cycling": "Ride", "swimming": "Swim"}.get(
            str(sport).lower(), str(sport)),
        "start_date_local": (start or recs[0]["ts"]).strftime("%Y-%m-%dT%H:%M:%S"),
        "n": len(recs),
        "duree_h": (time[-1] - time[0]) / 3600.0,
        "dist_km": (recs[-1]["dist"] - recs[0]["dist"]) / 1000.0
                   if recs[-1]["dist"] and recs[0]["dist"] is not None else None,
    }
    return streams, meta


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print("Sessions :")
    for x in sessions(sys.argv[1]):
        print(f"  {x['sport']:12s} {x['km']:7.2f} km · {x['h']:5.2f} h")
    s, m = load_fit(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"{Path(sys.argv[1]).name} · {m['sport']} · {m['start_date_local']}")
    print(f"  {m['n']} points · {m['duree_h']:.2f} h · {m['dist_km']:.2f} km")
    for k, v in s.items():
        if k.startswith("_"):
            continue
        n = sum(1 for x in v if x is not None)
        print(f"  {k:16s} {n}/{len(v)} valeurs")
