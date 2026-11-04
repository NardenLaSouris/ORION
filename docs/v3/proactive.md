# ORION PROACTIVE : architecture (étape 6, préparation)

État au 4 novembre 2026 : **architecture seulement**, sans code. L'autonomie d'ORION reste suspendue (décision du
2 novembre). PROACTIVE n'agira jamais sans elle, et même avec elle, il ne fera d'abord que proposer.

## 1. Rôle

Observer l'environnement logiciel d'ORION, comprendre ce qui se passe, repérer des opportunités et **proposer** des
initiatives. Exemples :
- « votre train est annulé : voulez-vous que je cherche le suivant ? » ;
- « la RTX que vous surveillez est au plus bas » ;
- « 3 clips attendent votre validation depuis 2 jours ».

Proposer n'est pas agir : toute action passe ensuite par MISSIONS ou par un outil, avec les permissions de F1.

## 2. Composants (tous sur les fondations existantes)

```
sources ──► observateurs ──► détecteurs d'opportunités ──► initiative ──► garde F1 ──► remise F3 ──► vous
(EventHub F3, SENTINEL,       (règles d'abord ;             (raison,        (module:      (présence,     │
 NEWS, WATCH, Frag Factory,    LLM encadré ensuite)          preuves,        proactive)    heures calmes, │
 agenda, KNOWLEDGE)                                          échéance)                     budget)       ▼
                                                                                        accepter / refuser /
                                                                                        « plus jamais ça »
                                                                                               │
                                                                     KNOWLEDGE (préférence) ◄──┘
```

| Composant | Rôle | Fondation |
|---|---|---|
| **Observateurs** | S'abonnent aux événements normalisés : `request_id`, classe de données, schéma. Rien n'est lu en dehors de l'EventHub ou d'une interface déclarée. | F3, KNOWLEDGE (`reader("module:proactive")`) |
| **Détecteurs** | Une règle par opportunité : condition, preuves exigées, délai, déduplication. Le LLM peut formuler, jamais décider seul. | `StructuredLLM` |
| **Initiative** | Ce qui est proposé, pourquoi (preuves citées), l'action qui suivrait et sa capacité, son échéance. | — |
| **Garde** | `Gate("module:proactive")` : en phase 1, `NOTIFY` seulement. Toute action est refusée pendant la suspension. | F1 |
| **Remise** | Présence, heures calmes, budget d'interruptions, regroupement. | F3 (`InterruptionPolicy`) |
| **Retour** | Accepter lance une mission ou un outil, après confirmation. Refuser est retenu. « Plus jamais ce genre de suggestion » devient une préférence dans KNOWLEDGE. | MISSIONS, KNOWLEDGE |

## 3. Règles

1. **Phase 1 (actuelle) : lecture, analyse, notification.** PROACTIVE ne fait que proposer. L'action ne part que sur
   votre « oui », sous votre principal.
2. **Preuves obligatoires.** Une initiative cite ce qui l'a déclenchée : l'événement, la surveillance, l'article.
   Jamais d'opportunité inventée.
3. **Budget.** Un nombre de suggestions borné par jour, avec une durée minimale entre deux. Le budget d'interruptions
   F3 s'applique en plus.
4. **Pas de catégories sensibles** : pas de déduction de santé, de religion, d'opinions…, comme dans KNOWLEDGE.
5. **Apprentissage du refus.** Une suggestion refusée deux fois de la même sorte n'est plus proposée sans votre
   demande.
6. **Arrêt d'urgence et suspension** : plus rien n'est observé pour agir, ni proposé (`module:*`).
7. **Traçabilité.** Chaque initiative, proposée ou retenue, est inscrite au registre, sans contenu personnel.

## 4. Lots proposés

| Lot | Contenu | Dépend de |
|---|---|---|
| P-1 | Module `proactive` : abonnements à l'EventHub, détecteurs par règles (clips en attente depuis longtemps, surveillance au plus bas, incident SENTINEL prolongé), initiatives en base, remise par F3 | F1, F3 |
| P-2 | Retour : accepter (MISSIONS ou outil confirmé), refuser, « plus jamais » (préférence KNOWLEDGE) ; vue d'ORION Control | P-1, KNOWLEDGE, MISSIONS M1 |
| P-3 | Détecteurs assistés par LLM (formulation, regroupement), toujours avec preuves | P-1 |
| P-4 | Phase 2 de l'autonomie : seulement par votre décision explicite, avec des autorisations bornées (F1 `Grant`) | votre accord |

## 5. Ce qui existe déjà et servira

- **WATCH** : opportunités de prix (score d'opportunité).
- **NEWS** : alertes d'importance.
- **SENTINEL** : incidents.
- **Frag Factory** : événements de clips.
- **Rappels gardés dans une maison vide**, présentés à votre retour (F3).
- **KNOWLEDGE** : intérêts et préférences, lecture tracée.

PROACTIVE les relie : il n'en remplace aucun.
