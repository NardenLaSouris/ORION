# ORION EVOLVE

Maintenance, diagnostic et évolution assistée du code d'ORION. Paquet `orion/evolve/`, inactif par défaut
(`[evolve] enabled = false`). C'est le module sensible : **les permissions sont imposées par le code**
(`policy.py`), jamais par les consignes données au LLM.

## Niveaux

| Niveau | Permis | Activation |
|---|---|---|
| 1 Observateur | journal → anomalies, diagnostics, rapports, propositions en file de validation | par défaut |
| 2 Développeur | en plus, dans l'espace de travail isolé : branche `evolve/…`, modifications, tests, commit si tout passe, Pull Request préparée | `[evolve] level = 2`, ou ORION Control (l'utilisateur seul) |
| 3 Mainteneur | intégration et déploiement | **refusé dans cette version** ; un réglage à 3 donne le niveau 2 |

- **Changer de niveau :** seul l'utilisateur peut le faire, par l'API d'ORION Control et entre 1 et 2. EVOLVE et le LLM
  n'ont aucun moyen de le faire.
- **Fusion et déploiement :** `merge` et `deploy` existent seulement pour refuser.

## Interdits permanents

**Fichiers protégés**, jamais modifiables :

| Catégorie | Fichiers |
|---|---|
| ses propres règles et contrôles | `orion/evolve/**` |
| achats | `orion/purchase/**` |
| leurs tests | `tests/test_evolve*`, `tests/test_purchase*` |
| secrets | `.env*`, clés, `*secret*`, `*token*` |
| configuration locale | `*.local.toml` |
| données et sauvegardes | `data/**` |
| modèles | `models/**` |
| déploiement et dépendances | `deploy/**`, `requirements*.txt`, `pyproject.toml` |
| historique Git et ancien paquet | `.git/**`, `jarvis/**` |

**Chemins :** sont refusés les chemins absolus, `..`, les liens qui sortent du dépôt, et tout ce qui est hors de
`orion/`, `tests/`, `docs/`, `scripts/`.

**Lignes ajoutées refusées** (un diff qui en contient ne peut pas être commité) :
- `shell=True`, `os.system`, `os.popen` ;
- `eval`, `exec`, `__import__` ;
- `pickle`, `marshal`, `ctypes` ;
- les clés privées, les jetons et les mots de passe en clair ;
- un `push --force` ;
- un chemin `/opt/orion`.

**Espace de travail :** il ne peut être ni l'installation active, ni un de ses dossiers de code, ni un de ses parents.
Seul `data/` peut l'accueillir (`data/evolve`).

**Commandes :** seuls `git` et l'interpréteur Python sont permis.
- Pas de shell.
- L'environnement est débarrassé des variables de secret (token, password, key…).
- Un délai limite chaque commande.

**Aucun accès en écriture à l'installation active.** Le dépôt est cloné (`git clone --no-hardlinks`) et la source n'est
jamais modifiée (test : `test_good_fix_is_committed_on_an_evolve_branch_and_production_is_untouched`).

## Observation (`observe.py`)

Lecture du journal systemd (`journalctl`, sans privilège) des unités `[evolve] journal_units`.

| Type | Quand | Suite |
|---|---|---|
| logicielle | trace Python, exception de programmation (`TypeError`, `KeyError`…), au moins une ligne dans le code d'ORION | diagnostic automatique ; proposition de correction après 3 occurrences |
| extérieure | délai, réseau, TLS, IMAP, HTTP 5xx, « injoignable » | jamais corrigée dans le code ; signalée seulement au-delà de 50 par analyse (dégradation durable) |
| latence | médiane de `[mesure] total` au-dessus de 6 s | rapport ; mesurer avant d'agir |

