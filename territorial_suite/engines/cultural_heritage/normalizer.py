"""Turn the raw feature hits of a cultural source into normalised records.

The mapping is driven entirely by the ``fields`` block of the descriptor, so adding a
source is a JSON edit. Nothing is invented: a value the source does not publish stays
``unknown``, and the administrative act is transcribed exactly as the service returns it.

Two real quirks of the SITAP data are handled here because they would otherwise end up in
the dossier verbatim: HTML entities inside text fields (``piazza de&#39; Mozzi``) and
``QDate``/``QDateTime`` objects coming back from the OGR provider.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional

from ...core.models import FeatureHit, SourceResult
from ...core.registry import DataSource
from .model import UNKNOWN, ActReference, CulturalAsset, Finding

#: Descriptor keys that carry the reference of an administrative act.
ACT_KEYS = {"act_law": "law", "act_document": "document", "act_date": "date",
            "act_authority": "authority", "code": "code"}

#: Attribute names that commonly hold each piece of information, tried in order when the
#: descriptor does not map the field explicitly. Only exact matches are used.
FALLBACK_KEYS = {
    "name": ("denominazione", "denominazi", "nome", "name", "oggetto", "bene",
             "nome_giard", "componente", "descrizione", "toponimo"),
    "asset_type": ("tipo_bene", "tipo", "classe", "class", "tipologia", "uso",
                   "tipobos", "tipo_area"),
    "municipality": ("comune", "nome_comun", "municipality"),
    "province": ("provincia", "prov", "province"),
    "region": ("regione", "region"),
}

_WHITESPACE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """Return a printable string: HTML entities resolved, whitespace collapsed."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else _stringify(value)
    if "&" in text:
        text = html.unescape(text)
    return _WHITESPACE.sub(" ", text).strip()


def _stringify(value: Any) -> str:
    """Render a provider value, including Qt date/time objects, as plain text."""
    for method in ("toString",):
        function = getattr(value, method, None)
        if callable(function):
            try:
                text = function("yyyy-MM-dd") if _looks_like_date(value) else function()
            except TypeError:  # pragma: no cover - other Qt types
                text = function()
            if text:
                return str(text)
    return str(value)


def _looks_like_date(value: Any) -> bool:
    return type(value).__name__ in ("QDate", "QDateTime")


def _attribute(attributes: Dict[str, Any], key: str) -> str:
    """Case-insensitive attribute lookup returning cleaned text."""
    if not key:
        return ""
    if key in attributes:
        return clean_text(attributes[key])
    lowered = {str(k).lower(): v for k, v in attributes.items()}
    return clean_text(lowered.get(key.lower(), ""))


def _first(attributes: Dict[str, Any], keys) -> str:
    for key in keys:
        value = _attribute(attributes, key)
        if value and value.upper() != "NULL":
            return value
    return ""


def _candidates(value: Any) -> tuple:
    """A descriptor field may name one attribute or several, best first."""
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return tuple(part.strip() for part in text.split(",") if part.strip())


