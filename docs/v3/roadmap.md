# ORION V3 — arbitrages et feuille de route validée

Mise à jour du 2 novembre 2026 après vos arbitrages. Elle complète `architecture_cible.md` (cible et interfaces) et
`news_personal_intelligence.md` (journal personnalisé).

**Aucune implémentation n'est commencée.** NEWS en cours n'est pas modifié, la production V2 n'est pas touchée, rien
n'est déployé, aucune connexion Google n'est demandée et aucune clé réelle n'est créée.

## 1. Décisions enregistrées

| # | Sujet | Décision |
|---|---|---|
| 1 | Google | Takeout d'abord ; Data Portability API plus tard ; YouTube API secondaire. Aucun accès Google réel sans votre validation explicite ; aucun scraping, aucun contournement des restrictions des API. |
| 2 | Données personnelles | Archive Takeout brute supprimée après import vérifié. Interactions gardées 180 jours, puis seulement des tendances agrégées. Consultation, correction et suppression possibles ; provenance et date toujours conservées. |
| 3 | Journal du matin | 07 h 30. 60 % d'actualité générale, 40 % de vos centres d'intérêt. Rubrique Sciences ajoutée ; International et Économie gardées. Tout reste réglable. |
| 4 | `ORION_DATA_KEY` | Hors du dépôt Git ; jamais dans les journaux ; permissions système restrictives ; sauvegarde sécurisée ; procédure écrite de rotation et de récupération. Aucune clé réelle créée sans votre validation. |
| 5 | NAS | Stockage externe facultatif : archives, documents, jeux de données, modèles inactifs, sauvegardes. Les bases opérationnelles restent locales. Une perte du NAS n'interrompt jamais ORION Core. |
| 6 | Autonomie | Suspendue par défaut ; vous seul l'activez. Phase 1 : lecture, analyse, notification. Actions extérieures, destructrices, financières ou irréversibles : autorisation adaptée. Interrupteur indépendant, sans arrêter l'assistant conversationnel. |
| 7 | Migration JSON → bases | Progressive, sauvegardée, vérifiable, idempotente, réversible. Aucun ancien fichier supprimé avant validation complète. |
| 8 | Correctifs V2 | Audit et liste (§ 7) ; aucune fusion automatique de `V2` dans `v3`. |
| 9 | Comptes système | Compte dédié pour EVOLVE et pour les imports de données personnelles ; moindre privilège ; exécution isolée. |

## 2. Contradictions techniques relevées, et résolution proposée

Chaque point oppose deux décisions, ou une décision et le code existant. La résolution proposée est appliquée dans
les documents ; celles marquées **(à confirmer)** sont reprises au § 6.

1. **Autonomie suspendue par défaut / automatismes déjà en service.**
   - V2 fait déjà des choses sans qu'on lui parle : rappels, routines, règles d'appel, annonces de mails,
     `AwayCallTrigger`, `EveWatch`. En v3, WATCH et NEWS vérifient et notifient seuls.
   - Si « autonomie » couvrait tout cela, la suspendre par défaut casserait la V2 et empêcherait de valider NEWS
     (première étape de la feuille de route).
   - **Résolution (à confirmer) :** deux origines distinctes.
     - `delegated:<id>` : ce que **vous** avez programmé (un rappel, une routine, une surveillance créée, l'heure du
       journal). Ce n'est pas de l'autonomie. Ces tâches tournent avec les droits fixés à leur création.
     - `module:<nom>` : ce qu'un module **décide de lui-même** (proposer un nouveau sujet, relancer, agir sur une
       détection). C'est l'autonomie, couverte par l'interrupteur.
   - L'interrupteur peut aussi, au choix, mettre en pause les tâches déléguées (« tout suspendre »). La conversation
     reste disponible dans tous les cas.
2. **60 % / 40 % du journal / formule de score.**
   - L'étude proposait un score `importance × 0,6 + intérêts × 0,4`. Votre décision porte sur la **composition** :
     60 % des places pour l'actualité générale, 40 % pour vos intérêts.
   - **Résolution :** deux listes classées séparément (importance pour la générale, correspondance pour la
     personnelle), puis quotas 60/40 arrondis. Une information « à ne pas manquer » entre toujours, même au-delà du
     quota.
   - **Tant que le moteur d'intérêts n'existe pas** (lot P1), les 40 % reviennent à l'actualité générale. Le journal
     n'invente pas d'intérêts.
