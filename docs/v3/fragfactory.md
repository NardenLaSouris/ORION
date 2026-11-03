# Frag Factory × ORION V3

Frag Factory (PC fixe) produit des clips CS2 à partir des enregistrements. ORION le **supervise** : il annonce
chaque clip prêt, fait valider à la voix (avec confirmation), donne le bilan du matin et relaie les demandes de
style ou de nouvelle version. **Tout le travail éditorial reste dans Frag Factory** ; ORION ne stocke ni vidéo ni
moteur de montage, seulement des événements et des fiches.

Branche de travail : `feature/fragfactory`, partie de `v3` à `234c5c5`. Non fusionnée, non déployée.

## 1. Chemin des données

```
Frag Factory (127.0.0.1:8742)        Agent Windows (PC fixe)              Core ORION (mini-PC)
  /api/integration/v1        <──  action « fragfactory »          <──  module « fragfactory »
  file d'événements (outbox)     boucle locale, jeton de liaison       POST /actions/fragfactory
  décisions, versions, bilans    opérations en liste fermée            (IP autorisées + ORION_AGENT_TOKEN)
```

- Aucun port de Frag Factory n'est ouvert sur le réseau : seul l'agent, sur la même machine, l'appelle.
- **Tirage + accusé** : ORION lit les événements, les enregistre, puis acquitte. ORION arrêté : rien n'est perdu.
- Deux jetons, jamais dans Git ni dans une interface : `ORION_AGENT_TOKEN` (existant, Core ↔ agent) et le jeton
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

## 4. Mise en service (rien n'est actif par défaut)

1. **Frag Factory** : Paramètres → Intégration ORION → activer ; Paramètres → Clés API → `ORION_LINK_TOKEN`
   (32 caractères minimum, par ex. `python -c "import secrets;print(secrets.token_urlsafe(32))"`). Redémarrer.
2. **Agent Windows du PC fixe** : dans `windows_agent.toml`, `[fragfactory] enabled = true` ; dans son `.env`,
   `FRAGFACTORY_LINK_TOKEN=<le même jeton>`. Redémarrer l'agent : il refuse de démarrer si le jeton manque.
3. **Core ORION** : `[tools.devices]` doit déclarer le PC fixe ; `[fragfactory] enabled = true` (et `device`
   si le PC n'est pas l'appareil par défaut). Redémarrer le Core.
4. **ORION Control** → Frag Factory : régler l'heure du bilan, les canaux (voix, écrit), les niveaux annoncés.

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
- Non éprouvé sur le mini-PC ni sur le réseau local : la chaîne réelle a tourné sur 127.0.0.1 d'un seul PC.
- Sans `tzdata`, Windows retombe sur l'heure locale pour le fuseau du bilan.