def looks_like_a_name(text: str) -> bool:
    """Whether a value reads as a name rather than as a code.

    SITAP publishes ``tipobos = 1`` and ``inside = 100.0``: printing those as the name of
    a heritage asset would be worse than admitting the source does not name it.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.upper() == "NULL":
        return False
    letters = sum(1 for ch in stripped if ch.isalpha())
    return letters >= 3


class CulturalHeritageNormalizer:
    """Builds :class:`CulturalAsset` records from the results of one source."""

    def __init__(self, source: DataSource) -> None:
        self.source = source

    # ------------------------------------------------------------------ pieces

    def act_of(self, attributes: Dict[str, Any]) -> ActReference:
        """Transcribe the administrative act declared by the feature, if any."""
        fields = self.source.fields or {}
        values = {}
        for descriptor_key, act_field in ACT_KEYS.items():
            values[act_field] = _attribute(attributes, fields.get(descriptor_key, ""))
        return ActReference(**values)

    def name_of(self, hit: FeatureHit) -> str:
        """Best available name, or the theme label when the source does not name it.

        A dataset that publishes only a classification code (``tipobos``) has no name to
        give: the record then carries the theme it belongs to, declared by the descriptor
        in ``fields.unnamed_label``, and the code stays in the attributes.
        """
        fields = self.source.fields or {}
        for key in _candidates(fields.get("label", "")):
            value = _attribute(hit.attributes, key)
            if looks_like_a_name(value):
                return value
        label = clean_text(hit.label)
        if looks_like_a_name(label):
            return label
        found = _first(hit.attributes, FALLBACK_KEYS["name"])
        if looks_like_a_name(found):
            return found
        fallback = clean_text(fields.get("unnamed_label", ""))
        return fallback or found or label or UNKNOWN

    def type_of(self, attributes: Dict[str, Any]) -> str:
        """Asset type as *published by the source*, never inferred."""
        fields = self.source.fields or {}
        for key in ("class", "type"):
            for candidate in _candidates(fields.get(key, "")):
                value = _attribute(attributes, candidate)
                if value:
                    return value
        return _first(attributes, FALLBACK_KEYS["asset_type"]) or UNKNOWN

    def place_of(self, attributes: Dict[str, Any], key: str) -> str:
        """Municipality / province / region as published, else ``unknown``."""
        fields = self.source.fields or {}
        value = _attribute(attributes, fields.get(key, ""))
        return value or _first(attributes, FALLBACK_KEYS.get(key, ())) or UNKNOWN

    def findings_for(self, asset: CulturalAsset) -> list:
        """Classify what may legitimately be said about this record."""
        findings = [Finding.DATA_PRESENT.value]
        if asset.intersects:
            findings.append(Finding.AREA_INTERSECTS_DATA.value)
        if self.source.official:
            findings.append(Finding.OFFICIAL_INFORMATION.value)
        if asset.act.present:
            findings.append(Finding.LEGAL_REFERENCE_PRESENT.value)
        return findings

    # ------------------------------------------------------------------ records

    def asset_from_hit(self, hit: FeatureHit, *, category: str) -> CulturalAsset:
        """Normalise one feature hit."""
        attributes = dict(hit.attributes or {})
        act = self.act_of(attributes)
        asset = CulturalAsset(
            global_id=f"{self.source.id}#{hit.fid or _first(attributes, ('id', 'id_bene'))}",
            name=self.name_of(hit),
            asset_type=self.type_of(attributes),
            category=category,
            subcategory=self.source.subcategory,
            municipality=self.place_of(attributes, "municipality"),
            province=self.place_of(attributes, "province"),
            region=self.place_of(attributes, "region"),
            source_id=self.source.id,
            source_name=self.source.name,
            source_feature_id=str(hit.fid or ""),
            source_url=self.source.url,
            authority=self.source.authority,
            act=act,
            legal_reference=self.source.legal_reference,
            evidence_level=self.source.evidence_level.value,
            data_nature=self.source.data_nature.value,
            verification_status=self.source.verification_status.value,
            # ``spatial.analyse_layer`` sets distance zero exactly when the feature
            # intersects the area; a point asset has no intersected surface, so the
            # distance is the only reliable test.
            intersects=_intersects(hit),
            intersect_area_m2=float(hit.intersect_area_m2 or 0.0),
            intersect_pct=float(hit.intersect_pct or 0.0),
            distance_m=None if _intersects(hit) else float(hit.distance_m),
            attributes=attributes,
        )
        asset.findings = self.findings_for(asset)
        return asset

    def assets_from_result(self, result: SourceResult, *,
                           category: str) -> list:
        """Normalise every hit of one source result."""
        return [self.asset_from_hit(hit, category=category) for hit in result.hits]


def _intersects(hit: FeatureHit) -> bool:
    """Whether the feature touches the area (distance zero means intersecting)."""
    return float(hit.distance_m or 0.0) <= 0.0


def superintendency_fields(source: DataSource, attributes: Dict[str, Any]
                           ) -> Optional[Dict[str, str]]:
    """Extract the office identity from a feature of the superintendency layer."""
    fields = source.fields or {}
    name = _attribute(attributes, fields.get("label", "")) or \
        _first(attributes, ("denominazione", "nome"))
    code = _attribute(attributes, fields.get("code", "")) or _attribute(attributes, "codice")
    if not name and not code:
        return None
    return {
        "code": code,
        "name": name or UNKNOWN,
        "office_type": _attribute(attributes, fields.get("office_type", "")),
        "website": _attribute(attributes, fields.get("website", "")),
        "pec": _attribute(attributes, fields.get("pec", "")),
    }
