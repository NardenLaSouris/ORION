# ORION PURCHASE

Achats conditionnels sous mandat. Paquet `orion/purchase/`, inactif par défaut (`[purchase] enabled = false`).
**Bac à sable seulement dans cette version** : aucun prestataire de paiement ni aucun marchand réel n'est branché.
`sandbox = false` est refusé au démarrage.

## Surveiller, proposer, acheter

Ce sont trois choses distinctes.

| Étape | Qui | Ce qui se passe |
|---|---|---|
| surveiller | WATCH | une offre est signalée (`watch.found`) ; ce n'est jamais une autorisation |
| proposer | PURCHASE | seulement s'il existe un **mandat actif** qui correspond ; sinon, rien n'est préparé |
| acheter | PURCHASE | seulement si tous les critères du mandat sont **vérifiés**, et selon le niveau d'autorisation |

## Mandat

| Champ | Rôle |
|---|---|
| `product` | carte identifiée (`rtx 5090`, `rtx 5090 d`…) ; `brands` et `models` sont facultatifs |
| `condition` | `new` seulement |
| `sellers` | vendeurs autorisés ; une liste vide est refusée. `min_seller_rating` donne la note minimale (0,9 par défaut) |
| `max_price`, `max_shipping`, `total_budget` | prix de l'article, frais de livraison, budget total du mandat |
| `max_purchases` | nombre d'achats permis (1 à 5) |
| `expires_at` | expiration, dans l'année |
| `min_warranty_months`, `min_returns_days` | garantie et délai de retour minimaux |
| `level` | niveau demandé pour ce mandat (1 à 3) |

- **Révocation :** immédiate. Elle annule aussi les commandes non payées. Elle est revérifiée avant le devis, après le
  devis et après le paiement.
- **Mandat épuisé :** il passe en `EXHAUSTED` quand le nombre d'achats ou le budget est atteint.

## Vérification d'une offre (`evaluate.py`)

Chaque critère est vrai, faux ou inconnu. **Un seul critère faux ou inconnu bloque l'achat.** Les critères :
- mandat actif et non expiré ;
- achats restants ;
- produit exact : ni un PC, ni une boîte vide, ni une 5090 D si la 5090 est demandée ;
- marque et modèle ;
- état neuf ;
- vendeur autorisé, et sa note ;
- devise en euros ;
- prix, frais de livraison et budget restant ;
- disponibilité ;
- garantie et délai de retour ;
- risque de fraude ou de contrefaçon : prix à plus de 40 % sous le marché, vendeur tiers sans note, offre signalée
  suspecte.

## Niveaux d'autorisation

| Niveau | Comportement | Conditions |
|---|---|---|
| 1 (par défaut) | commande préparée ; **l'utilisateur confirme** dans ORION Control | — |
| 2 | commande automatique | `[purchase] level ≥ 2` **et** mandat `level ≥ 2` **et** arrêt global inactif |
| 3 | commande automatique dans une enveloppe | en plus : `allow_level3 = true`, `per_purchase_max` et `period_max` définis et respectés ; sinon, niveau 2 |

Le niveau effectif est le plus bas entre le réglage et le mandat. Ce calcul est fait par le code
(`effective_level`), jamais par le LLM.

## Commande

1. **Devis du moment** (`Merchant.quote`), revérifié contre le mandat. Si le prix final est plus élevé qu'annoncé ou
   qu'un critère ne passe plus : `BLOCKED`.
2. **Budget réservé sous verrou**, puis paiement (`PaymentProvider.authorize`) :

   | Réponse | Suite |
   |---|---|
   | `approved` | commande chez le marchand ; en cas d'échec, paiement annulé (`void`) et `FAILED` |
   | `declined` | `REFUSED`, budget libéré |
   | `requires_action` (3-D Secure) | `AWAITING_AUTH` : la commande est suspendue et l'utilisateur s'authentifie lui-même auprès de sa banque. ORION ne contourne jamais l'authentification forte |

3. **Double commande impossible :** clé unique mandat + vendeur + adresse + total.
4. **Arrêt global** (ORION Control, ou à la voix : « arrête tous les achats ») : plus aucun achat automatique, et les
   commandes en attente de confirmation sont annulées. La reprise se fait dans ORION Control seulement.

États d'une commande : `PREPARED` → `AWAITING_CONFIRMATION` → `CONFIRMED` → `PAYING` → `AWAITING_AUTH` → `PLACED`.
Issues terminales : `REFUSED`, `FAILED`, `CANCELLED`, `BLOCKED`.

## Paiement et données sensibles

- **ORION ne voit jamais la carte.** Le prestataire détient le moyen de paiement ; idéalement, une carte virtuelle
  dédiée et plafonnée. ORION ne garde qu'une référence de transaction.
- **Détection des données de carte** (`contains_card_data`) : numéro valide selon Luhn, cryptogramme ou date
  d'expiration. Elles sont refusées dans un mandat, une commande ou l'historique.
- **Aucun mot de passe bancaire** n'est demandé ni enregistré.
- **Brancher un vrai paiement** exigera une autorisation explicite de l'utilisateur, la validation de l'environnement de
  paiement (prestataire, carte virtuelle, plafonds) et une mission distincte.

## Historique

- **Tables :** mandats, opportunités évaluées avec le détail de chaque critère, commandes avec l'historique de leurs
  états, réglages (arrêt global).
- **Événements tracés :** création, révocation, expiration, arrêt et reprise sont gardés dans l'historique commun.

## Interfaces

| Interface | Points d'entrée |
|---|---|
| Voix | `stop_purchases` seulement. Acheter, confirmer ou créer un mandat ne se fait jamais à la voix |
| API `/api/modules/purchase` | `GET` état (budget, dépenses, en attente) ; `GET`/`POST mandates` ; `POST mandates/<id>/revoke` ; `GET orders` ; `POST orders/<id>/confirm\|cancel\|auth` (`auth` : bac à sable) ; `GET opportunities` ; `POST stop\|resume` ; `GET history` |
| ORION Control | section PURCHASE |

## Tests (bac à sable)

Fichier `tests/test_purchase.py`. Aucune commande ni aucun paiement réels. Scénarios couverts :
- prix dépassé, produit différent (PC, autre carte), vendeur non autorisé ;
- disponibilité inconnue, frais inconnus ou trop élevés ;
- mandat expiré, budget dépassé ;
- double commande, paiement refusé, authentification requise (réussie ou échouée) ;
- révocation pendant une commande ;
- échec chez le marchand, devis du moment plus cher ;
- arrêt global, données de carte refusées, paiement réel impossible ;
- niveaux 1, 2 et 3 ;
- intégration avec WATCH.
