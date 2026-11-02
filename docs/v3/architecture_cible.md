# ORION — architecture cible et feuille de route

Audit au 2 novembre 2026. Il compare la vision « assistant conversationnel + agent autonome » au code réellement
présent (arbitrages et feuille de route validée : `roadmap.md`) :
- `V2` (`c66120f`, en production) ;
- `v3` (`b74fc49`, jamais déployée).

**Aucune implémentation n'est commencée ici.**

## 1. Ce qui existe déjà, module par module

La vision nomme dix modules. La plupart existent déjà en partie, sous d'autres noms.

| Module visé | Déjà dans le code | Où | Manques principaux |
|---|---|---|---|
| **CORE** | conversation (`agent.py`), fabrique (`factory.py`), bus d'événements synchrone (`events.py`), Core des outils avec registre, validation, permissions et confirmations (`tools/core.py`, `permissions.py`, politiques Cedar `tools/policy.py`), corrélation des demandes (`trace.py`), journal d'activité JSONL (`activity.py`) ; en V3 : registre de modules, base SQLite et migrations, historique commun, machines d'état, planificateur, limites (`orion/platform`) | V2 + v3 | permissions des **modules** (hors outils) non centralisées ; pas d'interrupteur d'autonomie ; événements sans schéma de données ni classe de sensibilité ; tâches non persistantes en V2 |
| **NEWS** | veille, bulletins, alertes (v3) ; surveillance des mails et suivi des échéances (V2 `mail/`) ; recherche Web SearXNG (`web/`) | v3 + V2 | journal personnalisé (`news_personal_intelligence.md`) ; validation réelle |
| **SENTINEL** | `--health` et `run_diagnostic` (`diagnostics.py`), état des appareils et du Katana (`api/status.py`), bascule du LLM et événements `system.error`/`system.recovered` (`llm/failover.py`), sources du fond d'écran (`wallpaper/collector.py`), présence réseau (`presence/network.py`), anomalies du journal (`evolve/observe.py`, v3) | V2 + v3 | pas de vue unifiée ; pas d'historique de santé ; l'observation du journal est rangée dans EVOLVE |
| **KNOWLEDGE** | faits retenus sur demande (`memory.py`), journal des échanges en SQLite (`journal.py`), vocabulaire et consignes appris (`learning.py`), contexte court (`context.py`), profils et terminaux (`profiles.py`), agenda (`agenda.py`), dossiers de mails (`mail/actions.py`) | V2 | pas de modèle commun ; pas de séparation personnel / public ; intérêts absents |
| **MISSIONS** | routines déclenchées par heure, intervalle, événement ou demande (`routines/`), minuteurs et rappels (`scheduling/`), règles d'appel avec tâches et reprises (`call/rules.py`), annonces de la veille (`scheduling/eve.py`), surveillances WATCH, commandes PURCHASE (v3) | V2 + v3 | **cinq moteurs de tâches distincts** ; aucun objectif à plusieurs étapes planifié |
| **CONTROL** | ORION Control (Windows, WebView2) par l'API d'administration, fond d'écran interactif, visage, sections V3 | V2 + v3 | historique transversal ; vue des tâches ; interrupteur d'autonomie |
| **CONTINUITY** | appels et messages Linphone, fil de conversation par canal (`agent._threads`), micro et sortie audio distants (`audio/network.py`, `winagent`), bascule maison / téléphone (`audio/switch.py`), terminaux | V2 | reprise d'une conversation d'un appareil à l'autre ; mémoire de la conversation après redémarrage |
| **PROACTIVE** | notifications vocales et suites proposées (`notifications/`), annonces de mails, appels en cas d'absence (`call/trigger.py`), accueil au retour (`presence`), mode Flex (`flex.py`), routeur d'alertes V3 (`platform/alerts.py`) | V2 + v3 | moteur de décision commun ; budget d'interruptions unique ; interrupteur d'autonomie |
| **EVOLVE** | niveaux 1 et 2, permissions codées, espace isolé (v3) | v3 | dépendance à SENTINEL pour l'observation |
| **ENVIRONMENT** | lumières Tuya et scènes (`tools/lights.py`), état de la maison (`home.py`), présence par capteurs (`presence/`), PC (allumer, éteindre, applications par `winagent`), Spotify et librespot, météo | V2 | modèle d'appareils commun ; pas de NAS dans le code |

**NAS :** aucune référence dans le code. Le « fonctionnement dégradé si le NAS est indisponible » suppose un rôle à
définir (§ 7).

## 2. Doublons et dépendances réelles

### Doublons à résorber (sans réécriture : par adaptateurs successifs)