3. **NAS pour les archives / archive Takeout supprimée après import.**
   - **Résolution :** l'archive Takeout ne va **jamais** sur le NAS. Elle reste dans une zone d'attente locale (compte
     d'import), puis est effacée après vérification.
   - « Archives » sur le NAS désigne les archives de documents et les sauvegardes, pas les imports bruts.
4. **Sauvegardes sur le NAS / données personnelles chiffrées.**
   - **Résolution :** toute sauvegarde de la base personnelle part chiffrée. La clé n'est **jamais** stockée à côté
     (ni sur le NAS, ni dans la même sauvegarde).
5. **`ORION_DATA_KEY` dans `.env` / permissions restrictives.**
   - Le `.env` de `/opt/orion` est lisible par tout le service, et donc par tout code qui tourne sous le compte
     `orion`. EVOLVE et les imports ne doivent pas y avoir accès.
   - **Résolution :** la clé passe par les identifiants de systemd (`LoadCredential=`), depuis un fichier
     `/etc/orion/credentials/data_key` (root, droits 600). Le service la lit dans `$CREDENTIALS_DIRECTORY`, jamais dans
     l'environnement.
   - En développement et en test : une clé factice générée par les tests. Jamais de clé réelle.
6. **Compte dédié aux imports / écriture dans la base personnelle.**
   - Deux comptes système qui écrivent dans la même base SQLite (WAL) se heurtent aux droits des fichiers `-wal` et
     `-shm`. Un import compromis aurait aussi accès à toute la base.
   - **Résolution :** le compte `orion-import` n'écrit que dans une **zone d'attente** : faits minimisés au format
     JSON, signés par un résumé. Le Core vérifie le schéma et intègre. Le compte d'import ne lit jamais la base
     personnelle ni la clé.
   - Même principe pour EVOLVE (`orion-evolve`) : espace isolé, proposition déposée, jamais d'écriture dans le dépôt
     en service.
7. **Provenance conservée / interactions supprimées à 180 jours.**
   - **Résolution :** au-delà de 180 jours, seul l'agrégat subsiste : intérêt, poids, période (première et dernière
     date), type de source (« recherches », « YouTube », « journal »). La provenance reste au niveau du résumé,
     jamais des requêtes une à une.
   - Les agrégats sont eux aussi consultables, corrigeables et supprimables, avec suppression en cascade.
8. **« Import vérifié » avant suppression de l'archive.** Le terme n'était pas défini. Définition retenue :
   - nombre d'entrées lues = intégrées + écartées (minimisation), chaque écart motivé ;
   - schéma valide ;
   - base relue après intégration ;
   - résumé (SHA-256) de l'archive consigné dans l'historique ;
   - vous avez vu le récapitulatif dans ORION Control.

   Ensuite seulement, l'archive est supprimée.
9. **Migration réversible / V2 en production sur JSON.**
   - La production lit et écrit ses JSON (`memory.json`, `routines.json`, `call_rules.json`, `schedule.json`,
     `mail_actions.json`…). Une migration qui les remplacerait ne serait pas réversible tant que V2 les modifie.
   - **Résolution :** la migration n'existe que dans `v3`, sur données factices, jusqu'à une mission de déploiement
     séparée. Le jour venu :
     - import en base avec résumé du fichier source ;
     - période de double lecture (la base fait foi, le JSON est régénéré à chaque écriture) ;
     - export inverse disponible ;
     - suppression du JSON seulement après validation.
10. **Autonomie phase 1 « notification » / politique d'interruption.** Une notification autonome reste soumise aux
    heures calmes et au budget d'interruptions (`platform/alerts.py`), sans exception.

## 3. Lots et dépendances

**CORE FOUNDATION** se découpe en cinq lots, chacun testable seul :

| Lot | Contenu | Dépend de |
|---|---|---|
| **F1** | Permissions centrales (`Capability`, `Grant`, `Permissions`), interrupteur d'autonomie, registre d'actions (ajout seul), adaptateur vers `PermissionManager` | rien |
| **F2** | Tâches persistantes et reprenables (idempotence au redémarrage) ; origines `delegated:` et `module:` | F1 |
| **F3** | Événements normalisés (`request_id`, classe de données, schéma) ; politique d'interruption unique (routeur d'alertes généralisé) | F1 |
| **F4** | Séparation des données en trois bases ; chiffrement (clé par identifiant systemd, versionnée) ; outil de migration JSON → base (factice) | F1, F3 (classes de données) |
| **F5** | Service de ressources et mode dégradé (Katana, STT, agent PC, NAS facultatif) ; comptes système et zones d'attente (spécification et scripts, non installés) | F1, F2 |

