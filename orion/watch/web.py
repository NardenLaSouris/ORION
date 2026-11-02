"""Source Web automatique de WATCH : chaque surveillance d'articles ou d'événements cherche aussi sur le Web, par
l'instance SearXNG locale d'ORION, sans que vous ayez à configurer de flux.

- La requête est tirée de la condition de la surveillance (mots-clés, jeu), jamais d'un texte libre envoyé tel quel ;
  pour un jeu connu, une seconde requête vise son site officiel.
- Résultats du dernier mois seulement (``time_range``) ; un résultat daté de plus de ``MAX_AGE_DAYS`` est écarté.
- La fiabilité vient du site : officiel (éditeur du jeu, site public d'un fabricant), média connu, sinon communauté.
  Seule une source officielle peut confirmer un événement (orion/watch/events.py) : un blog ou un forum ne déclenche
  jamais une alerte « confirmé ».
- Les offres (prix, stock) ne viennent jamais d'ici : une page de recherche ne donne ni prix final ni disponibilité
  fiables, et ORION ne « gratte » aucun site marchand.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from orion.platform.feeds import SourceError
from orion.watch.connectors import ITEMS, Connector, Item
from orion.watch.products import plain

log = logging.getLogger(__name__)

MAX_AGE_DAYS = 31
GAMES = {"pokemon_go": ("Pokémon GO", ("pokemongolive.com", "nianticlabs.com", "niantic.helpshift.com"))}
# Sites officiels hors jeux (fabricants, éditeurs) : annonce « officielle » seulement s'ils la publient eux-mêmes.
OFFICIAL = ("nvidia.com", "amd.com", "intel.com", "apple.com", "microsoft.com", "playstation.com", "xbox.com",
            "nintendo.com", "nintendo.fr", "gouv.fr", "europa.eu")
MEDIA = ("lemonde.fr", "france24.com", "francetvinfo.fr", "lefigaro.fr", "liberation.fr", "20minutes.fr",
         "ouest-france.fr", "bfmtv.com", "afp.com", "reuters.com", "apnews.com", "bbc.com", "theverge.com",
         "numerama.com", "01net.com", "frandroid.com", "lesnumeriques.com", "clubic.com", "jeuxvideo.com",
         "gamekult.com", "millenium.org", "jv.com", "ign.com", "eurogamer.net", "gamespot.com", "polygon.com",
         "dexerto.com", "dexerto.fr", "pokebip.com", "leekduck.com", "serebii.net", "pokemon.com")
# Mots de remplissage d'une demande (« quand les raids sont disponibles ») : ni cherchés ni exigés.
FILLER = {"disponible", "disponibles", "dispo", "sont", "seront", "sera", "arrivent", "arrive", "arriveront",
          "sortent", "sort", "revient", "reviennent", "retour", "prochain", "prochaine", "prochains", "prochaines"}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _on(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def reliability_of(url: str, condition: dict) -> str:
    host = host_of(url)
    game = GAMES.get(condition.get("game") or "")
    if (game and _on(host, game[1])) or _on(host, OFFICIAL):
        return "official"
    if _on(host, MEDIA):
        return "media"
    return "community"


def strong_keywords(keywords) -> list[str]:
    """Mots-clés porteurs de sens (sans le remplissage) ; tous si rien d'autre ne reste."""
    strong = [k for k in keywords if plain(k) not in FILLER]
    return strong or list(keywords)


def queries_for(condition: dict) -> list[str]:
    words = " ".join(strong_keywords(condition.get("keywords") or ()))
    game = GAMES.get(condition.get("game") or "")
    if not words:
        return []
    if game:
        return [f"{game[0]} {words}", f"{words} site:{game[1][0]}"]
    return [words]


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class WebSearchConnector(Connector):
    """``fetch({"condition": …})`` : articles trouvés sur le Web pour cette condition."""

    kind = ITEMS
    per_watch = True  # la requête dépend de la surveillance (cache et limite propres à chacune)
    reliability = "selon le site"

    def __init__(self, provider, name: str = "web", clock=None):
        import time

        self.name, self._provider, self._clock = name, provider, clock or time.time
        self.url = "SearXNG"

    def fetch(self, query: dict) -> list[Item]:
        condition = (query or {}).get("condition") or {}
        searches = queries_for(condition)
        if not searches:
            return []
        items, seen, errors = [], set(), []
        oldest = self._clock() - MAX_AGE_DAYS * 86400
        for text in searches:
            try:
                results = self._provider.search(text, time_range="month")
            except TypeError:  # moteur sans filtre de date
                results = self._provider.search(text)
            except Exception as exc:  # noqa: BLE001 - noté par la vérification, jamais remplacé par une invention
                errors.append(str(exc)[:120])
                continue
            for r in results:
                if r.url in seen:
                    continue
                seen.add(r.url)
                published = _timestamp(r.published_at)
                if published is not None and published < oldest:
                    continue  # vieil article : un événement passé n'est jamais annoncé comme nouveau
                items.append(Item(f"web:{host_of(r.url)}", r.title, r.snippet or "", r.url, published,
                                  reliability_of(r.url, condition), r.url))
        if errors and not items and len(errors) == len(searches):
            raise SourceError("recherche Web indisponible : " + errors[0])
        return items


def describe(condition: dict) -> dict[str, Any]:
    """Pour ORION Control : ce que la source Web cherchera pour cette condition."""
    return {"requêtes": queries_for(condition)}