| Mécanisme | Implémentations actuelles | Cible |
|---|---|---|
| Planification de tâches | `scheduling/scheduler.py` (minuteurs, rappels), moteur des routines, boucle des règles d'appel, `eve.py`, `agenda_watch.py`, `platform/scheduler.py` (V3), plus les boucles de `mail/watch.py`, `mail/actions.py`, `presence` | un service de tâches persistantes (§ 4.2) ; les moteurs existants deviennent des producteurs de tâches |
| Persistance | 10 fichiers JSON (`persist.py`), JSONL d'activité et du journal de la maison, SQLite du journal des échanges, SQLite V3 | trois bases : opérationnelle, publique, personnelle (§ 4.5) ; les JSON migrés un par un, avec sauvegarde |
| Heures calmes | `mail/watch.py`, `platform/alerts.py`, nuit du visage, configuration `[mail]` et `[modules]` | une seule politique d'interruption (§ 4.4) |
| Envoi d'une information à l'utilisateur | `NotificationManager` (voix), `AwayCallTrigger`, règles d'appel, annonces de mails, routeur d'alertes V3 | le routeur d'alertes généralisé, avec les autres en clients |
| Historique | `activity.jsonl`, `house_journal.jsonl`, `journal.sqlite`, `module_history` (V3), `policy_decisions.jsonl` | un registre d'actions (§ 4.3), les autres restant des vues spécialisées |
| Permissions | `PermissionManager` et Cedar (outils), politique d'EVOLVE, niveaux de PURCHASE, rôles des profils | un service de permissions central (§ 4.1) ; les politiques de modules y sont déclarées |
| Observation de pannes | `diagnostics.py`, `failover.py`, `evolve/observe.py`, sources du fond d'écran | SENTINEL |

### Dépendances qui imposent l'ordre

- **PROACTIVE, MISSIONS, EVOLVE, PURCHASE** agissent seuls : ils exigent **avant eux** le service de permissions central
  et l'**interrupteur d'autonomie**.
- **Personnalisation de NEWS et PROACTIVE** dépendent de KNOWLEDGE (profil, données personnelles séparées).
- **EVOLVE** a besoin de l'observation de SENTINEL, aujourd'hui dupliquée dans `evolve/observe.py`.
- **CONTROL** affiche ce que les autres exposent : il évolue avec chacun, ce n'est pas une étape isolée.
- **CONTINUITY** dépend de KNOWLEDGE (mémoire de conversation persistante) et du Core (identité, terminaux).

## 3. Architecture cible

```
                     ┌──────────── ORION CONTROL (pilotage) ─────────────┐
                     │  API d'administration (IP + jeton) /api/modules/… │
                     └────────────────────────┬──────────────────────────┘
 Conversation (voix, Linphone, fond d'écran)  │        Autonomie (suspendable)
            │                                 │                 │
 ┌──────────▼─────────────────────────────── ORION CORE ────────▼──────────────────────────┐
 │ Bus d'événements normalisé · Service de tâches persistantes · Permissions centrales      │
 │ Registre d'actions (traçabilité) · Politique d'interruption · Ressources (Katana, CPU)    │
 │ Core des outils (inchangé) · Registre de modules (orion/platform)                         │
 └───┬───────────┬────────────┬────────────┬─────────────┬────────────┬────────────┬──────────┘
   NEWS      SENTINEL     KNOWLEDGE     MISSIONS     PROACTIVE    ENVIRONMENT   EVOLVE
 (public)  (opérationnel) (personnel)  (tâches)    (décisions)   (maison)    (code, isolé)
```

**Principes :**
- **Conversation et autonomie** passent par le même Core, les mêmes outils et les mêmes permissions. Seule l'origine
  d'une action change : `utilisateur`, `module:<nom>` ou `routine`.
- **Aucun module n'appelle directement un autre module.** Ils communiquent par événements, tâches, ou une interface
  déclarée dans le registre. Seule exception : un service fourni par le Core.
- **Chaque module déclare ses dépendances, ses permissions demandées et ses classes de données.** Le registre refuse un
  module qui demande plus que ce qui lui est accordé.

## 4. Interfaces communes (à construire dans `orion/platform`, sans casser l'existant)

### 4.1 Permissions centrales et autonomie

```python
class Capability(str, Enum):  # ce qu'une action fait au monde
    READ, NOTIFY, CALL, CONTROL_DEVICE, WRITE_DATA, EXTERNAL_MESSAGE, SPEND, DESTRUCTIVE, CODE_CHANGE

@dataclass(frozen=True)
class Grant:  # accordé par l'utilisateur seul (ORION Control), jamais par un module
    principal: str            # "module:watch", "routine:<id>", "utilisateur"
    capability: Capability
    limits: dict              # plafonds, horaires, appareils
    expires_at: float | None

class Permissions(Protocol):
    def decide(self, principal: str, capability: Capability, context: dict) -> Decision: ...  # allow | ask | deny
    def autonomy_enabled(self) -> bool: ...   # interrupteur global de l'autonomie
```

