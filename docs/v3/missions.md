# ORION MISSIONS : analyse architecturale (étape 5)

État au 4 novembre 2026 : **lot M1 livré** (`orion/missions/module.py`, `tests/test_missions_module.py`), sur le
noyau de planification (`orion/missions/plan.py`). Dans la V3 en service, le module est actif mais **verrouillé** tant
que la vraie clé de données (F4) n'existe pas : les textes d'une mission sont personnels et ne sont jamais écrits en
clair. M2 (création à la voix, plan proposé par le LLM) n'est pas commencé.

## 0. Lot M1 : ce qui est en place

| Élément | Comportement |
|---|---|
| Stockage | base opérationnelle, table `missions` ; plan entier scellé (aad `missions:<id>:plan`) ; en clair seulement l'état, la révision, les compteurs et les dates ; clé absente : rien n'est écrit (423) |
| Cycle | `draft` (votre validation du plan) -> `active` -> `finished` ou `cancelled` |
| Décisions | création, validation du plan, lancement, validation d'une étape sensible, étape humaine faite, annulation : `user`, `voix` ou `control` seulement ; chacune au registre, sans texte |
| Lancement | sur votre action seulement : chaque étape prête devient une tâche F2 `schedule_for_user` (principal `delegated:<type>:<tâche>`, décidée par F1 au moment d'agir), clé de déduplication `missions:<id>:<étape>:r<révision>` |
| Suivi | `missions:refresh` (60 s, `delegated:missions:suivi`, NOTIFY) : états d'après F2, blocages qui vous attendent annoncés une fois (`missions.blocked`, privé), fin annoncée (`missions.finished`) ; il ne crée, ne relance ni n'annule aucune tâche ; rien n'est réécrit si rien ne change |
| Annulation | mission annulée, ses tâches encore actives annulées (coopératif F2) |
| Vérification d'un plan | graphe sans cycle, dépendances connues, identifiant et titre, type de tâche **existant** pour toute action ou vérification, champs d'étape limités |
| ORION Control | `GET /api/modules/missions`, `POST /api/modules/missions` (brouillon), `GET …/<id>`, `POST …/<id>/approve`, `…/launch`, `…/cancel`, `…/steps/<étape>/approve`, `…/steps/<étape>/complete` |
| Configuration | `[missions] enabled`, `refresh_seconds` |

Écart avec l'analyse : le principal d'une étape est celui que F2 donne à toute tâche que vous programmez
(`delegated:<type>:<tâche>`), pas `delegated:mission:<id>` ; la mission n'a pas de principal propre, donc aucun droit
à elle.

## 1. Objectif

Vous donnez un objectif (« organise un week-end à Rennes », « prépare ma déclaration d'impôts »). ORION :
1. le décompose en étapes ;
2. identifie les dépendances ;
3. construit un plan ;
4. le suit ;
5. repère les blocages ;
6. adapte le plan ;
7. fait le bilan.

Avant toute action sensible, il demande votre validation.

## 2. Principe : orchestrer les fondations, ne pas les doubler

| Besoin | Fondation existante | MISSIONS ajoute |
|---|---|---|
| Exécuter une étape, la reprendre après un redémarrage sans la refaire | **F2** : tâches persistantes, réservation, bail, idempotence, `recover` | le lien étape ↔ tâche (`Step.task_id`) |
| Savoir si une action est permise, au moment d'agir | **F1** : moteur central, principal `delegated:mission:<id>` | rien : la mission n'a aucun droit propre |
| Confirmer une action sensible | **F2** : confirmation liée à la demande, à usage unique | une validation *de plan* en plus (étape `sensitive`) : double garde |
| Annuler, arrêt d'urgence | **F2/F3** : annulation coopérative, `request_stop_all` | l'annulation d'une mission annule ses tâches |
| Notifier un blocage | **F3** : EventHub, politique d'interruption, présence, heures calmes | un événement `mission.blocked` |
| Connaître le contexte (contraintes, préférences) | **KNOWLEDGE** : `reader("module:missions").context(…, but)` | rien : lecture tracée |
| Ressources absentes (Katana, agent du PC) | **F5** : service de ressources, `busy()` | une étape attend au lieu d'échouer |
| Proposer un découpage | `StructuredLLM` (sortie validée par schéma) | le schéma d'un plan ; le plan proposé est **toujours** validé par `Plan.validate` et par vous |

Conséquence : MISSIONS n'a ni planificateur, ni file, ni moteur de permissions, ni notifications propres. C'est un
module V3 de plus (`BaseModule`) ; on peut le désactiver sans rien faire tomber.

## 3. Modèle (`orion/missions/plan.py`, livré)

- **Plan** : un objectif, une liste d'étapes (40 au plus), et une révision.
- **Étape** : `action` (une tâche F2 d'un type enregistré), `check` (une lecture) ou `human` (vous seul). Elle porte
  aussi ses dépendances (`after`), le drapeau `sensitive`, votre validation (`approved`), son état, sa tâche et une
  note.
- **Validation** : graphe sans cycle, dépendances connues, nature valide, et un type de tâche pour toute action.
- **`refresh`** : recalcule les états à partir de ceux des tâches F2 (`TASK_TO_STEP`) et des dépendances. Il rend
  les étapes prêtes à lancer. Les étapes sensibles non validées et les étapes humaines attendent.
- **`blockers`** : signale un échec avec sa raison, une validation attendue, une action de votre part attendue, ou
  une étape en cours depuis plus de 24 h.
- **`revise`** : un nouveau plan qui garde intact ce qui est fait. Une étape sensible dont le contenu change
  redemande votre validation.
- **`summary` / `report`** : avancement, et bilan dit (fait, en cours, bloqué).

## 4. Cycle de vie prévu (à développer)

```
objectif dit ──► proposition de plan (LLM, schéma) ──► Plan.validate ──► vous : « oui » / corrections
     ▲                                                                       │
     │                                                                       ▼
 bilan ◄── terminée ◄── refresh (chaque tour) ◄── tâches F2 (delegated:mission:<id>) ◄── étapes prêtes
                              │
                              └─► blocage ──► EventHub (F3) ──► vous ──► approve / complete_human / révision
```

- **Création** : depuis la conversation ou ORION Control seulement (`schedule_for_user`). Une mission est une tâche
  que *vous* avez programmée (`delegated`), jamais une initiative.
- **Exécution** : chaque étape prête devient une tâche F2 sous `delegated:mission:<id>`. F1 décide au moment
  d'agir ; pendant la suspension, l'arrêt d'urgence ou une fois une autorisation expirée, l'étape attend.
- **Persistance** : le plan est stocké en base opérationnelle. Ses textes (objectif, titres, notes) sont scellés
  (lot F4) : une mission peut contenir des données personnelles.
- **Reprise** : au démarrage, `refresh` d'après les tâches. Une tâche interrompue non idempotente met l'étape en
  attente de vous, sans relance automatique.

## 5. Règles de sûreté (déjà codées dans le noyau, ou à coder)

1. Une étape sensible n'est jamais lancée sans votre validation **de plan**, en plus de la confirmation F2 au moment
   d'agir. Exemples : un achat (`SPEND`), un message extérieur, une suppression, un changement de code. (Codé.)
2. Une révision ne relance jamais une étape terminée. (Codé.)
3. Une révision qui change une étape sensible déjà validée redemande la validation. (Codé.)
4. Aucune capacité propre : la mission agit sous votre délégation, bornée par F1. (À coder au raccordement.)
5. Un plan proposé par le LLM n'est que proposé : il est validé par le code (`Plan.validate`), puis par vous. (Codé
   pour la validation.)
6. Bilan honnête : une étape « faite » est une tâche F2 `done`. Il n'y a pas d'autre source de vérité. (Codé.)

## 6. Étapes de développement proposées

| Lot | Contenu | Dépend de |
|---|---|---|
| M1 (livré) | Module `missions` : stockage scellé des plans, suivi périodique d'après F2, routes d'ORION Control (plan, valider, lancer, terminer une étape humaine, annuler), événements `missions.blocked` et `missions.finished` | ce noyau, F2, F3 |
| M2 | Création à la voix et dans ORION Control ; proposition de plan par `StructuredLLM`, avec schéma et validation ; vous corrigez et validez | M1 |
| M3 | Types de tâches utiles aux missions (lectures : météo, Web, agenda, KNOWLEDGE ; rappels, notes), avec leurs capacités déclarées | M1, F2 |
| M4 | Adaptation : sur un blocage, proposition de révision (`revise`) soumise à votre accord | M2 |
| M5 | Bilan à la fin et bilan périodique (dans le journal NEWS ou une notification) | M1 |

## 6 bis. Préparation de M2 (4 novembre 2026 ; implémentation reportée)

Décision du propriétaire : M1 est livré ; M2 ne commence qu'une fois le socle quotidien suffisamment restauré
(`docs/v3/recuperation_v2.md` : présence, mail, agenda, éclairage, Spotify, appels).

**Constat préalable.** Un seul type de tâche F2 est enregistré aujourd'hui (`news.bulletin`). Un plan ne peut donc
contenir, en pratique, que des étapes humaines : proposer des plans par le LLM avant M3 donnerait des missions presque
entièrement « à faire vous-même ». **Ordre recommandé : M3 (quelques types de tâche en lecture) avant ou avec M2.**

| Élément de M2 | Contenu prévu | Garde-fous |
|---|---|---|
| Schéma de plan | objet JSON : `objective`, `steps[]` (`id`, `title`, `kind`, `after`, `task_kind`, `params`, `sensitive`) ; `task_kind` choisi dans la liste des types enregistrés, fournie au LLM | `StructuredLLM.ask` (sortie validée par schéma) puis `Plan.validate` puis `MissionsModule.create` (types existants, champs limités) ; rien n'est créé sans passer les trois |
| Sensibilité | le LLM peut marquer une étape sensible ; le code **ajoute** la marque pour toute étape dont le type de tâche a une capacité SPEND, SEND, DELETE ou CHANGE_CODE (jamais retirée par le LLM) | double garde : validation du plan, puis confirmation F2 au moment d'agir |
| Voix | « organise… », « prépare… » : ORION propose le plan, le lit en résumé (nombre d'étapes, étapes sensibles), demande « je le garde en brouillon ? » ; « valide la mission » / « lance la mission » / « annule la mission » ; « où en est ma mission ? » | création en brouillon seulement ; validation et lancement : phrases explicites, comme le démute Discord ; jamais d'initiative |
| ORION Control | vue Missions : liste, détail (étapes, états, blocages), boutons valider / lancer / valider une étape / étape faite / annuler, édition du brouillon (titres, ordre, dépendances, sensibilité) | les routes de M1 ; base verrouillée : bandeau, rien d'affiché |
| Correction | vous modifiez le brouillon (Control) ou dites « enlève l'étape 3 » ; une révision passe par `Plan.revise` | une étape sensible modifiée redemande votre validation (déjà codé) |
| LLM indisponible | pas de proposition : création manuelle dans Control seulement | jamais de plan inventé par des règles |

**Dépendances.** M1 (livré) ; `StructuredLLM` (existant) ; la clé F4 pour que MISSIONS soit déverrouillé en service ;
M3 pour des plans utiles.

**Essais prévus.** Schéma refusé (champ inconnu, type inexistant, cycle) ; étape sensible ajoutée par le code ; brouillon
jamais lancé sans « lance » ; LLM indisponible ; phrases de validation au milieu d'une autre conversation (ignorées) ;
vue Control en base verrouillée.

## 7. Risques

- **Plans irréalistes du LLM local** (Qwen 7B) : le schéma, la validation et votre accord filtrent. Le plan doit
  rester court (40 étapes au plus).
- **Données personnelles dans les objectifs** : textes scellés, rien en clair dans les journaux, comme les tâches
  F2.
- **Étapes hors d'ORION** (téléphoner, signer) : nature `human`. ORION ne prétend jamais les avoir faites.