Le reste de la feuille de route en dépend ainsi :

| Étape | Dépend de |
|---|---|
| NEWS P1 (journal sans données personnelles) | rien ; se poursuit en parallèle, sans toucher à NEWS en cours |
| SENTINEL | F3, F5 |
| KNOWLEDGE (base personnelle, migration de `memory.json`) | F4 |
| NEWS P2–P4 (Takeout, intérêts, pertinence) | KNOWLEDGE, F5 (compte d'import) |
| NEWS P5 (OAuth Google) | P2, votre accord explicite |
| MISSIONS | F1, F2 |
| PROACTIVE | F1, F3, MISSIONS |
| CONTINUITY | KNOWLEDGE, PROACTIVE |
| EVOLVE niveau 2 | SENTINEL, F5 (compte `orion-evolve`) |
| ENVIRONMENT (NAS) | F5 |

## 4. Critères de validation

**Communs à chaque lot :**
- suite complète verte sur le mini-PC (miroir `~/orion-v3`) ;
- aucun secret ni texte privé dans les journaux (test dédié) ;
- données factices uniquement ;
- aucun changement de comportement de la V2 (les tests V2 existants passent sans modification) ;
- documentation `docs/v3` mise à jour.

| Lot | Critères propres |
|---|---|
| F1 | Un module ne peut ni s'accorder ni étendre une autorisation. Avec l'autonomie suspendue, toute origine `module:*` reçoit `deny`, sauf `READ`, et la conversation fonctionne. `SPEND`, `DESTRUCTIVE`, `EXTERNAL_MESSAGE` ne sont jamais `allow` pour une origine autonome sans autorisation bornée et non expirée. L'état de l'interrupteur survit au redémarrage, et suspendu reste la valeur par défaut si l'état est illisible. Chaque décision et chaque action sont au registre, qui refuse modification et suppression. |
| F2 | Une tâche interrompue reprend après redémarrage sans s'exécuter deux fois. Les tentatives et l'attente entre elles sont respectées. Annulation propre. |
| F3 | Tout événement porte `request_id` et classe de données. Un événement hors schéma est refusé en test. Le budget d'interruptions n'est jamais dépassé. |
| F4 | La migration est idempotente : deux passages donnent le même résultat. Retour arrière vérifié (export inverse identique à la source). Une base personnelle sans clé est illisible. Rotation de clé testée sur données factices. Aucune donnée personnelle dans les bases opérationnelle et publique. |
| F5 | Pour chaque module, un test « ressource absente », dont NAS absent : Core démarre et converse. Les comptes d'import et d'EVOLVE ne peuvent lire ni la clé ni la base personnelle (test de permissions sur fichiers). |

## 5. Risques de migration

| Risque | Parade |
|---|---|
| Perte ou corruption de données JSON | Sauvegarde vérifiée (résumé SHA-256) avant toute migration ; source jamais modifiée ; import en transaction |
| Double écriture V2 et V3 pendant la transition | Une seule version en service à la fois ; double lecture côté base seulement ; JSON régénéré |
| Perte de la clé de données | Sauvegarde hors ligne de la clé (papier ou gestionnaire de mots de passe) ; procédure de récupération testée sur une clé factice |
| Clé compromise | Clé versionnée (identifiant dans chaque chiffré) ; rotation par rechiffrement progressif ; ancienne clé révoquée après relecture |
| Base plus récente que le code (retour à une version précédente) | Déjà refusé par `Store.migrate` ; sauvegarde avant chaque migration |
| Droits de fichiers incohérents entre comptes | Écriture par le seul Core ; zones d'attente par compte ; test des droits |
| Interruption de la V2 par un module V3 | Aucun déploiement sans mission séparée ; interrupteur d'autonomie suspendu par défaut |
| NAS absent ou lent | Rien d'opérationnel sur le NAS ; écritures vers le NAS en tâche différée avec reprise |

## 6. Décisions restantes

1. **Périmètre de l'interrupteur (§ 2.1).** Proposition : il couvre l'autonomie (`module:*`), avec une option
   « tout suspendre » qui couvre aussi les tâches que vous avez programmées. Les rappels restent toujours actifs.
2. **Réactivation de l'autonomie à la voix.** ORION Control seul, ou aussi la voix ? La voix seule n'authentifie pas
   le propriétaire de façon forte. Proposition : ORION Control seul pour activer ; la voix peut seulement suspendre.
3. **Emplacement de la sauvegarde de la clé.** Papier, gestionnaire de mots de passe, clé USB hors ligne ?
4. **Modèle du NAS et protocole** (SMB, NFS), et dossier dédié. Rien ne presse : F5 le traite comme facultatif.
5. **Fusion des correctifs V2 dans `v3` (§ 7) :** quand vous le déciderez, par une fusion explicite.
6. **Ordre F1 → F5 et parallélisme avec NEWS P1.** Proposition : NEWS P1 et F1 en parallèle, car ils ne se touchent
   pas.

## 7. Correctifs V2 à porter vers `v3`

`v3` part de `a1b92a8` (tag `v2.0.0`). Deux commits V2 n'y sont pas :

| Commit | Contenu | Utilité | Dépendances | Risques de portage |
|---|---|---|---|---|
| `6f8c11f` | Mesures : une commande rapide n'est plus comptée « chemin=llm » (`agent.py` : le planificateur LLM marque `latency["planner"]`) | Statistiques de latence justes ; SENTINEL s'appuiera sur ces mesures | Aucune | Faible : `agent.py` n'est pas modifié par `v3` |
| `c66120f` | Rappels : `cancel_all_reminders` (après confirmation, en nommant ce qui sera supprimé). Échéances de mail : aucun rappel sans votre accord, « oui » juste après l'annonce suffit. ORION Control : suppression d'un rappel et « Tout supprimer », date affichée. | Correction d'un défaut réel (rappels posés d'office sur un mail de banque) ; meilleure maîtrise des rappels | Suite proposée (`follow_up`) dans les notifications vocales : présente dans `v3` | `planner.py`, `factory.py` et `app.js` sont aussi modifiés dans `v3` |

