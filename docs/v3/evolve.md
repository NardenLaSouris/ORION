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

## Mode autonome (`autopilot.py`)

Demandé par le propriétaire le 4 novembre 2026 : ORION interroge Claude Code d'elle-même quand elle ne comprend pas une
demande, et pour ses propres propositions ; Claude Code juge et prépare. Révisé le même jour (feuille de route de
stabilisation, phase 2) : **aucun déploiement sans l'intervention et l'approbation du propriétaire**.

1. **Repérage** : chaque réponse d'ORION qui avoue ne pas avoir compris (« je n'ai pas compris », « je ne sais pas
   encore faire »…) met la demande de côté (3 mots au moins, une fois par semaine pour la même). Les propositions
   d'ORION elle-même (origine « llm » ou « erreurs ») sont examinées aussi ; les vôtres, jamais sans vous.
2. **Travail** (toutes les 15 minutes, une action à la fois) : Claude Code en mode travail (`Read`, `Grep`, `Glob`,
   `Edit`, `Write`, écriture confinée au clone par `--restricted`, aucune commande) modifie le clone sur une branche
   `evolve/auto-…` et rend un verdict : « corrigé », « rien à faire », « à valider », « dangereux », avec un risque.
3. **Jugement** :
   - dangereux : rien n'est fait, vous êtes prévenu ;
   - à valider, ou risque moyen ou élevé : proposition en file d'attente, vous êtes prévenu ;
   - corrigé, sans risque : règles d'ORION (aucun fichier sensible : plateforme et permissions F1, arrêt d'urgence,
     confirmations, appels, mails, achats, connaissances, accès aux machines, configuration, assemblage
     `orion/factory.py`, EVOLVE, déploiement ; au plus 8 fichiers et 400 lignes ; aucun test supprimé ni affaibli ; un
     test ajouté ; ni migration de base, ni commande système), compilation, tests, sécurité, commit sur la branche,
     puis **« attend votre accord »** (alerte écrite). Un écart à ces règles : à valider.
4. **Votre accord** (ORION Control, vue « Évolution ») : « Approuver » montre le résumé et les fichiers et, après
   confirmation, prépare la demande (`data/evolve/deploy/request.json`, `approved_by = utilisateur`) ; « Refuser »
   abandonne le changement (aussi après « Approuver », tant que le déploiement n'est pas lancé). Refusé si le Core a
   changé depuis la préparation, si une demande attend déjà, ou au-delà de 3 déploiements par 24 heures. ORION ne peut
   pas s'approuver elle-même (`approve(…, "evolve")` : refus).
5. **Déploiement, lancé par vous** dans un terminal :

   ```bash
   ssh -t user@192.168.1.91 "bash /opt/orion-v3/deploy/v3_evolve_runner.sh"
   ```

   Sans terminal, l'exécutant ne fait rien (aucun déclenchement automatique : `deploy/v3_evolve_root.sh` retire
   l'ancien `orion-v3-evolve.path`). Il refuse une demande sans votre accord, récupère la branche dans `~/orion-dev`
   (`v3-evolve`), refait les contrôles de son côté (`deploy/v3_evolve_gate.py`, copie déployée, qu'EVOLVE ne peut pas
   modifier), **montre le changement et attend « oui »** (sinon la demande reste en attente), repasse la suite complète
   des tests sur ce commit, puis lance `deploy/v3_deploy.sh v3-evolve` (sauvegarde, avance rapide, contrôle de santé,
   retour automatique à la version précédente en cas d'échec).
6. **Retour** : ORION lit le résultat et vous l'écrit (jamais à voix haute, jamais d'appel). « Revenir en arrière »
   dans ORION Control prépare l'annulation d'un changement déployé (commit d'annulation `git revert`), lancée par vous
   avec la même commande, qui demande aussi « oui ».

Interrupteur : ORION Control, vue « Évolution » (« Activer » / « Couper le mode autonome »), niveau 2 exigé ; le moteur
des permissions (autonomie suspendue, autorisation bornée CODE_CHANGE) l'arrête aussi. Le niveau 3 (fusion et
déploiement par ORION) reste refusé.

Retrait du déclencheur automatique (une fois, avec sudo) :

```bash
ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_evolve_root.sh"
```

Journal : `~/sauvegardes/orion-v3/evolve.log`. Les commits d'EVOLVE arrivent dans `~/orion-dev`, branche `v3-evolve` :
le poste de développement les reprend avant chaque déploiement.

**Composants qu'EVOLVE ne modifie jamais seul** (contrôle indépendant `FORBIDDEN` du Core, et `policy.PROTECTED` /
`AUTO_PROTECTED` côté ORION) : moteur des permissions F1 et autonomie (`orion/platform/**`), arrêt d'urgence
(`orion/platform/autonomy.py`), clés et secrets F4 (`orion/foundation/**`, `*secret*`, `*token*`, `.env*`),
validation des actions (`orion/tools/confirmation.py`, `orion/tools/core.py`, `orion/llm/guard.py`), déploiement et
retour arrière (`deploy/**`, `scripts/v3_*`, `orion/evolve/**`), accès aux machines (`orion/winagent/**`,
`orion/platform/agentlink.py`, `orion/fragfactory/__init__.py`), assemblage (`orion/factory.py`), actions reçues du
réseau (`orion/api*`, `orion/wallpaper/server.py`). Un changement qui en touche un est envoyé à vous, jamais déployé.

Risque résiduel : les demandes incomprises (ce que vous avez dit ou écrit) partent chez Claude Code, après masquage
des adresses et numéros. Un changement jugé sans risque peut tout de même dégrader un comportement que les tests ne
couvrent pas : le retour arrière est alors à demander dans ORION Control, puis à lancer par vous.

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
