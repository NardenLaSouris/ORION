# ORION NEWS — Personal Intelligence & Daily Briefing

Étude et plan d'implémentation, au 2 novembre 2026. **Rien n'est implémenté ni connecté**, et aucune permission Google
n'a été demandée. Ce document sert à décider avant d'écrire le code.

## 1. Audit de NEWS (branche `v3`, `orion/news/`)

### Ce qui existe et se réutilise tel quel

| Composant | Fichier | Réutilisation pour le journal personnalisé |
|---|---|---|
| Collecte RSS/Atom sûre (DOCTYPE refusé, taille et délai bornés) | `orion/platform/feeds.py` | sources du journal, inchangé |
| Normalisation, adresse canonique, dédoublonnage | `news/pipeline.py` (`normalize`, `article_id`) | inchangé |
| Regroupement en événements (mots, noms propres, LLM pour les cas douteux) | `pipeline.py`, `engine._cluster` | inchangé ; ce sont les unités du journal |
| Confirmation (source officielle ou deux sources indépendantes) | `pipeline.analyze` | fiabilité affichée par information |
| Score d'importance sur 100 | `pipeline.score` | base du classement : « à ne pas manquer » |
| Suivi des évolutions (faits communiqués et faits nouveaux) | `Story.facts`, `communicated_facts`, `bulletin_facts` | section « évolutions des sujets suivis » |
| Synthèse sans invention (nombres, adresses, citations vérifiés) | `news/synthesis.py` (`summarize`, `grounded`) | résumé de chaque information |
| LLM encadré (schéma, données non fiables isolées, mode dégradé) | `orion/platform/intelligence.py` | classement thématique, explication |
| Bulletins programmés, rattrapage après redémarrage, jamais en double | `engine.bulletin_due` | devient le briefing du matin |
| Alertes selon la présence (voix, écrit, appel, heures calmes) | `orion/platform/alerts.py` | diffusion du briefing |
| Routes `/api/modules/news`, section Actualités d'ORION Control | `engine.admin`, `control/web/app.js` | ajout d'une vue « Journal » |
| Outil vocal `news_bulletin` | `news/tools.py` | devient « le journal », plus `approfondir` |
| Source « news » pour WATCH | `NewsConnector` | inchangé |

### Ce qui manque pour le journal demandé

| Besoin | État actuel |
|---|---|
| Pertinence **pour vous** | absente : seule l'importance générale compte |
| Rubriques « technologie et sciences », « internationale », « économie », « sujets personnels » | catégories fixes : géopolitique, défense, tech_ia, France, économie ; pas de rubrique personnelle ni « sciences » |
| Lien vers l'article dans le texte | les adresses sont en base (`news_articles.url`), pas dans le bulletin |
| Niveau de fiabilité par information | seul le statut « confirmé / non confirmé » existe |
| « Pourquoi c'est pertinent pour vous » | absent |
| Titres trompeurs | non détectés (seuls les doublons le sont) |
| Interactions : lu, ignoré, approfondi, masqué, suivi, correction | aucune trace |
| Commande « approfondis la deuxième » | absente |
| Profil d'intérêts, données personnelles | absent : NEWS ne connaît que des données publiques |

### Contraintes techniques constatées

- **Matériel :** le mini-PC n'a pas de GPU. Les synthèses et classements par LLM passent par le Katana (Qwen3 8B), sinon
  mode dégradé. Le journal doit donc pouvoir être composé sans LLM.
- **Fuseau horaire :** les heures de bulletin suivent celui du processus. La production V2 tourne à l'heure de Paris.
- **Écart entre branches :** la branche `v3` ne contient pas encore les correctifs V2 du 2 novembre (mesures, rappels,
  confirmation des échéances de mail). Une fusion de `V2` dans `v3` est à décider (§ 9).
- **Secrets :** le seul jeton OAuth stocké aujourd'hui (`data/spotify_token.json`) l'est **en clair**, dans un fichier
  du compte `orion`. Ce n'est pas un modèle à reproduire pour Google.
- **Chiffrement disponible :** `cryptography` 50 est déjà installée dans l'environnement de production. AES-GCM et
  Fernet sont donc disponibles sans nouvelle dépendance.
- **Agenda :** ORION lit déjà Google Agenda par l'adresse ICS secrète (`ORION_CALENDAR_ICS`, `orion/agenda.py`), en
  lecture seule et sans OAuth.

