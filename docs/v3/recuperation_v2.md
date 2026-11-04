# Fonctions quotidiennes de la V2 à récupérer dans la V3 (inventaire du 4 novembre 2026)

Depuis le 4 novembre, la V3 est le seul Core actif ; le Core V2 est arrêté et désactivé, gardé pour un retour
arrière (`docs/v3/reference_unique.md`). Le code de toutes ces fonctions est déjà dans la V3 (hérité de la V2) ; ce
qui manque, ce sont des **accès** (jamais repris des secrets de la V2), des **réglages réservés à root** et des
**données personnelles** (soumises à la clé de données F4). Rien n'a été copié depuis `/opt/orion`.

## 1. Déjà repris par la V3

| Fonction | État dans la V3 |
|---|---|
| Voix (« Hey Orion », voix fr_FR-orion-medium, micro et haut-parleurs du PC) | active, par l'agent V3 du PC |
| Transcription | GPU du Katana (large-v3, jeton de la V3), Whisper local en secours |
| LLM | Katana (`qwen3-abliterated:8b-v2`), secours local `qwen2.5:3b` |
| Outils du PC (applications, volume, médias, verrouillage, arrêt, fichiers) | actifs (`[tools.devices.pc]`, port 8875) |
| Allumage du PC par le réseau (`wake_pc`) | réglage repris (`mac`) |
| Visage | Katana, page du visage V3 (8865) |
| Fond d'écran, conversation | page V3 (8867) ; reste à la choisir dans Wallpaper Engine |
| Recherche Web, météo, minuteurs, NEWS, WATCH, SENTINEL, Discord, Frag Factory (instance d'essai) | actifs |

## 2. À récupérer

| Fonction | Dans la V2 | Ce qu'il faut pour la V3 | Qui | Données de la V2 concernées |
|---|---|---|---|---|
| **Mail** (tri, résumés, échéances) | Gmail IMAP/SMTP, `MAIL_PASSWORD` | un **nouveau mot de passe d'application** Gmail pour la V3 (`MAIL_PASSWORD` dans `/opt/orion-v3/.env`), puis `[mail]` comme en V2 ; au premier passage, les mails non lus deviennent « vus » sans annonce | vous (mot de passe), puis moi | `mail_seen.json`, `mail_actions.json` : non nécessaires (la V3 repart d'un état propre) |
| **Éclairage** (chambre, entrée) | Tuya local (`tinytuya`), `LIGHT_KEY_<PIÈCE>` | les **clés locales** des ampoules dans le `.env` de la V3 (ce sont celles des appareils : votre accord pour les reprendre, ou de nouvelles par réappairage), puis `[lights.rooms.*]` comme en V2 | vous (accord ou clés), puis moi | aucune |
| **Spotify** | lecteur `orion-librespot` (root) vers `/opt/orion/data/music.fifo`, jeton OAuth `spotify_token.json` de la V2 | (1) root : le lecteur écrit dans `/opt/orion-v3/data/music.fifo`, et la V3 peut le relancer (règle polkit pour `orion-v3`) ; (2) vous : une **nouvelle autorisation Spotify** (connexion OAuth) pour la V3 | vous (root, connexion), puis moi | `spotify_token.json` : jamais repris ; `spotify_catalog.json` : se reconstruit |
| **Appels** (passerelle Linphone, rappels qui vous appellent en votre absence) | service `orion-call` (root, `/etc/orion-call`), jeton `CALL_GATEWAY_TOKEN` | (1) root : un **nouveau jeton** de passerelle partagé avec la V3, la passerelle pointée vers la V3, et le port 8770 permis à la V3 (règles réseau) ; (2) moi : `[call]` comme en V2 ; (3) vous : un appel d'essai | vous (root), puis moi | `call_rules.json` (vos règles d'appel) : données personnelles |
| **Présence** (téléphone sur le Wi-Fi, capteurs) | pilote `network` (ping puis ARP de 192.168.1.124), capteurs déclarés | `[presence]` comme en V2 : aucune clé ; à vérifier sous le compte durci `orion-v3` (ping) | moi | `presence.json`, `house_journal.jsonl` : historique personnel |
| **Agenda** | `calendar.json`, adresse iCal privée `ORION_CALENDAR_ICS` | l'**adresse iCal** (secrète) dans le `.env` de la V3 | vous | `calendar.json` : données personnelles |
| **Routines, mémoire, apprentissages** | `routines.json`, `memory.json`, `learned.json` | reprise = import de données personnelles : après la **vraie clé de données F4**, avec votre accord | vous | ces trois fichiers |
| **Rappels** | `schedule.json` | rien : aucun rappel en attente dans la V2 (fichier vide) | — | — |
| **PC portable (Katana) comme appareil** | agent V2 du Katana | retiré le 4 novembre (votre accord) : le Katana ne sert plus que l'IA | — | — |

## 3. Ordre proposé

1. Présence (aucune clé : moi, dès que possible) ;
2. Mail (un mot de passe d'application) ;
3. Éclairage (votre accord sur les clés) ;
4. Agenda (l'adresse iCal) ;
5. Spotify et appels (une commande root préparée d'avance, puis vos essais) ;
6. Routines, mémoire, apprentissages, règles d'appel, historique de présence : avec la clé F4.
