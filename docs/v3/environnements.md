# Environnements : production V2 et développement V3 (mini-PC)

Décision du propriétaire du 3 novembre 2026 (option 1) : séparation stricte. Production V2 restaurée sur sa
référence stable ; V3 dans un environnement isolé du mini-PC, testable depuis le PC et le Katana, sans aucune
dépendance avec la production.

| | Production V2 | Développement V3 |
|---|---|---|
| Service systemd | `orion` (compte `orion`) | `orion-v3` (compte `orion-v3`, hors des groupes `orion` et `orion-call`) |
| Répertoire | `/opt/orion` (branche `V2`) | `/opt/orion-v3` (branche `v3`) |
| Données | `/opt/orion/data` | `/opt/orion-v3/data` (vide au départ, données fictives) |
| Configuration | `/opt/orion/config.local.toml` | `/opt/orion-v3/config.local.toml` (modèle `deploy/v3_env.toml`) |
| Secrets | `/opt/orion/.env` | `/opt/orion-v3/.env`, jeton propre généré à l'installation |
| Ports | 8765 visage, 8766 API, 8767 fond d'écran, 8770 passerelle d'appel | 8865 visage, 8866 API, 8867 fond d'écran et conversation |
| Journaux | `journalctl -u orion` | `journalctl -u orion-v3` |
| Audio | agent du PC (micro, haut-parleurs) | aucun (`[audio] mode = "none"`) : conversation écrite |
| ORION Control | raccourci « ORION Control » (`C:\Users\theju\jarvis`, branche `V2`) | `python -m orion.control --profile v3` (`C:\Users\theju\orion-v3`), réglages à part |

Partagés en lecture seulement, sans donnée personnelle : les modèles LLM (Ollama du mini et du Katana) et SearXNG
(recherche Web publique). Ollama garde les modèles en mémoire selon la dernière demande reçue : la V3 envoie le même
`keep_alive` que la production, sinon elle ferait décharger les modèles de celle-ci. Les deux environnements se
partagent aussi le temps de calcul du Katana et du mini. Le compte `orion-v3` ne peut pas lire `/opt/orion` (droits 750, groupe `orion`), et le
service lui masque en plus `/opt/orion`, `/etc/orion-call`, `/var/lib/orion` et `/var/lib/orion-call`.

## 1. Retour de la production en V2

### Vérifications préalables (faites le 3 novembre 2026)

- Tag `avant-v3-2026-11-02` = `0865079` = tête de la branche `V2` (GitHub et `/opt/orion`).
- Sauvegarde `~/orion-data-avant-v3-2026-11-02.tar.gz` : archive intègre (`gzip -t`), liste lisible.
- Compatibilité des données avec la V2 (code de `0865079` comparé à `cdae70e`) :
  - `journal.sqlite`, mails, règles d'appel, présence, mémoire, agenda : formats inchangés ;
  - `orion_v3.db`, `orion_ops.db`, `news_delivery.json` : propres à la V3, ignorés par la V2, **laissés en place** ;
  - `config.local.toml` : **incompatible**. La V2 refuse toute section inconnue (`[platform]`, `[watch]`, `[news]`,
    `[evolve]`, `[purchase]`) et ne démarrerait pas. Le fichier actuel n'ajoute que ce bloc à
    `config.local.toml.avant-v3-modules` (vérifié par `diff`) ;
  - `routines.json` : **incompatible**. La routine « réveil » contient l'étape `deliver_news_bulletin`, inconnue de
    la V2, qui ignorerait alors la routine entière. Seule cette étape, ajoutée le 2 novembre, est retirée.

### Opérations

1. Arrêt du service (`sudo systemctl stop orion`), pour une copie cohérente des bases SQLite.
2. Instantané complet, sans rien écraser : `~/orion-prod-v3-2026-11-03.tar.gz` (dossier `data/`,
   `config.local.toml`, commit en cours). Contrôle par `gzip -t` et liste.