- **Les outils existants** passent déjà par `PermissionManager` et Cedar. On les y raccorde par un adaptateur, sans les
  réécrire.
- **Interdits invariants dans le code :**
  - `SPEND`, `DESTRUCTIVE` et `EXTERNAL_MESSAGE` sont toujours `ask` pour une origine autonome, sauf autorisation
    explicite et bornée ;
  - aucun principal ne peut modifier ses propres autorisations ;
  - `CODE_CHANGE` est limité à l'espace isolé d'EVOLVE.
- **Interrupteur d'autonomie** (« Suspendre l'autonomie ») : toute tâche ou décision d'origine `module:*` reçoit `deny`,
  sauf la lecture et la journalisation. La conversation reste entièrement disponible. L'état est persistant et visible
  dans ORION Control et à la voix (« mets-toi en pause », « reprends »).

### 4.2 Tâches persistantes et reprenables

```python
@dataclass
class Task:
    id: str; owner: str; kind: str        # "watch.check", "call_rule.attempt", "routine.run", "mission.step"
    state: str                            # planned | running | waiting | done | failed | cancelled
    due_at: float | None; attempts: int; max_attempts: int; backoff: str
    checkpoint: dict                      # de quoi reprendre après un redémarrage
    origin: str                           # utilisateur | module:<nom> | routine:<id>
    request_id: str                       # corrélation (orion/trace.py)
```

- **Construction :** sur `platform/scheduler.py` et `platform/store.py`, en ajoutant la persistance des tâches et
  l'idempotence (une tâche ne s'exécute pas deux fois après un redémarrage).
- **Migration :** WATCH et PURCHASE d'abord, déjà en SQLite ; puis règles d'appel, routines, rappels.

### 4.3 Événements normalisés et registre d'actions

- **Événement :** `Event(type, source, payload, timestamp)` existe déjà. Il reçoit en plus `request_id`, `data_class`
  (`public` | `operational` | `personal`) et un schéma déclaré par type, vérifié en mode test.
- **Registre d'actions (ajout seul) :**
  - une ligne par action qui touche le monde : qui (principal), quoi (outil ou capacité), pourquoi (tâche, demande),
    décision de permission, résultat ;
  - les textes privés sont masqués comme en V2 (`loggable`) ;
  - sources : le journal d'activité et l'historique des modules, qui en deviennent des vues.

### 4.4 Politique d'interruption

- **Pour tout ce qui parle sans qu'on lui ait parlé :** notifications, alertes, appels, annonces, journal du matin.
- **Contenu :** heures calmes uniques, budget d'interruptions par jour et par canal, priorités, présence (règles actuelles
  de `platform/alerts.py`).
- **Évolution :** le routeur d'alertes devient ce service ; `NotificationManager` et `AwayCallTrigger` l'utilisent.

### 4.5 Séparation des données

