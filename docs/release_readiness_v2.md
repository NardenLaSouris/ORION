# Préparation de la release ORION v2.0.0

Audit du 16 octobre 2026, après la fusion de la PR #1 (V2 -> `main`). Rien n'a été publié, tagué ni déployé.

## 1. État Git

| Élément | Valeur |
|---|---|
| Dépôt | `NardenLaSouris/ORION` (remote `origin` des copies PC, mini-PC `/opt/orion`, `~/orion-dev`) |
| `main` | `02d4172` « Merge pull request #1 from NardenLaSouris/V2 » (parents : `de568df` ancien `main`, `2ffb61e` V2) |
| `V2` | `2ffb61e` ; **arborescence identique à `main`** (diff vide) |
| Production (mini-PC, `/opt/orion`) | branche `V2`, `2ffb61e` : même code que `main` |
| Tags | `v2-avant-abliterated` (point de sauvegarde) ; aucun tag de version |
| Modifications locales | aucune (copie de développement propre) |

## 2. Tests sur le commit `main` (`02d4172`)

Extraction séparée (`git worktree`), environnement Python de production :

- **1899 réussis, 0 échec, 5 ignorés** (suite complète).
- Les 3 ignorés de la couche audio des appels (`pylibsrtp`, `aiortc`, absents de l'environnement du Core) : **25 réussis**
  avec l'environnement de la passerelle (`/opt/orion-call/venv`, rien n'y a été installé).
- Non exécutés : `tests/test_control.py` (application Windows ORION Control, à vérifier sur le PC) et
  `tests/test_pipeline_e2e.py` (vrais moteurs, `ORION_E2E=1` ; non lancé pour ne pas charger le mini-PC en service).
- Couvert entre autres : package `orion` et renvois `jarvis` (lancement réel de `python -m jarvis --help` et
  `-m jarvis.winagent --help`), lecture des anciens secrets `JARVIS_*`, corrélation `[r:…]` et en-tête
  `X-Orion-Request`, ligne `[mesure]` et `scripts/latency_report.py`, garde-fous conversationnels
  (`tests/test_conversation_guards.py`), constats au passé, routines désactivées, confirmations par canal et par redite,
  mode dégradé (LLM injoignable, transcription GPU injoignable ou bloquée).

Ces tests sont unitaires et d'intégration avec doubles : **aucun ne prouve le fonctionnement vocal réel**.

## 3. Constats