**Vérification faite (sans rien modifier) :**
- fusion simulée de `V2` dans `v3` (`git merge-tree`) : aucun conflit ;
- suite complète sur l'arbre fusionné, dans un dossier temporaire du mini-PC (supprimé depuis) : **2113 réussis,
  5 ignorés** (`v3` seule : 2105) ;
- `app.js` fusionné : syntaxe valide.

**Risques restants :**
- **Planificateur :** les mots de domaine de `cancel_all_reminders` (« supprime », « rappels ») et ceux des outils
  v3 (NEWS, WATCH, PURCHASE) sont à revoir ensemble, en essai réel, pour détecter les mauvais choix d'outil.
- **Échéances de mail en v3 :** le routeur d'alertes de `v3` peut rendre une annonce seulement écrite (absence,
  heures calmes). La question reste alors en attente sans suite vocale. On répond plus tard par « confirme
  l'échéance » (comportement V2 conservé). Ce point est à vérifier lors de la fusion.
- **ORION Control :** le tableau de bord fusionné doit être vérifié visuellement (cartes des rappels avec les
  sections v3).

**Recommandation :** une fusion explicite `git merge --no-ff V2` dans `v3`, avant F1, quand vous la validerez. Ce
n'est pas un cherry-pick : il garderait la trace des commits et simplifierait les fusions suivantes.

## 8. Plan d'exécution — lot F1 du CORE FOUNDATION

**État (2 novembre 2026) :** F1 livré sur `v3`, non déployé. Vos arbitrages du 2 novembre sont appliqués : deux
niveaux d'arrêt (`suspended`, `emergency_stop`), réactivation depuis ORION Control seul, registre chaîné. Le résultat,
les mécanismes effectivement en place et les écarts sont décrits dans `foundation.md`. Le plan initial suit.

**Objectif :** poser les permissions centrales, l'interrupteur d'autonomie et le registre d'actions dans
`orion/platform`, sans changer le comportement de la V2 ni des modules v3 existants.

**Préalable :** votre accord sur ce plan et sur les décisions 1 et 2 du § 6. La fusion des correctifs V2 est
recommandée, mais pas bloquante.

### Fichiers