## 2. Intégration Google : ce qui est réellement possible

Ces informations sont issues de la documentation publique de Google telle que je la connais. **Elles sont à
revérifier dans la console Google Cloud au moment de la connexion**, car les API et leurs conditions changent.

### Constats

1. **Il n'existe pas d'API publique qui donne accès à tout « Google My Activity ».** OAuth ne permet pas de lire
   l'historique de recherche par une API classique. Aucun accès par scraping ou par cookies de session ne sera utilisé.
2. **Deux voies légitimes donnent accès aux historiques :**

   | Voie | Fonctionnement | Avantages | Limites |
   |---|---|---|---|
   | **Google Takeout** (recommandée en premier) | export manuel ; vous choisissez « Mon activité » (Recherche, YouTube) et « YouTube » (historique des vidéos regardées, des recherches), au format **JSON** ; vous déposez l'archive dans ORION Control | aucune permission OAuth, aucun projet Google Cloud, vous voyez ce que vous donnez | ponctuel : à refaire pour mettre à jour |
   | **Data Portability API** de Google | export programmé avec votre accord OAuth, par catégories d'activité (permissions du type `dataportability.myactivity.search`, `dataportability.myactivity.youtube`) ; ORION demande une archive, attend qu'elle soit prête, la télécharge et l'importe comme un Takeout | mise à jour automatique | réservée aux utilisateurs de l'Espace économique européen (vous en faites partie) ; permissions classées « restreintes » ; demande un projet Google Cloud ; accès ponctuel ou limité dans le temps |

3. **API YouTube Data v3** (permission `youtube.readonly`) : abonnements, playlists, vidéos « J'aime ». **L'historique
   de visionnage n'est pas accessible par cette API**, il ne l'est que par Takeout ou la Data Portability API.
4. **Google Agenda :** déjà lu par l'adresse ICS. Une connexion OAuth (`calendar.events.readonly`) n'apporterait que
   des métadonnées plus riches. Elle reste distincte, facultative, et non recommandée tant que l'ICS suffit.

### Permissions à demander, le cas échéant (minimales)

| Étape | Permission | Pourquoi | Obligatoire ? |
|---|---|---|---|
| A | aucune (import Takeout) | historiques de recherche et YouTube | recommandé en premier |
| B | `https://www.googleapis.com/auth/youtube.readonly` | abonnements et « J'aime », signaux d'intérêt durables | facultatif |
| C | `…/auth/dataportability.myactivity.search` et `…/dataportability.myactivity.youtube` | mise à jour automatique des historiques | facultatif, après A |
| D | `…/auth/calendar.events.readonly` | agenda par l'API | non recommandé (ICS déjà en place) |

- **Aucune permission en écriture.** Aucune permission Gmail : la boîte mail est déjà lue par IMAP en V2.
- **Aucune permission d'identité** (`openid`, `email`) n'est nécessaire.

### Contraintes OAuth pour un usage personnel

- **Projet Google Cloud :** à créer **par vous**, avec votre compte. ORION ne crée aucun compte et ne saisit aucun
  identifiant.
- **Statut « Test » :** l'application reste en test, avec vous seul comme utilisateur autorisé. Pas de vérification
  Google à demander pour un usage personnel. Contrepartie connue : les jetons de rafraîchissement d'une application en
  test expirent au bout de 7 jours, il faudra donc se reconnecter (à confirmer dans la console).
- **Flux d'autorisation :** application de bureau, avec PKCE et redirection locale (`http://127.0.0.1:<port>`), lancée
  depuis ORION Control sur le PC, qui a un navigateur.
  - Le flux « appareil à saisie limitée » n'accepte qu'une liste réduite de permissions et ne convient pas.
  - Le jeton obtenu est transmis au Core par l'API d'administration, déjà protégée par IP et jeton, puis chiffré
    aussitôt.
- **Révocation :**
  - bouton dans ORION Control : appel à `https://oauth2.googleapis.com/revoke`, puis effacement local du jeton ;
  - la page « Applications tierces » de votre compte Google permet aussi de révoquer de votre côté.

### Gestion des jetons

- **Où :** `data/personal/google_token.enc`, chiffré en AES-GCM (bibliothèque `cryptography`), droits 600, compte
  `orion`.
