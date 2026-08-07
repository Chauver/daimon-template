# Daïmon — ton coach d'endurance dans Claude Code

Daïmon est un **agent coach personnel** qui vit dans un dépôt git : Claude Code y joue le rôle
d'entraîneur — il récupère tes données (montre → intervals.icu), analyse ta forme, tient un
journal longitudinal (séances, décisions, blessures, poids…), propose tes séances et les pousse
sur ta montre. **Toi, tu valides.** Il s'adapte à TON sport, TES capacités et TES contraintes
via un questionnaire d'onboarding — puis il apprend de chaque séance.

> Né du « Daïmon » original construit pour une prépa IRONMAN 2027. Ce template est le squelette,
> vierge de toute donnée personnelle : ton Claude le remplit pour toi.

## Prérequis

- **Claude Code** (abonnement Claude Pro/Max) — [claude.com/claude-code](https://claude.com/claude-code)
- **Python 3.10+** avec `pandas` et `requests` (`pip install pandas requests`)
- Une montre GPS (Garmin/Polar/Suunto/Wahoo/COROS) reliée à **[intervals.icu](https://intervals.icu)** (gratuit)
- Optionnel : capteur de puissance / home-trainer, Zwift, un compte Cloudflare (appli iPhone PWA)

## Installation (10 minutes)

```bash
# 1. Crée TON dépôt depuis ce template (bouton « Use this template » sur GitHub)
#    ⚠️ choisis PRIVÉ : le journal contiendra des données de santé.
git clone git@github.com:<toi>/<ton-daimon>.git && cd <ton-daimon>

# 2. Configure les clés (intervals.icu → Settings → Developer)
cp .env.example .env    # puis édite .env

# 3. Lance Claude Code — l'onboarding démarre tout seul
claude
```

À la première session, Claude détecte qu'aucun athlète n'est configuré et exécute
**`ONBOARDING.md`** : un questionnaire (profil, objectif, santé, matériel, disponibilités…)
à partir duquel il génère ta configuration complète — CLAUDE.md personnalisé, garde-fous santé,
courbe de volume, doctrine d'entraînement recherchée pour TON sport, et ta première semaine.

## Ce que Daïmon sait faire (hérité de l'original, adapté à toi)

- **Pipeline quotidien** : sync intervals.icu → charge (CTL/ATL/TSB) → indice de forme composite
  (FC repos, HRV, sommeil, ressenti) → tableaux de bord (`KPI.md`, `index.html`, PWA optionnelle)
- **Mémoire longitudinale** : journal factuel append-only + synthèse évolutive + fil des décisions —
  le coach se souvient de tout et raisonne en tendances
- **Analyses fines** : cardio course (dérive corrigée de l'allure), home-trainer ERG, vélo route
  (« puissance pédalée »), reconnaissance de parcours GPX, météo réelle par séance
- **Trackers** : blessures (0-10 par zone), poids, alcool (confondant HRV), contexte matériel
  (chaussures, musique/cadence), budget
- **Séances vers la montre** : push intervals.icu → Garmin/Zwift, tests de seuils protocolisés
  (FTP 20', incrémental PMA, CSS natation), garde-fous non négociables

## Philosophie

1. **Le coach propose, l'athlète valide.** Toujours.
2. **Les garde-fous santé priment sur le plan.** Ils sont écrits dès l'onboarding.
3. **Une donnée absente ne s'invente pas.** Deux points ne font pas une tendance.
4. **Macro > méso > micro.** L'état du jour peut annuler une séance, jamais la structure.
5. **Ta doctrine est la tienne** : générée par recherche pour ton sport, taguée par niveau de
   preuve, enrichie au fil de ta prépa.

## Structure

```
CLAUDE.md          ← le « système d'exploitation » du coach (personnalisé à l'onboarding)
ONBOARDING.md      ← le questionnaire de démarrage (une seule fois)
doctrine/          ← méthodologie sourcée + règles de programmation/séances
journal/           ← LA MÉMOIRE : séances, synthèse, décisions, trackers (jamais dans git public !)
data/              ← exports bruts (gitignore)
*.py, *.sh         ← pipeline, analyses, push
web/ + worker.js   ← PWA iPhone optionnelle (Cloudflare, mot de passe)
```

## Licence & données

Code : usage personnel libre. **Tes données restent chez toi** : dépôt privé, `data/` ignoré,
la PWA est protégée par mot de passe. Daïmon aide à décider — il ne remplace ni ton jugement
ni un avis médical.
