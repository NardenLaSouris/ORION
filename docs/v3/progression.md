# ORION V3 — progression (point de reprise)

Mis à jour à chaque livraison. À relire en premier après une interruption.

## État au 3 novembre 2026

- **Branche d'intégration :** `v3`. Dépôt de travail : `C:\Users\theju\jarvis` (PC fixe). Les tests tournent sur le
  mini-PC, dans le miroir `~/orion-v3`, synchronisé par le script `v3sync.sh` du scratchpad.
- **Dernier commit validé :** voir `git log -1 v3`. La suite complète passe (2243 tests au dernier passage).
- **Production `/opt/orion` (mini-PC) :** branche `v3`, commit `cdae70e`, depuis le 2 novembre au soir, à la demande
  explicite du propriétaire (« V3 partout »).
  - `[platform]`, WATCH, NEWS, EVOLVE et PURCHASE sont activés.
  - Retour possible vers la V2 : tag `avant-v3-2026-11-02`, et sauvegardes `~/orion-data-avant-v3-2026-11-02.tar.gz`
    et `config.local.toml.avant-v3-modules`.

## Blocage : décision du propriétaire attendue

La mission « autonomie maximale » (3 novembre) part d'une production V2 intouchable et d'un environnement V3 isolé.
Ce n'est plus le cas : la production tourne déjà sur `v3`, et aucun environnement V3 isolé n'existe. En attendant la
décision, **aucun déploiement sur `/opt/orion`**. Le développement continue sur `v3`, avec tests, commits et push.

Options proposées :
1. **Revenir à la V2 en production, et créer un environnement V3 isolé** sur le mini (`/opt/orion-v3`) :
   - autres ports, autres bases, copie de la configuration ;
   - sans audio ni appels, sans secret de production ;
   - les déploiements V3 iront ensuite là.
2. **Considérer `/opt/orion` comme l'environnement V3** et autoriser explicitement les déploiements V3 dessus.

## Fait récemment

- F1, F2 : `docs/v3/foundation.md`.
- Surveillances automatiques : source Web (SearXNG), sources et fréquence recalculées, événements passés ignorés.
- Bulletin NEWS mis en page (HTML) : servi au PC par `/bulletin` sur le port du fond d'écran ; outil
  `deliver_news_bulletin` (navigateur du PC, sinon mail à soi) ; étape ajoutée à la routine réveil ; ouverture au
  démarrage du PC.
- F3, premier point : l'origine des alertes NEWS vient de l'identité de ce qui les émet, plus jamais du contexte.

## F3 : livré sur `v3` (non déployé)

- Classification NEWS par l'identité de ce qui émet : `87dc49d`.
- Annulation coopérative et bail renouvelé : `d313ec3`.
- `EventHub` (événements, décisions, remises), notifications manquées, récapitulatif, relance : `bc6b96d`.
- Documentation : `docs/v3/foundation.md`, § « Lot F3 ».
- Reste à faire : rapport au propriétaire, puis F4 (stockage et chiffrement) après son accord, comme le demande la
  validation de F3.

## Prochain chantier

En attendant les décisions (déploiement, F4), travaux indépendants, sans déploiement :
- revue d'ORION Control de F3 : **faite**, sur une démonstration factice. Les tableaux ne débordent plus de la page (`a074167`) ;
- inventaire Google Takeout : **prêt**. `python -m orion.imports.takeout <archives ou dossier>` lit seulement
  l'index des archives (noms, tailles), jamais leur contenu, et produit un rapport Markdown. Il est testé sur des
  archives factices.

## Google Takeout

Export en cours chez le propriétaire. Ne rien récupérer automatiquement. Quand les fichiers seront dans
l'environnement de développement :
1. faire l'inventaire (catégories, formats, volumes) ;
2. présenter le rapport ;
3. attendre une validation explicite avant tout import.
