"""Identification des produits dans un titre d'annonce : marque, modèle, variante, et ce qui n'est pas le produit.

Priorité : cartes graphiques NVIDIA GeForce RTX 50 (RTX 5090 en tête). Une annonce n'est retenue que si elle désigne la
carte elle-même : un PC complet, un portable, une boîte vide, une photo, un waterblock ou une autre carte de la gamme
ne comptent jamais comme « une RTX 5090 ».
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

BRANDS = {
    "asus": ("asus", "rog", "tuf gaming"), "msi": ("msi",), "gigabyte": ("gigabyte", "aorus"), "zotac": ("zotac",),
    "pny": ("pny",), "palit": ("palit",), "gainward": ("gainward",), "inno3d": ("inno3d", "inno 3d"),
    "nvidia": ("founders edition", "nvidia fe", "fe edition"), "manli": ("manli",), "colorful": ("colorful",),
}
MODELS = (
    "rog astral", "astral", "tuf", "prime", "suprim liquid", "suprim", "gaming trio", "vanguard", "ventus",
    "aorus master", "aorus xtreme", "master", "gaming oc", "windforce", "solid", "amp extreme", "amp", "infinity",
    "phantom", "game rock", "phoenix", "ichill", "x3", "argb", "epic-x", "founders edition", "fe",
)
# Annonces qui ne sont pas la carte seule (titres français et anglais des places de marché).
NOT_THE_CARD = (
    "pc gamer", "pc complet", "pc fixe", "ordinateur", "tour gaming", "unite centrale", "gaming pc", "desktop",
    "laptop", "portable", "notebook", "boite vide", "empty box", "box only", "carton vide", "photo", "image only",
    "pour pieces", "for parts", "hs", "en panne", "defectueuse", "faulty", "waterblock", "water block", "backplate",
    "support gpu", "anti sag", "cable", "adaptateur", "riser", "skin", "sticker", "location", "rental", "precommande",
    "pre-order", "preorder", "reservation",
)
LINEUP = ("5090", "5080", "5070", "5060", "5050", "4090", "4080")
FAMILY = re.compile(r"\b(?:rtx|geforce)\s*(?:rtx\s*)?(50[5-9]0|40[6-9]0)(?:\s*(ti|super|d))?\b")


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@dataclass(frozen=True)
class ProductMatch:
    family: str  # « rtx 5090 »
    variant: str  # « », « d », « ti », « super »
    brand: str  # « asus »… ou « » si inconnue
    model: str  # « rog astral »… ou « »
    excluded: str  # raison si ce n'est pas la carte seule, « » sinon

    @property
    def key(self) -> str:
        """Clé du produit pour l'historique des prix (même carte, même clé)."""
        return " ".join(p for p in (self.family + (f" {self.variant}" if self.variant else ""), self.brand,
                                    self.model) if p)

    @property
    def is_card(self) -> bool:
        return not self.excluded


def identify(title: str) -> ProductMatch | None:
    """Carte RTX nommée dans le titre ; None si le titre n'en nomme aucune."""
    text = f" {plain(title)} "
    families = {m.group(1) for m in FAMILY.finditer(text)}
    match = FAMILY.search(text)
    if match is None:
        return None
    number, variant = match.group(1), (match.group(2) or "")
    brand = next((b for b, names in BRANDS.items() if any(f" {n} " in text for n in names)), "")
    model = next((m for m in MODELS if f" {m} " in text), "")
    if model in ("founders edition", "fe"):
        brand, model = "nvidia", "founders edition"
    excluded = next((w for w in NOT_THE_CARD if f" {w} " in text), "")
    if not excluded and len(families) > 1:
        excluded = "plusieurs modèles dans l'annonce"
    return ProductMatch(f"rtx {number}", variant, brand, model, excluded)


def wanted(product: str) -> tuple[str, str]:
    """Produit demandé (« rtx 5090 », « RTX 5090 D ») -> (famille, variante)."""
    match = FAMILY.search(f" {plain(product)} ")
    if match is None:
        return plain(product), ""
    return f"rtx {match.group(1)}", match.group(2) or ""


def matches(product: str, title: str) -> ProductMatch | None:
    """Annonce de la carte demandée elle-même (bonne famille, bonne variante, pas un PC ni une boîte vide) ; None
    sinon. Une demande sans variante (« RTX 5090 ») n'accepte que la version de base (pas la 5090 D chinoise)."""
    family, variant = wanted(product)
    found = identify(title)
    if found is None or found.family != family or found.variant != variant or not found.is_card:
        return None
    return found
