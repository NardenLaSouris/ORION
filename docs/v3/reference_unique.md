# ORION V3, référence unique (4 novembre 2026)

Décision du propriétaire : **une branche (`v3`), un Core actif (V3), un agent Windows (V3), une installation de
référence par machine**. La V2 devient la référence historique et le filet de sécurité, pas un environnement
parallèle.

## 1. Audit de départ (4 novembre 2026, 10 h 30)

| Élément | Constat |
|---|---|
| Git | `v3` (`6dd7411`) développée à part ; `V2` (`15621a0`) faisait tourner ORION ; copies de travail : `jarvis` (V2), `orion-v3` (v3), `orion-v3-agent` (commit figé `5d9c13c`), `orion-fragfactory` (`feature/fragfactory`, déjà intégrée à `v3`), `jarvis/.worktrees/avp` (recherche) |
| Mini-PC | `orion` (Core V2, `/opt/orion`, `15621a0`) : tout l'assistant (voix, outils du PC, lumières, mails, présence, appels, Spotify, visage, fond d'écran) ; `orion-v3` (`/opt/orion-v3`) : sans voix (`audio = "none"`), outils du PC désactivés, ressources bridées (3 Go, priorité basse) ; `orion-call`, `orion-librespot` (annexes V2) |
| PC fixe | deux agents : V2 (port 8765, `jarvis`, venv de `jarvis`) et V3 (port 8875, copie `orion-v3-agent`, venv de `jarvis`) ; deux ORION Control (V2 au démarrage, V3 lancé à la main) ; démarrage : « ORION Agent », « ORION Agent V3 », « ORION Control » ; secrets de l'agent V3 dans `%LOCALAPPDATA%`, en partie dans le stockage isolé de l'application Claude |
| Katana | Ollama (tâche « JARVIS Ollama »), transcription (tâche « ORION Transcription », un seul jeton : celui de la V2), visage (Chrome en kiosque sur le visage V2, port 8765), agent V2 (« JARVIS Agent.lnk ») |
| Réseau | la V3 n'a pas le droit de joindre la transcription du Katana (règle nftables d'isolation) |
| Droits | sur le mini-PC, `sudo` sans mot de passe seulement pour redémarrer ou arrêter les services ; installer des règles réseau ou désactiver un service demande votre mot de passe |

## 2. Organisation cible

| Machine | Référence unique |
|---|---|
| Git | `v3` : seule branche de développement ; `V2` gelée (maintenance explicite seulement) |
| Mini-PC | `/opt/orion-v3`, service `orion-v3`, son `.env`, ses bases ; déploiement `deploy/v3_deploy.sh` (gardes, sauvegarde vérifiée, avance rapide seulement, contrôle de santé, retour automatique) |
| PC fixe | `C:\Users\theju\.orion` : `releases\<commit>` (code), `current` / `previous`, `venv` (son propre Python), `agent.toml` (+ `agent.local.toml`), `data` (`.env`, journaux, liaison Discord), lanceur stable `pc_start.pyw` ; un raccourci « ORION Agent » et un « ORION Control » (démarrage de session et menu Démarrer) |
| Katana | services IA seulement : Ollama, transcription (GPU, jeton de la V3), visage en kiosque sur le visage V3 (port 8865) ; plus d'agent ORION |

