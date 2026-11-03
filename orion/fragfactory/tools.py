"""Outils vocaux Frag Factory (enregistrés seulement si ``[fragfactory] enabled = true``).

Règles communes :
- **identifiant de clip jamais inventé** : paramètre masqué ``clip`` lu dans les mots (``references``), transformé
  en clip concret par ``check`` *avant* la permission et la confirmation ; un identifiant proposé par le LLM est
  rejeté par ``evidence`` ;
- **lecture = aucune modification** : les outils de lecture et de conseil (recommandation de style comprise) ne
  changent rien et ne lancent aucun rendu ;
- **actions confirmées** : valider, refuser, demander une retouche, produire une version passent par la
  confirmation d'ORION (question qui nomme le clip), puis Frag Factory revérifie le statut.
- Publication : **aucun outil**. Elle reste une action de l'interface de Frag Factory.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from orion.fragfactory import references
from orion.fragfactory.engine import FragFactoryModule, Unavailable, clip_announcement
from orion.tools.base import Param, Risk, Tool, ToolError
from orion.watch.products import plain

MAX_SPOKEN = 900
STATUS_SPOKEN = {"PENDING_REVIEW": "en attente de validation", "APPROVED": "validé", "REJECTED": "refusé",
                 "REVISION_REQUESTED": "à retravailler", "PUBLISHED": "publié"}
INTENT_WORDS = [("trois", ("trois versions", "3 versions", "plusieurs versions")),
                ("nerveux", ("nerveux", "nerveuse", "dynamique", "rythme", "punchy", "energique")),
                ("humour", ("humoristique", "drole", "humour", "marrant", "rigolo")),
                ("references", ("mes references", "mon style", "style habituel", "comme d habitude")),
                ("court", ("courte", "court", "percutant", "percutante", "rapide")),
                ("tendances", ("tendance", "tendances", "a la mode", "trend"))]
TIER_FILTERS = {"pepite": ("pepite", "pepites"), "bon": ("bon clip", "bons clips"), "a_trier": ("a trier",)}
REWORK_WORDS = {"start_earlier": ("plus tot", "contexte", "avant"), "end_later": ("plus tard", "reaction", "chute",
                                                                                  "fin trop tot", "jusqu au bout"),
                "start_later": ("debut traine", "commencer plus tard"), "end_earlier": ("fin traine", "finir plus tot"),
                "framing": ("cadrage", "recadr"), "voices": ("voix", "son"), "subtitles": ("sous titre", "sous-titre"),
                "effects": ("zoom", "ralenti", "effet")}


def _say(text: str) -> str:
    return text[:MAX_SPOKEN]


def intent_of(text: str) -> str | None:
    t = f" {plain(text)} "
    for intent, words in INTENT_WORDS:
        if any(f" {w} " in t for w in words):
            return intent
    return None


def tier_of(text: str) -> str | None:
    t = f" {plain(text)} "
    return next((tier for tier, words in TIER_FILTERS.items() if any(f" {w} " in t for w in words)), None)


def since_of(text: str) -> str | None:
    """Période dite : « aujourd'hui », « hier », « cette semaine », « ce mois-ci », « cette nuit »."""
    t = f" {plain(text)} "
    now = datetime.now(timezone.utc)
    spans = [(("cette nuit", "nuit derniere"), 14), (("aujourd hui",), 24), (("hier",), 48),
             (("cette semaine", "semaine"), 24 * 7), (("ce mois", "mois"), 24 * 31)]
    for words, hours in spans:
        if any(f" {w} " in t for w in words):
            return (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    return None


def rework_items(text: str) -> list[str] | None:
    t = f" {plain(text)} "
    items = [k for k, words in REWORK_WORDS.items() if any(w in t for w in words)]
    return items or None


def _clip_id(value: str) -> int:
    kind, arg = references.parse(value)
    if kind != "clip":
        raise ToolError("invalid_parameters", "Je ne sais pas de quel clip vous parlez.")
    return int(arg)


def fragfactory_tools(engine: FragFactoryModule) -> list[Tool]:
    def clip_param(pending_only: bool) -> Param:
        return Param(str, "clip désigné par la demande", required=True, max_length=80, hidden=True,
                     resolve=references.reference, reread=True, evidence=references.evidence,
                     check=lambda ref: engine.resolve(ref, pending_only=pending_only))

    def read(op: str, args: dict | None = None) -> Any:
        try:
            return engine.request(op, args or {})
        except Unavailable as exc:
            raise ToolError("fragfactory_unavailable", "Frag Factory ne répond pas pour le moment.") from exc

    def describe_clip(c: dict) -> str:
        parts = [f"« {c['title']} »", f"{c.get('tier_label', c.get('tier'))}",
                 STATUS_SPOKEN.get(c.get("status"), c.get("status", ""))]
        if c.get("duration_s"):
            parts.append(f"{round(c['duration_s'])} secondes")
        if c.get("category_labels"):
            parts.append(", ".join(c["category_labels"][:3]))
        text = ", ".join(p for p in parts if p) + "."
        if c.get("summary"):
            text += f" {c['summary'][:220].rstrip('.')}."
        if c.get("versions"):
            text += f" {len(c['versions'])} version(s) rendue(s)."
        return text

    # --- lecture ------------------------------------------------------------------------------------------------
    def status() -> dict:
        s = read("status")
        running = s.get("running") or {}
        doing = (f"Analyse en cours : {running.get('filename') or running.get('kind')} à "
                 f"{round((running.get('progress') or 0) * 100)} %. ") if running else "Aucun traitement en cours. "
        queued = sum(v for k, v in (s.get("jobs") or {}).items() if k.endswith(":queued"))
        return {"text": _say(f"Frag Factory est opérationnel. {doing}{queued} traitement(s) en file, "
                             f"{s.get('pending_reviews', 0)} clip(s) à valider.")}

    def list_clips(niveau: str | None = None, periode: str | None = None, recherche: str | None = None) -> dict:
        args: dict[str, Any] = {"order": "score", "limit": 5}
        if niveau:
            args["tier"] = niveau
        if periode:
            args["since"] = periode
        if recherche:
            args["q"] = recherche
        items = read("clips", args).get("items") or []
        engine._present([i["id"] for i in items])  # noqa: SLF001 - contexte de la conversation
        if not items:
            return {"text": "Je ne trouve aucun clip correspondant.", "count": 0}
        listed = " ; ".join(f"{n}. {i['title'][:60]} ({STATUS_SPOKEN.get(i['status'], i['status'])})"
                            for n, i in enumerate(items, 1))
        return {"text": _say(f"{len(items)} clip(s) : {listed}."), "count": len(items)}

    def pending() -> dict:
        items = read("clips", {"status": "PENDING_REVIEW", "order": "score", "limit": 5}).get("items") or []
        total = read("status").get("pending_reviews", len(items))
        engine._present([i["id"] for i in items])  # noqa: SLF001
        if not items:
            return {"text": "Aucun clip n'attend votre validation.", "count": 0}
        listed = " ; ".join(f"{n}. {i['title'][:60]}" for n, i in enumerate(items, 1))
        more = f" (les {len(items)} mieux notés)" if total > len(items) else ""
        return {"text": _say(f"{total} clip(s) attendent votre validation{more} : {listed}."), "count": total}

    def get_clip(clip: str) -> dict:
        c = read("clip", {"clip_id": _clip_id(clip)})
        engine.focus(c["id"])
        return {"text": _say(describe_clip(c)), "clip_id": c["id"], "status": c.get("status")}

    def play(clip: str) -> dict:
        cid = _clip_id(clip)
        c = read("clip", {"clip_id": cid})
        if not c.get("rendered"):
            raise ToolError("not_rendered", "Ce clip n'a pas encore de version rendue à regarder.")
        read("open_clip", {"clip_id": cid})
        engine.focus(cid)
        return {"text": f"J'ouvre « {c['title'][:80]} » sur votre PC.", "clip_id": cid}

    def statistics(periode: str | None = None) -> dict:
        s = read("stats", {"since": periode} if periode else {})
        t = s.get("tiers") or {}
        text = (f"{s.get('clips', 0)} clip(s){' sur la période' if periode else ''} : {t.get('pepite', 0)} pépite(s), "
                f"{t.get('bon', 0)} bon(s), {t.get('a_trier', 0)} à trier.")
        st = s.get("statuses") or {}
        if st:
            text += f" Validés : {st.get('APPROVED', 0)}, refusés : {st.get('REJECTED', 0)}, " \
                    f"en attente : {st.get('PENDING_REVIEW', 0)}."
        if s.get("funniest"):
            text += f" Le plus drôle : « {s['funniest']['title'][:60]} »."
        if s.get("profiles_used"):
            top = max(s["profiles_used"].items(), key=lambda kv: kv[1])
            text += f" Profil de montage le plus utilisé : {top[0]}."
        return {"text": _say(text)}

    def nightly_report() -> dict:
        items = read("reports", {"limit": 1}).get("items") or []
        if not items:
            return {"text": "Frag Factory n'a encore produit aucun bilan. Le prochain sera fait à l'heure réglée."}
        r = items[0]
        return {"text": _say(r.get("spoken", "")), "report_id": r.get("id")}

    def history(periode: str | None = None) -> dict:
        items = read("history", {"since": periode, "limit": 10} if periode else {"limit": 10}).get("items") or []
        if not items:
            return {"text": "Aucune analyse terminée sur cette période."}
        done = [i for i in items if i.get("status") == "done"]
        failed = [i for i in items if i.get("status") == "failed"]
        text = f"{len(done)} analyse(s) terminée(s)"
        if failed:
            text += f", {len(failed)} en échec"
        text += ". " + " ; ".join(f"{(i.get('filename') or '?')[:40]} : {i.get('clips', 0)} clip(s)" for i in done[:4])
        return {"text": _say(text + ".")}

    def analyze_style(clip: str) -> dict:
        cid = _clip_id(clip)
        d = read("style", {"clip_id": cid, "measure": True})
        engine.focus(cid)
        if not d.get("rendered"):
            return {"text": _say(d.get("message", "Ce clip n'a pas encore été monté.")
                                 + " Pourquoi il a été retenu : " + "; ".join(d.get("structure", {}).get(
                                     "why_selected", [])[:2]) + ".")}
        effects = "; ".join(f"{e['effect']} ({e['value']})" for e in d["configured"]["effects"][:5])
        text = f"Version {d.get('version')} : {effects}."
        m = d.get("measured")
        if m:
            text += (f" Mesuré sur la vidéo : {m.get('cuts_per_min')} coupes par minute, {m.get('zoom_count')} zoom(s)"
                     f", sous-titres {'détectés' if (m.get('subtitles') or {}).get('presence', 0) > 0.3 else 'peu visibles'}.")
        comp = d.get("comparison") or {}
        if comp.get("available"):
            if comp.get("habitual"):
                text += f" Comme vos références : {', '.join(comp['habitual'][:3])}."
            if comp.get("missing"):
                text += f" Absent par rapport à vos habitudes : {', '.join(comp['missing'][:3])}."
            if comp.get("unusual"):
                text += f" Inhabituel chez vous : {', '.join(comp['unusual'][:3])}."
        else:
            text += f" {comp.get('reason', '')}"
        why = d.get("structure", {}).get("why_selected", [])
        if why:
            text += f" Retenu pour : {why[0]}."
        if d.get("uncertainties"):
            text += " À noter : les mesures sont des estimations."
        return {"text": _say(text)}

    def recommend(clip: str, intention: str | None = None) -> dict:
        cid = _clip_id(clip)
        d = read("recommend", {"clip_id": cid, "intent": intention or "auto"})
        engine.focus(cid)
        engine._state("proposals", {"clip_id": cid, "proposals": d.get("proposals", [])})  # noqa: SLF001
        parts = []
        for p in d.get("proposals", [])[:3]:
            line = f"{p['number']}. {p['title']} : {p['rationale']}"
            if p.get("unsupported"):
                line += f" (non disponible : {p['unsupported'][0]['effect'].lower()})"
            parts.append(line)
        return {"text": _say(" ".join(parts) + " Dites « produis la version 1 » pour lancer l'une d'elles."
                             if parts else "Aucune proposition possible pour ce clip.")}

    # --- actions confirmées ----------------------------------------------------------------------------------------
    def _question(verb: str):
        def ask(params: dict) -> str:
            try:
                c = read("clip", {"clip_id": _clip_id(params["clip"])})
                return (f"{verb} « {c['title'][:80]} » ({c.get('tier_label', '')}, "
                        f"{STATUS_SPOKEN.get(c.get('status'), '')}, clip {c['id']}) ?")
            except ToolError:
                return f"{verb} ce clip ?"
        return ask

    def approve(clip: str) -> dict:
        r = engine.act("approve", _clip_id(clip), expected="PENDING_REVIEW")
        return {"text": "Clip validé. Il n'est pas publié : la publication reste à faire dans Frag Factory.",
                "status": r.get("status")}

    def reject(clip: str) -> dict:
        r = engine.act("reject", _clip_id(clip))
        return {"text": "Clip refusé.", "status": r.get("status")}

    def revision(clip: str, elements: list | None = None, note: str | None = None) -> dict:
        r = engine.act("revision", _clip_id(clip), items=list(elements or []), note=note or "")
        auto = r.get("automatic") or []
        text = "Retouche demandée"
        text += ", nouvelles bornes appliquées et nouveau rendu lancé." if auto else "."
        return {"text": text, "status": r.get("status")}

    def render(clip: str, proposition: int | None = None) -> dict:
        cid = _clip_id(clip)
        saved = engine._fresh("proposals") or {}  # noqa: SLF001
        if saved.get("clip_id") != cid or not saved.get("proposals"):
            raise ToolError("no_proposal", "Demandez d'abord une proposition de montage pour ce clip.")
        number = proposition or 1
        p = next((x for x in saved["proposals"] if x.get("number") == number), None)
        if p is None:
            raise ToolError("not_found", f"Il n'y a pas de proposition {number}.")
        extra = {"overrides": p.get("overrides") or {}, "title": str(p.get("title", ""))[:120]}
        if p.get("base_profile"):
            extra["base_profile"] = p["base_profile"]
        if p.get("target_duration_s"):
            extra["target_duration_s"] = float(p["target_duration_s"])
        r = engine.act("version", cid, **extra)
        return {"text": f"Nouvelle version lancée ({p['title']}). Elle reviendra dans votre file de validation ; "
                        "les versions précédentes sont conservées.", "job": r.get("job")}

    def render_question(params: dict) -> str:
        saved = engine._fresh("proposals") or {}  # noqa: SLF001
        n = params.get("proposition") or 1
        p = next((x for x in saved.get("proposals", []) if x.get("number") == n), None)
        return f"Je produis une nouvelle version « {p['title']} » de ce clip ?" if p else \
            "Je produis une nouvelle version de ce clip ?"

    level = Param(str, "niveau de qualité dit", required=False, max_length=12, hidden=True, resolve=tier_of,
                  reread=True)
    period = Param(str, "période dite", required=False, max_length=40, hidden=True, resolve=since_of, reread=True)
    search = Param(str, "mots-clés du clip cherché, avec les mots de la demande", required=False, max_length=80)
    intent = Param(str, "intention de montage", required=False, max_length=16, hidden=True, resolve=intent_of,
                   reread=True)
    items = Param(list, "ce qui manque au clip", required=False, hidden=True, resolve=rework_items, reread=True)
    note = Param(str, "ce qui manque, avec les mots de la demande", required=False, max_length=300)
    proposal = Param(int, "numéro de la proposition de montage", required=False, minimum=1, maximum=3)
    say = lambda r: r["text"]  # noqa: E731
    return [
        Tool("fragfactory_status", "État de Frag Factory : traitements en cours, file, clips à valider.", {},
             {"text": "état"}, Risk.SAFE, status, say=say, capability="READ"),
        Tool("fragfactory_list_clips", "Liste des clips Frag Factory (meilleurs d'abord), filtrés par niveau, période "
             "ou mots-clés.", {"niveau": level, "periode": period, "recherche": search}, {"text": "liste"}, Risk.SAFE,
             list_clips, say=say, capability="READ"),
        Tool("fragfactory_list_pending_reviews", "Clips qui attendent la validation.", {}, {"text": "liste"},
             Risk.SAFE, pending, say=say, capability="READ"),
        Tool("fragfactory_get_clip", "Fiche d'un clip : titre, niveau, statut, durée, catégories, pourquoi il a été "
             "retenu.", {"clip": clip_param(False)}, {"text": "fiche"}, Risk.SAFE, get_clip, say=say,
             capability="READ"),
        Tool("fragfactory_play_clip", "Ouvre un clip dans Frag Factory sur le PC pour le regarder.",
             {"clip": clip_param(False)}, {"text": "ouvert"}, Risk.SAFE, play, say=say, capability="READ"),
        Tool("fragfactory_get_statistics", "Statistiques de Frag Factory (clips, niveaux, validations, profils).",
             {"periode": period}, {"text": "statistiques"}, Risk.SAFE, statistics, say=say, capability="READ"),
        Tool("fragfactory_get_nightly_report", "Dernier bilan de Frag Factory (« le bilan de la nuit »).", {},
             {"text": "bilan"}, Risk.SAFE, nightly_report, say=say, capability="READ"),
        Tool("fragfactory_get_analysis_history", "Analyses terminées récemment par Frag Factory.",
             {"periode": period}, {"text": "historique"}, Risk.SAFE, history, say=say, capability="READ"),
        Tool("fragfactory_analyze_clip_style", "Décrit le style de montage d'un clip, le compare à vos références et "
             "explique pourquoi il a été retenu.", {"clip": clip_param(False)}, {"text": "analyse"}, Risk.SAFE,
             analyze_style, say=say, capability="READ"),
        Tool("fragfactory_recommend_editing_style", "Propose des styles de montage pour un clip (ne lance aucun "
             "rendu).", {"clip": clip_param(False), "intention": intent}, {"text": "propositions"}, Risk.SAFE,
             recommend, say=say, capability="READ"),
        Tool("fragfactory_approve_clip", "Valide un clip (ne le publie pas).", {"clip": clip_param(True)},
             {"text": "validé"}, Risk.CONFIRMATION_REQUIRED, approve, question=_question("Je valide"), say=say,
             capability="WRITE_DATA"),
        Tool("fragfactory_reject_clip", "Refuse un clip.", {"clip": clip_param(True)}, {"text": "refusé"},
             Risk.CONFIRMATION_REQUIRED, reject, question=_question("Je refuse"), say=say, capability="WRITE_DATA"),
        Tool("fragfactory_request_revision", "Demande une retouche d'un clip (ce qui manque).",
             {"clip": clip_param(False), "elements": items, "note": note}, {"text": "retouche"},
             Risk.CONFIRMATION_REQUIRED, revision, question=_question("Je demande une retouche de"), say=say,
             capability="WRITE_DATA"),
        Tool("fragfactory_request_render", "Produit une nouvelle version d'un clip à partir d'une proposition de "
             "montage faite juste avant.", {"clip": clip_param(False), "proposition": proposal},
             {"text": "rendu lancé"}, Risk.CONFIRMATION_REQUIRED, render, question=render_question, say=say,
             capability="WRITE_DATA"),
    ]


READ_TOOLS = ("fragfactory_status", "fragfactory_list_clips", "fragfactory_list_pending_reviews",
              "fragfactory_get_clip", "fragfactory_play_clip", "fragfactory_get_statistics",
              "fragfactory_get_nightly_report", "fragfactory_get_analysis_history", "fragfactory_analyze_clip_style",
              "fragfactory_recommend_editing_style")
__all__ = ["fragfactory_tools", "READ_TOOLS", "clip_announcement", "re"]
