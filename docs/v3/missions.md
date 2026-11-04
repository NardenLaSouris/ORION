# ORION MISSIONS : analyse architecturale (étape 5)

État au 4 novembre 2026 : **analyse et noyau de planification** (`orion/missions/plan.py`,
`tests/test_missions_plan.py`). Rien n'est encore branché sur le Core : aucune mission ne s'exécute.

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
| M1 | Module `missions` : stockage scellé des plans, `refresh` périodique d'après F2, routes d'ORION Control (plan, valider, terminer une étape humaine, annuler), événement `mission.blocked` | ce noyau, F2, F3 |
| M2 | Création à la voix et dans ORION Control ; proposition de plan par `StructuredLLM`, avec schéma et validation ; vous corrigez et validez | M1 |
| M3 | Types de tâches utiles aux missions (lectures : météo, Web, agenda, KNOWLEDGE ; rappels, notes), avec leurs capacités déclarées | M1, F2 |
| M4 | Adaptation : sur un blocage, proposition de révision (`revise`) soumise à votre accord | M2 |
| M5 | Bilan à la fin et bilan périodique (dans le journal NEWS ou une notification) | M1 |

## 7. Risques

- **Plans irréalistes du LLM local** (Qwen 7B) : le schéma, la validation et votre accord filtrent. Le plan doit
  rester court (40 étapes au plus).
- **Données personnelles dans les objectifs** : textes scellés, rien en clair dans les journaux, comme les tâches
  F2.
- **Étapes hors d'ORION** (téléphoner, signer) : nature `human`. ORION ne prétend jamais les avoir faites.
