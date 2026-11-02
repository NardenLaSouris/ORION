"""Diagnostic d'une anomalie : éléments qui le justifient, fichiers, dépendances, conséquences, pistes.

Toujours une base déterministe (lignes de la trace, extraits de code, imports, hypothèses par type d'exception) ; le
LLM, s'il est disponible, ajoute une analyse validée : il ne peut citer que des fichiers présents dans la trace ou
leurs dépendances, et chaque affirmation garde sa source (« règles » ou « llm »).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orion.evolve.observe import EXTERNAL, LATENCY, Anomaly
from orion.evolve.workspace import Workspace
from orion.platform.intelligence import StructuredLLM

HYPOTHESES = {
    "KeyError": "clé absente d'un dictionnaire (donnée incomplète, format changé, valeur jamais initialisée)",
    "AttributeError": "objet d'un autre type que prévu, souvent None non géré",
    "TypeError": "argument d'un mauvais type ou en nombre incorrect (appel modifié d'un côté seulement ?)",
    "ValueError": "valeur hors du format attendu (conversion, date, nombre)",
    "IndexError": "liste plus courte que prévu (réponse vide ?)",
    "NameError": "nom non défini (import manquant, faute de frappe)",
    "UnboundLocalError": "variable utilisée avant d'être affectée dans une branche",
    "ZeroDivisionError": "division par zéro (liste vide, durée nulle)",
    "JSONDecodeError": "contenu JSON illisible (fichier abîmé, réponse vide)",
    "InvalidTransition": "passage d'état non prévu par la machine d'état",
    "AssertionError": "invariant non respecté",
}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cause", "solutions", "files", "confidence"],
    "properties": {
        "cause": {"type": "string", "maxLength": 600},
        "solutions": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 300}},
        "files": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 120}},
        "confidence": {"type": "string", "enum": ["faible", "moyenne", "élevée"]},
    },
}
TASK = ("Analyse cette anomalie d'ORION (assistant vocal en Python). Donne la cause la plus probable, au plus trois "
        "pistes de correction et les fichiers concernés (seulement parmi ceux des extraits). Ne propose aucune "
        "modification de sécurité, de permissions ni de secrets.")


@dataclass
class Diagnosis:
    anomaly: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    consequences: str = ""
    hypotheses: list[str] = field(default_factory=list)
    solutions: list[str] = field(default_factory=list)
    confidence: str = "faible"
    by: str = "règles"
    code: dict = field(default_factory=dict)


def _imports(source: str) -> list[str]:
    found = re.findall(r"^\s*(?:from\s+(orion(?:\.\w+)*)\s+import|import\s+(orion(?:\.\w+)*))", source, re.M)
    return sorted({(a or b).replace(".", "/") + ".py" for a, b in found})


def diagnose(anomaly: Anomaly, workspace: Workspace | None = None, llm: StructuredLLM | None = None) -> Diagnosis:
    if anomaly.kind == EXTERNAL:
        return Diagnosis(anomaly.signature, "Panne d'un service extérieur ou du réseau : pas un défaut du code.",
                         evidence=anomaly.samples, consequences="fonction dégradée tant que le service ne répond pas",
                         hypotheses=["service distant indisponible, lent ou qui coupe la connexion"],
                         solutions=["surveiller ; vérifier le service ou le réseau ; aucune modification du code"],
                         confidence="élevée")
    if anomaly.kind == LATENCY:
        return Diagnosis(anomaly.signature, anomaly.title, consequences="réponses lentes",
                         hypotheses=["étape la plus lente à lire dans les lignes [mesure] (stt, choix, llm, tts)"],
                         solutions=["mesurer chaque étape sur une semaine (scripts/latency_report.py) avant d'agir"])
    own = [f for f in anomaly.frames if f["path"].startswith("orion/")]
    files = list(dict.fromkeys(f["path"] for f in reversed(own)))
    code, dependencies = {}, []
    if workspace is not None:
        for frame in own[-3:]:
            excerpt = workspace.read(frame["path"], frame["line"])
            if excerpt:
                code[f"{frame['path']}:{frame['line']}"] = excerpt
        for path in files[:2]:
            full = workspace.read(path, 1, around=10_000)
            dependencies += _imports("\n".join(line.split(": ", 1)[-1] for line in full.splitlines()))
    dependencies = sorted(set(dependencies) - set(files))
    module = files[0].split("/")[1] if files and "/" in files[0] else "?"
    diagnosis = Diagnosis(anomaly.signature, f"{anomaly.exception} levée dans {files[0] if files else '?'}"
                          + (f" ({anomaly.message})" if anomaly.message else ""),
                          evidence=anomaly.samples + [f"{f['path']}:{f['line']} dans {f['function']}" for f in own[-3:]],
                          files=files, dependencies=dependencies[:10],
                          consequences=f"la fonction en cause échoue (module {module}) ; {anomaly.count} occurrence(s)",
                          hypotheses=[HYPOTHESES.get(anomaly.exception, "à examiner dans le code")], code=code)
    if llm is not None and llm.usable and code:
        allowed = set(files) | set(dependencies)
        answer = llm.ask(TASK, SCHEMA, {"trace": "\n".join(diagnosis.evidence), **{k[:30]: v for k, v in code.items()}})
        if answer is not None:
            diagnosis.solutions = answer["solutions"]
            diagnosis.hypotheses.append(f"(llm) {answer['cause']}")
            diagnosis.files += [f for f in answer["files"] if f in allowed and f not in diagnosis.files]
            diagnosis.confidence, diagnosis.by = answer["confidence"], "règles + llm"
    return diagnosis
