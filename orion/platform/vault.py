"""Clé des données personnelles et chiffrement au repos (CORE FOUNDATION, lot F4 ; docs/v3/foundation.md § « Lot F4 »).

- **Clé** : un trousseau JSON ``{"current": "<id>", "keys": {"<id>": "<32 octets en base64>"}}``. Le service le reçoit
  par les identifiants de systemd (``ImportCredential=`` ou ``LoadCredential=``) dans ``$CREDENTIALS_DIRECTORY``,
  jamais par l'environnement, ``.env``, Git ni les journaux. Sans trousseau : données personnelles **verrouillées**.
- **Chiffrement** : AES-256-GCM, nonce aléatoire de 96 bits, données associées (AAD) qui lient chaque chiffré à sa place
  (table, enregistrement, champ) : un chiffré recopié ailleurs ne s'ouvre pas. Chaque chiffré porte l'identifiant de
  sa clé, pour la rotation.
- **Sous-clés** dérivées (HKDF-SHA-256) de chaque clé maîtresse : chiffrement, étiquettes (HMAC déterministe, pour
  dédoublonner sans garder le texte) et empreinte (identifie une clé sans la révéler).

``repr`` et ``str`` ne montrent jamais une clé.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "ox1."  # texte scellé, version 1 du format
MAGIC = b"OX1"
KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
CREDENTIAL = "orion.data_key"  # nom de l'identifiant systemd par défaut ([platform] data_key)


class VaultError(RuntimeError):
    pass


class Locked(VaultError):
    """Données personnelles verrouillées : clé absente, illisible ou incorrecte."""


def _derive(master: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"orion.{purpose}.v1".encode()).derive(master)


@dataclass(frozen=True)
class _Key:
    enc: bytes
    tag: bytes
    fingerprint: str


class DataKeyring:
    """Clés maîtresses : ``current`` chiffre ; les autres restent pour ouvrir ce qu'elles ont chiffré (rotation)."""

    def __init__(self, keys: dict[str, bytes], current: str):
        if current not in keys:
            raise VaultError("clé courante absente du trousseau")
        derived = {}
        for key_id, master in keys.items():
            if not KEY_ID.match(key_id):
                raise VaultError(f"identifiant de clé invalide : {key_id[:40]!r}")
            if len(master) != 32:
                raise VaultError("clé maîtresse de 32 octets attendue")
            derived[key_id] = _Key(_derive(master, "personal.enc"), _derive(master, "tag"),
                                   _derive(master, "fingerprint")[:8].hex())
        self._keys, self.current = derived, current

    def __repr__(self) -> str:
        return f"DataKeyring(current={self.current!r}, keys={self.ids()!r})"

    __str__ = __repr__

    def ids(self) -> list[str]:
        return sorted(self._keys)

    def fingerprints(self) -> dict[str, str]:
        return {key_id: key.fingerprint for key_id, key in sorted(self._keys.items())}

    def has(self, key_id: str) -> bool:
        return key_id in self._keys

    # --- Chiffrement --------------------------------------------------------------------------------------

    def encrypt(self, plaintext: bytes, aad: str) -> bytes:
        kid = self.current.encode("ascii")
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self._keys[self.current].enc).encrypt(nonce, plaintext, aad.encode("utf-8"))
        return MAGIC + bytes([len(kid)]) + kid + nonce + sealed

    def decrypt(self, blob: bytes, aad: str) -> bytes:
        key_id, nonce, sealed = self.parse(blob)
        if key_id not in self._keys:
            raise Locked(f"clé {key_id} absente du trousseau")
        try:
            return AESGCM(self._keys[key_id].enc).decrypt(nonce, sealed, aad.encode("utf-8"))
        except InvalidTag as exc:
            raise VaultError("chiffré altéré, déplacé ou clé incorrecte") from exc

    @staticmethod
    def parse(blob: bytes) -> tuple[str, bytes, bytes]:
        if len(blob) < 4 or blob[:3] != MAGIC:
            raise VaultError("chiffré inconnu")
        size = blob[3]
        kid, nonce, sealed = blob[4:4 + size], blob[4 + size:16 + size], blob[16 + size:]
        if len(nonce) != 12 or len(sealed) < 16:
            raise VaultError("chiffré tronqué")
        return kid.decode("ascii"), nonce, sealed

    @staticmethod
    def key_of(blob: bytes) -> str:
        return DataKeyring.parse(blob)[0]

    # --- Texte scellé (colonnes des bases opérationnelle et publique) ----------------------------------------

    def seal(self, text: str, aad: str) -> str:
        return PREFIX + base64.urlsafe_b64encode(self.encrypt(text.encode("utf-8"), aad)).decode("ascii")

    def unseal(self, value: str, aad: str) -> str:
        if not is_sealed(value):
            raise VaultError("texte non scellé")
        try:
            blob = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
        except (ValueError, binascii.Error) as exc:
            raise VaultError("texte scellé illisible") from exc
        return self.decrypt(blob, aad).decode("utf-8")

    def tag(self, text: str) -> str:
        """Étiquette déterministe (HMAC) : égalité vérifiable sans garder le texte (clés de dédoublonnage)."""
        return "t1." + hmac.new(self._keys[self.current].tag, text.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


# --- Trousseau : fichier, identifiant systemd ----------------------------------------------------------------

def parse_keyring(text: str) -> DataKeyring:
    try:
        data = json.loads(text)
        keys = {str(k): base64.b64decode(str(v), validate=True) for k, v in data["keys"].items()}
        return DataKeyring(keys, str(data["current"]))
    except VaultError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error) as exc:
        raise VaultError("trousseau illisible") from exc


