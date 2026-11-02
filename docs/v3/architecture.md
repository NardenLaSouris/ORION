# ORION V3 — architecture

Branche `v3`, partie du tag `v2.0.0`. **Rien de V3 n'est déployé** : la production reste sur V2. Toute la V3 est
désactivée par défaut (`enabled = false` dans chaque section). Sans activation explicite, ORION se comporte exactement
comme V2, tests compris.

## Principes

1. **Réutiliser V2 au lieu de le doubler.** Les modules V3 passent par l'existant :
   - le bus d'événements (`orion/events.py`) ;
   - les notifications vocales (`orion/notifications`) ;
   - la présence (`orion/presence`) ;
   - les appels et messages Linphone (`orion/call`) ;
   - l'API d'administration (`orion/api`) et ORION Control (`orion/control`) ;
   - le LLM avec repli (`orion/llm/failover.py`).
2. **Le LLM conseille, les règles décident.** Le LLM (Qwen3 sur le Katana) aide à interpréter, regrouper et résumer.
   - Ses réponses sont du JSON validé contre un schéma.
   - Aucune décision sensible ne dépend de lui : déclencher une alerte, autoriser un achat, accorder une permission.
   - Aucun code qu'il produit n'est exécuté pour évaluer une condition.
3. **Mode dégradé partout.** Si le Katana est indisponible, chaque module garde un chemin sans LLM : collecte, règles
   et résumés simples.
4. **Données extérieures = données non fiables.** Articles, pages et flux sont mis entre balises et nettoyés. Ils ne
   sont jamais suivis comme des instructions, et ils ne sont jamais exécutés.
5. **États explicites.** Chaque élément suivi (surveillance, commande, anomalie) a une machine d'état déclarée. Un
   passage non prévu est refusé. C'est la leçon de l'audit V2 (bug de l'accusé « c'est noté »).
6. **Traçabilité.** L'historique commun et le journal des alertes sont lus par ORION Control.

## Socle commun : `orion/platform/`

| Fichier | Rôle |
|---|---|
| `store.py` | Base SQLite unique (`[modules] database`) : migrations versionnées par module, transactions imbriquables, sauvegarde à chaud, refus d'une base plus récente que le code |
| `history.py` | Historique commun des modules (création, vérification, déclenchement, refus, erreur) |
| `states.py` | Machines d'état déclaratives (`InvalidTransition` sur un passage interdit) |
| `scheduler.py` | Tâches périodiques : pas de double exécution, attente doublée après chaque échec (plafonnée), retour au rythme normal au premier succès |
| `limits.py` | Limites de fréquence : fenêtre glissante et intervalle minimal (requêtes par source, appels par jour) |
| `modules.py` | Registre : ordre des dépendances, cycles refusés, module en échec isolé, état, routes `/api/modules/<nom>/…` |
| `alerts.py` | Routage des alertes selon la présence (voir plus bas) ; chaque envoi est journalisé |
| `intelligence.py` | LLM encadré : schéma vérifié, données non fiables isolées, `None` en mode dégradé |
| `feeds.py` | HTTP borné (`http`/`https` seulement, taille et délai limités, identification honnête) et lecture RSS/Atom sûre (DOCTYPE/ENTITY refusés) |

### Alertes (WATCH, NEWS, PURCHASE)

| Présence | Canaux |
|---|---|
| à la maison (confirmée) | voix ; pendant les heures calmes, message écrit à la place |
| absent (confirmé) | message écrit ; plus un appel Linphone si le niveau est au moins `HIGH`, le budget le permet et ce n'est pas l'heure calme |
| inconnue | message écrit seulement, jamais d'appel |

- **Niveaux :**
  - `INFO` : gardé dans l'historique, rien n'est envoyé ;
  - `NORMAL` ;
  - `HIGH` ;
  - `CRITICAL`.
- **Budget d'appels** (`[modules]`) : 3 par jour au plus, 15 minutes d'écart minimum. Il est relu depuis la base, donc un
  redémarrage ne le remet pas à zéro.
- **Heures calmes :** jamais contournées, sauf réglage explicite `critical_ignores_quiet`.
- **Dédoublonnage :** une alerte de même clé déjà envoyée dans la fenêtre (12 h par défaut) n'est pas renvoyée.
- **Texte privé :** seul le titre est dit ou écrit.

### API et ORION Control

- **API du Core :** `GET /api/modules` donne l'état de tous les modules. `/api/modules/<nom>/…` sert les routes du
  module. Les protections sont les mêmes que pour le reste : IP autorisée et jeton.
- **Pont d'ORION Control :** `/bridge/modules/…` est relayé tel quel, le jeton reste dans le pont. Chaque module a sa
  section dans l'interface.

### Configuration

Sections ajoutées à `config.toml`, toutes inactives par défaut :
- `[modules]` : base et règles des alertes ;
- `[watch]` ;
- `[news]` ;
- `[evolve]` ;
- `[purchase]`.

### Répartition des machines (inchangée)

- **Mini-PC :** les modules, la base, le planificateur et les alertes.
- **Katana :** les appels au LLM uniquement. Facultatifs : en mode dégradé, on s'en passe.
- **PC fixe :** ORION Control, rien de V3 n'y tourne.

## Modules

Décrits dans leurs propres documents, au fur et à mesure :
- `watch.md` ;
- `news.md` ;
- `evolve.md` ;
- `purchase.md`.

### Dépendances

- `purchase` dépend de `watch`.
- `news` alimente `watch` par une source « news ».
- Aucun cycle : le registre les refuse.
