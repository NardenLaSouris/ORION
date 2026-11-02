# ORION NEWS

Veille d'actualités autonome. Paquet `orion/news/`, inactif par défaut (`[news] enabled = false`).

## Deux modes

1. **Bulletins programmés.**
   - **Heures :** `08:00` et `20:00` par défaut, modifiables dans ORION Control (jusqu'à 6 heures).
   - **Contenu :** les événements au-dessus du seuil « bulletin », nouveaux ou qui ont évolué depuis le précédent. Ils
     sont marqués « Évolution : » s'ils avaient déjà été donnés.
   - **Pour chaque sujet :** statut (confirmé, ou non confirmé à ce stade), synthèse, faits confirmés, éléments
     incertains, sources et dates de publication.
   - **Actualité faible :** une phrase courte, jamais de remplissage.
   - **Après un redémarrage :** un bulletin manqué de moins de 2 heures est publié, jamais deux fois.
2. **Alertes spontanées.**
   - **Premier signalement :** un événement au-dessus du seuil « alerte » est signalé une fois.
   - **Nouveau signalement :** seulement s'il évolue vraiment (faits nouveaux et score en hausse d'au moins 10) ou s'il
     vient d'être confirmé.
   - **Une simple répétition médiatique** (mêmes faits, autre média) ne déclenche rien.

## Traitement

Il est déterministe (`pipeline.py`) et se déroule ainsi :
1. **Collecte :** flux RSS ou Atom de `[news] feeds`, avec leur catégorie et la fiabilité de la source.
2. **Normalisation :** texte brut et adresse canonique, sans paramètres de suivi. Un article de plus de 48 heures est
   écarté.
3. **Dédoublonnage :** même adresse, ou même titre.
4. **Regroupement par événement :**
   - les mots du titre doivent être proches (Jaccard ≥ 0,30) ;
   - les noms propres doivent être compatibles : « Séisme à Lyon » n'est pas « Séisme à Marseille » ;
   - entre 0,15 et 0,30, le LLM départage, s'il est disponible.
5. **Analyse :** l'événement est « confirmé » s'il a une source officielle, ou deux sources indépendantes fiables
   (agence, média).
6. **Score sur 100** (voir plus bas).
7. **Synthèse** (`synthesis.py`).
8. **Distribution :** par le routeur d'alertes commun.

## Score et seuils (réglages, pas des vérités)

`score = 100 × importance × qualité`, réduit de 15 % si l'événement n'est pas confirmé.

- **Importance**, avec des poids réglables :

  | Composante | Poids | Exemples |
  |---|---|---|
  | gravité | 30 | conflit, attentat, catastrophe ; crise, grève, sanctions |
  | portée | 20 | international, national, nombre de sources |
  | conséquences | 10 | pénurie, coupures, évacuation, hausse des prix |
  | évolution | 10 | nouveau développement |

- **Qualité**, entre 0,5 et 1 : fiabilité de la meilleure source et nouveauté. Une nouvelle anodine, même fraîche et
  bien sourcée, reste aux archives.

| Score | Traitement |
|---|---|
| 0–29 | archivage |
| 30–49 | bulletin |
| 50–69 | bulletin prioritaire |
| 70–84 | alerte spontanée |
| 85–100 | alerte prioritaire (niveau `CRITICAL`) |

Les seuils sont modifiables dans ORION Control ; ils doivent rester croissants.

## Notifications

Elles passent par le routeur commun (`orion/platform/alerts.py`), selon la présence :

| Présence | Canaux |
|---|---|
| à la maison | voix |
| absent | écrit, plus un appel pour une alerte |
| inconnue | écrit seulement |

- **Heures calmes :** toujours respectées.
- **Budget d'appels propre à NEWS,** en plus du budget commun : 3 par jour et 15 minutes d'écart par défaut,
  modifiables. Il est relu depuis l'historique au démarrage.
- **Les bulletins n'appellent jamais.**

## Sécurité de l'information

- **Données non fiables :** articles et flux sont du texte brut. Pour le LLM, ils sont isolés entre balises, avec une
  consigne de ne suivre aucune instruction qu'ils contiennent. Un article ne peut pas fermer sa balise.
- **Rien n'est inventé.** Une synthèse du LLM est refusée si elle contient un nombre absent des articles, une adresse
  Web ou une citation entre guillemets. ORION revient alors à la synthèse sans LLM : titre, statut et sources.
- **Faits et incertitudes séparés :** les faits confirmés et les éléments incertains sont présentés à part.

## Mode dégradé (Katana indisponible)

La collecte, le dédoublonnage, le regroupement (sans le cas douteux), le score, les bulletins et les alertes
continuent. Seules les synthèses rédigées disparaissent.

## Interfaces

| Interface | Points d'entrée |
|---|---|
| Voix | `news_bulletin` (« quelles sont les actualités ? ») : le dernier bulletin, recomposé en aperçu s'il date de plus de 6 heures |
| API `/api/modules/news` | `GET` état ; `GET/PUT settings` (heures, catégories, seuils, appels, alertes et bulletins actifs) ; `GET sources` (état de chaque flux) ; `GET stories[?category=]` ; `GET stories/<id>` (articles) ; `GET bulletins` ; `POST bulletins` (aperçu, jamais envoyé) ; `GET alerts` ; `POST collect` |
| WATCH | source `news` : les articles récents, pour « préviens-moi si l'on annonce… » |
| ORION Control | section NEWS |

## Configuration

```toml
[news]
enabled = true
bulletin_times = ["08:00", "20:00"]
[news.feeds.afp]
url = "https://…/rss"
category = "geopolitique"   # geopolitique, defense, tech_ia, france, economie
reliability = "agency"      # official, agency, media, blog
```

Aucun flux n'est livré par défaut : leur choix vous revient, ainsi que le respect des conditions d'utilisation des
éditeurs.

## Limites connues

- **Calibrage :** le lexique de gravité et les seuils ont été réglés sur des exemples, pas sur une semaine de vraie
  actualité. Ils sont à ajuster après usage.
- **Regroupement :** il repose sur les mots et les noms propres des titres. Deux titres très différents pour un même
  événement peuvent rester séparés si le LLM est indisponible.
