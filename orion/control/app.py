"""ORION Control : fenêtre (WebView2), icône dans la zone de notification, raccourci Ctrl+Shift+O.

  pythonw -m orion.control            # ouvre l'interface
  pythonw -m orion.control --hidden   # démarre dans la zone de notification (démarrage automatique)
  pythonw -m orion.control --profile v3   # environnement V3 isolé (réglages et jeton à part, sans raccourci clavier
                                          # ni démarrage automatique : ceux de la production restent intacts)
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from orion.control.bridge import Bridge
from orion.control.settings import PROFILES, SettingsStore, app_dir

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TITLE = "ORION Control"
STARTUP_LINK = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / \
    "ORION Control.lnk"
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY, WM_QUIT = 0x2, 0x4, 0x4000, 0x312, 0x12
SHORTCUT = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:JC_LINK); $s.TargetPath = $env:JC_TARGET; "
            "$s.Arguments = '-m orion.control --hidden'; $s.WorkingDirectory = $env:JC_DIR; "
            "$s.Description = 'ORION Control'; $s.Save()")


def set_autostart(enabled: bool) -> None:
    """Raccourci dans le dossier Démarrage de l'utilisateur (aucun droit administrateur)."""
    STARTUP_LINK.with_name("JARVIS Control.lnk").unlink(missing_ok=True)  # ancien nom : jamais deux démarrages
    if not enabled:
        STARTUP_LINK.unlink(missing_ok=True)
        return
    env = {**os.environ, "JC_LINK": str(STARTUP_LINK), "JC_TARGET": str(Path(sys.executable).with_name("pythonw.exe")),
           "JC_DIR": str(ROOT)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT], env=env, check=True,
                   capture_output=True, timeout=30)


def single_instance(profile: str = "") -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ORIONControl" + profile)
    return ctypes.windll.kernel32.GetLastError() != 183


class Hotkey(threading.Thread):
    """Ctrl+Shift+O, global : appelle ``action`` depuis son propre fil (boucle de messages Windows)."""

    def __init__(self, action):
        super().__init__(name="raccourci", daemon=True)
        self._action = action
        self._thread_id = 0

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("O")):
            log.warning("Ctrl+Shift+O déjà utilisé par un autre programme")
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                self._action()
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


def tray_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Le visage d'ORION en miniature : disque noir, cercle vert, anneau doré, centre vide.
    draw.ellipse((2, 2, 62, 62), fill=(14, 14, 14, 255), outline=(60, 139, 111, 255), width=3)
    draw.ellipse((15, 15, 49, 49), outline=(201, 164, 92, 255), width=5)
    return image


class ControlApp:
    def __init__(self, bridge: Bridge, hidden: bool, title: str = TITLE, hotkey: bool = True):
        import webview

        self._webview = webview
        self.bridge = bridge
        self.visible = not hidden
        self.title = title
        self.window = webview.create_window(title, bridge.url, width=1240, height=820, min_size=(960, 620),
                                            background_color="#0b0f17", hidden=hidden)
        self.window.events.closing += self._on_closing
        self.hotkey = Hotkey(self.toggle) if hotkey else None
        self.tray = None
        self._quitting = False

    def _on_closing(self):
        if self._quitting:
            return True
        self.hide()
        return False

    def show(self) -> None:
        self.window.show()
        self.window.restore()
        self.visible = True

    def hide(self) -> None:
        self.window.hide()
        self.visible = False

    def toggle(self, *_args) -> None:
        self.hide() if self.visible else self.show()

    def quit(self, *_args) -> None:
        self._quitting = True
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.tray is not None:
            self.tray.stop()
        self.bridge.stop()
        self.window.destroy()

    def run(self) -> None:
        import pystray

        menu = pystray.Menu(pystray.MenuItem("Ouvrir / masquer (Ctrl+Shift+O)", self.toggle, default=True),
                            pystray.MenuItem("Quitter ORION Control", self.quit))
        self.tray = pystray.Icon("jarvis-control", tray_image(), self.title, menu)
        self.tray.run_detached()
        if self.hotkey is not None:
            self.hotkey.start()
        self._webview.start(gui="edgechromium", private_mode=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orion.control", description=TITLE)
    parser.add_argument("--hidden", action="store_true", help="démarrer dans la zone de notification")
    parser.add_argument("--profile", choices=[p for p in PROFILES if p], default="",
                        help="v3 : environnement de développement V3 isolé (réglages et jeton à part)")
    args = parser.parse_args(argv)
    title, core_url = PROFILES[args.profile]
    folder = app_dir(args.profile)
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=folder / "control.log", encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    if not single_instance(args.profile):
        ctypes.windll.user32.MessageBoxW(None, f"{title} est déjà ouvert : l'icône près de l'horloge.", title, 0x40)
        return 0
    if args.profile:  # jamais le jeton du dépôt (production), jamais le raccourci de démarrage de la production
        store = SettingsStore(folder, env_file=None, core_url=core_url)
        bridge = Bridge(store, lambda enabled: None)
    else:
        bridge = Bridge(SettingsStore(folder), set_autostart)
    bridge.start()
    log.info("%s démarré (Core : %s)", title, bridge.settings.core_url)
    ControlApp(bridge, args.hidden, title).run()  # V3 seule : son raccourci clavier aussi
    return 0
