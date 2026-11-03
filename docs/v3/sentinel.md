# ORION SENTINEL — santé d'ORION et de ses ressources

Étape 3 de la feuille de route (`architecture_cible.md` § 5). Code : `orion/sentinel/`. Lecture seule : SENTINEL ne
répare rien, il constate. Les reprises sont celles de chaque composant : le LLM revient seul au Katana, la
transcription au GPU, les agents des PC à leur retour.

## Ce qu'il fait

- **Sondage** chaque minute (`[sentinel] probe_seconds`) de toutes les ressources du service de ressources
  (lot F5) :
  - LLM principal (Katana) ;
  - transcription sur GPU ;
  - agents des PC ;
  - recherche Web ;
  - NAS.

  Une ressource « non configurée » n'est jamais surveillée.
- **Historique** : un échantillon à chaque changement d'état, plus un battement toutes les 15 minutes ; 7 jours
  gardés (`history_days`). Le dernier état connu avant la fenêtre reste, pour qu'une longue panne compte en entier.
- **Disponibilité sur 7 jours** : part du temps passé disponible, et non part des sondages.
- **Incidents** :
  - un incident s'ouvre à la première panne et se ferme au retour, marqué « levé sans intervention » ;
  - **une panne n'est annoncée qu'au-delà de 2 minutes** (`notify_after_seconds`). Une micro-coupure reste dans
    l'historique, sans bruit ;
  - le retour n'est annoncé que si la panne l'a été ;
  - l'annonce dit aussi ce qu'ORION fait en attendant, d'après les déclarations « sans elle » du service de
    ressources.
- **Alertes** : ce sont des initiatives du module (`module:sentinel`), soumises à l'autonomie et aux heures calmes
  comme les autres. Pendant la suspension, elles sont refusées mais gardées dans l'historique. Le sondage lui-même
  est une lecture (`sentinel:probe` : `READ`), permise pendant la suspension.

## Accès

- ORION Control, vue **Santé** :
  - les incidents en cours et ceux des 7 derniers jours ;
  - pour chaque ressource : son état, depuis quand, sa disponibilité sur 7 jours et le comportement d'ORION sans
    elle ;
  - un bouton « Vérifier maintenant ».
- Voix : « comment va le système ? » (`sentinel_status`). Par exemple : « Tout fonctionne normalement. Aucun
  incident sur 7 jours. » ou « Problème en cours : LLM principal (Katana) indisponible depuis 12 min… ».
- Routes : `GET /api/modules/sentinel`, `GET …/incidents`, `GET …/history?resource=`, `POST …/probe`.

## Critères de validation (feuille de route)

| Critère | Vérification |
|---|---|
| Vue de santé unique | vue Santé et route du module (`test_sentinel.py`) |
| Historique sur 7 jours | battements, changements d'état, purge, disponibilité au temps passé (tests) |
| Panne du Katana détectée et levée sans intervention | test : détection, annonce après 2 minutes, retour constaté, incident fermé « levé sans intervention » |

L'essai d'une vraie coupure du Katana n'a pas été fait. Le Katana sert aussi la production V2, et le couper pour
un essai la priverait de son LLM principal. À faire lors d'une coupure planifiée, ou quand vous éteindrez le portable.

## Limites

- Les sondes vérifient l'accès réseau (lot F5) : un service ouvert mais en erreur reste « disponible ».
- L'observation du journal reste dans EVOLVE (`evolve/observe.py`) ; son rattachement à SENTINEL est prévu avec
  EVOLVE (étape 9).
