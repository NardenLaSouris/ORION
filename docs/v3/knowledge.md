# ORION KNOWLEDGE : les connaissances partagées par ORION

Étape 4 de la feuille de route (`architecture_cible.md` § 5). Code : `orion/knowledge/`. Tests :
`tests/test_knowledge.py`, avec des clés et des données fictives uniquement.

## 1. Ce que c'est

Le modèle commun de ce qu'ORION sait de vous et de votre entourage. Il est structuré, sourcé, daté, chiffré, et
destiné à servir NEWS (personnalisation), MISSIONS, PROACTIVE et CONTINUITY. Ces modules ne le lisent que par une
interface déclarée et tracée. La mémoire V2 (`memory.json` : « retiens que… ») reste en service. Sa migration est
prête, mais n'est pas faite (§ 6).

## 2. Modèle

```
entité (vous, Julie, Nantes, « les cartes graphiques »…)
  └─ assertion : sujet — prédicat — valeur | objet (entité) [négation]
       nature : fait | préférence | intérêt | hypothèse
       état   : active | contestée | remplacée | retirée | expirée
       confiance (0 à 1), provenances (qui, d'où, quand, identifiant de la demande),
       historique (chaque changement, sa raison, son auteur), validité, expiration
conflit : deux valeurs incompatibles pour la même place, en attente de votre arbitrage
```