Pourquoi `C:\Users\theju\.orion` : hors d'AppData (l'application Claude, installée en paquet isolé, y détourne les
écritures de ses programmes ; c'est ce qui avait caché le `.env` de l'agent V3), et hors de `C:\Users\theju\ORION`
(bac à sable des outils de fichiers d'ORION : ses secrets y seraient lisibles à la voix).

## 3. Mise à jour du PC (`deploy\pc_install.ps1`)

1. commit V3 visé (par défaut `origin/v3`), **refusé s'il n'est pas sur `origin/v3`** ;
2. modifications affichées (agent, Control, outils, configuration) ;
3. code copié dans `releases\<commit>`, dépendances installées si `requirements-pc.txt` a changé ;
4. **contrôle des imports avant tout arrêt** (une version cassée n'arrête rien ; constaté : `httpx` manquant) ;
5. arrêt propre (fichier d'arrêt du superviseur), bascule de `current`, redémarrage par l'Explorateur ;
6. vérification : l'agent écoute, puis le Core V3 le voit « en ligne » ;
7. échec : retour automatique à `previous`.

`-Rollback` revient à la version précédente, `-Stop` arrête, `-Autostart` installe les raccourcis. Le verrou d'instance
unique et la relance après plantage (superviseur) sont conservés. Journal : `data\deploiements.log`.

## 4. Journal de la migration (4 novembre 2026)

| Heure | Opération | Résultat |
|---|---|---|
| 10:45 | Installation de référence du PC (`cb52722`) : agent V3 depuis `C:\Users\theju\.orion`, secrets et liaison Discord repris | agent vérifié par le Core V3 ; Discord « lié » ; Frag Factory connecté |
| 10:50 | Core V3 : un seul jeton d'agent (`ORION_V3_AGENT_TOKEN`) pour l'audio, les outils, la musique et le fond d'écran (`0d43e31`, déployé) | 2475 tests réussis sur le mini-PC |
| 10:53 | Retrait de l'agent V2 et de l'ancien ORION Control (`pc_install.ps1 -RetireV2`) | un seul agent (8875), plus rien sur 8765 ; raccourcis V2 et V3 anciens mis de côté dans `.orion\raccourcis-retires` |
| 10:55 | Core V3 actif : voix par l'agent V3, réveil « Hey Orion », voix fr_FR-orion-medium, outils du PC, visage ; ancienne configuration gardée (`config.local.toml.avant-reference-20261104-095508`) ; transformation : `deploy/v3_reference_config.py` | « En veille — dites « Hey Orion » » ; micro du PC diffusé vers le Core V3 |
| 10:57 | Visage du Katana sur le visage V3 (`open_face.ps1`, ancienne version `.v2-20261104`) | Chrome en kiosque sur `http://192.168.1.91:8865/` |
| 11:09 | `v3_reference_root.sh` lancé par vous : règles réseau installées, ressources du Core principal, Core V2 arrêté et désactivé | V2 `inactive` / `disabled`, `/opt/orion` intact |
| 11:12 | Transcription du Katana : jeton propre à la V3 (sur votre accord ; ancien gardé : `token.txt.v2-20261104`) | « STT : GPU distant http://192.168.1.73:8766 » ; large-v3 sur CUDA, 401 sans jeton |
| 11:25 | Agent V2 du Katana retiré (sur votre accord ; `deploy/katana_retrait_agent_v2.ps1`) : processus arrêtés, raccourci « JARVIS Agent.lnk » retiré ; sauvegarde `C:\Users\theju\orion-retrait-agent-v2` (configuration, jeton, raccourci) ; dossier `C:\Users\theju\jarvis` intact | plus rien sur 8765 ; transcription seule (une instance, tâche « ORION Transcription »), jeton V3 accepté, 401 sans jeton ; client du Core V3 : transcription réelle passée par le GPU (`last_source = gpu`) |
| 11:00 | Copies de travail `orion-v3-agent` et `orion-fragfactory` retirées (propres, intégrées à `v3`, inutilisées ; branches gardées) | — |

Refusé par le contrôle de sécurité automatique de la session (non contourné) : rotation du jeton de transcription du
Katana, arrêt du Core V2. Ces deux opérations attendent votre accord (§ 6).

## 5. Retours arrière

- **PC** : `deploy\pc_install.ps1 -Rollback` (version précédente) ; l'ancien agent V2 : ses raccourcis sont dans
  `C:\Users\theju\.orion\raccourcis-retires`, son code dans `C:\Users\theju\jarvis` (branche `V2`).
- **Core** : `sudo systemctl stop orion-v3 && sudo systemctl enable --now orion`, puis remettre
  `config.local.toml.avant-reference-…` si besoin ; `/opt/orion`, ses données, ses secrets et ses sauvegardes ne sont
  pas touchés.
- **Katana** : `open_face.ps1.v2-20261104` (visage V2) ; `token.txt.v2-20261104` (ancien jeton de transcription) ; agent V2 : `powershell -ExecutionPolicy Bypass -File C:\Users\theju\orion-retrait-agent-v2\katana_retrait_agent_v2.ps1 -Retour`.

## 6. Ce qui vous attend

1. ~~**Sur le mini-PC**~~ (fait le 4 novembre à 11:09) : `sudo bash /home/user/orion-v3/deploy/v3_reference_root.sh`. Il installe les règles réseau qui
   permettent à la V3 de joindre la transcription du Katana, donne à la V3 les ressources du Core principal, arrête et
   désactive le Core V2 (conservé).
2. ~~**Transcription du Katana pour la V3**~~ (faite à 11:12) : dites-moi « autorise la rotation du jeton de transcription ». Je donnerai
   au service du Katana un jeton propre à la V3 (l'ancien est gardé à côté). En attendant, la V3 transcrit avec son
   Whisper local (plus lent).
3. **Pare-feu du Katana** (administrateur) : la règle « JARVIS Agent (TCP 8765, mini-PC uniquement) » ne sert plus (agent V2 retiré le 4 novembre).
4. **Fond d'écran** : dans Wallpaper Engine, écran principal, choisissez `edge://http://192.168.1.91:8867/` (déjà dans
   votre liste) à la place de `…:8767`.
5. **Pare-feu Windows** (administrateur) : la règle « JARVIS Agent HTTP » (port 8765) ne sert plus.
6. **Fonctions encore propres à la V2**, qui demandent vos accès (jamais repris des secrets de la V2) : mails (mot de
   passe d'application Gmail), lumières (clés locales des ampoules), Spotify, appels (passerelle `orion-call`),
   présence ; vos rappels, routines et souvenirs de la V2 attendent la vraie clé de données (F4) avant toute reprise.
7. **Frag Factory réel** : `deploy\pc_fragfactory_reel.ps1` montre ce qu'il ferait ; `-Autoriser` sauvegarde et
   vérifie votre base, puis relie votre vraie instance.
8. **Redémarrage du PC** : `deploy\pc_controle_demarrage.ps1` avant (conforme le 4 novembre à 11:35) puis après ; il vérifie un seul superviseur, un seul agent V3, un seul ORION Control, les raccourcis, les ports, et l'agent vu par le Core. Frag Factory (instance d'essai) n'a pas de démarrage automatique : `deploy\pc_fragfactory_essai.ps1` après le redémarrage.
9. **Fonctions de la V2 à récupérer** : `docs/v3/recuperation_v2.md`.