def dump_keyring(keys: dict[str, bytes], current: str) -> str:
    DataKeyring(keys, current)  # validation
    return json.dumps({"current": current, "keys": {k: base64.b64encode(v).decode("ascii") for k, v in keys.items()}},
                      indent=1) + "\n"


def load_keyring(name: str = CREDENTIAL, directory: str | Path | None = None) -> tuple[DataKeyring | None, str]:
    """Trousseau transmis par systemd (``$CREDENTIALS_DIRECTORY/<name>``) : (trousseau, "") ou (None, raison)."""
    directory = directory if directory is not None else os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        return None, "aucun identifiant systemd (clé absente)"
    path = Path(directory) / name
    try:
        return parse_keyring(path.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, f"identifiant {name} absent (clé absente)"
    except OSError:
        return None, f"identifiant {name} illisible"
    except VaultError as exc:
        return None, f"identifiant {name} : {exc}"


def new_key() -> bytes:
    return secrets.token_bytes(32)


# --- Champs à contenu personnel des bases opérationnelle et publique ------------------------------------------

CLEAR, SEALED, WITHHELD = "clair", "scellé", "retenu"
UNAVAILABLE = "(contenu indisponible : données personnelles verrouillées)"


class Sealer:
    """Texte personnel rangé dans une base qui n'est pas la base personnelle (texte d'un événement, résumé d'une
    confirmation, paramètres d'une tâche personnelle).

    - ``clair`` : base personnelle non configurée (comportement des lots F1 à F3) ;
    - ``scellé`` : chiffré avec la clé des données personnelles, lié à sa place (``aad``) ;
    - ``retenu`` : base personnelle configurée mais verrouillée (clé absente) : le texte n'est **pas** écrit ; il
      reste en mémoire jusqu'au redémarrage, puis devient indisponible. Jamais de repli en clair.
    """

    def __init__(self, keyring: DataKeyring | None = None, configured: bool = False):
        self._keyring = keyring
        self.mode = SEALED if keyring is not None else (WITHHELD if configured else CLEAR)

    def seal(self, text: str, aad: str) -> str:
        if self.mode == SEALED:
            return self._keyring.seal(text, aad)
        return text if self.mode == CLEAR else ""

    def open(self, value: str, aad: str) -> str | None:
        """Texte d'origine ; None s'il n'est pas lisible ici (retenu, ou scellé sans la clé)."""
        if is_sealed(value):
            if self._keyring is None:
                return None
            try:
                return self._keyring.unseal(value, aad)
            except VaultError:
                return None
        if self.mode == WITHHELD and value == "":
            return None
        return value

    def tag(self, text: str) -> str:
        """Clé de dédoublonnage : égalité préservée, texte jamais gardé hors du mode clair."""
        if not text or self.mode == CLEAR:
            return text
        if self.mode == SEALED:
            return self._keyring.tag(text)
        return "h1." + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
