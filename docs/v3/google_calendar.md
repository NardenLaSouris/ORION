# Agenda Google « ORION » dans la V3 (4 novembre 2026)

Le calendrier Google dédié **« ORION »** (synchronisé avec l'application Calendrier de l'iPhone) est lu par ORION V3,
**en lecture seule**, par son adresse secrète iCal. ORION ne crée, ne modifie et ne supprime jamais rien dans
Google Agenda, et ne lit aucun autre calendrier du compte.

État à la livraison : **code déployé, connecteur désactivé** (aucune adresse fournie). La base personnelle étant
verrouillée (clé F4 absente), il resterait « verrouillé » même activé.

## 1. Relier le calendrier (vous)

1. Sur un ordinateur : Google Agenda > Paramètres > calendrier « ORION » > *Intégrer l'agenda* > copiez **l'adresse
   secrète au format iCal** (`https://calendar.google.com/calendar/ical/…/private-…/basic.ics`).
2. Sur le PC, dans PowerShell :
   ```
   ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_calendar_setup.sh"
   ```
   Collez l'adresse quand le script la demande (rien ne s'affiche), puis répondez `o` pour activer.
3. Vérifiez dans ORION Control, vue **Agenda Google** : état « à jour », nombre d'événements.

Ne collez jamais l'adresse dans une conversation, un courriel ou un fichier du dépôt. Si elle a fuité : Google Agenda >
*Réinitialiser* l'adresse secrète, puis relancez le script. Couper : `… v3_calendar_setup.sh --desactiver` ; effacer
l'adresse de la V3 : `… --oublier`.

## 2. Ce que vous pouvez demander

| Demande | Outil (existant, `orion/agenda.py`) |
|---|---|
| « Qu'est-ce que j'ai prévu aujourd'hui ? », « … demain ? », « … jeudi ? » | `list_events` |
| « Quels sont mes prochains rendez-vous ? » | `next_events` |
| « Est-ce que je suis disponible vendredi ? » | `free_slots` (journées entières et plusieurs jours dits à part : « Attention, toute la journée : Vacances ») |
| « J'ai quoi chez le dentiste ? » | `search_events` |
| « Rappelle-moi ce rendez-vous une heure avant » | rappel créé **sur votre demande** par les rappels existants ; ORION le propose à l'annonce d'un nouvel événement, ne le crée jamais seul |

Après un échec de synchronisation, chaque réponse le dit : « Attention : votre agenda Google n'a pas pu être mis à
jour depuis 14 h 05 ; il peut ne pas être à jour. » Jamais de rendez-vous inventé, jamais d'agenda supposé à jour.
Le résumé de la semaine (« résume-moi mon agenda de la semaine ») passe par `list_events` jour par jour en attendant
un outil dédié (évolution prévue).

## 3. Architecture (réutilisation)

| Élément | Réutilisé | Ajouté |
|---|---|---|
| Lecture iCalendar | `orion.agenda.parse_ics`, `occurrences` (RRULE, EXDATE, TZID) | `RECURRENCE-ID` (occurrence modifiée), `STATUS:CANCELLED`, `SEQUENCE`, `BYMONTHDAY`, `expand()` |
| Outils de conversation | `calendar_tools` (aujourd'hui, demain, prochains, recherche, créneaux) | réserve de fraîcheur (`Calendar.caveat`), journées entières dans les disponibilités |
| Module | `BaseModule`, planificateur V3, routes `/api/modules/<nom>` | `orion/google_calendar` (`GoogleCalendarModule`, fournisseur `GoogleCalendarProvider`) |
| Données | base opérationnelle, `Sealer` (F4) | table `google_calendar` (état ; copie des événements scellée) |
| Annonces | EventHub F3 (`EventGate`, texte privé, déduplication) | `google_calendar.created/changed/cancelled/failing/recovered` |
| Permissions | moteur central F1 : tâche `google_calendar:sync` classée `delegated:google_calendar:sync`, NOTIFY (calendrier que vous avez relié) | — |
| Tâches F2 | — | aucune création ; les rappels restent ceux que vous demandez |
| ORION Control | vues des modules | vue « Agenda Google » : état, dernière synchronisation, nombre, échecs, activer / désactiver, synchroniser |

## 4. Synchronisation

- Au démarrage du Core puis toutes les 5 minutes ; une seule à la fois (verrou) ; reprise au tour suivant après une
  erreur.
- Fenêtre : 30 jours passés, 180 à venir. Occurrences identifiées par `UID@début` : sans doublon, idempotente.
- Changement : empreinte (titre, début, fin, journée entière, lieu). Supprimé : absent d'un flux **valide** et encore
  dans la fenêtre. Seuls les changements à venir sont annoncés ; plus de 5 changements d'un coup : un résumé.
- **Un échec (réseau, délai, réponse HTTP, page d'erreur, flux tronqué ou illisible) ne supprime jamais rien** : la
  dernière copie reste servie, avec la réserve de fraîcheur ; échec prolongé (30 min) annoncé une fois, reprise
  annoncée une fois.

Configuration (`config.local.toml`, aucune adresse) :

```toml
[google_calendar]
enabled = true
name = "ORION"
interval_seconds = 300
past_days = 30
future_days = 180
timeout = 15.0
max_bytes = 5000000
failure_alert_minutes = 30
```

## 5. Rapport de sécurité

| Exigence | Mise en œuvre | Vérifié par |
|---|---|---|
| Adresse = secret | `.env` de la V3 (640, `user:orion-v3`), saisie masquée sur le Core ; jamais en argument de commande | `deploy/v3_calendar_setup.sh` |
| Jamais dans Git, journaux, erreurs, ORION Control, LLM | messages d'erreur sans adresse (`IcalError`), journal : nombres seulement ; état sans adresse ; le module ne parle pas au LLM, les outils ne rendent que titres, heures, lieux | `test_the_secret_never_appears_in_logs_status_or_errors` |
| Seul le calendrier autorisé | adresse acceptée seulement si https, `calendar.google.com`, format iCal **privé** ; aucune autre source | `test_a_missing_or_foreign_address_is_never_used` |
| Pas d'OAuth ni de compte de service | lecture iCal seule | — |
| Données personnelles | copie scellée F4 sur disque ; rien en clair (ni titres ni lieux) | `test_the_snapshot_survives_a_restart_and_is_sealed_on_disk` |
| F4 verrouillé : pas de contournement | module « verrouillé » : aucune lecture, aucune écriture | `test_a_locked_personal_base_keeps_the_connector_off` |
| Lecture seule | aucune route ni outil d'écriture vers Google | `test_orion_control_routes_enable_disable_and_sync` |
| Échec ≠ suppression | flux vérifié (iCalendar complet) avant toute comparaison | `test_a_network_error_or_an_invalid_feed_never_deletes_anything` |
| Annonces sans répétition | clé de déduplication par changement (occurrence + empreinte) | `test_first_sync_is_a_baseline_then_changes_are_detected_and_announced_once` |
| Aucune tâche créée seule | rappels seulement proposés | même test (`tasks.list() == []`) |

Changement lié : l'ancien chemin qui lisait `ORION_CALENDAR_ICS` à chaque demande, sans état ni scellement
(`build_calendar`), est retiré ; `[calendar] ics` reste pour un fichier .ics non secret.

Limites connues : règles de répétition rares non gérées (BYSETPOS, BYDAY dans un mois ou une année) : l'occurrence
d'origine seule est montrée ; un événement privé partagé reste lisible par toute personne qui obtient l'adresse
(propriété de Google, d'où sa réinitialisation en cas de fuite).
