"""Clé des données personnelles, sauvegarde et restauration (lot F4) : outil d'administration, jamais lancé par ORION.

  python -m orion.platform.datakey generate <trousseau>          # nouveau trousseau (fichier 600, jamais écrasé)
  python -m orion.platform.datakey show <trousseau>              # identifiants et empreintes, jamais les clés
  python -m orion.platform.datakey rotate <trousseau>            # nouvelle clé courante, anciennes gardées
  python -m orion.platform.datakey rewrap <trousseau> <base>     # rechiffre tout avec la clé courante
  python -m orion.platform.datakey retire <trousseau> <id> <base>  # retire une clé dont plus rien ne dépend
  python -m orion.platform.datakey verify <trousseau> <base>     # déchiffre tout, compte les erreurs
  python -m orion.platform.datakey backup <trousseau> <base> <dossier>  # sauvegarde chiffrée + manifeste
  python -m orion.platform.datakey restore <trousseau> <manifeste> <base>  # restauration vérifiée

Le trousseau est un secret : il ne va ni dans Git, ni dans un journal, ni à côté d'une sauvegarde. En service, ORION
le reçoit de systemd (docs/v3/foundation.md, § « Lot F4 »). Rotation, rechiffrement et restauration se font service
arrêté (``rewrap``, ``retire``, ``restore``) ; la sauvegarde peut se faire service en marche.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from orion.platform.personal import PersonalStore, restore
from orion.platform.store import PERSONAL, Store
from orion.platform.vault import DataKeyring, VaultError, dump_keyring, new_key, parse_keyring

USAGE = __doc__.split("\n\n")[1]


def _read(path: Path) -> tuple[dict[str, bytes], str]:
    import base64
    import json

    parse_keyring(path.read_text(encoding="utf-8"))  # validation complète
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: base64.b64decode(v) for k, v in data["keys"].items()}, data["current"]


def _write(path: Path, keys: dict[str, bytes], current: str, *, new: bool = False) -> None:
    text = dump_keyring(keys, current)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if new:
        if path.exists():
            tmp.unlink()
            raise VaultError(f"{path} existe déjà : jamais écrasé")
        os.replace(tmp, path)
    else:
        os.replace(tmp, path)


def _key_id(keys: dict) -> str:
    """Identifiant daté, unique dans le trousseau (k20261103, k20261103-2…)."""
    base, n = time.strftime("k%Y%m%d"), 1
    while (kid := base if n == 1 else f"{base}-{n}") in keys:
        n += 1
    return kid


def _open(keyfile: Path, database: Path) -> PersonalStore:
    if not database.exists():
        raise VaultError(f"base {database} absente")
    store = PersonalStore(Store(database, PERSONAL), parse_keyring(keyfile.read_text(encoding="utf-8")))
    if store.locked:
        raise VaultError(f"base verrouillée : {store.reason}")
    return store


def _show(keyring: DataKeyring) -> str:
    return "\n".join(f"  {kid}{' (courante)' if kid == keyring.current else ''} : empreinte {fp}"
                     for kid, fp in keyring.fingerprints().items())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == "generate":
            path = Path(args[1])
            kid = _key_id({})
            _write(path, {kid: new_key()}, kid, new=True)
            print(f"Trousseau créé : {path}\n{_show(parse_keyring(path.read_text(encoding='utf-8')))}\n"
                  "Faites-en une copie hors ligne (papier, gestionnaire de mots de passe) : sans elle, une perte de "
                  "cette machine rend les données personnelles irrécupérables.")
            return 0
        if len(args) == 2 and args[0] == "show":
            print(_show(parse_keyring(Path(args[1]).read_text(encoding="utf-8"))))
            return 0
        if len(args) == 2 and args[0] == "rotate":
            path = Path(args[1])
            keys, _ = _read(path)
            kid = _key_id(keys)
            keys[kid] = new_key()
            _write(path, keys, kid)
            print(f"Nouvelle clé courante : {kid}. Ensuite : rewrap (service arrêté), puis retire de l'ancienne.\n"
                  f"{_show(parse_keyring(path.read_text(encoding='utf-8')))}")
            return 0
        if len(args) == 3 and args[0] == "rewrap":
            store = _open(Path(args[1]), Path(args[2]))
            total = 0
            while done := store.rotate():
                total += done
            print(f"{total} enregistrement(s) rechiffré(s) ; clés utilisées : {sorted(store.usage())}")
            return 0
        if len(args) == 4 and args[0] == "retire":
            path, kid = Path(args[1]), args[2]
            store = _open(path, Path(args[3]))
            store.retire(kid)
            keys, current = _read(path)
            keys.pop(kid, None)
            _write(path, keys, current)
            print(f"Clé {kid} retirée du trousseau.")
            return 0
        if len(args) == 3 and args[0] == "verify":
            result = _open(Path(args[1]), Path(args[2])).verify()
            print(f"{result['records']} enregistrement(s), {len(result['errors'])} erreur(s)")
            for error in result["errors"][:20]:
                print(f"  {error}")
            return 1 if result["errors"] else 0
        if len(args) == 4 and args[0] == "backup":
            manifest = _open(Path(args[1]), Path(args[2])).backup(Path(args[3]))
            print(f"Sauvegarde chiffrée : {manifest} (la clé n'y est pas)")
            return 0
        if len(args) == 4 and args[0] == "restore":
            keyring = parse_keyring(Path(args[1]).read_text(encoding="utf-8"))
            result = restore(Path(args[2]), Path(args[3]), keyring)
            kept = f" ; ancienne base gardée : {result['kept']}" if result["kept"] else ""
            print(f"Restauration vérifiée : {result['records']} enregistrement(s){kept}")
            return 0
    except (VaultError, OSError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