| # | Constat | Classe |
|---|---|---|
| 1 | Essais réels non faits (voix, appels, redémarrages des machines, coupure du Katana) : `docs/release_checklist_v2.md` | **Bloquant** jusqu'à leur réussite |
| 2 | `orion/__init__.py` déclare `__version__ = "0.1.0"` (non utilisé par le code, mais incohérent avec un tag `v2.0.0`) | Important : à passer à `2.0.0` avant le tag |
| 3 | La production suit la branche `V2`, pas `main` (même code aujourd'hui) | Important : après la release, la faire suivre le tag `v2.0.0` |
| 4 | Aucune ligne `[mesure]` en production depuis le déploiement (aucune demande reçue) ; format vérifié par les tests et la commande de mesure exécutée sur le journal réel | Important : à observer pendant les essais |
| 5 | Démarrage autonome jamais vérifié par un vrai redémarrage : mini-PC (services), Katana (tâche « ORION Transcription » en S4U, agent) | Important : essais R1 à R3 |
| 6 | Lanceurs encore sous l'ancien nom : raccourci d'ORION Control (PC, `-m jarvis.control`), démarrage de l'agent du Katana (`-m jarvis.winagent`) ; fonctionnent par les renvois `jarvis` | Mineur, documenté |
| 7 | L'agent du PC fixe et celui du Katana exigent une session ouverte (micro, haut-parleurs, fenêtres) | Mineur : conception, documenté |
| 8 | `/opt/jarvis` (copie figée) toujours présent | Mineur : conservé pour le retour arrière, à supprimer après validation |
| 9 | Un clone neuf sans `python scripts/download_models.py` échoue aux tests du mot de réveil (deux modèles de base non versionnés) | Mineur : procédure d'installation |
| 10 | `searxng/config/settings.yml` réécrit par SearXNG sur chaque machine : ne jamais le modifier dans le dépôt (une mise à jour a été bloquée le 16 octobre) | Mineur, documenté |
| 11 | Les erreurs et délais dépassés ne sont pas comptés par `latency_report.py` (commandes `journalctl` ci-dessous) | Amélioration future |

Vérifié sans problème : aucun secret ni fichier sensible suivi par Git ; dépendances déclarées cohérentes avec les imports
(`requirements.txt`, `-call`, `-control`, `-policy`, `-neutts` ; `ctranslate2` vient avec `faster-whisper`) ; voix
d'ORION et modèle « Hey Orion » versionnés ; aucune erreur au journal de `orion` et `orion-call` depuis la migration ;
services `orion` (`-m orion`), `orion-call` (`-m orion.call.gateway`), `orion-librespot` actifs depuis `/opt/orion`.

## 4. Mesures sur sept jours

Sur le mini-PC, après les essais (le journal est gardé 30 jours) :

    cd /opt/orion
    journalctl -u orion --since "-7 days" -o cat | .venv/bin/python scripts/latency_report.py

Commande vérifiée sur le journal de production le 16 octobre 2026 (elle répond « Aucune ligne [mesure] » tant qu'ORION
n'a reçu aucune demande). Le rapport donne, par chemin (`deterministe`, `llm`) et par source de transcription
(`gpu`, `local`) : nombre, médiane, p90 et maximum de `stt`, `choix` (choix d'outil par le LLM), `outil`,
`llm_premier`, `llm_total`, `tts_premier`, `total` (fin de la phrase -> début de la réponse parlée).

Erreurs et délais dépassés, sur la même période :

    journalctl -u orion --since "-7 days" -o cat | grep -cE "Traceback|ERROR"
    journalctl -u orion --since "-7 days" -o cat | grep -c "LLM principal indisponible"
    journalctl -u orion --since "-7 days" -o cat | grep -c "Transcription sur le GPU .* indisponible"
    journalctl -u orion --since "-7 days" -o cat | grep -c "L'action a pris trop de temps"
    journalctl -u orion-call --since "-7 days" -o cat | grep -cE "Traceback|ERROR"

Repères (mesures d'octobre, à confirmer) : transcription GPU 0,4 à 0,8 s, locale environ 3 s ; choix d'outil par le
LLM 1 à 2,6 s ; synthèse de la première phrase 0,2 à 0,4 s ; total déterministe attendu sous 2 s.

À investiguer : `source=local` fréquent alors que le Katana est allumé (service de transcription arrêté ou saturé) ;
`total` p90 au-delà de 5 s ; part `chemin=llm` élevée pour des commandes simples (candidates à une commande
déterministe) ; toute ligne `Traceback` ; « LLM principal indisponible » répété (réseau, Katana en veille).

## 5. Versionnement proposé

- Convention : tags `vMAJEUR.MINEUR.CORRECTIF` annotés sur `main` ; les tags descriptifs existants
  (`v2-avant-abliterated`) restent des points de sauvegarde.
- Tant que les essais réels ne sont pas faits, `main` (`02d4172`) est une **version candidate**, pas une release.
- Commit candidat : celui qui passera `__version__` à `2.0.0` (constat 2), seul changement prévu avant le tag ;
  à défaut, `02d4172` (code testé ci-dessus, identique à la production).

### Notes de version proposées (v2.0.0)

ORION V2 : assistante vocale française distribuée (mini-PC, Katana, PC fixe).
- « Hey Orion », vérification Whisper, transcription `large-v3` sur GPU avec repli local, voix `fr_FR-orion-medium`.
- Outils sécurisés : permissions par profil et Cedar, confirmations par canal et par redite, journal d'audit.
- Maison et PC : lumières, Spotify, agenda, rappels, mails (confirmés), agents Windows, applications (liste noire).
- Appels et messages Linphone, règles d'appel (absence, veille d'un rendez-vous), mode Flex.
- Visage, ORION Control, fond d'écran interactif.
- Observabilité : identifiants de demande, mesures de latence et rapport.
- Identité ORION partout (`orion`, `/opt/orion`) ; compatibilité `jarvis` et `JARVIS_*` gardée.
- Limites connues : agents Windows liés à une session ouverte ; essais vocaux réalisés le JJ/MM ; voir
  `docs/release_checklist_v2.md`.

## 6. Procédure de release (à votre retour)

1. **État de `main`** (copie de développement) :

       cd ~/orion-dev && git fetch origin --tags && git status --short && git log --oneline -1 origin/main

2. **Essais réels** : `docs/release_checklist_v2.md`, tous les essais marqués « bloquant » réussis.
3. **Version** (sur V2, puis PR vers `main` comme pour #1) :

       sed -i 's/__version__ = "0.1.0"/__version__ = "2.0.0"/' orion/__init__.py
       git commit -am "Version 2.0.0"

   Push depuis le PC (le mini-PC n'a pas d'accès GitHub) :

       git fetch ssh://user@192.168.1.91/home/user/orion-dev V2:refs/remotes/mini/V2
       git push origin refs/remotes/mini/V2:refs/heads/V2

   puis PR V2 -> `main` et fusion sur GitHub.
4. **Derniers tests sur le commit de `main`** :

       cd ~/orion-dev && git fetch origin && git worktree add --detach ~/orion-release origin/main
       cd ~/orion-release && ln -s ~/orion-dev/models/whisper models/whisper
       ln -s ~/orion-dev/models/openwakeword/melspectrogram.onnx models/openwakeword/
       ln -s ~/orion-dev/models/openwakeword/embedding_model.onnx models/openwakeword/
       /opt/orion/.venv/bin/python -m pytest -q tests

5. **Rien d'imprévu** : `git diff origin/V2 origin/main --stat` vide ; `git status` propre.
6. **Tag** (après votre approbation), sur le commit vérifié :

       COMMIT=$(git rev-parse origin/main)
       git tag -a v2.0.0 "$COMMIT" -m "ORION v2.0.0"

   et depuis le PC : `git fetch ssh://user@192.168.1.91/home/user/orion-dev refs/tags/v2.0.0:refs/tags/v2.0.0`
   puis `git push origin v2.0.0`.
7. **Notes de version** : GitHub -> Releases -> Draft a new release -> tag `v2.0.0`, coller les notes ci-dessus.
8. **Vérification** : `git ls-remote --tags origin v2.0.0` doit afficher le commit de l'étape 6.
9. **Production sur la release** : `cd /opt/orion && git fetch origin --tags && git checkout v2.0.0`, puis
   `sudo -n systemctl restart orion` et `bash /opt/orion/deploy/appels.sh --code`.
10. **Limites connues** : reporter dans les notes celles du tableau du §3 encore ouvertes.

### Si des corrections sont nécessaires avant la release

Corriger sur V2 (copie de développement), tests complets, commit, déploiement en production (procédure habituelle),
nouvel essai des points concernés de la checklist, nouvelle PR V2 -> `main` ; le commit candidat devient le nouveau
commit de fusion. Ne jamais déplacer un tag déjà poussé : publier `v2.0.1` à la place.

## 7. Retour arrière

Les données (`/opt/orion/data` : mémoire, routines, rappels, agenda, journal des échanges), `config.local.toml` et
`.env` ne sont pas suivis par Git : changer de version du code ne les modifie pas.

**Avant toute mise à jour de la production**, sauvegarde datée :

    sudo tar czf /root/orion-avant-maj-$(date +%F).tar.gz /opt/orion/data /opt/orion/config.local.toml /opt/orion/.env

**Revenir à une version précédente du code** (même emplacement `/opt/orion`) :

    cd /opt/orion && git checkout 2ffb61e        # ou le tag précédent
    sudo -n systemctl restart orion && bash /opt/orion/deploy/appels.sh --code

**Revenir à l'installation d'avant la migration** (`/opt/jarvis`, code `2ffb61e`) : `docs/migration_orion.md`,
« Retour arrière (mini-PC) », avec la sauvegarde `/root/orion-migration-2026-10-16.tar.gz` (non vérifiée ici :
`/root` n'est lisible qu'avec sudo ; à contrôler par `sudo tar tzf /root/orion-migration-2026-10-16.tar.gz | head`).

**Restaurer les données** depuis une sauvegarde : arrêter `orion`, `sudo tar xzf <sauvegarde> -C /`, redémarrer.

Agents Windows : le code précédent se remet par `git checkout <commit>` dans le dossier du PC (puis relance de l'agent),
et par `scripts/update_remote_agent.sh` pour le Katana. Transcription du Katana : `server.py.avant-trace` dans
`C:\Users\theju\orion-stt`.
