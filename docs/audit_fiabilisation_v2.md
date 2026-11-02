# Audit et fiabilisation d'ORION V2 — 2 novembre 2026

Branche `fiabilisation-v2` (partie de V2 `2ffb61e`, identique à `main` `02d4172`). Rien n'est déployé : la
production (`/opt/orion`) reste sur `2ffb61e`. Chaque affirmation ci-dessous est marquée **[vérifié]** (commande ou
test exécuté), **[hypothèse]** ou **[à faire]** (intervention humaine ou essai physique).

## A. État initial

| Élément | Constat |
|---|---|
| Git | PC, `~/orion-dev` et `/opt/orion` sur `2ffb61e`, sans modification suivie ; `/opt/orion` garde ses copies `config.local.toml.avant-*` et `.env.avant-stt` (non suivies, conservées) ; seul tag `v2-avant-abliterated` [vérifié] |
| Services (mini-PC) | `orion`, `orion-call`, `orion-librespot`, `ollama` actifs ; comptes dédiés, `ProtectSystem=strict`, `NoNewPrivileges`, `Restart=on-failure` (librespot `always`) [vérifié] |
| Redémarrage du 24/10 à 08 h 04 | pas une panne : `apt-daily-upgrade` (mises à jour automatiques d'Ubuntu, systemd réexécuté) a relancé les services ; tout est reparti en 31 s [vérifié] |
| Machine | 22 jours sans redémarrage, 10,6 Go de RAM disponibles sur 15, disque à 11 % [vérifié] |
| Ports | 22, 8765 (visage), 8766 (API d'administration, IP autorisées), 8767 (fond d'écran, jeton de page), 8770 (passerelle d'appel) ouverts au réseau local ; Ollama 11434 et SearXNG 8080 en local seulement [vérifié] |
| Tests de départ | 1899 réussis, 5 ignorés (suite complète sur V2) [vérifié] |
| Analyse statique (ruff : pyflakes, bugbear, async) | aucun nom indéfini ni import inutile ; 13 remarques, dont une réelle (tâche asyncio sans référence) ; les 7 `except: pass` sont voulus et commentés [vérifié] |
| Katana, PC fixe | non sollicités : production en service, aucun accès nécessaire à l'audit |

Le journal de production du 16 octobre au 2 novembre a servi de base : 6 lignes `[mesure]`, des avertissements de
mail en grand nombre, quelques coupures de LLM, aucune erreur de transcription.

## B. Anomalies

| Id | Gravité | Description | Cause | Composants | Statut |
|---|---|---|---|---|---|
| F1 | **P1** | « C'est noté » dit pendant l'appel d'une règle n'était jamais compris : la phrase partait au LLM, la règle finissait « décroché sans accusé » et un résumé était envoyé par message (journal des 19 et 28 octobre) | `_listen_and_answer` reprenait l'appel décroché puis remettait `_in_call` à `None` avant la première phrase ; la condition de l'accusé ne pouvait jamais être vraie. Les tests couvraient `CallRules`, pas ce chemin de l'agent | `orion/agent.py` | **corrigé** (`cf5b8e9`) et 4 tests |
| F2 | P3 | « Ce n'était pas pour toi » pendant un appel aurait pu durcir le mot de réveil | même attribut que F1 (`_report_false_wake`) | `orion/agent.py` | **corrigé** avec F1 |
| F3 | **P2** | Au téléphone, le « on » final semble raccourci | **Cause isolée, dans la sortie du modèle Piper elle-même.** Alignements de la voix `fr_FR-orion-medium` : chaque phrase finit par « . » (23 ms) puis la fin de phrase (12 ms), et la voix est encore voisée quand l'audio s'arrête (60 à 75 dB dans les 10 dernières ms, dernier échantillon non nul) ; la nasale finale est en plus courte (23 + 23 ms dans « disposition. », contre 58 + 46 dans « bon. »). Les phrases finies par une consonne ou par « demain. » s'éteignent (37 à 45 dB). Le transport est hors de cause : le rééchantillonnage de la passerelle retient 0,7 ms (mesuré), l'envoi RTP ne perd rien et complète par du silence, et la conversation attend la fin de la voix (`drain`) avant de raccrocher. Ni « … », ni « ! », ni une pause en phonèmes ne règlent le problème de façon fiable | `orion/tts/piper.py` | **atténué** (`0822cef`) : une fin coupée en pleine voyelle est prolongée de 40 ms par son miroir, qui s'éteint sans discontinuité. Mesuré : « Attention. » passe de −23,7 à −42,2 dBFS et finit à 0 ; les phrases qui s'éteignent déjà sont rendues à l'identique. La voix ne change pas. **Gain perçu à confirmer à l'oreille** (essai V10) ; `[tts] soften_endings = false` rend la sortie brute |
| F4 | [hypothèse] | Au raccrochage par ORION, le tampon de gigue du téléphone (de 40 à 200 ms) pourrait encore contenir la fin de la dernière phrase | non mesurable sans le téléphone | passerelle d'appel | **non modifié** : à écouter (essai V10). Si c'est confirmé, attendre environ 0,3 s après `drain` avant le BYE |
| F5 | P2 | Relais du micro de l'appel WebRTC lancé par `asyncio.ensure_future` sans référence : la boucle ne garde qu'une référence faible, la tâche peut être ramassée en plein appel (silence) | tâche non conservée | `orion/call/media.py` | **corrigé** (`919e13d`) |
| F6 | P3 | Connexion IMAP perdue en pleine opération (délai de lecture, coupure TLS, « abort ») : traces complètes dans le journal (8 fois en deux semaines), et « L'action a échoué. » pour l'utilisateur au lieu de « La boîte mail ne répond pas. » | seule l'ouverture de session convertissait les erreurs réseau en `MailError` | `orion/mail/provider.py` | **corrigé** (`15d1476`) : 7 opérations et 27 tests |
| F7 | P2 | Les mises à jour automatiques d'Ubuntu redémarrent ORION et la passerelle d'appel (le 24/10 à 08 h 04), y compris pendant un appel s'il y en a un | `unattended-upgrades` avec `needrestart` | système | **[à faire]** décision humaine (voir E) |
| F8 | P3 | Boîte mail : 225 délais dépassés, 99 coupures TLS et 57 refus « abort » en deux semaines, avec une vérification toutes les 5 min | réseau ou Gmail ; ORION réessaie déjà | mail | constaté, rien à corriger côté ORION |
| F9 | P3 | Open-Meteo : 50 réponses HTTP 503 | panne du service extérieur | météo | constaté ; la réponse d'erreur est déjà gérée |
| F10 | P3 | Latence : sur les 5 demandes passées par le LLM, le choix d'outil coûte de 1,85 à 2,2 s pour des phrases sans outil (« Merci Aurélien », « C'est noté »), soit près de la moitié du `total` (de 4,6 à 6 s) | un appel de planification avant chaque réponse libre | `orion/tools/planner.py`, agent | **non modifié** : 6 mesures ne suffisent pas pour optimiser (voir G) |
| F11 | P3 | Transcription : « C'est noté » entendu « C'est nous » puis « et notez » ; « Merci Orion » entendu « Merci Aurélien » | qualité de l'audio téléphonique | STT | non modifié : avec F1 corrigé, le « Merci Orion » qui suivait aurait suffi à acquitter. Élargir l'accusé à « c'est nous » serait risqué |
| F12 | rappel | Points de `release_readiness_v2.md` encore ouverts : `__version__` à 0.1.0, production sur V2, démarrage sans session non vérifié, anciens lanceurs, `/opt/jarvis` | — | — | inchangés, hors de cette mission |

## C. Modifications

| Commit | Fichiers | Tests ajoutés |
|---|---|---|
| `cf5b8e9` accusé « c'est noté » pendant l'appel | `orion/agent.py` | `tests/test_call_acknowledgement.py` (4) : accusé de l'appel en cours, « Merci Orion », phrase sans rapport, « c'est noté » hors appel |
| `0822cef` fin de phrase de Piper | `orion/tts/piper.py`, `orion/config.py`, `orion/factory.py`, `config.toml` | `tests/test_tts_endings.py` (4) : prolongation sans clic, extinction, fins naturelles et sons courts intacts |
| `919e13d` relais du micro WebRTC | `orion/call/media.py` | couvert par les 53 tests d'appel (environnement de la passerelle) |
| `15d1476` coupure IMAP en pleine opération | `orion/mail/provider.py` | `tests/test_mail_lost.py` (27) : 5 erreurs × 5 opérations, refus de protocole non masqué, `replied` toujours « inconnu » |
| documentation | `docs/audit_fiabilisation_v2.md`, `docs/release_readiness_v2.md`, `docs/release_checklist_v2.md` (V10 et I11 ajoutés) | — |

Aucun changement d'architecture. Aucun secret ni aucune donnée persistante n'a été touché.

## D. Résultats

| Suite | Avant | Après |
|---|---|---|
| Suite complète (`/opt/orion/.venv`, mini-PC, `nice`) | 1899 réussis, 5 ignorés | **1934 réussis, 0 échec, 5 ignorés** |
| Appels audio (`/opt/orion-call/venv`) : media, rtp, sip, gateway, linphone | — | **53 réussis** |
| Non exécutés | `test_control` (Windows) ; `test_pipeline_e2e` (vrais moteurs, `ORION_E2E=1`), pour ne pas charger la production | idem |

Mesures avant/après sur la vraie voix (synthèse hors ligne, niveau des 10 dernières ms) :

| Phrase | Avant | Après |
|---|---|---|
| « C'est ORION. » | 824 ms, −29,5 dBFS | 864 ms, −42,9 dBFS, finit à 0 |
| « Votre disposition. » | 1324 ms, −18,4 dBFS | 1364 ms, −33,5 dBFS, finit à 0 |
| « Attention. » | 708 ms, −23,7 dBFS | 748 ms, −42,2 dBFS, finit à 0 |
| « Très bien, c'est bon. » | 1173 ms, −52,4 dBFS | inchangé |
| « Dans votre agenda pour demain. » | 1567 ms, −58,4 dBFS | inchangé |

Ce que les tests ne prouvent pas : le rendu audible au téléphone, l'accusé dans un vrai appel Linphone, le comportement
après un vrai redémarrage.

## E. Risques restants et interventions humaines

1. **Essais réels** (`docs/release_checklist_v2.md`), en particulier :
   - I11 : « c'est noté » pendant un vrai appel de règle ;
   - V10 : écoute des fins de phrase, au téléphone et aux enceintes, avec `soften_endings` à `true` puis à `false` ;
   - R1 à R4 : redémarrages.
2. **Mises à jour automatiques (F7)**, au choix :
   - les garder (ORION repart seul en 30 s) ;
   - exclure les unités ORION du redémarrage automatique, par un fichier `/etc/needrestart/conf.d/orion.conf` contenant `$nrconf{override_rc}{qr(^orion)} = 0;` puis un redémarrage manuel quand c'est pratique ;
   - fixer une heure de mise à jour par `systemctl edit apt-daily-upgrade.timer`.

   C'est un réglage système : il vous revient.
3. **Hypothèse F4** (fin de la dernière phrase avant le raccrochage) : à écouter avant d'y toucher.
4. **Déploiement** de `fiabilisation-v2` : sur validation explicite seulement. L'ordre et les commandes sont plus bas.
5. Restent du rapport précédent : version 0.1.0, production sur V2, `/opt/jarvis` à supprimer après validation, anciens lanceurs.

## F. État de préparation

V2 est stable dans son fonctionnement courant : deux semaines sans panne, replis LLM vérifiés en production (retour en
10 s), services durcis et relancés seuls, 1934 tests verts. L'audit a pourtant trouvé un défaut fonctionnel réel (F1)
qu'aucun test ne voyait, parce qu'il se trouvait entre deux composants testés séparément. C'est la principale leçon
pour V3 : il faut des tests de scénario de bout en bout, sur les chemins rares (appels, accusés, annonces de la veille),
pas seulement des tests de module.

Avis : V2, une fois cette branche fusionnée et les essais bloquants de la checklist passés, est une base saine pour
V3. Avant ces essais, c'est une version candidate.

## G. Feuille de route V3 (à préparer, pas à implémenter maintenant)

1. **Scénarios de bout en bout scriptés** : appel de règle, accusé, résumé par message, annonce de la veille. Ce sont
   les mêmes faux appareils que `tests/test_call.py`, mais enchaînés comme en production.
2. **Machine d'état de conversation explicite** dans l'agent : appel en cours, annonce, confirmation en attente.
   Aujourd'hui, ce sont des attributs indépendants (`_in_call`, `_follow_up`, `_after_confirmation`, `_clarify`) remis
   à zéro à des endroits différents. F1 vient de là.
3. **Latence du choix d'outil (F10)** : après une semaine de `[mesure]`, si la médiane du `choix` reste vers 2 s pour
   les phrases sans outil, envisager une préclassification sans LLM (remerciements, accusés, salutations) avant le
   planificateur.
4. **Post-traitement de la voix** : un seul étage (fin de phrase, effet, normalisation) testé à part, pour préparer un
   autre moteur sans toucher à la voix ORION.
5. **Compteurs d'erreurs** dans `scripts/latency_report.py` (point 11 de `release_readiness_v2.md`) : mail, météo,
   coupures de LLM par jour, pour voir une dérive.
6. **Retrait de la compatibilité JARVIS**, une fois les lanceurs migrés et `/opt/jarvis` supprimé : c'est déjà
   documenté dans `docs/migration_orion.md`.

## Déploiement proposé (sur validation explicite)

Sur le mini-PC, avec un appel ni en cours ni prévu dans les minutes qui suivent :

    cd ~/orion-dev && git switch V2 && git merge --ff-only fiabilisation-v2
    git -C /opt/orion pull --ff-only ~/orion-dev V2
    sudo -n systemctl restart orion
    bash /opt/orion/deploy/appels.sh --code
    journalctl -u orion -u orion-call -n 40 --no-pager

`appels.sh --code` copie le code de la passerelle et relance `orion-call` lui-même : c'est nécessaire pour le
correctif F5. Ensuite, les essais V10 et I11.
