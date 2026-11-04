"""Configuration du Core V3 actif : voix par l'agent V3 du PC, transcription du Katana, réveil « Hey Orion », outils du
PC, visage. Sauvegarde de l'ancienne configuration à côté. Aucun secret ici."""
import re
import shutil
import sys
import time
import tomllib
from pathlib import Path

p = Path("/opt/orion-v3/config.local.toml")
s = p.read_text(encoding="utf-8")
backup = p.with_name("config.local.toml.avant-reference-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(p, backup)


def drop(section: str, text: str) -> str:
    """Retire une section [section] et son contenu (jusqu'à la section suivante)."""
    return re.sub(r"(?ms)^\[" + re.escape(section) + r"\]\n.*?(?=^\[|\Z)", "", text)


for tool in ("open_url", "open_application", "close_application", "list_running_applications", "set_volume",
             "mute_volume", "unmute_volume", "lock_pc", "shutdown_pc", "media_play_pause", "media_next",
             "media_previous"):
    s = drop("tools." + tool, s)
s = drop("audio", s)
s = drop("stt", s)
s = s.replace('[face]\nenabled = true\n', '[face]\nenabled = true\nbirthday = "06-27"\njarvis_birthday = "2026-10-02"\n', 1)

s = s.rstrip() + '''

# --- Core actif unique (4 novembre 2026) : voix et outils du PC par l'agent Windows V3 --------------------------------
[audio]
remote = "http://192.168.1.128:8875"

[stt]
remote = "http://192.168.1.73:8766"   # Katana (GPU) ; Whisper local en secours

[wake_word]
phrase = "Hey Orion"
model = "models/openwakeword/hey_orion_fr.onnx"
threshold = 0.99
patience = 1
sure_score = 0.0

[tools.files]
enabled = true

[tools.devices.pc]
name = "votre PC"
url = "http://192.168.1.128:8875"
default = true
mac = "f0:2f:74:b0:81:2a"
aliases = ["mon pc", "le pc", "ce pc", "mon ordinateur", "l'ordinateur", "cet ordinateur", "mon ordi", "l'ordi",
           "pc principal", "mon pc principal", "pc fixe", "mon pc fixe", "ordinateur fixe", "mon ordinateur fixe",
           "pc de jeu", "mon pc de jeu", "pc gamer", "mon pc gamer", "la tour", "ma tour", "pc du bureau"]

[tools.devices.mini]
name = "le mini-PC"
aliases = ["mini pc", "le mini pc", "mon mini pc", "le serveur", "mon serveur", "le core", "jarvis core",
           "toi même", "ta machine"]
'''
tomllib.loads(s)  # lève si le fichier n'est plus un TOML valide : rien n'est écrit
if "--dry-run" in sys.argv:
    print(s)
    backup.unlink()
else:
    p.write_text(s + "\n", encoding="utf-8")
    print("écrit ; ancienne configuration :", backup)
