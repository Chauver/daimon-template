# ONBOARDING — Daïmon s'adapte à SON athlète

> **Pour Claude (Code).** Ce protocole s'exécute UNE FOIS, à la première session (déclencheur :
> `journal/ATHLETE.md` n'existe pas). Ton rôle : interviewer l'athlète, puis GÉNÉRER sa
> configuration complète. Prends ton temps — c'est la fondation de tout le reste.
> Pose les questions PAR PETITS GROUPES (pas les 40 d'un coup), en t'adaptant aux réponses.

---

## Phase 1 — Le questionnaire

### A. Profil
1. Prénom / comment veux-tu que je t'appelle ? Et moi, tu veux m'appeler comment ? (défaut : Daï)
2. Âge, sexe, taille, poids approximatif ?
3. Métier / niveau de compréhension physiologique souhaité (vulgarisé ↔ technique) ?
4. Quel(s) sport(s) ? (course, vélo, triathlon, trail, natation, autre — plusieurs possibles)
5. Ton histoire sportive en quelques lignes : années de pratique, meilleurs résultats,
   ce dont tu es fier, ce qui t'a manqué.

### B. Objectif
6. Objectif principal : une course/un événement précis (lequel, quelle date) ? Une performance
   chiffrée ? Ou de la forme générale sans échéance ?
7. Objectif secondaire éventuel (course intermédiaire, perte de poids, régularité…) ?
8. À quel point c'est sérieux : plaisir / ambitieux / prioritaire dans ta vie ?

### C. Santé & garde-fous (⚠️ section la plus importante)
9. Blessures passées ou chroniques ? (zone, ancienneté, ce qui les déclenche)
10. Zones fragiles à surveiller même sans blessure actuelle ?
11. Suivi médical, traitements, contre-indications ? (à 45+ : un test d'effort médical récent
    est fortement recommandé avant de structurer de l'intensité — le suggérer s'il n'y en a pas)
12. Y a-t-il un volume/une intensité qui t'a déjà cassé par le passé ? (c'est le futur plafond)
> Claude : chaque réponse ici devient un GARDE-FOU NON NÉGOCIABLE dans CLAUDE.md §7,
> avec compteur dédié si pertinent (comme le plafond course/Achille du Daïmon d'origine).

### D. Données & matériel
13. Montre/capteurs ? (marque, ceinture FC, capteur de puissance, home-trainer…)
14. As-tu un compte intervals.icu ? (sinon : guide-le pour le créer et connecter Garmin/
    Polar/Suunto/Wahoo — c'est le canal de données de Daïmon, gratuit)
15. Applis : Zwift ? Strava (pour l'historique) ?
16. Balance à la maison (suivi poids optionnel) ?

### E. Seuils connus (sinon : tests à programmer en semaines 1-6)
17. FTP vélo ? Allure seuil course ? CSS natation ? FC max / FC repos / LTHR ?
18. Ces valeurs datent de quand ? (périmées = à retester, pas à croire)

### F. Vie réelle
19. Budget temps hebdomadaire RÉALISTE : par jour, quels créneaux ? (matin/midi/soir, durées)
20. Contraintes récurrentes (gardes, astreintes, enfants, déplacements) ?
21. Vacances/immobilisations déjà connues dans les 6 prochains mois ?
22. Le sommeil : régulier ? court ? Alcool : tu veux qu'on le trace (confondant HRV) ?

### G. Préférences de coaching
23. Ton attendu : cash / encourageant / les deux ?
24. Séances : tu aimes la variété ou la routine ? La musique (cadence) ?
25. Ce que ton ancien coach/plan faisait et que tu ne veux PLUS.

---

## Phase 2 — La génération (ce que Claude construit à partir des réponses)

1. **`journal/ATHLETE.md`** — la fiche d'identité sportive complète (réponses synthétisées).
2. **`CLAUDE.md`** — remplacer chaque bloc `{{...}}` du template par les vraies valeurs :
   athlète, objectif, seuils, squelette hebdo, garde-fous (section C = sacrée).
3. **`.env`** — depuis `.env.example` : clés intervals.icu, seuils, lieu de base (météo).
4. **`journal/decisions.md`** — écrire D1 (objectif + plan), D2 (garde-fous santé),
   D3 (canal de données), datées du jour. Le fil des décisions démarre ici.
5. **Courbe de volume** — construire les 52 semaines (ou la durée jusqu'à l'échéance) dans
   `build_web_state.py` (WK) : moyenne = ce que le budget temps de F.19 permet VRAIMENT,
   vagues 3+1, pic ≤ 1,6× la moyenne, affûtage si course. **Adapter à l'ÂGE** : après ~48-50 ans,
   la récupération dicte le rythme — envisager des cycles 2+1 ou 3+1 avec semaine allégée plus
   marquée, échauffements plus longs, 48 h entre deux séances intenses, et le renfo/musculation
   devient NON NÉGOCIABLE (masse musculaire et densité osseuse). Mésocycles récup alignés sur F.21.
6. **Adapter les constantes personnelles héritées du Daïmon d'origine** (sweep obligatoire —
   elles appartiennent à l'athlète précédent) :
   - `build_web_state.py` : `RACE`, `PLAN_START`, `WK`, `titre`, seuils du dict `seuils`
   - `readiness_model.py` : zones FC (`band()`), FC repos de référence
   - `ht_analysis.py` : `FTP`, `PLAFOND_IM`, `HR_MAX_BIKE`, consignes cadence
   - `fitness_model.py` : plafonds de volume par discipline (garde-fous C), `SWIM_OW_IF`
   - `push_*.py` : valeurs par défaut (FC cibles, watts)
   - `web/index.html` : rien (piloté par le state) ; `wrangler.jsonc` : nom du worker
7. **Doctrine** — lancer une recherche approfondie (littérature 2020+) sur LE(S) sport(s) et
   LE profil de l'athlète → écrire `doctrine/methodologie.md` + alimenter
   `doctrine/banque_evidence.jsonl` (schéma dans `doctrine/README.md`). Celle du Daïmon
   d'origine (triathlon longue distance) ne se copie PAS : elle se régénère.
8. **Premier cycle** — proposer la semaine 1 (prudente : on calibre, on ne performe pas),
   avec les tests de seuils manquants placés en semaines 2-6.
9. **PWA (optionnel)** — si l'athlète la veut : compte Cloudflare gratuit, `wrangler.jsonc`
   renommé, `APP_PASSWORD` en secret, déploiement git-connecté. Sinon : `index.html` local suffit.

## Phase 2 bis — L'ARCHÉOLOGIE STRAVA (le miel de l'historique)

> Vécu du Daïmon d'origine : son export Strava a livré ses 4 tests PMA 2024 (dont sa vraie
> valeur pic, 30 W au-dessus de son souvenir), la recette exacte du bloc d'entraînement qui
> avait marché, et les puissances réelles de ses courses — la base de tout le pacing. Ne pas
> sauter cette étape : la mémoire de l'athlète arrondit, ses fichiers non.

1. **Demander l'export complet** : Strava → Réglages → Mon compte → « Télécharger vos données »
   (arrive par mail en quelques heures, zip avec `activities.csv` + tous les .fit.gz).
2. **Le mettre EN SÉCURITÉ** dès réception : copie dans `~/Documents/Strava_export/` (pas un
   dossier temporaire), et noter le chemin en mémoire.
3. **Indexer** : lire `activities.csv` (id, date, nom, type, description — les descriptions
   contiennent souvent les notes d'époque de l'athlète, précieuses).
4. **Extraire le miel AVEC l'athlète** (lui demander ce qui compte, puis fouiller) :
   - **les COURSES** (toutes) : puissance/FC/allure réelles, pacing par quart, ce qui a tenu
     ou lâché → c'est le diagnostic du profil (facteur limitant central vs périphérique) ;
   - **les TESTS** (FTP, PMA/EFX, CSS, VMA…) : les archiver dans `journal/tests_*.jsonl`
     (append-only) — chaque test historique = un point de référence gratuit ;
   - **les séances « PHARES »** : les blocs qui ont précédé ses meilleures perfs — en tirer
     LA recette qui a marché sur LUI (format, progression, fréquence), plutôt qu'une doctrine
     générique ;
   - outil : `fit_streams.py` lit chaque .fit et rend les mêmes flux que l'API intervals.icu
     (`gunzip` d'abord ; analyse minute par minute, paliers, FC fin de palier).
5. **En tirer les valeurs de référence** : meilleur test = étalon historique (l'écart actuel/pic
   dit la marge de progression réaliste) ; courses = fractions d'intensité réellement tenues
   (IF course) → les cibles de pacing futures se calent dessus, pas sur des tables génériques.

## Phase 3 — Le contrat

Terminer par un récapitulatif : objectif, garde-fous, squelette hebdo, prochaine séance —
et la règle d'or héritée du Daïmon d'origine : **le coach propose, l'athlète valide** ;
le micro peut annuler une séance, jamais le méso ni le macro ; les garde-fous santé priment
sur toute optimisation. Puis supprimer ce déclencheur d'onboarding de CLAUDE.md.
