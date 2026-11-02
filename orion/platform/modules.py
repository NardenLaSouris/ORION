"""Registre des modules V3 : démarrage dans l'ordre des dépendances, arrêt inverse, état et API pour ORION Control.

Un module (WATCH, NEWS, EVOLVE, PURCHASE) expose :
- ``name`` (identifiant d'URL), ``title`` (affiché), ``requires`` (modules dont il dépend) ;
- ``start`` / ``stop`` ;
- ``status()`` : un petit dictionnaire (état, compteurs, dernière erreur) pour le tableau de bord ;
- ``admin(method, parts, query, data)`` : ses routes sous ``/api/modules/<nom>/…`` -> (code HTTP, contenu JSON).

Un module dont le démarrage échoue est marqué « en échec » ; ceux qui en dépendent ne démarrent pas, les autres oui.
Les dépendances circulaires sont refusées à l'enregistrement.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

NOT_FOUND = (404, {"status": "error", "error": "not_found"})


class ModuleError(RuntimeError):
    pass


@runtime_checkable
class Module(Protocol):
    name: str
    title: str
    requires: tuple[str, ...]

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> dict: ...

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]: ...


class BaseModule:
    name = "module"
    title = "Module"
    requires: tuple[str, ...] = ()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def status(self) -> dict:
        return {}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        return NOT_FOUND


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, Module] = {}
        self._state: dict[str, str] = {}
        self._errors: dict[str, str] = {}

    def register(self, module: Module) -> None:
        if not isinstance(module, Module):
            raise ModuleError(f"{module!r} n'est pas un module")
        if module.name in self._modules:
            raise ModuleError(f"Module déjà enregistré : {module.name}")
        self._modules[module.name] = module
        try:
            self.order()
        except ModuleError:
            del self._modules[module.name]
            raise
        self._state[module.name] = "arrêté"

    def get(self, name: str) -> Module | None:
        return self._modules.get(name)

    def names(self) -> list[str]:
        return list(self._modules)

    def order(self) -> list[str]:
        """Ordre de démarrage (dépendances d'abord) ; ModuleError sur un cycle. Une dépendance absente est permise :
        le module dépendant sera simplement refusé au démarrage."""
        done: list[str] = []
        visiting: set[str] = set()

        def visit(name: str, path: tuple[str, ...]) -> None:
            if name in done or name not in self._modules:
                return
            if name in visiting:
                raise ModuleError("Dépendance circulaire : " + " -> ".join((*path, name)))
            visiting.add(name)
            for dependency in self._modules[name].requires:
                visit(dependency, (*path, name))
            visiting.discard(name)
            done.append(name)

        for name in self._modules:
            visit(name, ())
        return done

    def start(self) -> None:
        for name in self.order():
            module = self._modules[name]
            missing = [d for d in module.requires if self._state.get(d) != "actif"]
            if missing:
                self._state[name], self._errors[name] = "non démarré", f"dépend de : {', '.join(missing)}"
                log.warning("Module %s non démarré : %s indisponible(s)", name, ", ".join(missing))
                continue
            try:
                module.start()
                self._state[name] = "actif"
                self._errors.pop(name, None)
                log.info("Module %s démarré", name)
            except Exception as exc:  # noqa: BLE001 - un module en échec n'empêche ni ORION ni les autres modules
                self._state[name], self._errors[name] = "en échec", f"{type(exc).__name__}: {exc}"[:300]
                log.exception("Module %s : démarrage impossible", name)

    def stop(self) -> None:
        for name in reversed(self.order()):
            if self._state.get(name) != "actif":
                continue
            try:
                self._modules[name].stop()
            except Exception:  # noqa: BLE001
                log.exception("Module %s : arrêt incomplet", name)
            self._state[name] = "arrêté"

    def status(self) -> list[dict]:
        out = []
        for name, module in self._modules.items():
            try:
                details = module.status()
            except Exception as exc:  # noqa: BLE001 - l'état d'un module ne fait jamais tomber l'API
                details = {"erreur": f"{type(exc).__name__}"}
            out.append({"name": name, "title": module.title, "state": self._state.get(name, "arrêté"),
                        "error": self._errors.get(name, ""), "requires": list(module.requires), "details": details})
        return out

    def admin(self, method: str, route: list[str], query: dict, data: Any) -> tuple[int, Any]:
        """``/api/modules`` : état de tous ; ``/api/modules/<nom>/…`` : routes du module (s'il est actif)."""
        if not route:
            return (200, self.status()) if method == "GET" else NOT_FOUND
        module = self._modules.get(route[0])
        if module is None:
            return 404, {"status": "error", "error": "module_not_found"}
        if self._state.get(route[0]) != "actif":
            return 503, {"status": "error", "error": "module_inactive", "message": f"Module {route[0]} inactif."}
        return module.admin(method, route[1:], query, data)
