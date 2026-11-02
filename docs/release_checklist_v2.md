# Checklist des essais réels — ORION v2.0.0

À faire à la maison, sur l'installation de production (`/opt/orion`). Cocher chaque essai ; **B** = bloquant pour
la release (un échec bloque le tag), **I** = important (échec à corriger ou à documenter comme limite).

Journal en direct pendant les essais (sur le PC) :

    ssh user@192.168.1.91 "journalctl -u orion -u orion-call -f -o cat"

Chaque demande porte un identifiant `[r:xxxxxx.n]` ; la ligne `[mesure]` de la demande donne ses durées.

## Chaîne vocale

| # | Action | Attendu | Journal | Réussite | Échec |
|---|---|---|---|---|---|
| V1 B | Dire « Hey Orion » à distance normale, 5 fois | « Oui, monsieur ? » à chaque fois | `[wake] Wake word détecté` | 5 sur 5 (4 sur 5 si le second essai réussit) | moins de 4, ou pas de réponse |
| V2 I | 10 minutes de jeu, Discord ou vidéo à voix haute, sans parler à ORION | aucun réveil | `[wake_rejected]` possibles, aucun `[wake] Wake word détecté` | 0 réveil retenu | une conversation ouverte sans « Hey Orion » |
| V3 B | Après un réveil involontaire : « Fausse alerte » puis, une autre fois, « Faux réveil, rendors-toi » | retour en veille, aucune action | `[routing] predefined:false_wake`, `[sleep]` | aucun outil exécuté | un rappel ou une autre action créés |
| V4 B | « Éteins le PC portable », puis « Oui, éteins mon PC portable » | extinction confirmée, une seule question | `[routing] tool:quick`, `"confirmation": "accepted"` | une seule question, extinction | question reposée, ou abandon |
| V5 I | « Allume la lumière de la chambre », « Mets le son à 30 % », « Arrête Spotify » | action immédiate | `[mesure] chemin=deterministe` | réponse en moins de 2 s (`total`) | `chemin=llm` pour ces phrases |
| V6 B | Une demande quelconque, Katana allumé | réponse correcte | `STT … [gpu]`, `[mesure] … source=gpu` | `source=gpu` | `source=local` alors que le Katana est allumé |
| V7 B | Arrêter le service de transcription du Katana (`Stop-ScheduledTask "ORION Transcription"`), puis une demande | réponse un peu plus lente, correcte | `Transcription sur le GPU … indisponible : Whisper local`, `[local]` | réponse correcte en moins de 6 s | pas de réponse, erreur |
| V8 I | Relancer la tâche, attendre 2 min, puis une demande | retour au GPU | `de nouveau disponible`, `[gpu]` | `source=gpu` dans les 60 s | reste en `local` |
| V9 I | 10 demandes variées | réponses parlées, voix ORION | `[mesure] total=…` | médiane `total` ≤ 3 s, p90 ≤ 6 s | au-delà |
| V10 I | Au téléphone (appel Linphone) puis aux enceintes : « Qui es-tu ? », « Merci » (réponses finies par « ORION. », « disposition. »), une fois avec `[tts] soften_endings = true`, une fois à `false` (config.local.toml, redémarrage d'ORION) | le « on » final entendu en entier avec `true` | aucune ligne (écoute) | « on » complet, aucun clic, voix inchangée | « on » toujours coupé (garder la mesure et revoir docs/audit_fiabilisation_v2.md, F3 et F4) |

## Actions et sécurité

| # | Action | Attendu | Journal | Réussite | Échec |
|---|---|---|---|---|---|
| A1 B | « Rappelle-moi dans 2 minutes de sortir le linge » | rappel dit 2 min plus tard | `create_reminder`, puis l'annonce | rappel à l'heure | absent, ou heure fausse |
| A2 B | « Fous alert. », « Note demain réunion » | aucun rappel | `Appel d'outil écarté` ou `Action écartée` | aucun `create_reminder` exécuté | rappel créé |
| A3 B | « Hier le son était à 50, c'était trop fort » | commentaire, aucune action | `Action écartée … constat au passé` ou aucune ligne `[tool]` | volume inchangé | volume changé |
| A4 B | « Et si je voulais éteindre la lumière, comment je ferais ? » | explication, lumière inchangée | `Action écartée … hypothèse` | lumière inchangée | lumière éteinte |
| A5 B | « Verrouille le PC », puis « Oui, ouvre Discord » | rien n'est verrouillé ; « oui » ne vaut pas | `"confirmation": "abandoned"` | PC non verrouillé | PC verrouillé |
| A6 I | « Lance la routine X » (routine active) | routine exécutée | `run_routine` succès, étapes | toutes les étapes | erreur |
| A7 B | « Désactive la routine X », puis « Lance la routine X » | refus : « La routine … est désactivée… » | `routine_disabled` | rien n'est exécuté | routine lancée |
| A8 I | « Réactive la routine X » | réactivée | `enable_routine` | routine active (ORION Control) | erreur |
| A9 B | PC fixe éteint : « Ouvre Discord sur le PC » | échec dit clairement | `"success": false`, `device_unreachable` | ORION dit que le PC ne répond pas | « c'est fait » |

## Intégrations

| # | Action | Attendu | Journal | Réussite | Échec |
|---|---|---|---|---|---|
| I1 B | Message Linphone « Quelle heure est-il ? » | réponse écrite | `[message]`, `[message_reply]`, `[r:…]` | réponse dans le fil | aucune réponse |
| I2 B | Appeler ORION depuis Linphone, poser une question, raccrocher | dialogue audio | `orion-call` : appel décroché, terminé | conversation audible dans les deux sens | coupure, silence |
| I3 I | « Appelle-moi dans 2 minutes pour me dire que tu fais un test » (confirmer) | appel 2 min plus tard | règle créée, appel | appel reçu | rien |
| I4 I | « Qu'est-ce que j'ai de prévu demain ? », « Ajoute dans mon agenda « essai » du 1er au 3 » | agenda lu, période ajoutée | `list_events`, `add_period` | réponses exactes | erreur |
| I5 I | « Retiens que ma couleur préférée est le vert », puis après redémarrage d'ORION « Quelle est ma couleur préférée ? » | souvenir retrouvé | `remember`, puis `recall` | « vert » | oublié |
| I6 I | Allumer, éteindre, régler une lumière ; une scène | lumières conformes | `light_*` succès | état réel conforme | écart |
| I7 B | « Ouvre Spotify sur le PC », puis « Ferme Spotify » (confirmer) | ouvert, puis fermé | `open_application`, `close_application` | état réel conforme | « ouvert » sans fenêtre |
| I8 I | « Ouvre Wallpaper Engine » ; « Ouvre regedit » | le premier s'ouvre ; le second est refusé | `application_blocked` pour regedit | conforme | regedit ouvert |
| I9 I | Fond d'écran : clic sur une lumière, fiche météo | lumière basculée, fiche ouverte | `Fond d'écran : light_toggle` | conforme | rien |
| I10 I | Une demande vocale, puis chercher son identifiant | toutes ses lignes portent le même `[r:…]` ; l'agent du PC aussi (`data\winagent.log`) | `[r:…]` | même identifiant partout, aucun texte privé dans `[mesure]` | identifiants manquants |
| I11 B | « Appelle-moi dans 2 minutes pour me rappeler le test » (confirmer), décrocher, dire « C'est noté » | « C'est noté, monsieur. Je ne vous rappellerai pas à ce sujet. », puis ORION raccroche | `appel acquitté` (et non `décroché sans accusé`) | aucun résumé envoyé par message | phrase envoyée au LLM, résumé envoyé |

## Redémarrages et résilience

| # | Action | Attendu | Journal | Réussite | Échec |
|---|---|---|---|---|---|
| R1 B | Redémarrer le mini-PC (`sudo reboot`) | services relancés seuls | `systemctl is-active orion orion-call orion-librespot ollama` | 4 × `active`, ORION répond à « Hey Orion » en moins de 3 min | service inactif |
| R2 B | Redémarrer le Katana, **sans ouvrir de session** | transcription GPU et LLM disponibles | `server.log` (Katana), `[gpu]` | `source=gpu` dans les 5 min | `local` persistant |
| R3 I | Ouvrir la session du Katana | agent du Katana relancé | `Get-NetTCPConnection -LocalPort 8765` | agent en écoute | agent absent |
| R4 B | Redémarrer le PC fixe, ouvrir la session | agent relancé (« ORION Agent »), micro reconnecté | `Micro distant connecté` | ORION entend à nouveau | micro absent |
| R5 I | Couper le Wi-Fi du Katana 2 min, une demande, rétablir | repli local puis retour GPU et LLM | `LLM principal indisponible`, `de nouveau joignable` | réponses pendant et après | blocage |
| R6 I | `python -m orion --health` après tous les essais | état sain | sortie de la commande | aucun `ERREUR` | erreur |

Après les essais : relever les mesures (`docs/release_readiness_v2.md`, §4) et cocher le résultat de chaque ligne.