Une même cause (exception et dernière fonction d'ORION en cause) donne une seule anomalie, avec un compteur.

## Diagnostic (`diagnose.py`)

- **Base par règles :**
  - éléments du journal ;
  - fichiers et lignes de la trace ;
  - extraits du code (dans le clone) ;
  - dépendances (imports) ;
  - conséquences ;
  - hypothèse selon le type d'exception.
- **Apport du LLM, facultatif :**
  - une cause et des pistes, validées par un schéma ;
  - il ne peut citer que des fichiers de la trace ou de leurs dépendances ;
  - la source de chaque élément est indiquée : « règles » ou « règles + llm ».

## Correctifs (niveau 2)

Pour une anomalie **logicielle**, ou une proposition **acceptée par l'utilisateur** :
1. Une branche `evolve/<origine>-<id>` est créée depuis la branche suivie, mise à jour depuis la source.
2. Les modifications sont appliquées par remplacement exact (le texte cherché doit apparaître une seule fois). Seuls les
   nouveaux fichiers de test peuvent être créés. **Un test de non-régression dans `tests/` est obligatoire.**
3. Les vérifications s'enchaînent :
   - compilation de tout le code ;
   - suite de tests ;
   - sécurité du diff : fichiers protégés, lignes refusées, dépendances.
4. **Le moindre échec interdit le commit.** La branche est alors supprimée et le clone revient exactement à son état
   d'avant (`discard`).
5. Si tout passe : un commit sur la branche `evolve/…`, et une Pull Request préparée dans
   `data/evolve/pull-requests/<id>.md`. Elle n'est poussée que si `[evolve] remote` est configuré, sans `--force` et
   vers une nouvelle branche `evolve/…` seulement.
6. Rien n'est fusionné ni déployé. La relecture et la décision restent humaines.

Les modifications viennent du LLM (schéma validé), ou sont fournies par l'utilisateur via l'API. Dans les deux cas,
elles passent par les mêmes contrôles.

## Retour arrière

Il n'y a rien à défaire en production : EVOLVE n'y touche pas.
- **Un correctif en échec :** il est annulé automatiquement.
- **Un correctif prêt :** supprimer sa branche `evolve/…` dans le clone, ou ne pas fusionner la Pull Request.
- **L'espace de travail entier** (`data/evolve`) peut être supprimé : il sera recloné.

## Propositions

- **Champs :** description, intérêt, difficulté, dépendances, risques, coût estimé, plan de réalisation, tests
  nécessaires.
- **Origines :** l'utilisateur, les erreurs répétées (automatique), ou le LLM (`suggest`, schéma validé).
- **Statut :** toujours « en attente » au départ. Seul l'utilisateur accepte ou refuse.
- **Aucun développement** sans acceptation, même si le LLM juge l'idée intéressante.

## Consulter Claude Code (`claude_code.py`)

EVOLVE peut interroger Claude Code (abonnement du propriétaire, pas l'API) pour des propositions d'amélioration et,
au niveau 2, pour rédiger le correctif d'une anomalie ou d'une proposition acceptée.

- **Comment** : `claude -p` lancé dans le clone isolé (`data/evolve/repo`), jamais dans `/opt/orion-v3` ; outils
  `Read`, `Grep`, `Glob` seulement, `--restricted` (fichiers confinés au clone, aucune commande, pas de web, réglages
  utilisateur ignorés), aucun serveur MCP, aucune session gardée ; réponse au format `--json-schema`, revérifiée par
  ORION. Demande par l'entrée standard, jamais sur la ligne de commande.
- **Quand** : bouton « Demander à Claude Code » (ORION Control, vue « Évolution », question facultative), et de
  lui-même après une nouvelle anomalie logicielle (`claude_auto`). Au plus `claude_per_day` (6) consultations par
  24 heures, les deux ensemble ; chaque consultation est inscrite à l'historique (« consultation claude »).
- **Ce qui part** : votre question, les anomalies ouvertes (type, titre, nombre, fichiers), les résumés de diagnostic,
  les titres déjà proposés, puis ce que Claude Code lit lui-même dans le clone (le code suivi par Git : ni `.env`, ni
  `config.local.toml`, ni `data/`). Adresses, numéros, paramètres d'adresse web et phrases citées « … » sont masqués.
  Jamais vos messages, mails, agenda ni connaissances.
- **Ce qui revient** : au plus trois propositions, origine « claude », en attente : vous seul les acceptez. Un
  correctif rédigé par Claude Code passe par la même politique, les mêmes tests et la même Pull Request préparée.
- **Jeton** : `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) dans `/opt/orion-v3/.env`, passé au seul processus
  Claude Code ; l'environnement des tests du clone l'ignore (`clean_env`).

Mise en place (sur le Core) :

```bash
ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_claude_code_root.sh"   # programme
claude setup-token                                                                       # jeton (PC)
ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_claude_code_setup.sh"       # saisie masquée
```

Couper : `v3_claude_code_setup.sh --desactiver` ; effacer le jeton : `--oublier` (et le révoquer côté Claude).

## Interfaces

`/api/modules/evolve` :

| Route | Rôle |
|---|---|
| `GET` | état (niveau, compteurs) |
| `PUT level` | changer de niveau : 1 ou 2, par l'utilisateur |
| `POST scan` | analyser le journal maintenant |
| `GET anomalies` | liste des anomalies |
| `GET`/`POST anomalies/<signature>` | détail et diagnostic / nouveau diagnostic |
| `GET`/`POST patches` | correctifs, avec résultats des tests, branche et Pull Request |
| `GET`/`POST proposals` | propositions |
| `POST proposals/<id>/accept\|reject` | décision de l'utilisateur |
| `GET history` | historique |

La section EVOLVE d'ORION Control s'appuie sur ces routes.

## Risque résiduel

Au niveau 2, les tests du clone exécutent du code modifié (écrit par le LLM et vérifié par la politique) sous le compte
d'ORION. L'environnement est débarrassé des secrets, mais l'exécution n'est pas isolée du réseau. **Le niveau 2 ne
doit être activé qu'en connaissance de cause.** Un conteneur ou un compte dédié sans réseau serait l'étape suivante.