| Fichier | Contenu |
|---|---|
| `orion/platform/permissions.py` (nouveau) | `Capability` ; `Grant` (principal, capacité, limites, expiration, accordé par, date) ; `Decision` (`allow` / `ask` / `deny` et raison) ; `Permissions.decide(principal, capability, context)` ; invariants codés en dur (§ 4.1 de `architecture_cible.md`) |
| `orion/platform/autonomy.py` (nouveau) | `AutonomySwitch` : état persistant en base (`suspended` par défaut, et aussi si l'état est illisible) ; `suspend(by, reason)` ; `resume(by)` réservé au propriétaire depuis ORION Control ; historique de chaque bascule |
| `orion/platform/ledger.py` (nouveau) | `ActionLedger` : table en ajout seul (déclencheurs SQLite qui refusent `UPDATE` et `DELETE`), une ligne par décision et par action (principal, capacité, outil, `request_id`, décision, résultat, horodatage) ; textes passés par `loggable` |
| `orion/platform/grants.py` (nouveau) | Stockage des autorisations en base ; création et révocation par l'origine `utilisateur` seule (jamais par un module, test dédié) |
| `orion/tools/permissions.py` | Adaptateur : `PermissionManager.decide` consulte `Permissions` quand on le lui fournit (paramètre facultatif, absent en V2 : comportement identique) ; correspondance `Risk` / outil → `Capability` déclarée par outil, avec une valeur par défaut prudente |
| `orion/platform/build.py` | Création de `Permissions`, `AutonomySwitch` et `ActionLedger` dans `build_v3`, inactifs tant que `[platform] autonomy` n'est pas configuré |
| `orion/api` (route) | `GET /api/autonomy` (état), `POST /api/autonomy` (suspendre, reprendre) ; lecture du registre `GET /api/ledger` (paginé, filtré) |
| `orion/control/web/app.js` | Section « Autonomie » : interrupteur, raison, historique ; vue « Registre d'actions » |
| `tests/test_platform_permissions.py` (nouveau) | Tests des invariants, de l'interrupteur, du registre et de l'adaptateur |
| `docs/v3/foundation.md` (nouveau) | Contrat des interfaces F1 |

### Étapes

1. **Interfaces et invariants :** `permissions.py`, avec ses tests d'abord. Invariants : aucun principal ne modifie
   ses propres autorisations ; `SPEND`, `DESTRUCTIVE` et `EXTERNAL_MESSAGE` toujours `ask` pour `module:*` sans
   autorisation bornée et valide ; `CODE_CHANGE` limité à l'espace d'EVOLVE ; principal inconnu → `deny`.
2. **Interrupteur :** `autonomy.py`. Défaut suspendu ; persistance par migration `Store` (module `platform_autonomy`) ;
   état illisible → suspendu ; la conversation n'est jamais concernée (origine `user`).
3. **Registre :** `ledger.py`. Migration avec déclencheurs d'ajout seul ; masquage des textes privés ; rotation par
   ancienneté (exportée, jamais modifiée).
4. **Adaptateur :** `PermissionManager` reçoit `Permissions` en option. Sans lui, comportement V2 identique (toute la
   suite V2 doit rester verte sans modification de test). Avec lui, chaque `submit` du Core des outils écrit au
   registre.
5. **Câblage v3 :** `build_v3` et routes d'API ; WATCH, NEWS et PURCHASE déclarent leur principal (`module:watch`…).
   Leurs notifications passent par `Permissions` (`NOTIFY`), en attendant F2 et les origines déléguées.
6. **ORION Control :** interrupteur et registre ; vérification visuelle dans le navigateur intégré, sur des données
   factices servies par le Core de test.
7. **Validation :**
   - suite complète sur le mini-PC (`v3sync.sh`) ;
   - test de garde : aucun secret dans le registre ;
   - revue du diff ;
   - rapport.

   Aucun déploiement.

### Tests prévus (extraits)

- `test_module_cannot_grant_itself_a_capability`
- `test_suspended_autonomy_denies_module_actions_but_not_conversation`
- `test_unreadable_autonomy_state_means_suspended`
- `test_spend_destructive_external_always_ask_for_module_origin`
- `test_expired_or_out_of_limits_grant_is_ignored`
- `test_ledger_rejects_update_and_delete`
- `test_ledger_masks_private_text`
- `test_permission_manager_without_platform_behaves_like_v2`
- `test_every_tool_submit_is_recorded_in_ledger`

### Hors du lot F1

- Tâches persistantes (F2).
- Événements normalisés et politique d'interruption (F3).
- Bases séparées, chiffrement et clé (F4).
- Comptes système et NAS (F5).
- Tout accès Google.
- Toute modification de NEWS en cours.
- Toute modification de la production V2.
