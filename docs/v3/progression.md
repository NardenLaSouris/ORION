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
- **Environnement V3 isolé : installé le 3 novembre** (`/opt/orion-v3`, service `orion-v3`, compte `orion-v3`).
  - Version déployée : voir le journal ci-dessous ; base personnelle verrouillée (aucune clé).
  - Contrôlé : compte hors des groupes d'ORION, production masquée par le service, ports 8865 à 8867 joignables
    depuis le PC, API refusée sans jeton, conversation écrite qui répond.
  - Redémarrage sans mot de passe : `sudo systemctl restart orion-v3` (ce service seulement).
  - ORION Control V3 : `pythonw -m orion.control --profile v3` depuis `C:\Users\theju\orion-v3`, jeton V3 rangé
    dans `%APPDATA%/ORION Control V3`.

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

## Journal du travail autonome (directive du 3 novembre 2026, propriétaire absent)

| Heure | Chantier | Commit | Tests | Déployé dans V3 |
|---|---|---|---|---|
| 12:30 | Audit d'isolation V2/V3 (`environnements.md` § 3), `deploy/v3_isolation.sh` | `1404472` | 2276 | non (demande `sudo`) |
| 12:40 | Raccourci menu Démarrer « ORION Control V3 » (PC fixe, hors démarrage automatique) | — | — | — |
| 12:45 | NEWS P1 : journal quotidien (`news.md`, § « Journal quotidien ») | `7087b82` | 2291 | oui, vérifié |
| 13:00 | NEWS P1 : corrections après un journal réel (un sujet par place, chiffres d'affirmation) | `7c9f14b` | 2292 | oui, vérifié |

**En attente du propriétaire :**
- `sudo bash /home/user/orion-v3/deploy/v3_isolation.sh` (règles réseau et limites de ressources de la V3) ;
- création d'une vraie clé de données personnelles (F4), seulement s'il le décide ;
- inventaire Takeout, quand l'export sera déposé.

## Prochain chantier

1. F5 : service de ressources et mode dégradé (Katana, transcription, agents, NAS facultatif) ; spécification et
   scripts des comptes `orion-import` et `orion-evolve`, sans installation.
2. Essai réel du journal pendant une semaine dans V3 ; réglages d'après les journaux obtenus.
3. Ensuite, selon les dépendances de la feuille de route : KNOWLEDGE (P2, base personnelle) une fois la clé décidée ;
   MISSIONS (F1, F2).

## Google Takeout

Export en cours chez le propriétaire. Ne rien récupérer automatiquement. Quand les fichiers seront dans
l'environnement de développement :
1. faire l'inventaire (catégories, formats, volumes) ;
2. présenter le rapport ;
3. attendre une validation explicite avant tout import. L'import n'ira que dans la base personnelle chiffrée (F4),
   avec une vraie clé dont vous aurez décidé la création et la copie hors ligne.
