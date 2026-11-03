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
bash /opt/orion-v3/deploy/v3_deploy.sh            # référence v3-sync du miroir ~/orion-dev
```

Le script (`deploy/v3_deploy.sh`) ne peut viser que la V3 :
- **Cible fixe.** Le dossier, le service et le compte sont écrits en dur, et contrôlés auprès de systemd. Il refuse
  de continuer si `config.local.toml` utilise un port de la production, ou si le dépôt V3 a des modifications
  locales.
- **Avance rapide seulement.** Une version qui ne descend pas de la version en place est refusée.
- **Sauvegarde vérifiée avant chaque déploiement.** `scripts/v3_backup.py` la range dans
  `~/sauvegardes/orion-v3/<date UTC>/`. Les 14 dernières sont gardées.
- **Contrôle de santé.** Le service doit être actif, le journal ne doit contenir aucune trace d'erreur, les modules
  doivent être démarrés et le visage doit répondre.
- **Retour automatique en cas d'échec.** Le script revient au commit précédent et redémarre.
- **Production prouvée intacte.** Son heure de démarrage est comparée avant et après.
- **Journal** : `~/sauvegardes/orion-v3/deploiements.log`.

Les deux chemins ont été essayés en réel le 4 novembre 2026 : un déploiement normal, et un commit jetable qui
empêchait le Core de démarrer. Dans le second cas, la panne a été détectée et la V3 est revenue seule à la version
précédente ; la production n'a pas été touchée.

Restaurer les données :
```bash
sudo systemctl stop orion-v3
/opt/orion-v3/.venv/bin/python /opt/orion-v3/scripts/v3_backup.py restore ~/sauvegardes/orion-v3/<date>
sudo systemctl start orion-v3
```
- La restauration est refusée si le service tourne ou si la sauvegarde ne se vérifie pas (résumés SHA-256 et
  `integrity_check`).
- Les fichiers actuels sont mis de côté dans `data/avant-restauration-<date>/`, jamais effacés.

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

## 3. Audit d'isolation V2/V3 (3 novembre 2026)

Contrôles en lecture seule sur le mini-PC, et lecture du code. La production n'a été ni lue en profondeur, ni
sollicitée, ni modifiée.

### Conforme

| Point | Constat |
|---|---|
| Comptes | `orion-v3` n'appartient qu'à son groupe ; `orion` (production) n'est pas dans `orion-v3`. |
| Fichiers de la production | `/opt/orion`, `/opt/jarvis` : 750, groupe `orion` ; `/var/lib/orion`, `/var/lib/orion-call` : 700 ; `/etc/orion-call` : 750. Aucun fichier sensible lisible par tous (recherche de `.env`, jetons, bases). |
| Fichiers de la V3 | `/opt/orion-v3` : 750, groupe `orion-v3` ; la production ne peut pas la lire. |
| Service V3 | `ProtectSystem=strict`, `ProtectHome` (sauvegardes de `/home/user` masquées), `PrivateTmp`, `PrivateDevices`, `NoNewPrivileges`, aucune capacité ; production masquée (`InaccessiblePaths`). |
| Journaux | `orion-v3` n'est ni dans `adm` ni dans `systemd-journal` : il ne lit pas le journal de la production. |
| Secrets | Jeton V3 généré à l'installation ; aucun secret de la production dans `/opt/orion-v3`. Clé des données personnelles absente. |
| Identifiants systemd | `/etc/credstore*` : 700 root. |
| Droits sudo | `user` ne peut redémarrer sans mot de passe que `orion-v3` (règle à part). |
| Ports | V3 sur 8865 à 8867, ouverts dans `ufw` au PC et au Katana seulement ; API V3 : 401 sans jeton. |
| Ollama partagé | même `keep_alive` que la production : la V3 ne fait jamais décharger ses modèles. |

### Écarts trouvés et correction préparée

1. **Réseau local : le fond d'écran de la production autorise toujours `127.0.0.1`** (`orion/wallpaper/server.py`).
   - Tout processus du mini-PC, donc un service V3 compromis, pourrait lire les statistiques de la production
     (agenda, rappels) et obtenir son jeton de page. Il pourrait alors lancer les commandes rapides (lumières,
     Spotify, suppression d'un rappel).
   - L'API de la production autorise aussi `127.0.0.1`, mais exige son jeton.
   - Le code V3 ne contacte jamais ces ports ; le risque suppose un code V3 défaillant ou malveillant.
   - Corriger la production est hors périmètre : elle est intouchable.
   - **Correction préparée** : `deploy/v3_isolation.nft`. Des règles nftables par compte refusent à `orion-v3`
     les ports 8765, 8766, 8767 et 8770 du mini-PC, par la boucle locale comme par son adresse réseau. Elles
     refusent aussi les agents du PC et du Katana (8765, 8766).
   - Ollama et SearXNG restent joignables.
2. **Aucune limite de ressources sur `orion-v3`.** Un emballement de la V3 pouvait priver la production de mémoire,
   et le noyau aurait pu tuer la production plutôt que la V3.
   - **Correction préparée** : `MemoryHigh=2G`, `MemoryMax=3G`, `MemorySwapMax=512M`, `OOMScoreAdjust=500` (la V3
     est sacrifiée la première), `CPUWeight=50`, `IOWeight=50`, `TasksMax=512`.
3. `/opt/jarvis` (ancienne copie de la production) n'était protégé que par ses droits ; il est désormais aussi masqué
   au service.

Tout est appliqué par `sudo bash /home/user/orion-v3/deploy/v3_isolation.sh`.
- Le script est idempotent et ne touche jamais à la production.
- Il recharge les règles à chaque démarrage d'`orion-v3` (`ExecStartPre`) : sans elles, le service ne démarre pas.
- Il vérifie le résultat à la fin : chaque port de la production doit être « bloqué » pour `orion-v3`, et Ollama
  doit rester « joint ».
- Essai en conteneur isolé du réseau :
  - sans les règles, `orion-v3` joint un faux 8767 ;
  - avec les règles, il est bloqué sur 8766, 8767 et 8770, et joint toujours 11434 ;
  - un autre compte n'est pas affecté ;
  - le rechargement est idempotent.
- `deploy/v3_env.sh` l'appelle désormais, pour une réinstallation.

**État : appliqué par le propriétaire le 3 novembre 2026.** Contrôle du 4 novembre :
- les règles installées (`/etc/orion-v3/isolation.nft`) sont identiques au dépôt ;
- `ExecStartPre` les charge à chaque démarrage (statut 0) ;
- `MemoryMax`, `CPUWeight` et `OOMScoreAdjust` sont actifs ;
- `/opt/jarvis` est masqué ;
- les comptes `orion-import` et `orion-evolve` sont installés.

L'agent Windows V3 (port 8875 du PC) reste joignable, comme prévu.

### Risques acceptés

- Le compte `user` (administrateur) appartient aux groupes `orion` et `orion-v3`, et au groupe `docker`
  (équivalent root). Sa compromission toucherait les deux environnements ; c'est le compte d'administration.
- Le visage (8765 et 8865) n'a pas de filtre d'adresse dans le code ; `ufw` le limite au PC et au Katana. Il
  n'affiche qu'un état d'animation.
- La V3 et la production partagent le processeur du mini-PC et du Katana (LLM) ; les limites ci-dessus
  donnent la priorité à la production sur le mini-PC.
