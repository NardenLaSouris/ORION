# ORION DISCORD : votre Discord Desktop piloté par ORION

Module V3. Code : `orion/discord/` (Core), `orion/winagent/discord_rpc.py` et `discord_actions.py` (agent Windows V3).
ORION agit sur **votre application Discord Desktop**, avec votre compte, sans bot.

## 1. Audit des interfaces (3 novembre 2026)

| Interface | Ce qu'elle permet | Retenue |
|---|---|---|
| **RPC local (IPC)** : tube `\\?\pipe\discord-ipc-N` ouvert par Discord Desktop, documenté (developers/docs/topics/rpc) | `SELECT_VOICE_CHANNEL` (rejoindre, changer, quitter), `GET_SELECTED_VOICE_CHANNEL` (salon, participants), `GET/SET_VOICE_SETTINGS` (`mute`, `deaf` explicites), `GET_GUILDS`, `GET_CHANNELS`, événements `VOICE_CHANNEL_SELECT`, `VOICE_SETTINGS_UPDATE`, `VOICE_STATE_CREATE/UPDATE/DELETE`, `VOICE_CONNECTION_STATUS` | **oui** |
| RPC par WebSocket | les mêmes commandes, mais déprécié et réservé aux anciens participants de la bêta | non |
| Discord Social SDK, Game SDK | intégration dans un jeu (activités, salons du jeu) ; pas de contrôle du client existant | non |
| API REST et bots | un bot est un autre compte : il ne peut pas couper votre micro ni vous déplacer | non (demandé : pas de bot) |
| Raccourcis clavier | bascules seulement (risque d'inversion), aucun état lisible, ne marchent que si vous les avez définis comme globaux | non |
| Reconnaissance visuelle | fragile, et une interface fiable existe | non |
| Self-bot, jeton de compte extrait | interdits par Discord et par la demande | jamais |

Vérifié sur le PC : Discord Desktop ouvre bien `discord-ipc-0`.

**Restrictions de Discord, vérifiées dans sa documentation :**
- les portées `rpc`, `rpc.voice.read` et `rpc.voice.write` exigent une application déclarée dans le Developer Portal ;
- une application non approuvée ne sert qu'à **50 testeurs au plus**. Vous en êtes le propriétaire, donc testeur
  d'office ;
- une application non approuvée est aussi limitée à **10 serveurs et 10 salons**. Selon la documentation, au-delà,
  `GET_GUILDS` et `GET_CHANNELS` peuvent ne pas tout rendre. Rejoindre par le nom ne marche alors que pour les salons
  rendus ;
- **une seule application à la fois peut modifier les réglages vocaux**. Si une autre application les tient déjà (un
  Stream Deck, par exemple), le micro et la sourdine peuvent être refusés à ORION ;
- **les réglages posés par une application sont annulés quand elle se déconnecte**, et réappliqués quand elle
  revient. Voir le § 4.

## 2. Architecture

```
« ORION, mute-moi »
  -> Core : compréhension sans LLM (orion/discord/intent.py), sinon choix d'outil du LLM
  -> moteur central des permissions (CONTROL_DEVICE ; lecture : READ), inscrit au registre d'actions
  -> module DISCORD (orion/discord/engine.py) : salon choisi d'après le nom, règles de sûreté, résultat au registre
  -> HTTP (réseau local, jeton propre à la V3) -> agent Windows V3 (port 8875)
  -> RPC local officiel -> Discord Desktop
ORION Control, vue Discord -> /api/modules/discord/... -> même module, même moteur (principal « admin »)
```

- **Le Core ne dépend ni de Windows ni de Discord.** Il ne connaît que l'agent et ses actions `discord_*`.
- **Agent Windows V3, distinct de celui de la production.** Il tourne sur le port 8875, sous le compte d'usage du PC,
  avec `audio = false` et `pc_tools = false`, et son propre jeton (`ORION_V3_AGENT_TOKEN`). Il n'accepte que
  192.168.1.91. L'agent de production (port 8765, `C:\Users\theju\jarvis`) n'est pas touché.
- **Agent hors ligne.** Le module reste actif et le déclare : agent hors ligne depuis quand, et ce qui ne marche plus.
  Toute demande répond « Je n'ai pas accès à Discord : l'agent du PC ne répond pas. ». L'état revient seul au retour
  de l'agent, car le Core le sonde toutes les 5 s. Un redémarrage de l'agent est reconnu, sa numérotation des
  événements repartant de zéro.
- **Discord fermé, redémarré ou mis à jour.** L'agent se reconnecte seul, avec un délai qui grandit jusqu'à 30 s.

## 3. Fonctions (V1)

| Demande | Outil | Commande Discord |
|---|---|---|
| « rejoins le vocal Général », « … sur le serveur Amis » | `discord_join` | `SELECT_VOICE_CHANNEL` |
| « change de salon » | `discord_join` (ORION demande lequel) | — |
| « quitte Discord », « quitte le vocal », « déconnecte-moi du vocal » | `discord_leave` | `SELECT_VOICE_CHANNEL` vide |
| « mute-moi », « coupe mon micro » | `discord_mute` | `SET_VOICE_SETTINGS {mute: true}` |
| « démute-moi », « réactive mon micro » | `discord_unmute` | `{mute: false, deaf: false}` |
| « mets-moi en sourdine », « deafen » | `discord_deafen` | `{deaf: true}` |
| « enlève-moi la sourdine », « réactive le son » (si vous y êtes en sourdine) | `discord_undeafen` | `{deaf: false, mute: true}` |
| « qui est dans le vocal ? » | `discord_participants` | état gardé par l'agent |
| « suis-je mute ? », « où suis-je sur Discord ? » | `discord_status` | état gardé par l'agent |

- **Mute et deafen ne sont jamais confondus.** On reprend le vocabulaire de Discord en français :
  - couper le micro, c'est « mute » ;
  - mettre en sourdine, c'est « deafen » : le casque est coupé, et Discord coupe aussi le micro.

  Pour ne pas viser le son du PC par erreur, ORION ne vise Discord que dans ces cas :
  - « mets en sourdine » seul vise Discord seulement avec « moi » ou un mot de Discord (discord, vocal, micro) ;
  - « réactive le son » vise Discord seulement si vous y êtes en sourdine.

  Sinon, ces phrases restent pour le son du PC.
- **Jamais de bascule.** Chaque commande est une valeur explicite. Une question ne change rien : elle donne l'état.
  Une négation (« ne me démute pas ») n'est jamais traitée comme une commande.
- **Rejoindre par le nom.** Les accents, la casse et les emojis sont ignorés, et un nom partiel suffit (« jeux » pour
  « Jeux vidéo »). Si deux salons portent le même nom, celui du serveur actuel l'emporte ; sinon, ORION demande
  lequel, jamais au hasard.
- **Événements.** L'agent suit les changements de salon, les arrivées et départs des participants, les changements
  de micro et de sourdine, et la fermeture ou le retour de Discord. Ils sont affichés dans ORION Control et ne sont
  jamais annoncés à voix haute.

## 4. Sûreté

- **Le démute vient toujours de vous.** `unmute` et `undeafen` sont refusés à toute origine autre que la conversation
  ou ORION Control. Le refus est inscrit au registre. Le LLM ne peut proposer `discord_unmute` que si la demande
  contient un mot de démute.
- **Fin de sourdine : le micro reste coupé** (réglage `undeafen_keeps_mute`). Sinon, Discord rouvrirait le micro tel
  qu'il était avant la sourdine. ORION le dit : « Votre micro reste coupé : dites « démute-moi » pour le rouvrir. »
- **Aucun changement de salon sans demande.** Rejoindre un salon nommé quitte le salon actuel, parce que c'est ce que
  vous avez demandé. ORION ne change jamais de salon de lui-même.
- **Réglages annulés à la déconnexion (§ 1).** Si l'agent s'arrête alors qu'ORION vous a coupé le micro, Discord le
  rouvre. Pour qu'un micro ne se rouvre jamais en silence :
  - l'agent garde sur disque le dernier état observé ;
  - à la reconnexion, si Discord montre un micro ouvert alors qu'il était coupé, l'agent le recoupe aussitôt ;
  - cette correction est inscrite au registre (`discord.safety_remute`) et affichée dans ORION Control.

  L'agent ne fait jamais l'inverse. **Limite :** pendant la coupure de l'agent elle-même, le micro peut être ouvert.
  Arrêter l'agent pendant un appel où vous êtes coupé n'est donc pas sans effet.
- **Registre d'actions.** Pour chaque action, il garde deux lignes, liées par l'identifiant de la demande :
  - la décision du moteur central (`tool:discord_mute` ou `discord:mute`) ;
  - le résultat (`discord.mute` : exécutée, ou refusée avec sa raison).

  Les noms des participants n'y figurent jamais. Ils ne sont gardés qu'en mémoire.
- **Aucun audio.** L'agent V3 n'ouvre ni micro ni haut-parleur (`audio = false`) et ne reçoit aucun flux de Discord.
- **Secrets.**
  - Le secret de l'application Discord reste dans `%LOCALAPPDATA%\ORION V3 Agent\.env`, sur le PC. Il ne va jamais au
    Core ni dans le dépôt.
  - Le jeton OAuth2 est chiffré par Windows (DPAPI, lié à votre session) dans le même dossier.
  - Un jeton refusé est effacé, et il faut alors relier ORION.
- **Autonomie.** Le sondage d'état est une lecture (`discord:poll` : `READ`). Une action venant d'une initiative de
  module est refusée pendant la suspension, comme le reste.

## 5. Ce que vous devez faire (une fois)

1. **Créer l'application Discord.** Sur https://discord.com/developers/applications : *New Application*, nommée par
   exemple « ORION ». Aucun bot n'est à ajouter.
2. **Déclarer l'adresse de retour.** *OAuth2*, puis *Redirects* : ajoutez exactement `http://localhost` et
   enregistrez.
3. **Récupérer les identifiants.** Sur la page *OAuth2*, copiez le *Client ID* et le *Client Secret* (*Reset Secret*
   s'il n'est pas affiché).
4. **Les écrire dans le fichier de l'agent.** Ouvrez `%LOCALAPPDATA%\ORION V3 Agent\.env` sur le PC et remplissez
   ces deux lignes :
   ```
   DISCORD_CLIENT_ID=<Client ID>
   DISCORD_CLIENT_SECRET=<Client Secret>
   ```
5. **Relancer l'agent V3.** `powershell -File C:\Users\theju\orion-v3\deploy\v3_agent.ps1`
6. **Relier ORION.** Dans ORION Control V3, vue Discord, cliquez sur *Relier ORION à Discord*. Puis, **dans Discord**,
   acceptez la fenêtre « ORION souhaite… » (*Autoriser*).

Démarrage avec Windows (facultatif) : placez dans le dossier Démarrage (`shell:startup`) un raccourci vers
`powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Users\theju\orion-v3\deploy\v3_agent.ps1`.

## 6. Limites

- Discord Desktop doit être ouvert sur le PC. Le navigateur et le mobile n'ont pas de RPC local.
- Les limites de l'application non approuvée s'appliquent (§ 1) : 10 serveurs et 10 salons, une seule application à
  la fois sur les réglages vocaux.
- Les participants ne sont connus que dans votre salon actuel.
- « Quitte Discord » quitte le salon vocal et ne ferme pas l'application.

## 7. Suite prévue (non développée)

Le module est déjà prêt pour la suite :
- plusieurs serveurs : l'annuaire des serveurs et salons existe déjà ;
- profils audio : `SET_VOICE_SETTINGS` accepte aussi le volume, l'entrée et la sortie ;
- commandes selon le salon : l'état connaît le salon et le serveur ;
- interactions avec les participants : l'agent reçoit déjà leurs événements ;
- liens avec les autres modules ORION.

## 8. Tests

`tests/test_discord.py` : 38 tests, sur un faux Discord qui parle le même protocole de trames.
- **Autorisation** : le jeton est chiffré sur disque, et un jeton révoqué est effacé.
- **Réglages vocaux explicites.** Ils sont refusés sans valeur.
- **Rejoindre, changer, quitter**, avec le refus de changer de salon sans demande, et les abonnements par salon.
- **Participants.** Ils arrivent et repartent.
- **Discord fermé puis rouvert** : reconnexion automatique.
- **Micro rouvert pendant une coupure de l'agent** : il est recoupé, jamais l'inverse.
- **Agent non configuré.** Il ne parle jamais à Discord.
- **Configuration de l'agent V3** : son propre jeton, et le secret refusé dans le TOML.
- **Routes audio** refusées quand l'audio est désactivé.
- **Module du Core**, testé sur :
  - les noms avec accents et emojis, les doublons, le serveur actuel ;
  - la fin de sourdine qui garde le micro coupé ;
  - le démute refusé à une initiative ;
  - ORION Control qui passe par le moteur central ;
  - l'agent hors ligne puis de retour, et le redémarrage de l'agent ;
  - le registre sans nom de participant.
- **Compréhension** :
  - les phrases de la demande ;
  - mute et deafen jamais confondus ;
  - « réactive le son » selon que vous êtes en sourdine dans Discord ou non ;
  - les questions et les négations.
- **Construction** : jeton propre exigé, et sondage classé `READ`.
