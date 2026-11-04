"""Plan d'une mission (MISSIONS, étape 5 ; docs/v3/missions.md) : un objectif décomposé en étapes dépendantes.

Ce module est le **noyau de planification**, sans effet sur le monde : il valide un plan (graphe sans cycle,
dépendances connues, étapes sensibles marquées), dit quelles étapes sont prêtes, propage l'état des étapes à partir
de celui de leurs tâches (lot F2), repère les blocages, accepte une révision du plan sans perdre ce qui est fait, et
produit le bilan. L'exécution reste celle des tâches persistantes (F2) et des permissions centrales (F1) : une étape
« action » devient une tâche, jamais un appel direct.

Natures d'étape :
- ``action`` : une tâche F2 (type enregistré par la plateforme, capacité déclarée par ce type) ;
- ``check`` : une lecture ou une vérification (outil en lecture seule, KNOWLEDGE, état d'un module) ;
- ``human`` : ce que vous seul pouvez faire ou décider (valider, fournir une information, agir hors d'ORION).

Une étape ``sensitive`` (achat, message extérieur, suppression, changement de code) exige votre validation avant
d'être lancée, même si le moteur central l'autoriserait (double garde : plan et F1).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Iterable

STEP_KINDS = ("action", "check", "human")
# État d'une étape ; « blocked » : une dépendance a échoué ou a été annulée (l'étape ne pourra pas partir seule).
STEP_STATES = ("pending", "ready", "waiting_approval", "running", "waiting_human", "done", "failed", "skipped",
               "cancelled", "blocked")
FINAL = frozenset({"done", "failed", "skipped", "cancelled"})
SUCCESS = frozenset({"done", "skipped"})
# État d'une tâche F2 -> état de l'étape qui la porte.
TASK_TO_STEP = {"pending": "running", "running": "running", "held": "waiting_human", "cancel_pending": "running",
                "done": "done", "failed": "failed", "expired": "failed", "cancelled": "cancelled",
                "interrupted": "waiting_human"}
MAX_STEPS = 40
STALL_HOURS = 24.0


class PlanError(ValueError):
    pass


@dataclass
class Step:
    id: str
    title: str
    kind: str = "action"
    after: list[str] = field(default_factory=list)  # dépendances : identifiants d'étapes
    task_kind: str = ""  # type de tâche F2 (étape « action »)
    params: dict = field(default_factory=dict)
    sensitive: bool = False
    approved: bool = False  # votre validation (étape sensible)
    state: str = "pending"
    task_id: str = ""
    note: str = ""  # dernier fait marquant (raison d'un échec, d'une attente)
    changed_at: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Blocker:
    step: str
    reason: str
    needs_you: bool


@dataclass
class Plan:
    objective: str
    steps: list[Step]
    revision: int = 1

    # --- Validation ---------------------------------------------------------------------------------------

    def validate(self) -> None:
        if not self.objective.strip():
            raise PlanError("objectif vide")
        if not 1 <= len(self.steps) <= MAX_STEPS:
            raise PlanError(f"de 1 à {MAX_STEPS} étapes")
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise PlanError("identifiants d'étapes en double")
        known = set(ids)
        for s in self.steps:
            if s.kind not in STEP_KINDS:
                raise PlanError(f"étape {s.id} : nature inconnue {s.kind!r}")
            if s.kind == "action" and not s.task_kind:
                raise PlanError(f"étape {s.id} : une action désigne son type de tâche")
            missing = [d for d in s.after if d not in known]
            if missing:
                raise PlanError(f"étape {s.id} : dépendance inconnue {', '.join(missing)}")
            if s.id in s.after:
                raise PlanError(f"étape {s.id} : dépend d'elle-même")
        self.order()  # lève sur un cycle

    def order(self) -> list[str]:
        """Ordre topologique (stable) ; PlanError sur un cycle."""
        by_id = {s.id: s for s in self.steps}
        done: list[str] = []
        state: dict[str, int] = {}

        def visit(sid: str, path: tuple[str, ...]) -> None:
            if state.get(sid) == 2:
                return
            if state.get(sid) == 1:
                raise PlanError("cycle : " + " -> ".join((*path, sid)))
            state[sid] = 1
            for dep in by_id[sid].after:
                visit(dep, (*path, sid))
            state[sid] = 2
            done.append(sid)

        for s in self.steps:
            visit(s.id, ())
        return done

    # --- Avancement ------------------------------------------------------------------------------------------

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise PlanError(f"étape inconnue : {step_id}")

    def refresh(self, task_states: dict[str, str] | None = None, now: float | None = None) -> list[Step]:
        """Recalcule chaque état d'après les tâches (``task_id`` -> état F2) et les dépendances. Rend les étapes
        prêtes à lancer (ni sensibles sans validation, ni humaines : celles-là attendent)."""
        now = time.time() if now is None else now
        task_states = task_states or {}
        by_id = {s.id: s for s in self.steps}
        for sid in self.order():
            s = by_id[sid]
            if s.state in FINAL:
                continue
            if s.task_id and s.task_id in task_states:
                self._set(s, TASK_TO_STEP.get(task_states[s.task_id], s.state), now)
                continue
            deps = [by_id[d] for d in s.after]
            if any(d.state in ("failed", "cancelled", "blocked") for d in deps):
                self._set(s, "blocked", now, "une étape préalable a échoué")
            elif all(d.state in SUCCESS for d in deps):
                if s.kind == "human":
                    self._set(s, "waiting_human", now)
                elif s.sensitive and not s.approved:
                    self._set(s, "waiting_approval", now)
                elif s.state in ("pending", "blocked"):
                    self._set(s, "ready", now)
            else:
                self._set(s, "pending", now)
        return [s for s in self.steps if s.state == "ready"]

    @staticmethod
    def _set(s: Step, state: str, now: float, note: str = "") -> None:
        if s.state != state:
            s.state, s.changed_at = state, now
            if note:
                s.note = note

    def approve(self, step_id: str) -> Step:
        s = self.step(step_id)
        if s.state != "waiting_approval":
            raise PlanError("cette étape n'attend pas de validation")
        s.approved = True
        s.state = "pending"
        return s

    def complete_human(self, step_id: str, note: str = "") -> Step:
        s = self.step(step_id)
        if s.state != "waiting_human" or s.kind != "human":
            raise PlanError("cette étape n'attend rien de vous")
        s.state, s.note = "done", note[:200]
        return s

    def blockers(self, now: float | None = None, stall_hours: float = STALL_HOURS) -> list[Blocker]:
        """Ce qui empêche la mission d'avancer, et si cela demande votre intervention."""
        now = time.time() if now is None else now
        out = []
        for s in self.steps:
            if s.state == "failed":
                out.append(Blocker(s.id, f"échec : {s.note or 'sans détail'}", True))
            elif s.state == "waiting_approval":
                out.append(Blocker(s.id, "attend votre validation (étape sensible)", True))
            elif s.state == "waiting_human":
                stalled = s.changed_at and now - s.changed_at > stall_hours * 3600
                out.append(Blocker(s.id, "attend une action de votre part" + (" depuis longtemps" if stalled else ""),
                                   True))
            elif s.state == "running" and s.changed_at and now - s.changed_at > stall_hours * 3600:
                out.append(Blocker(s.id, "en cours depuis plus de 24 h", False))
        return out

    # --- Révision ------------------------------------------------------------------------------------------

    def revise(self, steps: Iterable[Step]) -> "Plan":
        """Nouveau plan qui garde intactes les étapes terminées (même identifiant) : ce qui est fait ne se refait pas,
        et une étape sensible déjà validée qui change de contenu redemande votre validation."""
        old = {s.id: s for s in self.steps}
        new_steps = []
        for s in steps:
            before = old.get(s.id)
            if before is not None and before.state in SUCCESS:
                new_steps.append(before)
                continue
            if before is not None and before.approved and (before.task_kind, before.params) == (s.task_kind,
                                                                                               s.params):
                s.approved = True
            new_steps.append(s)
        plan = Plan(self.objective, new_steps, self.revision + 1)
        plan.validate()
        return plan

    # --- Bilan --------------------------------------------------------------------------------------------

    def finished(self) -> bool:
        return all(s.state in FINAL for s in self.steps)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for s in self.steps:
            counts[s.state] = counts.get(s.state, 0) + 1
        done = sum(counts.get(k, 0) for k in SUCCESS)
        return {"objective": self.objective, "revision": self.revision, "steps": len(self.steps),
                "done": done, "progress": round(done / len(self.steps), 2), "states": counts,
                "finished": self.finished(),
                "succeeded": self.finished() and all(s.state in SUCCESS for s in self.steps)}

    def report(self) -> str:
        """Bilan dit : ce qui est fait, ce qui reste, ce qui bloque."""
        s = self.summary()
        head = f"Mission « {self.objective} » : {s['done']} étape(s) sur {s['steps']} terminée(s)."
        blocked = self.blockers()
        if s["succeeded"]:
            return head + " Objectif atteint."
        if blocked:
            first = blocked[0]
            return head + f" Bloquée : {self.step(first.step).title} ({first.reason})."
        nxt = [st.title for st in self.steps if st.state in ("ready", "running")]
        return head + (f" En cours : {', '.join(nxt[:3])}." if nxt else "")

    def as_dict(self) -> dict:
        return {"objective": self.objective, "revision": self.revision, "steps": [s.as_dict() for s in self.steps]}

    @staticmethod
    def from_dict(data: dict) -> "Plan":
        plan = Plan(str(data.get("objective", "")), [Step(**s) for s in data.get("steps", [])],
                    int(data.get("revision", 1)))
        plan.validate()
        return plan