| Classe | Exemples | Base | Accès |
|---|---|---|---|
| opérationnelle | états, tâches, santé, journaux techniques | `data/orion_ops.db` | tous les modules (lecture), Core (écriture) |
| publique | articles, prix, événements publics | `data/orion_public.db` (aujourd'hui `orion_v3.db`) | modules publics |
| personnelle | faits retenus, journal des échanges, mails, intérêts, historiques importés, agenda | `data/personal/` (droits 600, chiffrement des secrets et des imports) | KNOWLEDGE seul ; les autres modules par une interface déclarée et tracée |

### 4.6 Ressources et mode dégradé

- **Service de ressources :**
  - `available("llm.primary")` : Katana ;
  - `available("stt.gpu")` ;
  - `available("agent.pc")` ;
  - `available("storage.nas")` ;
  - `load()` : CPU, mémoire du mini-PC.

  Ces informations existent déjà, éparses : `failover.degraded`, `RemoteSTT`, statut des appareils.
- **Chaque module déclare** ce qu'il fait sans chaque ressource. Ce comportement est vérifié par un test « ressource
  absente » par module.
- **Tâches lourdes** (EVOLVE niveau 2, imports) : différées si le mini-PC est chargé ou pendant une conversation.

## 5. Ordre stratégique ajusté

L'ordre proposé était : NEWS, fondations, SENTINEL, KNOWLEDGE, MISSIONS, CONTROL, CONTINUITY, PROACTIVE, EVOLVE,
ENVIRONMENT. Ordre recommandé d'après les dépendances réelles :

| # | Étape | Pourquoi à cette place | Critères de validation |
|---|---|---|---|
| 1 | **NEWS** : finir et valider (P1 du journal, sources réelles, une semaine d'usage) | demandé ; déjà avancé ; ne dépend de rien d'autre | essais réels ; aucune invention relevée ; bulletins à l'heure une semaine durant ; budget d'appels respecté |
| 2 | **Fondations** : permissions centrales et **interrupteur d'autonomie**, tâches persistantes, événements normalisés, registre d'actions, politique d'interruption, séparation des données | rien d'autonome ne doit tourner sans eux | tests : un module ne peut ni s'accorder une permission ni agir quand l'autonomie est suspendue ; une tâche reprend après redémarrage sans doublon ; toutes les actions apparaissent au registre |
| 3 | **SENTINEL** : regroupe `diagnostics`, l'état des appareils, la bascule du LLM, l'observation du journal | utile tout de suite, en lecture seule, peu risqué ; prérequis d'EVOLVE | vue de santé unique ; historique sur 7 jours ; une panne du Katana détectée et levée sans intervention |
| 4 | **KNOWLEDGE** : modèle commun des connaissances personnelles, migration de `memory.json` et du journal des échanges, moteur d'intérêts | prérequis de la personnalisation de NEWS, de CONTINUITY et de PROACTIVE | données personnelles dans leur seule base ; suppression vérifiable ; aucune catégorie sensible déduite |
| 5 | **NEWS personnel** (P2 à P5 du journal) | dépend de KNOWLEDGE | voir `news_personal_intelligence.md` |
| 6 | **MISSIONS** : objectifs en plusieurs étapes sur le service de tâches ; migration des règles d'appel et des routines | dépend des fondations | une mission suspendue, reprise et annulée proprement ; chaque étape passe par les permissions |
| 7 | **PROACTIVE** : décisions (« prévenir, proposer, agir ? ») sur la politique d'interruption et les permissions | avant CONTINUITY : les décisions déterminent ce qui doit suivre l'utilisateur | budget d'interruptions jamais dépassé ; autonomie suspendable ; décisions expliquées dans ORION Control |
| 8 | **CONTINUITY** : reprise d'une conversation d'un appareil à l'autre, mémoire de conversation persistante | dépend de KNOWLEDGE et de PROACTIVE | conversation commencée à la voix, poursuivie par Linphone avec son contexte |
| 9 | **EVOLVE** : rebranché sur SENTINEL ; niveau 2 sous un compte dédié | dépend de SENTINEL ; module sensible en dernier | aucune écriture hors de l'espace isolé ; niveau 3 toujours refusé |
| 10 | **ENVIRONMENT** : modèle d'appareils commun, NAS si décidé | l'existant V2 fonctionne ; peu urgent | inventaire des appareils unique ; mode dégradé par appareil |
| — | **CONTROL** | n'est pas une étape : chaque étape livre sa section | chaque module livré avec sa vue |

## 6. Ce qu'il ne faut pas faire

- **Réécrire le Core des outils, l'agent ou les moteurs V2 qui fonctionnent :** on les raccorde par adaptateurs, comme
  la route `/api/modules`.
- **Ajouter un module autonome avant l'étape 2.**
- **Fusionner `v3` dans `main` avant la validation réelle de NEWS et des fondations.**

## 7. Décisions arbitrées (2 novembre 2026)

Vos arbitrages sont consignés dans `roadmap.md` : décisions, contradictions relevées et leur résolution, lots
CORE FOUNDATION (F1 à F5) et leurs dépendances, critères de validation, risques de migration, décisions restantes,
correctifs V2 à porter et plan d'exécution du lot F1.

En résumé :
- **NAS :** stockage externe facultatif (archives, documents, jeux de données, modèles inactifs, sauvegardes
  chiffrées) ; bases opérationnelles locales ; sa perte n'interrompt jamais le Core.
- **Ordre :** fondations avant tout module autonome, découpées en F1 à F5 (`roadmap.md`, § 3).
- **Autonomie :** suspendue par défaut, activée par vous seul ; phase 1 limitée à la lecture, l'analyse et la
  notification ; interrupteur indépendant de la conversation. Son périmètre exact (origines `module:` et
  `delegated:`) reste à confirmer.
- **Migration JSON :** progressive, sauvegardée, vérifiable, idempotente, réversible ; aucun ancien fichier supprimé
  avant validation complète.
- **Branches :** aucune fusion automatique ; liste des correctifs V2 à porter dans `roadmap.md`, § 7.
- **Comptes système :** `orion-import` et `orion-evolve`, sans accès à la clé ni à la base personnelle ; ils déposent
  dans une zone d'attente que le Core vérifie.
