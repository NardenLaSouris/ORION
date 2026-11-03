# CORE FOUNDATION — lots F1 à F5 : permissions, autonomie, registre, tâches, événements, stockage chiffré

F1 et F2 livrés sur `v3` le 2 novembre 2026, jamais déployés (F2 : § « Lot F2 » plus bas). Inactif par défaut (`[platform] enabled = false`) : la V2 et les
modules v3 se comportent alors exactement comme avant.

## Fichiers

| Fichier | Rôle |
|---|---|
| `orion/platform/permissions.py` | `Capability`, `Grant`, `GrantStore`, `PermissionEngine.decide`, `Gate` (accès d'un module) |
| `orion/platform/autonomy.py` | `AutonomySwitch` : `active`, `suspended`, `emergency_stop` |
| `orion/platform/ledger.py` | `ActionLedger` : registre en ajout seul, chaîné par SHA-256, `verify` |
| `orion/platform/foundation.py` | assemblage ; raccord au Core des outils (`FoundationGate`) ; outils vocaux ; routes d'ORION Control |
| `tests/test_platform_permissions.py` | 50 tests |

## Principaux et capacités

- **Principaux :**
  - `user` : la conversation ;
  - `admin` : ORION Control ;
  - `delegated:<type>[:<id>]` : ce que vous avez programmé (routine, surveillance, mandat, appel programmé) ;
  - `module:<nom>` : initiative d'un module.

  Toute autre valeur est refusée.
- **Capacités :** `READ`, `NOTIFY`, `CALL`, `CONTROL_DEVICE`, `WRITE_DATA`, `EXTERNAL_MESSAGE`, `SPEND`,
  `DESTRUCTIVE`, `CODE_CHANGE`.

## Règles de décision (dans l'ordre)

1. Principal invalide : `deny`.
2. `user` ou `admin` : `allow`. Les règles existantes du Core des outils (profils, confirmations, Cedar) s'appliquent
   toujours en plus.
3. État d'autonomie illisible : `deny`.
4. Arrêt d'urgence : `deny` pour tout principal autonome, lecture comprise.
5. Suspension : `deny` pour `module:*`, sauf `READ`.
6. `CODE_CHANGE` hors de `module:evolve` ou hors de l'espace isolé : `deny`.
7. Autorisation valide qui couvre l'action (principal, échéance, plafond de montant, nombre par jour, appareils ;
   une même action vérifiée deux fois, comme un achat à la préparation puis au paiement, ne compte qu'une fois) :
   `allow`.
8. Capacité sensible (`SPEND`, `DESTRUCTIVE`, `EXTERNAL_MESSAGE`, `CODE_CHANGE`) : `allow` si vous l'avez confirmée,
   sinon `ask`.
9. `delegated:*` : `allow` pour `READ`, `NOTIFY`, `CALL`, `CONTROL_DEVICE`, `WRITE_DATA`.
10. `module:*` en état actif : `allow` pour `READ` et `NOTIFY` (phase 1).
11. Sinon : `deny`.

**Échec sûr :** une exception du moteur vaut `deny`. Si le registre ne peut pas inscrire la décision, l'action
autonome n'a pas lieu ; la conversation, elle, continue.

## Interrupteur

| Changement | Voix | ORION Control |
|---|---|---|
| Vers plus strict (suspendre, arrêt d'urgence) | oui : `suspend_autonomy`, `emergency_stop` | oui |
| Vers moins strict (réactiver) | **jamais** | session authentifiée (IP autorisée et jeton) et phrase exacte « réactiver l'autonomie » ; confirmation, puis saisie de la phrase dans l'interface ; la phrase est vérifiée par le Core |

- **Défaut :** `suspended`.
- **Suspendre pendant un arrêt d'urgence** ne le lève pas.
- **Persistance :** l'état est relu en base à chaque décision.
- **Traçabilité :** chaque changement, et chaque refus de changement, est inscrit au registre.

## Points de contrôle (« toute action sensible passe par le moteur central »)

| Point | Principal | Capacité |
|---|---|---|
| Core des outils (`ToolCore.submit` et la réponse à une confirmation), par `FoundationGate` | `user`, `admin`, `delegated:routine` | déclarée par l'outil (`tool.capability`), sinon `WRITE_DATA` (outil sans risque) ou `DESTRUCTIVE` (outil à confirmer) |
| Routeur d'alertes v3 (chaque envoi, puis chaque appel) | `Alert.origin`, sinon `module:<source>` | `NOTIFY`, `CALL` |
| Alertes de WATCH | `delegated:watch:<id>` | `NOTIFY`, `CALL` |
| Appels programmés (règles d'appel, appel d'absence) : `CallManager.request` d'origine `routine` | `delegated:call` | `CALL` |
| PURCHASE : préparation automatique, puis paiement (montant final) | `delegated:purchase:<mandat>` | `SPEND` |
| EVOLVE : écriture d'un correctif | `module:evolve` | `CODE_CHANGE` (espace isolé) |
| Planificateur des modules : avant **chaque** tâche, pas seulement en début de tour | — | arrêt d'urgence ou état illisible : plus rien ne part |

## Registre d'actions

- **Ajout seul :**
  - la classe n'expose ni modification ni suppression ;
  - les déclencheurs SQLite refusent `UPDATE` et `DELETE`.
- **Chaînage :** `hash = SHA-256(hash précédent + contenu canonique)`. `verify()` détecte une ligne modifiée, une ligne
  supprimée au milieu et un chaînage rompu.
- **Contenu :** aucune valeur de paramètre, aucun texte privé (`loggable`) ; seuls le nom des paramètres, le montant,
  l'appareil et l'indicateur « espace isolé ».

## ORION Control

Section **Autonomie** :
- état ;
- trois boutons : Activer, Suspendre, Arrêt d'urgence ;
- autorisations, avec révocation ;
- registre (100 dernières lignes) ;
- intégrité de la chaîne et empreinte de tête.

Routes de l'API d'administration : `GET|POST /api/autonomy`, `GET /api/ledger`, `GET /api/ledger/verify`,
`GET /api/grants`, `DELETE /api/grants/<id>`.

## Écarts par rapport à la conception, et limites connues

1. **Raccord au Core des outils :** une enveloppe (`FoundationGate`) remplace le paramètre de `PermissionManager`
   prévu. `PermissionManager` et `ToolCore` ne sont pas modifiés.
2. **`grants.py` :** fusionné dans `permissions.py`.
3. **Arrêt d'urgence :** il refuse aussi la lecture aux origines autonomes (plus strict que « sauf la lecture »). Il
   bloque aussi le paiement d'un achat que vous confirmez pendant l'arrêt.
4. **Indicateur « confirmé »** (en F2 : confirmations liées à une demande, voir le lot F2) **:** les modules le passent eux-mêmes. Seuls les chemins utilisateur le fixent :
   `PurchaseEngine.confirm` et la route EVOLVE d'ORION Control. C'est une confiance interne au processus, pas une
   preuve. Lier chaque confirmation à son identifiant de demande est prévu en F2 et F3.
5. **Ce que le registre détecte :**
   - une modification naïve, mais pas une réécriture complète de la chaîne par quelqu'un qui a accès en écriture à
     la base : il faudrait une signature HMAC avec une clé extérieure (F4) ;
   - pas une troncature de la fin : l'empreinte de tête est affichée pour comparaison.
6. **Résultats d'exécution des outils :** ils ne sont pas inscrits au registre, seulement les décisions. Les résultats
   restent dans le journal d'activité.
7. **Automatismes V2 hors du Core des outils et des appels** (raccordés en F2) **:** les rappels qui sonnent, les annonces de mails et
   `EveWatch` parlent sans passer par le moteur. Ce sont des notifications à la voix, pas des actions sur le monde.
   Leur passage par le moteur est prévu avec le service de tâches (F2).
8. **NEWS (isolé)** (corrigé en F2, sans modifier NEWS) **:** ses alertes partent sous `module:news` ; plateforme activée et autonomie suspendue, elles sont
   donc bloquées. Faire passer les bulletins programmés en `delegated:news` demande une modification de NEWS, à
   coordonner. La plateforme étant désactivée par défaut, le développement de NEWS n'est pas affecté.
9. **Session « authentifiée » :** c'est l'authentification existante d'ORION Control (jeton et IP autorisée). Il n'y a
   pas d'ouverture de session propre à chaque utilisateur.
10. **Rotation du registre par ancienneté :** non faite (prévue plus tard, par export, jamais par suppression).
11. **Correction annexe :** `Store` découpe désormais correctement les migrations qui contiennent un déclencheur
    (`BEGIN … ; END`).

---

# Lot F2 : tâches persistantes et corrections de F1

## Fichiers

| Fichier | Rôle |
|---|---|
| `orion/platform/tasks.py` (nouveau) | `TaskService` : tâches persistantes, réservation atomique, reprise, expiration, échecs ; `TaskGate` (accès d'un module) |
| `orion/platform/confirmations.py` (nouveau) | `ConfirmationStore` : confirmation liée à une demande précise, à usage unique |
| `orion/platform/running.py` (nouveau) | principal de la tâche en cours (variable de contexte posée par la plateforme) |
| `orion/platform/permissions.py` | `blocked()` (état d'autonomie seul, sans registre) ; `decide(confirmation=…)` ; `GrantStore.is_active` |
| `orion/platform/scheduler.py` | classification de chaque tâche périodique (principal, capacité), retenue tâche par tâche |
| `orion/platform/build.py` | `classify_job` ; type de tâche `news.bulletin` |
| `orion/platform/alerts.py` | `Alert.principal` reprend le principal de la tâche en cours du même module |
| `orion/platform/ledger.py` | migration 2 (`key_id`, `signature`) ; `Keyring` (HMAC-SHA-256, rotation) |
| `orion/notifications/manager.py`, `voice.py` | annonces soumises au moteur central ; revérification juste avant de parler |
| `orion/scheduling/eve.py` | veille des rendez-vous soumise au moteur central |
| `orion/factory.py`, `api/server.py`, `control/*` | raccordements ; routes et vue d'ORION Control |
| `tests/test_platform_tasks.py` (nouveau) | 31 tests |

NEWS n'est **pas modifié** (aucun fichier de `orion/news/`), conformément à la coordination demandée ; un test le
vérifie.

## Tâches programmées par vous et initiatives des modules

| | Tâche programmée par vous | Initiative d'un module |
|---|---|---|
| Principal | `delegated:<type>:<tâche>` | `module:<nom>` |
| Création | `schedule_for_user(by=…)` : conversation (`user`), voix, ORION Control ; toute autre origine est refusée et inscrite au registre | `TaskGate.schedule` : principal fixé, seulement les types du module marqués `module=True` |
| Suspension | continue | retenue (sauf lecture), sans perdre d'essai ; repart à la reprise |
| Arrêt d'urgence | retenue | retenue |

La **capacité** d'une tâche vient de son type, enregistré par le code de la plateforme (`register`), jamais de celui
qui crée la tâche ; un type ne se réenregistre pas. Aucun module ne s'accorde donc une capacité en choisissant un type.

**Tâches périodiques des modules** (planificateur existant), classées par la plateforme (`classify_job`) :

| Tâche | Principal | Capacité |
|---|---|---|
| `news:bulletins` (heures que vous avez réglées) | `delegated:news:bulletins` | `NOTIFY` |
| `news:collect` (collecte des flux) | `module:news` | `READ` |
| `watch:<id>` (votre surveillance) | `delegated:watch:<id>` | `READ` |
| `evolve:scan` | `module:evolve` | `READ` |
| inconnue | `module:<préfixe>` | `NOTIFY` (la plus surveillée) |

Pendant son exécution, une tâche porte son principal (`orion.platform.running`). Une alerte du **même** module sans
origine déclarée le reprend. Ainsi le bulletin de 8 h part sous `delegated:news:bulletins` et continue pendant la
suspension. Une alerte spontanée, née de la collecte, reste `module:news` et est refusée pendant la suspension. C'est
la correction de classification demandée, faite sans toucher à NEWS.

## Exécution d'une tâche (`run_due`)

1. **Arrêt d'urgence ou état illisible :** rien ne part, tâches programmées comprises. C'est vérifié avant le tour
   et **avant chaque tâche** : un arrêt déclenché par la première tâche d'une file retient les suivantes.
2. **Reprise des interrompues** (`recover`, au démarrage puis à chaque tour) et expiration (`expires_at` dépassée :
   `expired`).
3. **Réservation atomique :** `UPDATE … WHERE state = 'pending' AND attempts = <lu>`, avec un bail daté (`timeout` du
   type). Deux fils ou deux processus sur la même base ne l'exécutent jamais tous les deux. Une copie périmée ne
   réserve plus rien.
4. **Décision du moteur central au moment de l'exécution :**
   - les autorisations sont relues, donc rien n'hérite d'une autorisation expirée ou révoquée depuis la création ;
   - une tâche liée à une autorisation (`grant_id`) échoue si celle-ci n'est plus valide, même si une autre la
     couvrirait ;
   - `allow` : exécution sous le principal de la tâche ;
   - `ask` : demande de confirmation liée, tâche `held` ;
   - retenue par l'état : retour en attente sans perdre d'essai ;
   - autre refus : `failed`.
5. **Résultat :**
   - succès : `done`, ou occurrence suivante pour une tâche périodique, sans rattrapage en rafale après un arrêt ;
   - échec : nouvel essai, avec une attente doublée à chaque fois (30 s, 1 min, 2 min…, plafonnée à 6 h), jusqu'à
     `max_attempts`, puis `failed`.
6. **Annulation pendant l'exécution :** elle l'emporte. Le résultat est ignoré et inscrit comme tel.

**Interruption brutale :** une tâche `running` dont le bail est échu a été interrompue (processus tué, coupure).
- Type déclaré **idempotent** : elle repart, et l'essai compte.
- Sinon : `interrupted`, **jamais relancée d'elle-même**, car l'action a pu avoir lieu. Vous la relancez (`retry`)
  ou l'annulez depuis ORION Control.
- Pour rendre idempotente une action extérieure, le code reçoit une clé unique par exécution (`TaskRun.run_key`).

**Doublons à la création :** une clé `key` (identifiant de la demande) rend une création répétée sans effet, par
exemple après un double clic ou un nouvel essai réseau.

**Traçabilité :**
- au registre d'actions : création (`task.create`), retenue (`task.hold`, une ligne par changement et non par tour),
  décision (`task:<type>`), reprise (`task.recover`), échec (`task.fail`), expiration (`task.expire`), annulation
  (`task.cancel`), relance (`task.retry`), résultat (`task.result`) ;
- une ligne par exécution dans `task_runs` (début, fin, issue, erreur) ;
- aucune valeur de paramètre : les noms seulement, et les résultats passent par `loggable`.

## Confirmation liée à une demande

`ConfirmationStore` :
- **Contenu :** une demande porte un identifiant, le principal qui agira, l'action, l'empreinte SHA-256 des
  paramètres, une échéance et un usage unique.
- **Cycle :**
  - `request` (le moteur ou le service de tâches) ;
  - `approve` : la voix, la conversation ou ORION Control ; un module ne le peut jamais, car il n'a pas accès au
    magasin ;
  - `consume` au moment d'agir : une seule mise à jour conditionnelle ; tout doit correspondre, la demande ne doit être
    ni expirée, ni refusée, ni déjà utilisée.
- **Moteur :** `decide(…, confirmation=<id>)` ne consomme la demande que si l'action est sensible et qu'aucune
  autorisation ne la couvre.
- **Tâche périodique :** chaque occurrence sensible demande une nouvelle confirmation.

ORION Control → Autonomie → « Confirmations en attente » (« Accorder une fois », « Refuser » ou « Retirer ») et
« Tâches » (« Confirmer », « Relancer », « Annuler »). Routes : `GET /api/tasks[/<id>]`,
`POST /api/tasks/<id>/cancel|retry|confirm`, `GET /api/confirmations`, `POST /api/confirmations/<id>/approve|refuse`.

## Annonces vocales et veille (corrections de F1)

`NotificationManager.permit` (posé seulement si `[platform]` est activé) soumet chaque annonce (`NOTIFY`) :

| Source | Principal | Suspendue | Arrêt d'urgence |
|---|---|---|---|
| `reminder`, `timer` | `delegated:reminder`, `delegated:timer` | dite | retenue |
| `calendar`, `routines`, `flex` | `delegated:calendar`, `delegated:routine`, `delegated:flex` | dite | retenue |
| `mail` (nouveaux mails, annonce réglée par vous) | `delegated:mail` | dite | retenue |
| `mail` « Échéance » (trouvée dans un mail) | `module:mail` (posé par le code d'ORION) | retenue | retenue |
| `alerts` (modules V3) | déjà décidée par le routeur d'alertes | — | — |
| autre | `module:<source>` | retenue | retenue |

- **Principal déclaré :** un principal posé dans les métadonnées n'est accepté que s'il est `module:` ou
  `delegated:`, jamais `user` ni `admin`.
- **File vocale :** le canal revérifie chaque annonce juste avant de la dire. Un arrêt d'urgence survenu entre-temps
  la retire (« l'arrêt d'urgence passe avant la file »).
- **Veille (`EveWatch`, « appelle-moi la veille ») :** principal `delegated:eve`, capacité `NOTIFY`. Refusée, elle est
  gardée et publiée au premier passage après la reprise, tant que l'élément n'est pas arrivé.

## Registre : limites du chaînage SHA-256 et évolution vers la signature

**Ce que le chaînage détecte :**
- une ligne modifiée, insérée ou supprimée au milieu, par quelqu'un qui ne recalcule pas la chaîne ;
- toute modification par l'application, puisque la classe n'expose ni modification ni suppression et que les
  déclencheurs refusent `UPDATE` et `DELETE`.

**Ce qu'il ne détecte pas** (un test le démontre : `test_hash_chain_alone_cannot_detect_a_full_rewrite_but_a_signature_does`) :
1. **Réécriture complète :** quelqu'un qui peut écrire dans `orion_ops.db` peut retirer les déclencheurs, modifier
   une ligne et recalculer tous les résumés. La chaîne reste alors cohérente, parce que SHA-256 n'a pas de secret.
2. **Troncature de la fin :** supprimer les dernières lignes laisse une chaîne valide. Seule la comparaison avec une
   empreinte de tête notée ailleurs la révèle.
3. **Remplacement du fichier** par une copie plus ancienne, cohérente : même effet.
4. **Horodatage :** c'est l'horloge locale ; il ne prouve pas la date.
5. **Portée :** la chaîne prouve ce qui a été inscrit, pas ce qui ne l'a pas été. Une action faite hors du moteur
   n'y figure pas ; c'est le rôle des tests de garde.
6. **Contenu :** décisions et noms seulement, sans valeur de paramètre ; le registre ne permet pas de rejouer une
   action.

**Préparé en F2 (inactif) :**
- **Migration 2 :** colonnes `key_id` et `signature`. Elles restent vides sans clé, et les lignes existantes ne sont
  pas touchées.
- **`Keyring` :** clés HMAC-SHA-256 de 32 octets au moins, `{identifiant: clé}` plus la clé courante. Sa
  représentation (`repr`) ne montre aucune clé.
- **Ce que signe `ActionLedger(…, keyring=…)` :** le résumé de chaque ligne.
- **Ce que `verify` refuse :**
  - une signature fausse ;
  - une clé inconnue ;
  - une ligne non signée après la première ligne signée (retour en arrière).
- **Lignes anciennes :** celles écrites avant la signature restent acceptées.
- **Rotation :** les anciennes clés restent dans le trousseau pour vérifier leurs lignes, et seule la clé courante
  signe.

**À faire dans le lot chiffrement (F4), sans création de clé réelle avant votre accord :**
1. Clé fournie par un identifiant systemd chiffré (`LoadCredentialEncrypted`), hors de la base et du dépôt, jamais
   écrite dans les journaux. Copie dans le gestionnaire de mots de passe et copie hors ligne, comme décidé pour la clé
   de données. Ce sera de préférence une clé distincte de celle des données.
2. Ligne de scellement à l'activation : première ligne signée, qui reprend l'empreinte de tête non signée, donc toute
   la chaîne antérieure.
3. Ancrage externe périodique de l'empreinte de tête (message écrit, fichier sur une autre machine), contre la
   troncature et le remplacement du fichier.
4. Procédure de rotation et de restauration, testée avant tout usage avec des données réelles.
5. Limite qui restera : la clé HMAC vit dans le processus d'ORION. Quelqu'un qui contrôle ce processus peut signer.
   Une signature asymétrique, avec une clé hors de la machine ou un ancrage externe, est la seule parade complète.
   À arbitrer en F4.

## Tests (F2)

`tests/test_platform_tasks.py`, 31 tests sur des données factices :

| Exigence | Test |
|---|---|
| Redémarrage avec des tâches en attente | `test_pending_tasks_survive_a_restart_and_run_once` |
| Interruption pendant l'exécution | `test_interrupted_non_idempotent_task_is_never_rerun_by_itself`, `test_interrupted_idempotent_task_resumes_after_its_lease` |
| Double exécution | `test_two_processes_never_run_the_same_task_twice` (deux processus, deux connexions, en même temps), `test_a_stale_claim_cannot_be_reused`, `test_creation_is_deduplicated_by_key` |
| Révocation pendant l'attente | `test_revoking_a_grant_while_the_task_waits_blocks_it`, `test_an_expired_grant_is_never_inherited` |
| Arrêt d'urgence pendant une file | `test_emergency_stop_during_a_queue_stops_the_rest_and_resumption_goes_through_suspended`, `test_emergency_stop_takes_priority_over_the_voice_queue` |
| Reprise après retour à SUSPENDED | même test (puis l'initiative attend `active`), `test_eve_watch_is_held_on_emergency_stop_and_published_after_resumption` |
| Bulletin NEWS programmé pendant la suspension | `test_a_scheduled_news_bulletin_runs_while_suspended_but_a_spontaneous_alert_is_refused` (vrai moteur NEWS, flux simulés), `test_a_one_off_bulletin_you_schedule_is_a_persistent_task_that_runs_while_suspended` |
| Initiative spontanée de NEWS refusée | même test (alerte « bloquée », registre `module:news` `deny`) |
| Expirées et échouées | `test_expired_and_failed_tasks_are_handled_and_traced` |
| Confirmation liée | `test_confirmation_is_single_use_bound_to_principal_action_params_and_expiry`, `test_held_task_runs_once_with_a_confirmation_bound_to_it` |
| Distinction des origines | `test_user_tasks_and_module_initiatives_are_distinct_and_cannot_be_forged`, `test_suspension_holds_module_initiatives_without_consuming_attempts` |
| Annonces vocales | `test_reminders_and_mail_announcements_go_through_the_engine`, `test_without_the_foundation_notifications_are_unchanged` |
| Registre | `test_hash_chain_alone_cannot_detect_a_full_rewrite_but_a_signature_does`, `test_signature_rotation_unknown_keys_and_unsigned_rows`, `test_ledger_v1_database_is_migrated_without_touching_existing_rows` |

## Écarts par rapport à la demande, et risques résiduels (F2)

1. **Les tâches existantes ne sont pas migrées dans `TaskService` :**
   - rappels et minuteurs V2 : base JSON de V2, production intouchée ;
   - routines ;
   - tâches périodiques des modules : recréées au démarrage, comme avant.

   `TaskService` est l'infrastructure. Il porte aujourd'hui les bulletins NEWS ponctuels (`news.bulletin`). Les
   tâches périodiques des modules sont classées et retenues par le planificateur, mais ne sont pas persistées une à
   une. Leur migration est proposée en F3, avec les événements normalisés.
2. **Annonce refusée pendant l'arrêt d'urgence :** un rappel qui sonne pendant l'arrêt n'est pas dit plus tard. Il
   est inscrit au registre (`deny`) et dans le journal d'activité (`notification.failed`). Seule la veille
   (`EveWatch`) est gardée pour la reprise. À arbitrer : rejouer les rappels manqués à la reprise, ou non.
3. **Annonce des nouveaux mails classée `delegated:mail` :** elle continue pendant la suspension, parce que vous
   l'avez réglée (`[mail] announce`). Seule l'échéance trouvée dans un mail est une initiative. Si vous préférez la
   suspendre aussi, c'est une ligne (`SOURCE_PRINCIPALS`).
4. **Une tâche en cours n'est pas interrompue** par l'arrêt d'urgence. Python ne permet pas de tuer un fil
   proprement. L'arrêt retient tout ce qui n'a pas commencé et toute action suivante, chaque action étant revérifiée.
   Une tâche longue doit donc découper son travail en actions soumises au moteur.
5. **Durée du bail :** une tâche plus longue que le `timeout` de son type serait jugée interrompue par un autre
   processus. Un seul processus ORION utilise la base aujourd'hui. Il faudra un renouvellement du bail si plusieurs
   processus partagent un jour les tâches.
6. **Classification par contexte :** pendant `news:bulletins`, une alerte de NEWS qui ne serait pas un bulletin
   partirait elle aussi sous `delegated:news:bulletins`. C'est le code de NEWS qui décide de ce qu'il émet dans cette
   tâche, et aujourd'hui il n'y émet que le bulletin. La correction propre (`origin=` sur l'alerte du bulletin, une
   ligne dans NEWS) attend votre coordination avec NEWS.
7. **`confirmed=True` (F1) reste accepté** pour les chemins utilisateur existants (achat confirmé, EVOLVE depuis ORION
   Control). Le passage de ces chemins à des confirmations liées est prévu en F3.
8. **Signature du registre :** préparée et testée avec une clé factice, mais inactive. Aucune clé réelle n'a été
   créée.

---

# Lot F3 : événements normalisés, notifications, annulation coopérative

Livré sur `v3` le 3 novembre 2026, développé et testé sans déploiement (voir `docs/v3/progression.md`).

## Fichiers

| Fichier | Rôle |
|---|---|
| `orion/platform/eventhub.py` (nouveau) | `Event`, `EventHub`, `EventGate`, `InterruptionPolicy` |
| `orion/platform/alerts.py` | canaux choisis par `InterruptionPolicy.route` ; chaque alerte inscrite au hub (`record`) |
| `orion/notifications/manager.py` | `attach_hub` : les notifications V2 passent par le hub (décision, remise) |
| `orion/platform/tasks.py` | annulation coopérative, effets revérifiés, bail renouvelé |
| `orion/platform/foundation.py` | hub, récapitulatif à la reprise, outils vocaux, routes `/api/events` |
| `orion/news/engine.py` | origine déclarée des bulletins et des alertes spontanées |
| `tests/test_platform_events.py` (nouveau), `tests/test_platform_tasks.py` | 21 + 8 tests |

## Événement, décision, remise

Les trois sont séparés et gardés dans `orion_ops.db` :

1. **Événement** (`events`) :
   - **Contenu :** type `domaine.nature`, source, principal, titre, texte (privé ou non), priorité (`low`, `normal`,
     `high`, `critical`), clé de dédoublonnage, échéance de validité, données.
   - **Provenance :** fixée par le code d'ORION. Un module (NEWS, SENTINEL…) publie par un `EventGate` à identité fixe,
     limité à ses propres types ; il ne peut jamais publier sous `user`.
2. **Décision** (`event_decisions`), une seule règle, appliquée dans cet ordre :
   1. échéance passée : `expired` ;
   2. moteur central (`NOTIFY`) :
      - refus pour arrêt d'urgence ou état indéterminé : `missed` ;
      - autre refus : `denied` ;
   3. priorité basse : `kept` ;
   4. heures calmes :
      - `deferred` jusqu'à leur fin ;
      - `kept` si l'échéance tombe avant ;
      - sauf ce dont vous avez fixé l'heure (rappel, minuteur, agenda, routine) et le critique si c'est réglé ;
   5. sinon `announce`.

   Événement répété avec la même clé dans les 12 h : `duplicate`.
3. **Remise** (`event_deliveries`) :
   - par canal ;
   - en cas d'échec, nouvel essai (30 s, 1 min, 2 min…, plafonné à 1 h) jusqu'à l'échéance, y compris après un
     redémarrage d'ORION ;
   - permission revérifiée juste avant chaque remise ;
   - jamais de remise après l'échéance.

Le routeur d'alertes des modules garde ses canaux (voix, écrit, appel, budget d'appels), mais les choisit par la même
règle (`InterruptionPolicy.route`) et inscrit chaque alerte et sa décision au hub.

## Notifications manquées (arrêt d'urgence)

| | Pendant SUSPENDED | Pendant EMERGENCY_STOP | À la reprise |
|---|---|---|---|
| Rappels, minuteurs | dits | gardés `missed` | récapitulatif, relance manuelle si non obsolète |
| Annonces des nouveaux mails | dites | gardées `missed` | récapitulatif ; jamais d'annonce rétroactive de tous les mails |
| Alertes des modules | selon leur origine | gardées `missed` | idem |

- **Jamais de rejeu automatique :** ni la remise différée, ni la reprise ne rejouent un événement manqué.
- **Récapitulatif :** il est donné une seule fois, au premier état moins strict après l'arrêt d'urgence (« Pendant
  l'arrêt d'urgence, vous avez manqué : 2 rappels, 1 annonce de mail. »).
- **Relance :** voix (« mes notifications manquées », « relance la deuxième ») ou ORION Control (Autonomie →
  Notifications manquées) ; un module ne le peut jamais.
- **Obsolète :** échéance passée. Rappel : 6 h ; minuteur et routine : 1 h ; rendez-vous : 2 h ; mail : 3 jours. Une
  notification obsolète n'est jamais rejouée.

## Classification des alertes NEWS

- L'origine vient de l'identité et du type réel de ce qui émet :
  - bulletin : `delegated:news:bulletins` (ou le principal du bulletin ponctuel) ;
  - alerte spontanée : `module:news`, toujours.
- `Alert.principal` ne lit plus jamais le contexte de la tâche appelante : `principal_for` est retiré. Un test
  déclenche une alerte spontanée pendant un bulletin programmé : elle est refusée pendant la suspension, le bulletin
  passe.

## Annulation coopérative et bail

- `cancel` sur une tâche en cours : état `cancel_pending`, jamais annoncé comme fait.
- La tâche s'arrête à son prochain `TaskRun.checkpoint()`, qui lève `TaskCancelled`. Elle passe alors `cancelled`,
  avec l'inscription `task.stopped`.
- `TaskRun.effect(capability, …)` : point de contrôle, puis décision du moteur central juste avant chaque effet
  extérieur (`PermissionDenied` si refusé).
- **Tâche terminée sans point de contrôle :** elle reste `done` (ou `cancelled` si périodique, sans occurrence
  suivante), avec le message « n'a pas pu s'interrompre » et l'inscription `task.cancel_late`.
- **Arrêt d'urgence :** toutes les tâches en cours passent `cancel_pending`. La réponse vocale dit combien tournent
  encore ; elle ne prétend jamais que tout est arrêté.
- **Bail :** un fil le renouvelle pendant l'exécution (tous les `timeout`/3). Une tâche active n'est jamais reprise par
  un autre processus ; seul un processus arrêté laisse son bail échoir.
- **Processus arrêté pendant une annulation :** la tâche passe `cancelled`, avec « résultat inconnu ».

## Écarts et risques résiduels (F3)

1. **Annonces V2 sans canal écrit :** elles n'ont que la voix. Une notification V2 différée par les heures calmes est
   dite à leur fin, pas écrite. Les alertes des modules gardent l'écrit et l'appel.
2. **Présence ignorée :** la règle ne s'en sert pas encore pour les notifications V2, comme avant F3. Un rappel est dit
   même si la maison est vide. À arbitrer : le garder pour votre retour ?
3. **Point de contrôle volontaire :** l'annulation coopérative ne sert que si le code de la tâche appelle
   `checkpoint` ou `effect`. Les types existants (`news.bulletin`) sont courts ; une tâche longue doit le faire.
4. **Planificateur des modules :** ses tâches périodiques ne passent pas encore par `TaskService` (pas de
   `cancel_pending` pour elles). L'arrêt d'urgence les retient avant chaque tâche, comme en F1.
5. **Échéances par défaut :** celles des notifications sont fixées par source, et non réglables dans ORION Control.

# Lot F4 : stockage séparé et chiffrement des données personnelles

## Trois bases, trois classes de données

| Base | Classe | Contenu | Chiffrement |
|---|---|---|---|
| `orion_ops.db` | opérationnelle | registre, autonomie, autorisations, confirmations, tâches, événements | textes personnels **scellés** |
| `orion_v3.db` | publique | actualités, surveillances, prix, calendrier d'événements publics, EVOLVE, achats (bac à sable), alertes | aucun |
| `orion_personal.db` | personnelle | faits, futurs imports (KNOWLEDGE, Takeout) | chaque enregistrement chiffré |

- `Store(path, data_class)` refuse une table d'une autre classe (`DATA_CLASSES`, `orion/platform/store.py`) : une
  table personnelle ne peut pas atterrir par erreur dans la base opérationnelle ou publique.
- **Textes personnels de la base opérationnelle** (`Sealer`, `orion/platform/vault.py`) : titre, texte et données
  d'un événement (rappel, expéditeur d'un mail), résumé d'une confirmation, paramètres et résultat d'une tâche
  déclarée `personal=True`. Trois modes :
  - `clair` : base personnelle non configurée (`[platform] personal = false`) : comportement de F3 ;
  - `scellé` : chiffrés avec la clé des données personnelles, liés à leur place ; les clés de dédoublonnage
    deviennent des étiquettes HMAC (égalité vérifiable, texte jamais gardé) ;
  - `retenu` : base personnelle configurée mais clé absente : le texte n'est **jamais** écrit. Il reste en mémoire
    jusqu'au redémarrage, puis s'affiche « contenu indisponible ». Une tâche personnelle est refusée à la création,
    et une tâche personnelle déjà programmée attend la clé sans s'exécuter.

## Chiffrement

- AES-256-GCM (bibliothèque `cryptography`, déjà utilisée par la passerelle d'appel), nonce aléatoire de 96 bits.
- Données associées : `personal:<collection>:<identifiant>` ou `events:<id>:title`… Un chiffré recopié à une autre
  place, ou modifié d'un seul bit, est refusé.
- Chaque chiffré commence par l'identifiant de sa clé (rotation). Sous-clés dérivées par HKDF-SHA-256 : chiffrement,
  étiquettes, empreinte. L'empreinte (8 octets) identifie une clé dans ORION Control sans la révéler.
- En clair dans la base personnelle : collection, identifiant (opaque), identifiant de clé, dates. Jamais le contenu.
- Un témoin chiffré par clé détecte une clé fausse dès l'ouverture : la base reste alors verrouillée, sans jamais
  produire de faux déchiffrement.

## Gestion de la clé

- **Format** : trousseau JSON `{"current": "k20261103", "keys": {"k20261103": "<32 octets en base64>"}}`.
- **En service** : la clé arrive par les identifiants de systemd, jamais par `.env`, l'environnement, Git ou les
  journaux. Pour l'environnement V3, l'unité contient `ImportCredential=orion-v3.data_key`. Le trousseau se range,
  chiffré par la machine, dans `/etc/credstore.encrypted/orion-v3.data_key` :

  ```bash
  sudo systemd-creds encrypt --name=orion-v3.data_key <trousseau> /etc/credstore.encrypted/orion-v3.data_key
  ```

  - systemd le déchiffre au démarrage, avec la clé de la machine, dans un dossier en mémoire que seul le service lit.
  - Le compte `orion-v3` ne lit jamais ce fichier ni la clé de la machine.
  - La production aura son propre identifiant (`orion.data_key`) : les deux environnements ne partagent jamais de clé.
- **Aucune clé réelle n'a été créée.** Sans identifiant, la base est verrouillée et ORION fonctionne normalement.
  Créer la vraie clé reste votre décision, et se fait avec `generate`.
- **Copie hors ligne obligatoire** : le trousseau lui-même (44 caractères par clé), sur papier ou dans un gestionnaire
  de mots de passe. C'est la seule protection contre la perte de la machine.

## Outil d'administration (`python -m orion.platform.datakey`)

| Commande | Effet |
|---|---|
| `generate <trousseau>` | nouveau trousseau (fichier 600, jamais écrasé) ; affiche identifiant et empreinte |
| `show <trousseau>` | identifiants et empreintes, jamais les clés |
| `rotate <trousseau>` | ajoute une clé et la rend courante ; les anciennes restent pour lire |
| `rewrap <trousseau> <base>` | rechiffre tout avec la clé courante (service arrêté) |
| `retire <trousseau> <id> <base>` | retire une ancienne clé, refusé tant qu'un enregistrement en dépend |
| `verify <trousseau> <base>` | déchiffre tout, compte les erreurs (identifiants seulement) |
| `backup <trousseau> <base> <dossier>` | sauvegarde chiffrée et manifeste ; refusée dans un dossier qui contient une clé |
| `restore <trousseau> <manifeste> <base>` | restauration vérifiée ; la base en place est gardée à côté |

## Rotation de la clé

1. `rotate` : nouvelle clé courante. Les nouveaux enregistrements l'utilisent aussitôt, les anciens restent lisibles.
2. Le trousseau mis à jour remplace l'identifiant systemd, puis le service redémarre.
3. `rewrap`, service arrêté. La route `POST /api/personal/rotate` le fait aussi par lots de 500, service en marche.
4. `retire` de l'ancienne clé : refusé tant qu'un enregistrement en dépend.
5. Nouvelle copie hors ligne du trousseau. L'ancienne copie peut être détruite après une restauration d'essai.

Après une rotation, une sauvegarde faite avant reste lisible avec l'ancien trousseau, d'où l'intérêt de le garder
tant que cette sauvegarde existe.

## Sauvegarde (compatible NAS, sans dépendre d'un modèle)

- `backup` copie la base à chaud (API de sauvegarde de SQLite) dans un **dossier quelconque** : NAS monté en SMB ou
  NFS, disque externe, clé USB. Elle écrit à côté un manifeste : empreinte SHA-256, nombre d'enregistrements, clés
  utilisées, date. Le fichier ne contient que des chiffrés, et le NAS ne voit jamais rien en clair.
- **La clé n'est jamais à côté** : refus si le dossier contient un trousseau ou est le dossier des identifiants. La
  clé de la machine qui protège l'identifiant systemd reste sur le mini-PC ; la copie hors ligne est ailleurs.

## Restauration

1. Service arrêté.
2. `restore <trousseau> <manifeste> <base>`. L'outil vérifie l'empreinte SHA-256, copie la sauvegarde dans un
   fichier provisoire et déchiffre chaque enregistrement. Le nombre doit correspondre au manifeste.
3. Seulement ensuite il installe la base. Une base déjà en place est renommée `….avant-restauration-<date>`, jamais
   écrasée.
4. Mauvaise clé, sauvegarde altérée ou enregistrement illisible : refus, et rien n'est installé.

## Perte de la clé : conséquences

- **Sans le trousseau, ni la base personnelle ni ses sauvegardes ne peuvent être lues, par personne, y compris
  ORION.** Il n'existe aucune porte dérobée ni clé de secours.
- Ce qui reste : la base opérationnelle, la base publique et toute la V2. ORION démarre et converse. Les données
  personnelles sont « verrouillées », et les textes des événements scellés deviennent « contenu indisponible ».
- Perte de la machine seule : copie hors ligne remise en identifiant systemd, puis sauvegarde restaurée.
- Perte de la machine **et** de la copie hors ligne : données personnelles perdues. Il faut repartir d'une base vide,
  puis réimporter depuis les sources d'origine, Takeout par exemple, si elles existent encore.

## Migration progressive et réversible

- `PersonalStore.import_json(collection, fichier, identifiant)` importe une liste JSON, par exemple `memory.json`.
  - Elle ne modifie jamais la source.
  - Tout se fait dans une seule transaction, avec trace de l'empreinte de la source.
  - Elle est idempotente : un même élément garde le même identifiant, donc deux passages donnent le même résultat.
- `export_json` est l'export inverse. Il produit le fichier d'origine **octet pour octet** : ordre des éléments,
  ordre des clés, mise en forme d'ORION. Testé sur une mémoire factice produite par `MemoryStore`.
- Rien n'est migré aujourd'hui. `memory.json` reste celui de la V2, et la bascule de la mémoire vers la base
  personnelle se fera avec KNOWLEDGE, dans l'ordre suivant :
  1. import ;
  2. double lecture et comparaison ;
  3. bascule ;
  4. JSON régénéré par l'export tant que le retour arrière reste possible.

## Test complet demandé

`test_full_cycle_encrypt_stop_restart_recover_key_and_decrypt`, avec un vrai processus Core par étape, une clé
factice remise comme par systemd (`CREDENTIALS_DIRECTORY`) et des données factices :

1. démarrage, écriture d'un fait personnel et d'un événement : chiffrés, puis arrêt ;
2. aucun octet du texte en clair dans les fichiers de données (base et journal WAL) ;
3. redémarrage : déchiffrement ;
4. clé perdue : base verrouillée, événement « contenu indisponible », aucun contenu ;
5. autre clé : verrouillée, aucun faux déchiffrement ;
6. récupération, en remettant la copie hors ligne : tout est de nouveau lisible.

Avec systemd réel : une fois l'environnement V3 installé, le même cycle peut être rejoué avec une clé **factice**
rangée par `systemd-creds` (demande `sudo`), puis la clé factice est retirée. Rien n'a été fait sans votre accord.

## Rappels dans une maison vide (décision du 3 novembre 2026)

- Un rappel ou un rendez-vous (`RETURN_SOURCES`) qui survient quand la présence dit la maison **sûrement vide** n'est
  pas prononcé. Il reçoit la décision `awaiting_return`, et sa remise attend votre retour.
- Même chose s'il était différé par les heures calmes et que vous êtes parti entre-temps.
- **Au retour** (`ARRIVAL_CONFIRMED` de la présence) :
  - ce qui est encore valable est dit, une fois ;
  - ce qui est devenu obsolète (échéance dépassée : 6 h pour un rappel, 2 h pour un rendez-vous) n'est jamais
    rejoué. Il reste consultable parmi les notifications manquées, marqué « obsolète ».
- Présence inconnue (capteurs absents ou illisibles) : annoncé comme avant. On ne se tait que si la maison est sûrement
  vide.
- Les mails gardent leur propre règle (la V2 exige déjà la présence), comme les minuteurs et les routines.

## Écarts et risques résiduels (F4)

1. **Surveillances et achats en base publique** : les sujets surveillés et les mandats d'achat (bac à sable) disent
   quelque chose de vos goûts. Ils restent dans `orion_v3.db` pour l'instant ; ils passeront dans la base
   personnelle avec KNOWLEDGE.
2. **Messages d'erreur des tâches** (`last_error`) : non scellés. Une tâche personnelle qui échoue avec un message
   contenant votre texte le garderait en clair. Les types personnels doivent lever des erreurs sans contenu.
3. **Empreinte des paramètres d'une confirmation** : SHA-256 sans clé. Des paramètres très courts (un montant)
   pourraient être retrouvés par essais. Le résumé, lui, est scellé.
4. **Pas de chiffrement du fichier entier** : SQLCipher n'est pas disponible. Collections, dates et nombre
   d'enregistrements restent visibles dans la base personnelle, mais jamais leur contenu.
5. **Clé en mémoire du service** : un code qui tourne sous le compte du service peut la lire pendant qu'il tourne.
   F5 prévoit les comptes séparés (`orion-import`, `orion-evolve`), qui n'y auront pas accès.
6. **Rotation et dédoublonnage** : après une rotation, un événement identique à un autre publié juste avant n'est
   plus reconnu comme doublon (étiquette d'une autre clé). La fenêtre est de 12 heures.

# Lot F5 : ressources, mode dégradé, comptes séparés

## Service de ressources (`orion/platform/resources.py`)

| Ressource | Sonde | Sans elle |
|---|---|---|
| `llm.primary` (Katana) | état du LLM à secours (`degraded`) | Core : LLM de secours du mini-PC, puis règles. NEWS : synthèses simples. WATCH : demandes comprises par règles. EVOLVE : diagnostic par règles |
| `stt.gpu` (Katana) | connexion TCP à `[stt] remote` | Whisper local, plus lent |
| `agent.pc`, `agent.portable` | connexion TCP à l'agent | actions sur ce PC refusées avec une explication |
| `web.search` (SearXNG) | connexion TCP | Core : « je ne peux pas chercher ». WATCH : flux et pages déclarés seulement |
| `storage.nas` (facultatif) | dossier `[platform] nas_path` présent et inscriptible | aucun effet : rien d'opérationnel sur le NAS ; sauvegardes chiffrées vers un autre dossier |

- `available(nom)` : réponse gardée quelques secondes à quelques minutes selon la ressource. La sonde tourne dans un
  fil borné à 3 s : un partage réseau figé ne bloque jamais ORION. Une sonde en erreur, une ressource inconnue ou non
  configurée valent « indisponible ».
- Chaque passage de disponible à indisponible (et retour) est daté et journalisé ; ORION Control (vue Autonomie,
  « Ressources ») montre l'état, depuis quand, et ce que fait chaque partie d'ORION sans la ressource.
- `busy()` : conversation en cours (`Agent.conversing`), processeur au-delà de 85 % ou mémoire disponible sous 15 %.
  Une tâche déclarée `heavy=True` (imports, EVOLVE niveau 2) attend alors dans `TaskService`. Elle ne compte ni un
  échec ni un essai.
- Route `GET /api/resources`.
- NAS : n'importe quel modèle et protocole, du moment qu'il est monté comme un dossier. Aucun n'est configuré
  (`nas_path` vide : « non configuré »).

## Tests « ressource absente »

- **Core** : LLM principal hors ligne, transcription non configurée, Web coupé, NAS absent. Le service de ressources
  répond pour chacune, chaque ressource déclare son comportement de repli, et la fondation démarre
  (`test_platform_resources.py`). Démarrage et conversation réels sans NAS ni transcription GPU : environnement V3
  (`/discuter`).
- **NEWS** : sans LLM, la collecte, le regroupement, le score, les alertes (`test_news.py`, mode dégradé) et le
  journal (fiches résumées par la source) fonctionnent.
- **WATCH** : sans Web, la source `web` n'est pas créée, et les sources déclarées restent. Une source en panne
  marque sa surveillance « en échec », puis la reprise la relance (`test_watch.py`).
- **EVOLVE** : sans LLM, l'analyse du journal se fait par règles.
- **PURCHASE** : aucune ressource extérieure (bac à sable).

## Zones d'attente (`orion/platform/staging.py`)

Les comptes `orion-import` et `orion-evolve` ne touchent ni aux bases, ni au dépôt en service, ni à la clé. Ils
déposent des **lots** :
- un fichier JSON, déposé de façon atomique (nom provisoire, puis renommage) ;
- son contenu : type déclaré, source, date, éléments et empreinte SHA-256 des éléments.

Le Core vérifie chaque lot. Il refuse :
- tout ce qui n'est pas un fichier ordinaire (lien symbolique compris) ;
- un fichier déposé par un autre propriétaire que le compte de la zone ;
- plus de 5 Mo ou plus de 5000 éléments ;
- un format inconnu, un type non déclaré, une empreinte fausse, un élément hors schéma.

Un lot refusé va en quarantaine avec sa raison, jamais supprimé. Un lot accepté est remis à son destinataire, puis
rangé dans `done/`. Si le destinataire ne peut pas le prendre (base personnelle verrouillée), le lot attend, intact.

## Comptes `orion-import` et `orion-evolve` (`deploy/v3_accounts.sh`, **non installés**)

- Comptes système sans connexion, dans aucun groupe d'ORION.
- Zones : `/var/lib/orion-v3/staging/import` et `/var/lib/orion-v3/staging/evolve` (2770). Seul le compte de la
  zone y écrit ; `orion-v3` les lit par leur groupe et range les lots traités (`ReadWritePaths` dans un ajout à son
  unité).
- Espace de travail d'EVOLVE : `/var/lib/orion-v3/evolve` (700).
- Contrôle en fin de script : ni l'un ni l'autre ne lit les données V3, la base personnelle, le `.env`, les
  identifiants chiffrés ou la production. Aucun des deux ne lit la zone de l'autre.
- Essai complet en conteneur hors réseau : création, contrôles, dépôt par `orion-import`, lecture et rangement par
  `orion-v3`, écriture refusée à `orion-evolve` dans la zone d'import, relance idempotente.
- Installation : votre décision, plus tard (`sudo bash /home/user/orion-v3/deploy/v3_accounts.sh`). Rien ne l'utilise
  encore : les imports (Takeout) attendent votre accord, et EVOLVE niveau 2 attend SENTINEL.

## Écarts et risques résiduels (F5)

1. Les sondes des agents et de la transcription vérifient une connexion TCP, pas le service lui-même (jeton,
   modèle chargé). Un service ouvert mais en erreur reste « disponible » ; l'erreur est alors traitée par le
   composant, qui a déjà son propre repli (Whisper local, message d'erreur de l'agent).
2. `busy()` regarde la charge moyenne sur une minute : un pic court ne diffère pas une tâche lourde déjà commencée.
3. Le planificateur des modules ne consulte pas encore `busy()`. Ses tâches sont courtes, mais une collecte NEWS peut
   tomber pendant une conversation.
