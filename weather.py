#!/usr/bin/env python3
"""
weather.py — Contexte météo pour l'agent coach.

Fournit :
  • la température au lieu/heure d'une séance (capteur montre si dispo, sinon Open-Meteo)
  • une CORRECTION THERMIQUE de l'efficience FC : la chaleur élève la FC à effort donné,
    donc on normalise l'EF d'une séance chaude avant de la comparer à ta base (bâtie à
    température habituelle) — pour ne pas lire un faux signal de fatigue.
  • un DIAGNOSTIC ("séance plus dure car il faisait X°C de plus que d'habitude")
  • une ANTICIPATION : à partir des prévisions, alléger l'intensité en cas de forte chaleur.

API météo : Open-Meteo (gratuite, sans clé). Archive pour le passé, forecast pour l'avenir.
Prérequis fetch : pip install requests   (les fonctions de calcul marchent sans réseau)
"""
from __future__ import annotations
import statistics as st
from datetime import datetime

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# ------------------------------------------------------------------ calcul pur
def temp_baseline(temps):
    """Température habituelle d'entraînement = médiane des séances récentes fournies."""
    t = [x for x in temps if x is not None]
    return round(st.median(t), 1) if t else None

def thermal_ef_factor(temp, baseline, k=0.006, tol=3.0, cap=0.15):
    """Facteur (>= 1) à multiplier à l'EF observée pour annuler l'effet chaleur.
    Calé sur ~+1 bpm/°C (soit ~0,6% d'EF par °C au-dessus de habitude+3°C), plafonné à 15%.
    Coefficient calibrable sur tes propres séances chaud/frais une fois assez de données."""
    if temp is None or baseline is None:
        return 1.0
    dev = max(0.0, temp - baseline - tol)
    eff = min(cap, k * dev)
    return 1.0 / (1.0 - eff)

def heat_diagnosis(temp, baseline):
    if temp is None or baseline is None:
        return None
    dev = temp - baseline
    if dev >= 8:
        return f"séance sans doute plus dure : {temp:.0f}°C, soit {dev:.0f}°C au-dessus de ton habitude ({baseline:.0f}°C)"
    if dev >= 5:
        return f"un peu plus chaud que d'habitude ({temp:.0f}°C vs {baseline:.0f}°C)"
    if dev <= -8:
        return f"nettement plus frais que d'habitude ({temp:.0f}°C vs {baseline:.0f}°C)"
    return None

def plan_heat_advice(forecast_temp, baseline=None):
    """Recommandation d'ajustement d'intensité pour une séance à venir (pilotée par la
    température absolue ; l'écart à l'habitude n'est qu'une note)."""
    t = forecast_temp
    if t is None:
        return None
    dev = (t - baseline) if baseline is not None else None
    note = f" (soit {dev:.0f}°C de plus que ton habitude)" if (dev is not None and dev >= 8) else ""
    if t >= 32:
        lvl, adj, adv = "canicule", -8, "Réduis l'intensité (~8%), passe sur des cibles de FC plutôt que d'allure/puissance, hydrate-toi, et cours tôt le matin."
    elif t >= 27:
        lvl, adj, adv = "forte chaleur", -5, "Allège légèrement (~5%), privilégie les cibles de FC, décale à la fraîche si possible."
    elif t >= 22:
        lvl, adj, adv = "chaud", 0, "Rien à changer, mais hydrate-toi et vise le bas de la fourchette d'allure."
    else:
        lvl, adj, adv = "tempéré", 0, None
    if adv and note:
        adv += note
    return {"level": lvl, "temp": t, "intensity_adjust_pct": adj, "advice": adv}

# ------------------------------------------------------------------ réseau (Open-Meteo)
def _hour_index(times, target_iso):
    """Index de l'heure la plus proche dans la liste de timestamps Open-Meteo."""
    tgt = datetime.fromisoformat(target_iso[:16])
    best, bi = None, 0
    for i, ts in enumerate(times):
        d = abs((datetime.fromisoformat(ts[:16]) - tgt).total_seconds())
        if best is None or d < best:
            best, bi = d, i
    return bi

def fetch_temp_archive(lat, lon, when_iso):
    """Température réelle passée (°C) au lieu/heure donnés."""
    import requests
    day = when_iso[:10]
    r = requests.get(ARCHIVE, params={"latitude": lat, "longitude": lon,
        "start_date": day, "end_date": day, "hourly": "temperature_2m", "timezone": "auto"}, timeout=30)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    if not h.get("time"):
        return None
    return h["temperature_2m"][_hour_index(h["time"], when_iso)]

def fetch_temp_forecast(lat, lon, when_iso):
    """Température prévue (°C) au lieu/heure donnés (jusqu'à ~16 jours)."""
    import requests
    r = requests.get(FORECAST, params={"latitude": lat, "longitude": lon,
        "hourly": "temperature_2m", "forecast_days": 16, "timezone": "auto"}, timeout=30)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    if not h.get("time"):
        return None
    return h["temperature_2m"][_hour_index(h["time"], when_iso)]

def fetch_temp(lat, lon, when_iso):
    """Température au lieu/heure : archive réelle si dispo, sinon prévision (séances très
    récentes que l'archive n'a pas encore). Renvoie None si indisponible ou hors-ligne —
    l'appelant garde alors temp_c à None plutôt que de planter."""
    for fn in (fetch_temp_archive, fetch_temp_forecast):
        try:
            t = fn(lat, lon, when_iso)
            if t is not None:
                return t
        except Exception:
            continue
    return None

# ------------------------------------------------------------------ démo / tests
if __name__ == "__main__":
    # Tests hors-ligne des fonctions de calcul
    base = temp_baseline([14, 16, 15, 17, 13, 15, 16])
    print(f"Température habituelle (médiane) : {base}°C")
    for t in (15, 22, 26, 31):
        f = thermal_ef_factor(t, base)
        print(f"  {t}°C → correction EF ×{f:.3f}  | {heat_diagnosis(t, base) or 'RAS'}")
    print("Anticipation :")
    for t in (18, 24, 28, 33):
        a = plan_heat_advice(t, base)
        print(f"  prévu {t}°C → {a['level']} (ajust. {a['intensity_adjust_pct']}%) : {a['advice'] or 'RAS'}")


def fetch_temp_jour(lat, lon, date_iso):
    """(température à 08h, maximum de la journée) au lieu donné — pour l'appli :
    la séance du matin se court à la température de 08h, la planification du reste
    de la journée se juge au MAX. Renvoie (None, None) si hors-ligne."""
    import requests
    r = requests.get(FORECAST, params={"latitude": lat, "longitude": lon,
        "hourly": "temperature_2m", "start_date": date_iso, "end_date": date_iso,
        "timezone": "auto"}, timeout=30)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    times, temps = h.get("time") or [], h.get("temperature_2m") or []
    if not times:
        return None, None
    t08 = next((tt for hh, tt in zip(times, temps) if hh.endswith("T08:00")), None)
    valid = [tt for tt in temps if tt is not None]
    return t08, (max(valid) if valid else None)
