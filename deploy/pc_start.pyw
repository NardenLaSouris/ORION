"""Lanceur stable de l'installation ORION du PC fixe (C:\\Users\\theju\\.orion, deploy\\pc_install.ps1).

    pythonw pc_start.pyw agent      superviseur de l'agent Windows (une seule instance, relance automatique)
    pythonw pc_start.pyw control    ORION Control, dans la zone de notification

Ce fichier est copié à la racine de l'installation ; il lance le code de la version en service (``current`` :
dossier ``releases\\<commit>``). Les raccourcis (démarrage de session, menu Démarrer) ne changent donc jamais d'une
mise à jour à l'autre.
"""

import os
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
release = HOME / "releases" / (HOME / "current").read_text(encoding="utf-8").strip()
sys.path.insert(0, str(release))
os.chdir(release)  # l'agent enfant (python -m orion.winagent) part de ce dossier

what = sys.argv[1] if len(sys.argv) > 1 else "agent"
if what == "control":
    from orion.control.app import main as control

    sys.exit(control(["--profile", "v3", "--hidden"]))

from orion.winagent.supervise import main as supervise  # noqa: E402

sys.exit(supervise(["--config", str(HOME / "agent.toml"), "--env", str(HOME / "data" / ".env"),
                    "--log-file", str(HOME / "data" / "agent.log")]))