3. Copies de sûreté à côté des originaux : `config.local.toml.v3-2026-11-03` et `data/routines.json.v3-2026-11-03`.
4. Configuration V2 : `config.local.toml` reçoit le contenu de `config.local.toml.avant-v3-modules`.
5. `routines.json` : retrait de la seule étape `deliver_news_bulletin`.
6. Code : `git checkout V2` dans `/opt/orion` (fichiers non suivis conservés).
7. Contrôle de la configuration avec le code V2 (`load_config`), puis démarrage, journal (aucune routine ignorée,
   aucune erreur), `--health`.

Aucune base n'est écrasée ni supprimée : les fichiers propres à la V3 restent dans `data/`, inutilisés.

### Rollback (retour à l'état V3 du 3 novembre)

```bash
sudo systemctl stop orion
cd /opt/orion
git checkout v3            # cdae70e
cp config.local.toml.v3-2026-11-03 config.local.toml
cp data/routines.json.v3-2026-11-03 data/routines.json
sudo systemctl start orion
.venv/bin/python -m orion --health
```

En dernier recours, l'instantané `~/orion-prod-v3-2026-11-03.tar.gz` contient `data/` et la configuration de ce
moment.

## 2. Environnement V3 isolé

### Installation (une seule fois, par le propriétaire : demande `sudo`)

```bash
sudo bash /home/user/orion-v3/deploy/v3_env.sh
```

Le script `deploy/v3_env.sh` :
- crée le compte système `orion-v3` (sans connexion, dans aucun groupe d'ORION) ;
- clone la branche `v3` dans `/opt/orion-v3` (propriétaire `user`, groupe `orion-v3`) ;
- copie l'environnement Python et les modèles de la production (copie, aucun lien) ;
- génère un jeton propre (`ORION_AGENT_TOKEN`), sans jamais lire le `.env` de la production ;
- écrit la configuration à partir de `deploy/v3_env.toml` ;
- installe `orion-v3.service` (même durcissement que `orion`, plus les chemins de la production masqués) ;
- autorise `user` à redémarrer ce seul service sans mot de passe (`/etc/sudoers.d/orion-v3`, vérifié par `visudo`) ;
- ouvre dans `ufw` les ports 8865 à 8867 au PC fixe et au Katana seulement ;
- vérifie que le compte `orion-v3` ne peut pas lire la production avant de démarrer le service.

Essai complet du script dans un conteneur jetable, avec une fausse production : compte, droits, jeton, configuration,
règle sudo, contrôle d'isolation, refus d'une seconde installation. Essai du Core V3 avec données temporaires :
démarrage, conversation écrite (heure, rappel), extinction du PC refusée.

Il ne touche ni à `/opt/orion`, ni au service `orion`, ni à leurs comptes.

### Déploiement d'une version V3

```bash
git -C /opt/orion-v3 fetch ~/orion-dev v3-sync && git -C /opt/orion-v3 merge --ff-only FETCH_HEAD
sudo systemctl restart orion-v3
```

Rollback V3 : `git -C /opt/orion-v3 reset --hard <commit précédent>` puis redémarrage. La production n'est jamais
concernée.

### Tester depuis vos appareils

- Conversation écrite : `http://192.168.1.91:8867/discuter` (PC fixe et Katana).
- Tableau de bord : `http://192.168.1.91:8867/` ; bulletin : `http://192.168.1.91:8867/bulletin`.
- ORION Control V3 : `python -m orion.control --profile v3` depuis `C:\Users\theju\orion-v3` (Core
  `http://192.168.1.91:8866`, jeton V3 rangé à part, jamais celui de la production).

### Désinstallation

```bash
sudo systemctl disable --now orion-v3
sudo rm /etc/systemd/system/orion-v3.service /etc/sudoers.d/orion-v3
sudo systemctl daemon-reload
sudo ufw delete allow proto tcp from 192.168.1.128 to any port 8865:8867
sudo ufw delete allow proto tcp from 192.168.1.73 to any port 8865:8867
```

Le dossier `/opt/orion-v3` et le compte `orion-v3` peuvent ensuite être retirés à la main ; seules des données
fictives s'y trouvent.
