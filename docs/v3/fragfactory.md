# Frag Factory × ORION V3

Frag Factory (PC fixe) produit des clips CS2 à partir des enregistrements. ORION le **supervise** : il annonce
chaque clip prêt, fait valider à la voix (avec confirmation), donne le bilan du matin et relaie les demandes de
style ou de nouvelle version. **Tout le travail éditorial reste dans Frag Factory** ; ORION ne stocke ni vidéo ni
moteur de montage, seulement des événements et des fiches.

Fusionné dans `v3` le 3 novembre 2026. Le 4 novembre, l'intégration a été **migrée vers l'agent Windows V3** (port
8875, jeton `ORION_V3_AGENT_TOKEN`) : plus aucune dépendance à l'agent ni au jeton de la production. Elle est
déployée dans la V3 et reliée à une **instance d'essai** de Frag Factory, aux données fictives (§ 8).

## 1. Chemin des données

```
Frag Factory (127.0.0.1:8742)        Agent Windows V3 (PC fixe, 8875)      Core ORION (mini-PC)
  /api/integration/v1        <──  action « fragfactory »          <──  module « fragfactory »
  file d'événements (outbox)     boucle locale, jeton de liaison       POST /actions/fragfactory
  décisions, versions, bilans    opérations en liste fermée            (IP autorisée + ORION_V3_AGENT_TOKEN)
```

