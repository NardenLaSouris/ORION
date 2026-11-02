# CORE FOUNDATION — lot F1 : permissions centrales, autonomie, registre d'actions

Livré sur `v3` le 2 novembre 2026, jamais déployé. Inactif par défaut (`[platform] enabled = false`) : la V2 et les
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
4. **Indicateur « confirmé » :** les modules le passent eux-mêmes. Seuls les chemins utilisateur le fixent :
   `PurchaseEngine.confirm` et la route EVOLVE d'ORION Control. C'est une confiance interne au processus, pas une
   preuve. Lier chaque confirmation à son identifiant de demande est prévu en F2 et F3.
5. **Ce que le registre détecte :**
   - une modification naïve, mais pas une réécriture complète de la chaîne par quelqu'un qui a accès en écriture à
     la base : il faudrait une signature HMAC avec une clé extérieure (F4) ;
   - pas une troncature de la fin : l'empreinte de tête est affichée pour comparaison.
6. **Résultats d'exécution des outils :** ils ne sont pas inscrits au registre, seulement les décisions. Les résultats
   restent dans le journal d'activité.
7. **Automatismes V2 hors du Core des outils et des appels :** les rappels qui sonnent, les annonces de mails et
   `EveWatch` parlent sans passer par le moteur. Ce sont des notifications à la voix, pas des actions sur le monde.
   Leur passage par le moteur est prévu avec le service de tâches (F2).
8. **NEWS (isolé) :** ses alertes partent sous `module:news` ; plateforme activée et autonomie suspendue, elles sont
   donc bloquées. Faire passer les bulletins programmés en `delegated:news` demande une modification de NEWS, à
   coordonner. La plateforme étant désactivée par défaut, le développement de NEWS n'est pas affecté.
9. **Session « authentifiée » :** c'est l'authentification existante d'ORION Control (jeton et IP autorisée). Il n'y a
   pas d'ouverture de session propre à chaque utilisateur.
10. **Rotation du registre par ancienneté :** non faite (prévue plus tard, par export, jamais par suppression).
11. **Correction annexe :** `Store` découpe désormais correctement les migrations qui contiennent un déclencheur
    (`BEGIN … ; END`).
