# État de la V3 — 2 novembre 2026

Branche `v3`, partie du tag `v2.0.0` (`663e743`). **Rien n'est déployé** : la production reste sur V2 (`a1b92a8`). Toute
la V3 est désactivée par défaut.

## Réalisé

| Étape | Contenu | Commit |
|---|---|---|
| V3-00 | branche `v3` depuis `v2.0.0`, copie de travail isolée sur le mini-PC (`~/orion-v3`) | `663e743` |
| V3-01 | socle commun `orion/platform` (base SQLite et migrations, historique, états, planificateur, limites, registre des modules, alertes selon la présence, LLM encadré, flux sûrs) ; route `/api/modules` et relais d'ORION Control ; configuration V3 inactive par défaut | `c54d389` |
| V3-02 | WATCH : surveillances persistantes, connecteurs, RTX 5090 et score d'opportunité, événements Pokémon GO, outils vocaux | `815167a` |
| V3-03 | NEWS : pipeline de veille, bulletins, alertes, synthèse sans invention, source pour WATCH | `03297ac` |
| V3-04 | EVOLVE niveaux 1 et 2 : observation, diagnostic, propositions, correctifs isolés sous permissions imposées par le code | `10b72c8` |
| V3-05 | PURCHASE en bac à sable : mandats, vérification stricte, niveaux, paiement simulé, révocation, arrêt global | `c820fd0`, `8c2e384` |
| Interface | ORION Control : sections Surveillances, Actualités, Évolution, Achats, Notifications ; configuration documentée ; corrections après démonstration | dernier commit |

## Tests

- **Suite complète** (mini-PC, `/opt/orion/.venv`, `nice`) : **2105 réussis, 0 échec, 5 ignorés**. Au départ de la
  branche, il y en avait 1955. Les 150 nouveaux tests se répartissent ainsi :

  | Fichier | Domaine |
  |---|---|
  | `test_platform.py` | socle |
  | `test_platform_api.py` | API et pont |
  | `test_watch.py` | WATCH |
  | `test_news.py` | NEWS |
  | `test_evolve.py` | EVOLVE, avec un vrai Git dans un dépôt temporaire |
  | `test_purchase.py` | PURCHASE |
  | `test_v3_build.py` | construction depuis la configuration |

- **Ignorés,** comme en V2 : trois tests audio des appels, qui demandent l'environnement de la passerelle, et deux tests
  de bout en bout avec les vrais moteurs.
- **Analyse statique** (ruff : pyflakes, bugbear, style) : aucune remarque sur le code V3.
- **Syntaxe de l'interface :** `node --check` sur `app.js`.
- **Interface vérifiée dans le navigateur,** sur un Core de démonstration aux données fictives :
  - les cinq sections s'affichent ;
  - le détail d'une surveillance s'ouvre ;
  - un diagnostic EVOLVE se lance ;
  - un achat complet passe en bac à sable : confirmation, commande, mandat épuisé ;
  - l'historique des notifications est complet, avec le budget d'appels respecté.

### Défauts trouvés en cours de route et corrigés (chacun avec un test)

- **Segments d'URL non décodés par l'API du Core.** Un identifiant accentué ou encodé arrivait tel quel ; c'était sans
  effet en V2.
- **WATCH — prix maximum ignoré :** « moins de 2 000 € » perdait son « € » à la normalisation.
- **WATCH — dates ISO jamais lues.**
- **WATCH — offres suspectes dans l'historique des prix,** ce qui faussait le « plus bas observé ». Trouvé pendant la
  démonstration.
- **NEWS — score trop généreux** pour les nouvelles anodines. La fiabilité et la nouveauté modulent désormais
  l'importance au lieu de l'augmenter.
- **NEWS — regroupement trop large :** « Séisme à Lyon » et « Séisme à Marseille » étaient fusionnés. Les noms propres
  sont maintenant comparés.
- **EVOLVE — chemins de production mal relativisés :** `/opt/orion/orion/…` devenait `orion/orion/…`.
- **Interface :** état des modules lu au mauvais endroit, boutons inutiles sur un élément terminé, libellés internes.

## Problèmes restants et limites connues

1. **Aucun essai réel des modules V3.** Ils n'ont été testés qu'avec des sources, un LLM et des paiements simulés.
   Restent à essayer :
   - le routage vocal des nouvelles demandes (« surveille… », « quelles sont les actualités ? ») par le planificateur
     LLM ;
   - les flux RSS réels ;
   - la qualité des synthèses de Qwen3.
2. **Sources réelles à choisir.**
   - Aucun flux ni aucune source de prix n'est livré.
   - Le format `offers_json` suppose une source qui le fournit : API d'un marchand, comparateur autorisé ou petit
     service intermédiaire.
   - Le respect des conditions d'utilisation de chaque service vous revient.
3. **Calibrage de NEWS :** le lexique de gravité et les seuils sont réglés sur des exemples. Ils sont à ajuster après une
   semaine d'usage.
4. **EVOLVE au niveau 2 :** les tests du clone exécutent du code modifié sous le compte d'ORION, sans secrets mais sans
   isolement réseau. Un compte dédié ou un conteneur est recommandé avant de l'activer.
5. **PURCHASE :** bac à sable seulement. Brancher un vrai paiement demande :
   - un prestataire et une carte virtuelle dédiée plafonnée ;
   - votre autorisation explicite ;
   - une mission distincte.

   Les vrais marchands n'ont pas d'API d'achat commune : chaque marchand demanderait son adaptateur, et beaucoup
   n'autorisent pas la commande automatisée.
6. **Fuseau horaire :** les heures des bulletins suivent le fuseau du processus. Le service ORION de production tourne à
   l'heure de Paris (journal V2). À vérifier lors d'un futur déploiement.
7. **Erreur de configuration V3 :** une section mal remplie arrête le démarrage d'ORION avec un message clair. C'est
   voulu, pour ne pas tourner à moitié configuré, mais il faut le savoir.
8. **Interface d'ORION Control :** les tableaux larges défilent horizontalement dans une fenêtre étroite.

## Dépendances pour la suite

- **Décision de déploiement :** une mission séparée, après votre validation. Elle comprendra :
  - la fusion de `v3` ;
  - la configuration des sources ;
  - l'activation module par module, en commençant par NEWS et WATCH ;
  - une sauvegarde de la base `data/orion_v3.db`, créée seulement à l'activation.
- **Liste de sources fiables :** flux officiels Pokémon GO, médias et agences par catégorie, source de prix RTX
  conforme.
- **Pour EVOLVE niveau 2 :** un compte système dédié, et éventuellement un dépôt distant pour les Pull Requests.
- **Pour un PURCHASE réel :** prestataire de paiement, carte virtuelle, marchands compatibles.
