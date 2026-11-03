# ORION V3 — progression (point de reprise)

Mis à jour à chaque livraison. À relire en premier après une interruption.

## État au 3 novembre 2026

- **Branche d'intégration :** `v3`.
  - Dépôt de travail : `C:\Users\theju\orion-v3` (copie de travail `v3` du dépôt `C:\Users\theju\jarvis`, qui est
    revenu sur `V2`).
  - Les tests tournent sur le mini-PC, dans le miroir `~/orion-v3`, synchronisé par le script `v3sync.sh` du
    scratchpad.
- **Production `/opt/orion` (mini-PC) : V2 `0865079`**, restaurée le 3 novembre (docs/v3/environnements.md, § 1).
  - Données, configuration et routines rendues compatibles, sans rien écraser : copies `*.v3-2026-11-03`.
  - Instantané de l'état V3 de la production : `~/orion-prod-v3-2026-11-03.tar.gz`.
  - ORION Control du PC fixe et Katana remis sur le code V2.
- **Environnement V3 isolé :** préparé et testé.
  - Script `deploy/v3_env.sh` : essai complet en conteneur ; le Core V3 a aussi tourné sur des données temporaires.
  - **En attente d'une commande `sudo` du propriétaire** :
    `sudo bash /home/user/orion-v3/deploy/v3_env.sh`.
  - Ensuite, déploiements V3 dans `/opt/orion-v3` uniquement.

## Fait

- F1, F2, F3 : `docs/v3/foundation.md`.
- Séparation des environnements : `docs/v3/environnements.md`.
  - Mode sans audio, page `/discuter`, ORION Control `--profile v3`.
  - Configuration V3 sans secret ni service de la production.
- **F4 (stockage séparé et chiffrement)** : `docs/v3/foundation.md`, § « Lot F4 ».
  - Trois bases et leurs classes ; AES-256-GCM ; clé par identifiant systemd ; rotation, sauvegarde et restauration ;
    migration JSON réversible.
  - Textes personnels scellés dans la base opérationnelle.
  - Outil `python -m orion.platform.datakey`.
  - Test complet : chiffrement, arrêt, redémarrage, perte et récupération de la clé, déchiffrement.
  - **Aucune clé réelle créée, aucune donnée réelle importée.**
- Rappels dans une maison vide : gardés, présentés au retour ; jamais rejoués s'ils sont obsolètes.
- Inventaire Google Takeout : `python -m orion.imports.takeout <archives ou dossier>`. Il lit seulement l'index des
  archives.

## Prochain chantier

1. Dès l'installation de l'environnement V3 : déployer `v3` dans `/opt/orion-v3`, contrôler l'isolation (compte,
   ports, pare-feu, aucune lecture de `/opt/orion`), puis faire un premier essai depuis le PC (`/discuter`, ORION
   Control V3).
2. NEWS P1 en parallèle, sans toucher au socle : fiabilité des sources, journal sans données personnelles.
3. F5 (ressources et mode dégradé, comptes `orion-import` et `orion-evolve`), après accord.

## Google Takeout

Export en cours chez le propriétaire. Ne rien récupérer automatiquement. Quand les fichiers seront dans
l'environnement de développement :
1. faire l'inventaire (catégories, formats, volumes) ;
2. présenter le rapport ;
3. attendre une validation explicite avant tout import. L'import n'ira que dans la base personnelle chiffrée (F4),
   avec une vraie clé dont vous aurez décidé la création et la copie hors ligne.