| Règle | Comportement |
|---|---|
| **Faits, hypothèses, préférences** | Une hypothèse vient toujours d'une source automatique (un module, une déduction). Sa confiance est plafonnée à 75 %, et elle ne devient un fait que confirmée par vous. Une source automatique ne peut écrire qu'une hypothèse. |
| **Catégories sensibles** | Santé, religion, opinions politiques ou syndicales, vie sexuelle, origine, données judiciaires, biométriques ou génétiques : **jamais déduites**. Une hypothèse qui en touche une est refusée, et ce refus est inscrit au registre. Ce que *vous* dites vous-même est enregistré : c'est votre choix de le confier. |
| **Dédoublonnage** | Même sujet, prédicat, valeur et sens : une seule assertion. Les provenances s'ajoutent, et la confiance monte (« au moins une source a raison »). |
| **Contradictions** | Un prédicat à valeur unique (« habite », « travaille chez », « anniversaire »…) qui reçoit une autre valeur, ou la même valeur avec le sens inverse (« aime » / « n'aime pas »). **Vous l'emportez** : votre nouvelle valeur remplace l'ancienne, qui est gardée dans l'historique. **Une source automatique ne tranche jamais.** Les deux valeurs deviennent « contestées », et un conflit attend votre arbitrage dans ORION Control. |
| **Relations** | Une assertion peut viser une autre entité (« Julie est votre sœur »). `related(entité)` les rend dans les deux sens. Les noms et les alias sont reconnus sans accents ni casse. |
| **Recherche contextuelle** | Les mots de la demande, et du contexte fourni, sont comparés au texte, à la valeur, au prédicat et aux noms des entités. Le score tient compte de la confiance et de la fraîcheur. Une connaissance contestée est moins bien placée, et reste marquée comme telle. |
| **Conservation** | Faits et préférences : sans limite. Intérêts : 365 jours. Hypothèses : 180 jours (décision n° 2 du 2 novembre). Une validité terminée retire l'assertion. Une confirmation ou une nouvelle source fait repartir le délai. Une assertion remplacée ou retirée est effacée 180 jours après. La tâche `knowledge:retention` passe chaque jour : c'est une règle que vous avez fixée (`delegated`), pas une initiative. |
| **Oubli** | `forget` : suppression réelle de l'assertion et de son historique. `forget_everything` : tout, avec un compte final vérifiable (0). Vous seul (conversation ou ORION Control) pouvez faire oublier. |
| **Droit d'accès** | `export` : tout le contenu lisible (entités, assertions avec provenance et historique, conflits). |

## 3. Sécurité et données

- **Chiffrement.** Tout est chiffré dans la base personnelle du lot F4 (AES-256-GCM, un chiffré par enregistrement,
  lié à sa place). En clair, il ne reste que des identifiants aléatoires et des dates. Le test vérifie qu'aucun mot
  ne figure dans les fichiers.
- **Identifiants aléatoires.** Ils ne sont pas dérivés du contenu : les étiquettes HMAC changent à chaque rotation
  de clé, et un identifiant dérivé aurait cassé le dédoublonnage après une rotation.
- **Base verrouillée** (pas de clé, cas actuel de la V3). Le module reste actif et le dit (« verrouillée »). Les
  lectures des modules rendent une liste vide, les routes d'ORION Control répondent 423, et rien n'est écrit
  ailleurs.
- **Registre d'actions.** Il garde chaque lecture d'un module (principal, but, décision), chaque écriture, chaque
  arbitrage et chaque oubli, **sans jamais le contenu**.
- **Autonomie.** Lire est permis à un module pendant la suspension (`READ`) ; proposer une hypothèse
  (`WRITE_DATA`) y est refusé, comme toute écriture d'initiative.

## 4. Interface des autres modules

```python
reader = modules.knowledge.reader("module:news")          # principal fixé une fois
reader.context("cartes graphiques", "personnaliser le journal")
# -> [{"text": …, "kind": "interest", "confidence": 0.95, "status": "active"}]   (sans provenance)
reader.propose("s interesse a", "la Formule 1", source="article 42")  # hypothèse, ou None si refusée
```

- `context` exige un but. Il est décidé par le moteur central et inscrit au registre.
- `propose` ne crée jamais qu'une hypothèse. Elle est refusée pendant la suspension, ou sur une catégorie sensible.
- Aucun module n'a accès à la base elle-même.

## 5. ORION Control

La vue **Connaissances** montre :
- le nombre de connaissances, d'entités, de contradictions et d'hypothèses ;
- les contradictions, avec un bouton par valeur et sa source, et « aucune » ;
- la recherche, et la liste : nature, confiance, état, sources, avec les actions *Confirmer* (pour une hypothèse),
  *Retirer* et *Oublier*.

Chaque action passe par le moteur central (principal « admin »). Base verrouillée : un bandeau l'explique.

Routes :
- `GET /api/modules/knowledge` et `…/assertions`, `…/assertions/<id>` (avec l'historique), `…/search?q=`,
  `…/entities`, `…/conflicts`, `…/export` ;
- `POST …/assertions`, `…/assertions/<id>/confirm` ou `/retract`, `…/conflicts/<id>/resolve` ;
- `DELETE …/assertions/<id>`.

## 6. Ce qui attend votre décision

1. **La vraie clé de la base personnelle** (lot F4, `foundation.md`). Sans elle, KNOWLEDGE reste verrouillé.
2. **La migration de `memory.json` (V2).** `orion/knowledge/importers.py` est prêt.
   - `plan_memory_facts` montre ce qui serait importé, sans clé et sans rien écrire.
   - `import_memory_facts` est idempotent : un second passage n'ajoute rien, et la source n'est jamais modifiée.
     Chaque fait garde sa source `memory.json#<id>`.
   - Testé sur un fichier fictif seulement ; vos faits réels n'ont pas été importés.
3. **Les outils vocaux.** « Que sais-tu de moi ? », « oublie que… » restent ceux de V2 tant que la migration n'est
   pas faite, pour ne pas avoir deux mémoires concurrentes à la voix.

## 7. Suite prévue

- **NEWS P2 à P4** (intérêts, pertinence) : `reader("module:news").context(…)` pour le journal personnel.
- **MISSIONS** : un objectif et ses contraintes connues (« je ne conduis pas »), par la même interface.
- **PROACTIVE** : opportunités d'après les intérêts et les faits, toujours soumises à l'autonomie.
- **CONTINUITY** : mémoire de conversation persistante.
- **Recherche sémantique** (plongements) quand le volume le justifiera. La recherche par mots suffit à l'échelle
  actuelle.
