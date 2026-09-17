"""Data provenance.

Every layer the plugin puts in the project answers the question *"where does this come
from?"*: the source descriptor, the service URL, the authority, the retrieval time, the CRS
and the processing operation are written both as layer custom properties (machine
readable) and as QGIS layer metadata (visible in the layer properties dialog).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from . import log
from .constants import (
    METADATA_IDENTIFIER_PREFIX,
    PROP_LAYER_AREA_ID,
    PROP_LAYER_CATEGORY,
    PROP_LAYER_PROVENANCE,
    PROP_LAYER_SOURCE_ID,
)
from .models import Provenance


def stamp(layer: Any, provenance: Provenance, *, area_id: str = "",
          category: str = "") -> None:
    """Attach ``provenance`` to a QGIS map layer.

    :param layer: any ``QgsMapLayer``.
    :param provenance: the provenance record to store.
    :param area_id: id of the project area the layer belongs to.
    :param category: taxonomy category id.
    """
    if layer is None:
        return
    try:
        layer.setCustomProperty(PROP_LAYER_PROVENANCE, provenance.to_json())
        layer.setCustomProperty(PROP_LAYER_SOURCE_ID, provenance.source_id)
        if area_id:
            layer.setCustomProperty(PROP_LAYER_AREA_ID, area_id)
        if category:
            layer.setCustomProperty(PROP_LAYER_CATEGORY, category)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug(f"Cannot set provenance custom properties: {exc}")
    _write_metadata(layer, provenance, category=category)


def _write_metadata(layer: Any, provenance: Provenance, *, category: str = "") -> None:
    """Fill the QGIS layer metadata object from a provenance record."""
    try:
        from qgis.core import QgsAbstractMetadataBase

        metadata = layer.metadata()
        metadata.setIdentifier(f"{METADATA_IDENTIFIER_PREFIX}:{provenance.source_id or layer.name()}")
        metadata.setTitle(provenance.source_name or layer.name())
        abstract_lines = [
            provenance.notes or "",
            f"Fonte: {provenance.authority}" if provenance.authority else "",
            f"Servizio: {provenance.url}" if provenance.url else "",
            f"Natura del dato: {provenance.evidence_level.label_it}",
            f"Riferimento: {provenance.metadata_url}" if provenance.metadata_url else "",
            f"Scala: {provenance.scale}" if provenance.scale else "",
            f"Accuratezza dichiarata: {provenance.accuracy_m} m" if provenance.accuracy_m else "",
            f"Aggiornamento: {provenance.update_frequency}" if provenance.update_frequency else "",
            f"Ultima verifica sorgente: {provenance.last_verified}" if provenance.last_verified else "",
        ]
        metadata.setAbstract("\n".join(line for line in abstract_lines if line))
        if provenance.license:
            metadata.setLicenses([provenance.license])
        if provenance.attribution:
            metadata.setRights([provenance.attribution])
        if category:
            metadata.setKeywords({"territorial-suite": [category]})
        if provenance.metadata_url:
            link = QgsAbstractMetadataBase.Link("Metadata", "WWW:LINK", provenance.metadata_url)
            metadata.addLink(link)
        metadata.addHistoryItem(
            f"{provenance.retrieved_at} - {provenance.operation or 'retrieved'} - "
            f"{provenance.source_id}"
        )
        layer.setMetadata(metadata)
    except Exception as exc:  # pragma: no cover - metadata is best effort
        log.debug(f"Cannot write layer metadata: {exc}")


def read(layer: Any) -> Optional[Provenance]:
    """Return the provenance stored on ``layer``, or ``None``."""
    if layer is None:
        return None
    try:
        raw = layer.customProperty(PROP_LAYER_PROVENANCE, "")
    except Exception:  # pragma: no cover - defensive
        return None
    if not raw:
        return None
    try:
        return Provenance.from_dict(json.loads(raw))
    except ValueError:
        return None


def describe(layer: Any) -> str:
    """Return a human readable provenance summary for the UI."""
    provenance = read(layer)
    if provenance is None:
        return "Nessuna informazione di provenienza registrata."
    parts = [
        f"Sorgente: {provenance.source_name or provenance.source_id}",
        f"Ente: {provenance.authority}" if provenance.authority else "",
        f"Servizio: {provenance.url}" if provenance.url else "",
        f"Acquisito il: {provenance.retrieved_at}",
        f"CRS: {provenance.crs}" if provenance.crs else "",
        f"Operazione: {provenance.operation}" if provenance.operation else "",
        f"Natura del dato: {provenance.evidence_level.label_it}",
        f"Licenza: {provenance.license}" if provenance.license else "",
    ]
    return "\n".join(part for part in parts if part)


def as_report_entry(provenance: Provenance) -> Dict[str, Any]:
    """Return the dictionary used in the "Fonti" section of the dossier."""
    return {
        "id": provenance.source_id,
        "name": provenance.source_name,
        "authority": provenance.authority,
        "url": provenance.url,
        "metadata_url": provenance.metadata_url,
        "license": provenance.license,
        "attribution": provenance.attribution,
        "retrieved_at": provenance.retrieved_at,
        "evidence_level": provenance.evidence_level.value,
        "evidence_label": provenance.evidence_level.label_it,
        "scale": provenance.scale,
        "accuracy_m": provenance.accuracy_m,
        "update_frequency": provenance.update_frequency,
        "last_verified": provenance.last_verified,
        "official": provenance.official,
    }
