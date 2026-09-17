"""Where a cultural source belongs in the taxonomy, and how a regional layer is read.

Two distinct jobs:

* **filing a source** under a taxonomy sub-category. The descriptor says it; the taxonomy
  validates it. No classification logic is duplicated here.
* **reading a SITAP regional layer name**. SITAP publishes 144 regional feature types
  named ``REGIONE_art_142_g_boschi``, ``LAZIO_art_136_c_d`` and so on. Writing 144
  descriptors by hand would break at the first rename upstream, so the name itself is
  parsed - and only when the pattern is unambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ...core.taxonomy import Taxonomy

#: The taxonomy sub-categories this module is responsible for.
CULTURAL_CATEGORIES = (
    "cultural_heritage_assets",
    "archaeological_heritage",
    "landscape_assets_declared",
    "landscape_areas_by_law",
    "unesco_heritage",
    "heritage_administration",
)

#: Root the module reports under.
ROOT_CATEGORY = "landscape_cultural"

#: D.Lgs. 42/2004 art. 142 c. 1: letter -> what it protects. Used only to label a layer
#: whose name already states the letter.
ART142_LETTERS = {
    "a": "territori costieri",
    "b": "territori contermini ai laghi",
    "c": "fiumi, torrenti e corsi d'acqua",
    "d": "montagne oltre 1.600 / 1.200 m",
    "e": "ghiacciai e circhi glaciali",
    "f": "parchi e riserve",
    "g": "territori coperti da foreste e boschi",
    "h": "aree assegnate alle universita' agrarie e usi civici",
    "i": "zone umide",
    "l": "vulcani",
    "m": "zone di interesse archeologico",
}

#: Which sub-category a parsed regional layer belongs to.
ARTICLE_CATEGORY = {
    "10": "cultural_heritage_assets",
    "135": "landscape_assets_declared",
    "136": "landscape_assets_declared",
    "142": "landscape_areas_by_law",
    "157": "landscape_assets_declared",
}

_ARTICLE = re.compile(r"art[_\s]?(\d{2,3})", re.IGNORECASE)
_LETTER = re.compile(r"art[_\s]?142(?:_c[_\s]?1)?[_\s]?([a-m])(?:[_\s]|$)", re.IGNORECASE)
_REGION_PREFIX = re.compile(r"^([A-Za-z]+(?:[_\s][A-Za-z]+)?)[_\s]+(?:art|vincoli|perimetri)",
                            re.IGNORECASE)

#: Italian regions as they appear in the SITAP layer names, normalised to a proper name.
REGIONS = {
    "abruzzo": "Abruzzo", "basilicata": "Basilicata", "calabria": "Calabria",
    "campania": "Campania", "emiliaromagna": "Emilia-Romagna",
    "emilia_romagna": "Emilia-Romagna", "friuli": "Friuli-Venezia Giulia",
    "fvg": "Friuli-Venezia Giulia", "lazio": "Lazio", "liguria": "Liguria",
    "lombardia": "Lombardia", "marche": "Marche", "molise": "Molise",
    "piemonte": "Piemonte", "puglia": "Puglia", "sardegna": "Sardegna",
    "sicilia": "Sicilia", "toscana": "Toscana", "trentino": "Trentino-Alto Adige",
    "umbria": "Umbria", "valledaosta": "Valle d'Aosta", "veneto": "Veneto",
}


@dataclass
class LayerClassification:
    """What a SITAP layer name says about itself."""

    layer: str
    region: str = ""
    article: str = ""
    letter: str = ""
    category: str = ""
    label: str = ""

    @property
    def usable(self) -> bool:
        """Whether the name was understood well enough to build a descriptor."""
        return bool(self.category and self.region)

    @property
    def legal_reference(self) -> str:
        """The article the layer name states - quoted, not interpreted."""
        if not self.article:
            return ""
        base = f"D.Lgs. 42/2004 art. {self.article}"
        if self.letter:
            return f"{base} c. 1 lett. {self.letter}"
        return base


def classify_layer(layer: str, title: str = "") -> LayerClassification:
    """Read a SITAP feature-type name.

    Returns an unusable classification when the pattern is not recognised, which is the
    point: an unrecognised layer is skipped rather than filed somewhere plausible.
    """
    name = layer.split(":", 1)[-1]
    result = LayerClassification(layer=layer)

    article = _ARTICLE.search(name)
    if article:
        result.article = article.group(1)
        result.category = ARTICLE_CATEGORY.get(result.article, "")
    letter = _LETTER.search(name)
    if letter:
        result.letter = letter.group(1).lower()

    prefix = _REGION_PREFIX.match(name)
    candidate = (prefix.group(1) if prefix else name.split("_", 1)[0]).lower()
    result.region = REGIONS.get(candidate.replace(" ", "").replace("-", ""), "")

    if result.category:
        pieces = [f"art. {result.article}"]
        if result.letter:
            pieces.append(f"lett. {result.letter} - {ART142_LETTERS.get(result.letter, '')}")
        result.label = " ".join(pieces).strip(" -")
    else:
        result.label = title or name
    return result


class CulturalHeritageClassifier:
    """Files sources and records under the cultural sub-categories."""

    def __init__(self, taxonomy: Optional[Taxonomy] = None) -> None:
        self.taxonomy = taxonomy or Taxonomy.instance()

    def categories(self) -> List[str]:
        """The sub-categories that exist in the taxonomy, in dossier order."""
        known = {c.id for c in self.taxonomy.descendants(ROOT_CATEGORY)}
        ordered = [c.id for c in self.taxonomy.descendants(ROOT_CATEGORY)]
        return [c for c in ordered if c in known and c in CULTURAL_CATEGORIES] + \
               [c for c in CULTURAL_CATEGORIES if c not in known]

    def label(self, category: str) -> str:
        """Italian label of a sub-category."""
        return self.taxonomy.label(category, "it")

    def category_of(self, source) -> str:
        """Which sub-category a source belongs to, falling back to the root."""
        category = source.category or ""
        if category in CULTURAL_CATEGORIES:
            return category
        if ROOT_CATEGORY in self.taxonomy.ancestry(category):
            return category
        return ROOT_CATEGORY

    def group_by_category(self, sources) -> Dict[str, List]:
        """Group sources by the sub-category they declare."""
        grouped: Dict[str, List] = {}
        for source in sources:
            grouped.setdefault(self.category_of(source), []).append(source)
        return grouped