- Aucun port de Frag Factory n'est ouvert sur le réseau : seul l'agent, sur la même machine, l'appelle.
- **Tirage + accusé** : ORION lit les événements, les enregistre, puis acquitte. ORION arrêté : rien n'est perdu.
- Deux jetons, jamais dans Git ni dans une interface : `ORION_V3_AGENT_TOKEN` (Core V3 ↔ agent V3, jamais celui de
  la production) et le jeton
  de liaison (`FRAGFACTORY_LINK_TOKEN` dans le `.env` de l'agent = `ORION_LINK_TOKEN` dans Frag Factory).

## 2. Fichiers

| Fichier | Rôle |
|---|---|
| `orion/fragfactory/protocol.py` | Opérations autorisées et validation typée de leurs paramètres |
| `orion/fragfactory/link.py` | Client HTTP de l'agent vers Frag Factory (boucle locale, réponses bornées) |
| `orion/fragfactory/agent_action.py` | Action « fragfactory » de l'agent Windows |
| `orion/fragfactory/engine.py` | Module V3 : synchronisation, annonces, bilan, contexte de conversation, décisions |
| `orion/fragfactory/references.py` | Lecture des références de clip dans les mots (« le dernier », « clip 42 »…) |
| `orion/fragfactory/tools.py` | Les 14 outils vocaux |
| `orion/config.py`, `orion/platform/build.py`, `store.py` | Section `[fragfactory]`, construction, classes de tâches |
| `orion/winagent/config.py`, `__main__.py` | Section `[fragfactory]` de l'agent |
| `orion/tools/planner.py` | Règles et mots du domaine |
| `orion/control/web/app.js`, `index.html` | Section « Frag Factory » d'ORION Control |
| `tests/test_fragfactory.py` | 47 tests (Frag Factory simulé) |
| `tests/test_fragfactory_e2e.py` | Chaîne réelle Frag Factory → agent → module |
| `scripts/fragfactory_demo.py` | Pile locale complète pour essayer l'interface |

## 3. Outils vocaux

Lecture (capacité READ, sans confirmation) : `fragfactory_status`, `list_clips`, `list_pending_reviews`,
`get_clip`, `play_clip`, `get_statistics`, `get_nightly_report`, `get_analysis_history`, `analyze_clip_style`,
`recommend_editing_style`.
Action (confirmation obligatoire, question qui nomme le clip) : `approve_clip`, `reject_clip`,
`request_revision`, `request_render` (seulement à partir d'une proposition faite juste avant).
**Aucun outil de publication.** Valider ≠ publier.

Identifiant de clip : jamais inventé par le modèle. Il est lu dans les mots, vérifié auprès de Frag Factory
avant la confirmation (zéro ou plusieurs correspondances : « précisez », rien n'est fait), puis revérifié par
Frag Factory (statut attendu, clé d'idempotence). Une réponse ambiguë ou une erreur réseau ne valide rien.

## 4. Mise en service

1. **Agent Windows V3** (`deploy/v3_agent.toml`) : `[fragfactory] enabled = true`, et `url` vers l'instance visée.
   Le jeton de liaison est `FRAGFACTORY_LINK_TOKEN`, dans `%LOCALAPPDATA%\ORION V3 Agent\.env`. L'agent refuse de
   démarrer s'il manque.
2. **Frag Factory** : intégration ORION activée, et `ORION_LINK_TOKEN` égal au jeton de liaison. L'instance d'essai
   est réglée ainsi par `deploy/v3_fragfactory_essai.ps1`.
3. **Core V3** : `[fragfactory] enabled = true`, `agent_url = "http://192.168.1.128:8875"`. Le jeton
   `ORION_V3_AGENT_TOKEN` est dans le `.env` du Core. Si l'agent ou son jeton manquent, le module ne démarre pas et
   le reste d'ORION démarre quand même.
4. **ORION Control** → Frag Factory : l'heure du bilan, les canaux, les niveaux annoncés.

## 5. Vérification

- ORION Control → Frag Factory : « Connecté », bouton « Synchroniser » sans erreur.
- Frag Factory → ORION : « Connecté », requêtes reçues, « Événements en attente » qui retombe à 0.
- À la voix : « où en est Frag Factory ? », puis « quels clips attendent ma validation ? ».
- Tests : `pytest tests/test_fragfactory.py` ; chaîne réelle :
  `FRAGFACTORY_ROOT=<dossier> FRAGFACTORY_PYTHON=<python de Frag Factory> pytest tests/test_fragfactory_e2e.py`.
- Essai sans rien toucher : `python scripts/fragfactory_demo.py --ff-root <dossier> --ff-python <python>`.

## 6. Retour arrière

- Désactiver : `[fragfactory] enabled = false` (Core et agent) ; côté Frag Factory, désactiver l'intégration.
  Les deux applications fonctionnent alors exactement comme avant.
- Code : revenir sur `v3` (`git switch v3`) ; la branche `feature/fragfactory` reste intacte.
- Données : le module n'écrit que ses tables `ff_*` dans la base d'ORION ; elles sont ignorées si le module est
  désactivé. Frag Factory : migration 8 ajoutée (tables neuves, colonnes neuves), rien de supprimé.

## 7. Limites

- Pas de notification de bureau Windows (ORION n'a pas ce canal) : voix et écrit seulement.
- Démos CS2 non prises en charge par Frag Factory : `demo.imported` est déclaré, jamais émis.
- **Votre vraie instance (port 8742) n'est pas reliée.** Elle contient vos 168 enregistrements et leurs
  transcriptions de micro : des données personnelles réelles, qui ne vont pas dans la V3 sans votre accord. Pour la
  relier, mettez `url = "http://127.0.0.1:8742"` dans `deploy/v3_agent.toml`, et le jeton de liaison dans ses
  Paramètres → Clés API. Activez aussi son intégration ORION ; au premier redémarrage, sa migration 8 ajoute des
  tables.
- **Annonces non entendues dans la V3.** La V3 n'a ni voix (`[audio] mode = "none"`) ni messages écrits : les
  annonces sont décidées et gardées dans l'historique, mais pas entendues.
- Sans `tzdata`, Windows retombe sur l'heure locale pour le fuseau du bilan.

## 8. Essai réel du 4 novembre 2026 (V3)

La chaîne réelle complète a tourné sur l'instance d'essai, aux données fictives : mini-PC (Core V3), réseau local,
agent V3 du PC, Frag Factory (port 8752).

| Étape | Résultat |
|---|---|
| Synchronisation | 10 événements reçus, puis acquittés (curseur 10 des deux côtés) |
| Production de clips | auto-test : un vrai REC synthétique analysé, puis rendu 9:16 par FFmpeg |
| Annonces | décidées et gardées (heures calmes, pas de canal dans la V3) ; affichées dans ORION Control |
| « Où en est Frag Factory ? », « quels clips attendent ma validation ? » | état et liste exacts |
| « Valide le dernier clip » → « non » | rien n'est fait |
| « Valide le dernier clip » → « oui » | clip 2 `APPROVED` dans Frag Factory, avec clé d'idempotence ; inscrit au registre |
| « Refuse le clip 1 » → « oui » | clip 1 `REJECTED` |
| « Quel montage tu ferais pour le clip 2 ? » → « Produis la version 1 » → « oui » | nouvelle version rendue, revenue en validation |
| ORION Control, vue Frag Factory | liaison, clips à valider, décisions, annonces et événements conformes |

**Défauts trouvés pendant cet essai, et corrigés (chacun avec un test) :**
- « Valide le dernier clip » partait en recherche Web, à cause du mot « dernier ». La réponse inventée affirmait la
  validation. Désormais, une intention `tool.fragfactory` est testée avant la recherche, et le garde-fou des actions
  prétendues connaît les clips.
- « Produis la version 1 », sans clip nommé, ne trouvait pas le clip. C'est maintenant celui de la proposition qui
  vient d'être faite.
- « Je vais produire la version 1 » (réponse libre, sans outil) n'était pas reconnu comme une action prétendue.

Test de bout en bout automatisé (`tests/test_fragfactory_e2e.py`) : vrai Frag Factory, vrai agent, et vrai transport
V3 (`agent_transport`). Il passe sur le PC (93 s).