- **Clé :** `ORION_DATA_KEY` dans `.env`, générée une fois et jamais journalisée.
- **Ce qui est stocké :** le jeton de rafraîchissement seul ; le jeton d'accès reste en mémoire.
- **Journaux :** jamais de jeton, de code d'autorisation ou d'adresse de rappel complète. Un test le vérifie, comme
  pour le masquage des textes privés en V2.
- **Rotation :** si la clé change ou si le déchiffrement échoue, le jeton est considéré comme perdu et il faut se
  reconnecter. Il n'est jamais réécrit en clair.

## 3. Moteur d'intérêts personnels (Personal Interest Engine)

### Données d'entrée (minimisées)

Seulement ce qui sert à reconnaître un sujet :

| Source | Ce qui est gardé |
|---|---|
| Recherche Google | texte de la requête et horodatage |
| YouTube | titre de la vidéo ou requête, chaîne et horodatage |
| Interactions avec le journal | lu, ignoré, approfondi, masqué, suivi, correction |
| Abonnements YouTube | si l'étape B est acceptée |
| Demandes explicites | « suis l'actualité de… », surveillances WATCH, faits retenus (`memory.json`) |

- **Écartés à l'import :** sites visités (adresses), emplacements, appareils, résultats cliqués, publicités.
- **L'archive brute** est supprimée après l'import, par défaut ; elle peut être gardée si vous le décidez.

### Modèle d'un intérêt

```
Interest
  id, label (« cartes graphiques NVIDIA »), keywords, entities
  state        : proposé | confirmé | masqué | supprimé
  confidence   : 0..1
  durability   : ponctuel | récurrent | durable
  trend        : en hausse | stable | en baisse
  first_seen, last_seen
  provenance   : [{source, kind (recherche, vidéo, abonnement, interaction, demande), count, first, last}]
  user_edited  : vrai si corrigé par vous (jamais écrasé par l'apprentissage)
```

### Règles de construction (déterministes, le LLM ne sert qu'à nommer et regrouper)

1. **Extraction de sujets :**
   - mots et noms propres des requêtes et titres, comme pour le regroupement NEWS ;
   - regroupement par proximité, avec Jaccard et noms propres ;
   - le LLM local (Katana) peut proposer un libellé lisible : schéma validé, données non fiables isolées.
2. **Ponctuel ou durable :**
   - **ponctuel :** vu sur une seule journée ;
   - **récurrent :** vu sur au moins 3 jours différents en 30 jours ;
   - **durable :** vu sur au moins 3 semaines différentes en 90 jours.
3. **Fréquence et importance :** les signaux sont pondérés par leur nature (une demande explicite pèse plus qu'une
   recherche) et décroissent avec le temps (demi-vie de 30 jours).
4. **Tendance :** l'intensité des 14 derniers jours est comparée à celle des 60 précédents.
5. **Nouveaux intérêts :** ils sont créés à l'état **« proposé »**. Ils n'entrent dans le journal qu'avec une confiance
   suffisante ou une fois confirmés. ORION Control affiche « ORION pense que vous suivez : … » ; vous confirmez,
   corrigez ou supprimez.
6. **Une recherche ne vaut ni opinion ni adhésion.** Un intérêt dit « sujet que vous consultez », jamais « ce que vous
   pensez ». Les explications du journal restent factuelles : « vous avez cherché ce sujet 6 fois en octobre ».
7. **Catégories sensibles, au sens de l'article 9 du RGPD :** santé, religion, opinions politiques, orientation
   sexuelle, origine, syndicat.
   - Un filtre lexical les écarte de la création automatique ; elles ne sont jamais déduites.
   - Elles n'existent que si vous les ajoutez vous-même, et sont marquées « sensible » (jamais lues à voix haute devant
     d'autres personnes).
8. **Correction, consultation, suppression :**
   - chaque intérêt est modifiable ;
   - une suppression efface aussi ses signaux d'origine ;
   - « tout effacer » vide le profil et les signaux.

### Stockage séparé

- **Données personnelles :** une base distincte, `data/personal/interests.db` (droits 600). Elles ne vont jamais dans
  la base publique de NEWS (`data/orion_v3.db`) ; les modules publics n'y ont pas accès.
