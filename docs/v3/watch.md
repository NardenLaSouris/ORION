# ORION WATCH

Surveillances autonomes et persistantes. Paquet `orion/watch/`, inactif par défaut (`[watch] enabled = false`).

## Une surveillance

| Champ | Rôle |
|---|---|
| `id`, `name`, `description`, `created_at` | identité |
| `condition` | donnée structurée et validée (`orion/watch/models.py`), jamais du code |
| `sources` | sources autorisées (noms de `[watch] sources`) ; création refusée si une source est inconnue ou ne fournit pas ce type d'information |
| `interval_s` | fréquence (minimum `min_interval_minutes`) |
| `expires_at` | expiration facultative |
| `state` | `ACTIVE`, `PAUSED`, `TRIGGERED`, `COMPLETED`, `EXPIRED`, `FAILED` (passages déclarés, le reste est refusé) |
| `channel` | `auto` (selon la présence) ou `written` (écrit seulement) |
| `mode` | `once` (terminée au premier déclenchement) ou `repeat` |
| `min_reliability` | fiabilité minimale d'une source pour déclencher |

- **Persistance :** la base SQLite commune. Au redémarrage, les surveillances actives sont reprogrammées.
- **Historique :** trois traces sont gardées et lisibles dans ORION Control :
  - les vérifications (`watch_checks`) ;
  - ce qui a déjà été signalé (`watch_seen`) ;
  - les changements d'état (`module_history`).

## Conditions

| `kind` | Champs | Déclenche quand |
|---|---|---|
| `price` | `product`, `max_price`, `min_score`, `condition` (`new`/`any`) | une offre de la carte demandée, disponible, au prix final connu, non suspecte, sous le prix maximum ou au-dessus du score demandé (« bonne affaire » sans seuil : score ≥ 70) |
| `availability` | `product`, `max_price` | la carte est disponible chez un vendeur (signalé de nouveau après une rupture) |
| `event` | `keywords`, `game`, `require` (`confirmed` par défaut) | un événement correspondant est confirmé officiellement (source officielle et dates), ou son calendrier change |
| `announcement` | `keywords`, `require` (`official` par défaut) | une annonce officielle correspondante paraît |

## Sources (`[watch] sources`)

```toml
[watch]
enabled = true
[watch.sources.pokemongo]
type = "rss"
url = "https://…/flux-officiel.xml"
reliability = "official"
[watch.sources.boutiques]
type = "offers_json"
url = "https://…/api/offres?produit=rtx-5090"
```

- **Types de source :**
  - `rss` : flux RSS ou Atom ;
  - `offers_json` : flux d'offres au format décrit dans `orion/watch/connectors.py`, par exemple l'API d'un marchand,
    un comparateur autorisé ou un petit service intermédiaire.
- **Aucun site marchand n'est « gratté »,** et aucune protection n'est contournée. Une source qui refuse est simplement
  indisponible.
- **Aucune source n'est livrée par défaut.** Elles doivent respecter les conditions d'utilisation des services
  interrogés. Ce choix vous revient.
- **Requêtes :** limitées par source (`requests_per_source_per_hour`) et mises en cache 5 minutes, pour être partagées
  entre surveillances.
- **Valeurs inconnues :** elles restent `None`. Aucune disponibilité, aucun prix ni aucuns frais ne sont devinés.

## Prix et bonnes affaires (RTX 5090)

- **Identification** (`products.py`) :
  - marque, modèle et variante ; la 5090 D n'est pas la 5090 ;
  - sont exclus : PC complet, portable, boîte vide, photo, pièces, waterblock, lots de plusieurs cartes, précommandes.
- **Prix final :** prix plus livraison. Il est inconnu dès que l'un des deux l'est.
- **Historique :** un prix par vendeur et par heure au plus. Il sert au marché et au score.
- **Score sur 100** (`opportunity.py`, poids réglables dans `[watch] opportunity`) :

  | Composante | Poids |
  |---|---|
  | écart avec le marché | 35 |
  | historique sur 30 jours | 20 |
  | fiabilité du vendeur | 20 |
  | disponibilité | 10 |
  | conditions de vente (état, garantie, retour) | 10 |
  | livraison | 5 |

  Le prix de marché est la médiane d'au moins 3 offres fiables, neuves et disponibles.
- **Plafonds :**
  - donnée manquante : 69, jamais « exceptionnelle » ;
  - offre suspecte : 40. Une offre est suspecte quand son prix est à plus de 40 % sous le marché, quand un vendeur peu
    noté ou peu connu fait un gros rabais, ou quand le prix barré dépasse le plus haut observé (fausse promotion) ;
  - offre indisponible : 0.
- **Aucun achat** n'est fait par ce module. PURCHASE écoute l'événement `watch.found`, mais une surveillance n'est
  jamais une autorisation d'achat.

## Événements (Pokémon GO) et annonces

`events.py` classe chaque article :

| Statut | Quand |
|---|---|
| `official` | la source est déclarée officielle |
| `confirmed` | source officielle et dates d'activité |
| `leak` | données extraites du jeu, fichiers du jeu… |
| `rumor` | conditionnel, « selon des sources »… |
| `unverified` | un média rapporte ; ce n'est jamais une confirmation |

- **Une rumeur ou une fuite ne déclenche jamais** une surveillance qui exige une confirmation.
- **Dates lues :** en français, en anglais et au format ISO.
- **Calendrier des événements :** il signale un nouvel événement ou un changement de dates.

## Intelligence

- **Interprétation des phrases** (`interpret.py`) : les quatre exemples de la mission sont compris par des règles,
  sans LLM.
- **Rôle du LLM :** il ne sert qu'en dernier recours. Sa réponse est validée par le même schéma que les conditions
  saisies à la main. Aucun code produit par le LLM n'est exécuté.

## Notifications

Elles passent par le routeur commun (`orion/platform/alerts.py`) :
- voix à la maison ;
- écrit et appel si vous êtes absent ;
- écrit seul si la présence est inconnue ;
- heures calmes respectées et budget d'appels ;
- dédoublonnage.

Trois alertes au plus par vérification ; le reste est regroupé dans l'historique.

## Pannes

- **Une source en panne :** elle est notée et ne bloque pas les autres.
- **Toutes les sources en panne :** l'échec compte. Après `failure_limit` vérifications de suite, la surveillance passe
  en `FAILED` avec une alerte écrite. `resume` la relance.
- **Le planificateur :** il réessaie avec une attente doublée à chaque échec.

## Interfaces

| Interface | Points d'entrée |
|---|---|
| Voix | `create_watch` (après confirmation), `list_watches`, `pause_watch`, `resume_watch`, `delete_watch` (après confirmation) |
| API `/api/modules/watch` | `GET` liste ; `POST` création (phrase ou condition) ; `POST interpret` (aperçu) ; `GET sources` ; `GET prices?product=` ; `GET <id>` (vérifications, éléments trouvés, historique, calendrier) ; `POST <id>/pause\|resume\|check` ; `DELETE <id>` |
| ORION Control | section WATCH |

## Limites connues

- **Aucune source réelle n'a été interrogée pendant le développement,** seulement des sources simulées. Le format
  `offers_json` suppose une source qui le fournit.
- **Le routage vocal des demandes de surveillance** (planificateur LLM) reste à essayer en réel.
