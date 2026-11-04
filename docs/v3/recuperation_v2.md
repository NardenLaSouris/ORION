# Fonctions quotidiennes de la V2 à récupérer dans la V3 (plan du 4 novembre 2026)

Depuis le 4 novembre, la V3 est le seul Core actif ; le Core V2 est arrêté et désactivé, gardé pour un retour
arrière (`docs/v3/reference_unique.md`). Le code de toutes ces fonctions est déjà dans la V3 (hérité de la V2) ; ce
qui manque : des **accès** (aucun secret de la V2 n'est repris sans votre accord explicite), des **réglages réservés à
root**, et des **données personnelles** (soumises à la clé de données F4). Rien n'a été copié depuis `/opt/orion`.

Règles du plan : une fonction à la fois ; chaque étape qui touche vos accès ou le système vous est demandée avant ;
sauvegarde de la configuration avant chaque changement ; essai réel après chaque fonction ; aucun nouvel
environnement ni agent.

## 1. Déjà repris par la V3

| Fonction | État dans la V3 |
|---|---|
| Voix (« Hey Orion », voix fr_FR-orion-medium, micro et haut-parleurs du PC) | active, par l'agent V3 du PC |
| Transcription | GPU du Katana (large-v3, jeton de la V3), Whisper local en secours |
| LLM | Katana (`qwen3-abliterated:8b-v2`), secours local `qwen2.5:3b` |
| Outils du PC, allumage du PC par le réseau | actifs |
| Visage (Katana), fond d'écran (8867, à choisir dans Wallpaper Engine) | actifs |
| Recherche Web, météo, minuteurs, rappels, NEWS, WATCH, SENTINEL, Discord, Frag Factory (essai), MISSIONS (verrouillé) | actifs |

## 2. Plan, dans l'ordre

### 2.1 Présence (aucun secret) : FAITE le 4 novembre 2026 à 11:57

Réglages recopiés de la V2 sur votre accord (ancienne configuration : `config.local.toml.avant-presence-*`). Au démarrage : « Présence : 5 capteur(s), utilisateurs suivis : owner », « Présence réseau : telephone_monsieur connecté » ; le fond d'écran affiche « Quelqu'un est à la maison ». Reste à vérifier : la source des capteurs de porte et de mouvement, et un départ puis un retour réels.

| | |
|---|---|
| V2 | `[presence]` : téléphone `telephone_monsieur` suivi sur le Wi-Fi (pilote `network` : `ping` puis `ip neigh` vers 192.168.1.124, MAC vérifiée, absent après 10 min de silence), délai d'arrivée 45 s ; capteurs déclarés (porte et mouvement de l'entrée, machine à laver, thermomètre du salon), alimentés par l'API (`/api/presence`) |
| Dépendances | aucune bibliothèque ; `ping` (ICMP permis à tous les groupes : `ping_group_range = 0 2147483647`) ; même durcissement que la V2 (`AF_INET AF_INET6 AF_UNIX`, `NoNewPrivileges`) : comportement identique à la V2 (le repli `ip neigh`, qui demande `AF_NETLINK`, n'aboutit ni en V2 ni en V3) ; aucune règle réseau à changer |
| Configuration | `[presence]` et ses `[presence.sensors.*]` recopiés de la configuration V2 (réglages, pas des secrets : adresse IP et MAC de votre téléphone) |
| Autorisation | votre accord pour recopier ces réglages (adresse de votre téléphone) |
| Données | `presence.json`, `house_journal.jsonl` (historique) : non repris ; la V3 repart sans historique |
| À vérifier | qui alimente les capteurs de porte et de mouvement (`/api/presence` avec le jeton de l'API V2) : si un appareil de la maison les envoie, il faudra le pointer vers l'API V3 (8866) avec son jeton |
| Essai | sortir puis revenir avec le téléphone ; « suis-je à la maison ? » |

### 2.2 Mail

| | |
|---|---|
| V2 | `[mail]` : votre adresse Gmail, IMAP et SMTP Gmail, dossiers d'archive et de corbeille ; mot de passe `MAIL_PASSWORD` |
| Dépendances | `imaplib`, `smtplib` (bibliothèque standard, présents) ; sortie vers imap.gmail.com et smtp.gmail.com (permise : les règles de la V3 ne bloquent que des ports locaux) |
| Configuration | `[mail]` recopié (réglages sans secret) |
| Autorisation | **un nouveau mot de passe d'application Gmail pour la V3** (compte Google > Sécurité > Mots de passe des applications), que vous saisissez vous-même dans `/opt/orion-v3/.env` (commande préparée, saisie masquée) ; ou votre accord explicite pour reprendre celui de la V2 |
| Données | `mail_seen.json`, `mail_actions.json` : non repris ; au premier passage, les non-lus existants deviennent « vus » sans annonce (`orion/mail/watch.py`) |
| Essai | « ai-je des mails ? », réception d'un mail de test, annonce d'une échéance (aucun rappel créé sans votre « oui ») |

### 2.3 Agenda

| | |
|---|---|
| V2 | agenda local `calendar.json` et adresse iCal privée `ORION_CALENDAR_ICS` (lecture) |
| Dépendances | lecteur iCal intégré (`orion/agenda.py`, aucune bibliothèque) ; sortie HTTPS vers Google Agenda |
| Configuration | `[calendar]` (adresse lue dans le `.env`) |
| Autorisation | l'**adresse iCal privée** (Google Agenda > Paramètres > Adresse secrète au format iCal) dans le `.env` de la V3, saisie par vous ; ou votre accord pour reprendre celle de la V2 |
| Données | `calendar.json` (vos événements saisis à la voix) : données personnelles, reprise seulement avec votre accord |
| Essai | « qu'est-ce que j'ai demain ? » |

### 2.4 Éclairage

| | |
|---|---|
| V2 | `[lights]` : chambre (192.168.1.174) et entrée (192.168.1.142), Tuya local 3.5 (`tinytuya`), clés `LIGHT_KEY_CHAMBRE` et `LIGHT_KEY_ENTREE` |
| Dépendances | `tinytuya` (présent dans la V3) ; port 6668 des ampoules sur le réseau local (permis) |
| Configuration | `[lights.rooms.*]` recopiés (identifiants d'appareil, adresses, versions : réglages) |
| Autorisation | les **clés locales** sont celles des ampoules (fixées à l'appairage) : votre accord explicite pour les reprendre de la V2, ou un réappairage (nouvelles clés) |
| Essai | « allume la chambre », « éteins tout » |

### 2.5 Spotify

| | |
|---|---|
| V2 | lecteur `orion-librespot` (service système, compte `orion`) qui écrit dans `/opt/orion/data/music.fifo` ; le Core V2 relaie la musique vers le PC ; autorisation Spotify `SPOTIFY_CLIENT_ID` et jeton `data/spotify_token.json` |
| Dépendances | la V3 ne peut pas lire `/opt/orion` (`InaccessiblePaths`) : le lecteur doit écrire dans un tube de la V3 |
| Réglages root (commande préparée, lancée par vous) | `orion-librespot` vers `/opt/orion-v3/data/music.fifo` (le tube créé pour le groupe `orion-v3`) ; règle polkit permettant au compte `orion-v3` de relancer `orion-librespot` |
| Configuration | `[spotify] player_service = "orion-librespot"`, `player_system = true`, `device = "ORION"` |
| Autorisation | `SPOTIFY_CLIENT_ID` (identifiant de votre application Spotify) : votre accord pour le reprendre, ou une nouvelle application ; puis **une nouvelle connexion Spotify** pour la V3 (`python -m orion --spotify-login` sur le Core, vous acceptez dans le navigateur) ; le jeton de la V2 n'est jamais repris |
| Essai | « mets de la musique », pause, volume |

### 2.6 Appels

| | |
|---|---|
| V2 | passerelle `orion-call` (service système, `/opt/orion-call`, configuration `/etc/orion-call`, root) : Linphone (compte SIP d'ORION, mot de passe dans `/etc/orion-call/env`) ; le Core la joint sur `127.0.0.1:8770` avec `CALL_GATEWAY_TOKEN` ; `[call]` : appareil `linphone`, `remind_when_away`, règles d'appel `call_rules.json` |
| Dépendances | les règles réseau de la V3 lui interdisent 8770 (passerelle de la V2) ; le code de la passerelle a été copié depuis la V2 (`/opt/orion-call/lib`) : à vérifier contre la V3 |
| Réglages root (commande préparée, lancée par vous) | 8770 permis à la V3 dans `isolation.nft` ; un **nouveau jeton** de passerelle (`/etc/orion-call/env` et `/opt/orion-v3/.env`), passerelle relancée ; le mot de passe Linphone reste où il est |
| Configuration | `[call]` (appareil, adresses SIP : réglages) |
| Autorisation | votre accord pour la commande root ; votre accord pour reprendre vos règles d'appel (`call_rules.json`, données personnelles) ou les refaire |
| Essai | appel d'essai depuis ORION Control (`POST /api/call`), puis un rappel en votre absence |

### 2.7 Ensuite

Routines, mémoire, apprentissages (`routines.json`, `memory.json`, `learned.json`), règles d'appel et historique :
avec la vraie clé de données F4 et votre accord, par import idempotent (comme `memory.json` vers KNOWLEDGE).

## 3. Récapitulatif de ce qui vous sera demandé

| Fonction | Votre part | Commande root |
|---|---|---|
| Présence | accord (réglages de votre téléphone) | non |
| Mail | mot de passe d'application (ou accord pour celui de la V2) | non |
| Agenda | adresse iCal (ou accord pour celle de la V2) | non |
| Éclairage | accord pour les clés des ampoules (ou réappairage) | non |
| Spotify | identifiant d'application (ou accord), connexion Spotify | oui (lecteur, polkit) |
| Appels | accord pour les règles d'appel ; appel d'essai | oui (règles réseau, jeton de passerelle) |

## 4. Divers

- **Pare-feu du Katana** : la règle « JARVIS Agent (TCP 8765, mini-PC uniquement) » (entrée, autoriser, depuis
  192.168.1.91, tout programme) ne sert plus : rien n'écoute ni ne se connecte sur 8765, aucune autre règle ne
  concerne ce port ; le visage (Chrome vers le mini-PC, sortant) et la transcription (règle « ORION transcription »,
  8766) n'en dépendent pas. Retrait sans incidence, à faire en administrateur sur le Katana :
  `Remove-NetFirewallRule -DisplayName "JARVIS Agent (TCP 8765, mini-PC uniquement)"` (ou la désactiver d'abord :
  `Disable-NetFirewallRule`).
- **Pare-feu du PC** : même chose pour « JARVIS Agent HTTP » (port 8765).