- **Interface unique :** le briefing ne reçoit qu'une projection anonymisée, `InterestProfile.relevance(story) -> (score,
  raison)` ; il ne lit jamais l'historique.

## 4. Journal quotidien (Daily Briefing Engine)

### Composition (chaque matin à 07:30, heure réglable)

1. **Candidats :** événements NEWS des dernières 24 heures, ou depuis le précédent journal.
2. **Exclus :**
   - les doublons ;
   - les événements « archive » sans lien avec vos intérêts ;
   - les titres trompeurs : titre à sensation sans source confirmant, ou décalage entre le titre et le résumé (règles,
     puis LLM en confirmation) ;
   - les informations non vérifiées, sauf mention explicite « non confirmé ».
3. **Répartition (décision du 2 novembre) :** 60 % des places pour l'actualité générale, classée par importance
   (NEWS) ; 40 % pour vos intérêts, classés par correspondance. Les deux listes sont classées séparément, puis les
   quotas sont appliqués. Ils sont réglables. Tant que le moteur d'intérêts n'existe pas (P1), toutes les places vont à
   l'actualité générale. **Une information « à ne pas manquer »** (niveau alerte de NEWS) entre toujours, même au-delà
   du quota.
4. **Sélection :** de 5 à 10 informations, avec des quotas de diversité. Au plus 3 par rubrique, et au moins une
   « internationale » et une « économie » quand il y en a.
5. **Rubriques :**
   - À ne pas manquer ;
   - Vos sujets ;
   - Évolutions des sujets suivis ;
   - Technologie et sciences ;
   - International ;
   - Économie.

   « Sciences » (décidée) demande des flux dédiés : nouvelle catégorie `sciences`. International et Économie sont
   gardées.

### Fiche de chaque information

| Champ | Origine |
|---|---|
| Titre | titre du premier article fiable (jamais réécrit par le LLM) |
| Résumé | `synthesis.summarize` (vérifié), sinon le résumé de la source |
| Date de publication | `published_at` de l'article |
| Source et lien | `news_articles.source`, `url` |
| Fiabilité | officielle, confirmée par plusieurs sources, ou source unique non confirmée |
| Pourquoi pour vous | texte déterministe tiré de la provenance de l'intérêt (« lié à votre intérêt « RTX 5090 » : vos recherches et une surveillance WATCH ») ou « actualité majeure » |

### Diffusion

| Canal | Contenu |
|---|---|
| Matin, automatique | voix (5 titres et l'invitation « voulez-vous des détails ? ») si vous êtes à la maison, sinon message écrit court ; par le routeur d'alertes, heures calmes respectées |
| ORION Control, vue « Journal » | version écrite complète, avec liens et boutons « lu », « approfondir », « masquer ce sujet », « suivre », « pas pertinent » |
| Voix à la demande | « le journal », « les nouvelles du jour » : outil `news_briefing` |
| Approfondir | « parle-moi de la deuxième », « approfondis l'info sur… » : outil `news_more` ; synthèse plus longue, sources et évolution |

## 5. Apprentissage continu

| Interaction | Effet |
|---|---|
| Lue (ouverte dans ORION Control) | + sur l'intérêt correspondant |
| Ignorée (montrée, non ouverte en 48 h) | léger − ; jamais d'effet sur une « actualité majeure » |
| Approfondie (voix ou bouton) | ++ |
| Sujet masqué | intérêt masqué ; plus jamais dans « Vos sujets » ; réversible |
| « Suis ce sujet » | intérêt confirmé et durable |
| « Pas pertinent » | correction du poids ; trois corrections : intérêt proposé à la suppression |

- **Aucune déduction de caractéristique personnelle** : âge, santé, situation, opinions. Le profil ne contient que des
  sujets.
- **Interactions :** stockées dans la base personnelle, 180 jours (décidé, réglable). Au-delà, seules restent des
  tendances agrégées : intérêt, poids, période, type de source. La provenance est gardée au niveau du résumé, et les
  agrégats restent consultables, corrigeables et supprimables.

## 6. Sécurité et confidentialité

- **Minimisation** (§ 3) et séparation des bases personnelle et publique.
- **Jetons chiffrés** (§ 2) ; aucun secret ni texte d'historique dans les journaux, seulement des compteurs.
- **Traitement local :**
  - les historiques et le profil ne quittent jamais le réseau local ;
  - le seul LLM utilisé est celui du Katana, chez vous ;
  - aucun envoi à un service extérieur sans autorisation explicite et nominative.
- **Données non fiables :** les contenus importés et les articles ne sont jamais des instructions. Les mêmes garde-fous
  que NEWS s'appliquent : balises, schéma, synthèse vérifiée. Une requête de recherche importée qui contient « ignore
  tes consignes… » reste une chaîne de caractères.
- **Suppression et révocation :**
  - « Révoquer Google » : révocation chez Google et effacement du jeton ;
  - « Effacer mes données personnelles » : base personnelle, archives d'import, interactions ;
  - les deux sont accessibles dans ORION Control et tracés dans l'historique, sans contenu.

## 7. Plan d'implémentation (compatible avec le code existant)

| Lot | Contenu | Validation |
|---|---|---|
| P1 | Briefing sans données personnelles : rubriques, fiche complète (lien, fiabilité, « pourquoi : actualité majeure »), catégorie `sciences`, détection des titres trompeurs, vue « Journal », outils `news_briefing` et `news_more` | tests de composition (quotas, doublons, liens, aucune invention) ; essai réel d'une semaine avec de vrais flux |
| P2 | Stockage personnel chiffré (`orion/knowledge/personal_store.py`) et importeur Takeout (JSON « Mon activité » et YouTube), minimisation, suppression de l'archive | tests sur archives fictives ; aucune donnée personnelle dans la base publique ni dans les journaux |
| P3 | Moteur d'intérêts : extraction, durabilité, tendance, filtre des catégories sensibles, états proposé et confirmé, vue « Mes intérêts » (consulter, corriger, supprimer, tout effacer) | tests de règles (ponctuel ou durable, sensibles jamais créés, suppression en cascade) |
| P4 | Pertinence personnelle et apprentissage par interactions dans le journal | tests de classement ; une « actualité majeure » jamais écartée ; corrections prises en compte |
| P5 | OAuth Google (facultatif) : client PKCE et redirection locale dans ORION Control, jeton chiffré côté Core, révocation ; `youtube.readonly`, puis Data Portability si vous le décidez | essai avec votre compte, **seulement après votre accord** sur les permissions ; révocation vérifiée |

- **Emplacement du code :** P2 et P3 vont dans `orion/knowledge/`, pour préparer le module KNOWLEDGE
  (`architecture_cible.md`) plutôt que dans NEWS. NEWS ne fait que consommer un profil.
- **Ce qui reste en place :** NEWS en cours n'est pas interrompu. Le bulletin actuel reste, et le journal s'ajoute à
  côté jusqu'à validation.

## 8. Permissions Google nécessaires (récapitulatif pour votre décision)

Rien n'est demandé tant que vous n'avez pas choisi.

| Choix | Permissions | Ce que vous faites |
|---|---|---|
| Minimal (recommandé) | aucune | un export Takeout (Recherche et YouTube, JSON), déposé dans ORION Control |
| + YouTube | `youtube.readonly` | créer un projet Google Cloud, un identifiant « application de bureau », vous ajouter comme utilisateur test |
| + mise à jour automatique | `dataportability.myactivity.search`, `dataportability.myactivity.youtube` | idem, et activer la Data Portability API dans le projet |
| Agenda par API | `calendar.events.readonly` | non recommandé : l'ICS fonctionne déjà |

## 9. Décisions prises (2 novembre 2026)

Le détail est dans `roadmap.md`.

1. **Accès :** Takeout d'abord, Data Portability API plus tard, YouTube API secondaire. Aucun accès Google réel sans
   votre validation explicite ; ni scraping ni contournement des API.
2. **Archive brute :** supprimée après « import vérifié » (définition : `roadmap.md`, § 2.8). Elle ne va jamais sur le
   NAS. Elle est traitée par le compte `orion-import`, qui dépose les faits minimisés dans une zone d'attente
   vérifiée par le Core.
3. **Interactions :** 180 jours, puis agrégats seulement ; provenance et date conservées.
4. **Journal :** 07:30 ; 60 % général / 40 % intérêts ; rubrique Sciences ; tout réglable.
5. **`ORION_DATA_KEY` :** hors du dépôt et du `.env`. Elle est fournie au service par `LoadCredential=` depuis
   `/etc/orion/credentials/` (root, 600). Elle est versionnée pour la rotation, sauvegardée hors ligne et jamais à
   côté des sauvegardes chiffrées. Aucune clé réelle avant votre validation : clés factices en test.
6. **Reste à fixer :** la liste exacte des flux, sciences comprises, et leur fiabilité, avant l'essai réel de P1.
