"""Minimal, tolerant OGC capabilities parsing.

Public services disagree on namespaces, versions and casing, so the parser is
namespace-agnostic: tags are compared on their local name. It extracts only what the
plugin needs (title, layer/typename list, CRS, formats), never the full document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
# nosec B405 - every document goes through _safe_root() below, which refuses the DTD
# constructs this module would otherwise be exposed to.
from xml.etree import ElementTree  # nosec B405

from ...core.errors import SourceSchemaError

try:  # pragma: no cover - not shipped with QGIS, used when a distribution provides it
    from defusedxml.ElementTree import fromstring as _defused_fromstring
except ImportError:  # pragma: no cover - the normal case
    _defused_fromstring = None

#: A capabilities document arrives from the network, so it is untrusted input.
#: ``xml.etree`` does not resolve *external* entities - verified: an external entity
#: raises "undefined entity" - but it does expand *internal* ones, which is enough to
#: build an expansion bomb out of a few nested declarations. OGC capabilities are
#: schema-based (XSD) and never legitimately carry a DTD, so the whole construct is
#: refused rather than parsed.
_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _safe_root(payload: bytes) -> "ElementTree.Element":
    """Parse an untrusted capabilities document.

    :raises SourceSchemaError: when the document declares a DTD or cannot be parsed.
    """
    if _DOCTYPE.search(payload if isinstance(payload, bytes) else payload.encode("utf-8")):
        raise SourceSchemaError(
            "Il documento delle capabilities dichiara una DTD: rifiutato",
            detail="I servizi OGC pubblicano documenti basati su schema XSD; una "
                   "dichiarazione DOCTYPE o ENTITY non e' legittima e puo' essere usata "
                   "per un attacco di espansione.")
    if _defused_fromstring is not None:  # pragma: no cover - depends on the environment
        return _defused_fromstring(payload)
    return ElementTree.fromstring(payload)  # nosec B314 - DTD refused above


def local_name(tag: str) -> str:
    """Return the tag name without its XML namespace."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_elements(root: ElementTree.Element, name: str):
    """Yield every descendant whose local name matches ``name`` (case insensitive)."""
    wanted = name.lower()
    for element in root.iter():
        if local_name(element.tag).lower() == wanted:
            yield element


def first_text(element: ElementTree.Element, name: str, default: str = "") -> str:
    """Return the text of the first descendant with the given local name."""
    for child in iter_elements(element, name):
        if child.text:
            return child.text.strip()
    return default


@dataclass
class LayerInfo:
    """One advertised layer / feature type."""

    name: str
    title: str = ""
    crs: List[str] = field(default_factory=list)
    bbox_wgs84: Optional[tuple] = None
    formats: List[str] = field(default_factory=list)
    abstract: str = ""
    queryable: bool = True


@dataclass
class Capabilities:
    """The subset of a capabilities document the plugin uses."""

    service: str
    version: str = ""
    title: str = ""
    abstract: str = ""
    layers: List[LayerInfo] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)
    operations: Dict[str, str] = field(default_factory=dict)
    raw_size: int = 0

    def layer(self, name: str) -> Optional[LayerInfo]:
        """Return an advertised layer by name (case insensitive, namespace tolerant)."""
        wanted = name.lower()
        for info in self.layers:
            if info.name.lower() == wanted or info.name.lower().rsplit(":", 1)[-1] == \
                    wanted.rsplit(":", 1)[-1]:
                return info
        return None

    @property
    def layer_names(self) -> List[str]:
        """Names of every advertised layer."""
        return [info.name for info in self.layers]


def parse(payload: bytes) -> Capabilities:
    """Parse a WFS or WMS/WMTS capabilities document."""
    try:
        root = _safe_root(payload)
    except ElementTree.ParseError as exc:
        raise SourceSchemaError("Malformed capabilities document", detail=str(exc)) from exc

    root_name = local_name(root.tag).lower()
    version = root.attrib.get("version", "")
    if "wfs" in root_name:
        return _parse_wfs(root, version, len(payload))
    if "wmts" in root_name:
        return _parse_wmts(root, version, len(payload))
    if "wms" in root_name:
        return _parse_wms(root, version, len(payload))
    raise SourceSchemaError(f"Unsupported capabilities document: {root_name}")


def _service_title(root: ElementTree.Element) -> str:
    for container in ("ServiceIdentification", "Service"):
        for element in iter_elements(root, container):
            title = first_text(element, "Title")
            if title:
                return title
    return first_text(root, "Title")


def _parse_wfs(root: ElementTree.Element, version: str, size: int) -> Capabilities:
    caps = Capabilities(service="WFS", version=version, title=_service_title(root),
                        abstract=first_text(root, "Abstract"), raw_size=size)
    for element in iter_elements(root, "FeatureType"):
        name = first_text(element, "Name")
        if not name:
            continue
        crs_values = []
        for tag in ("DefaultCRS", "DefaultSRS", "OtherCRS", "OtherSRS", "SRS"):
            for child in iter_elements(element, tag):
                if child.text:
                    crs_values.append(child.text.strip())
        formats = [child.text.strip() for child in iter_elements(element, "Format")
                   if child.text]
        bbox = None
        for child in iter_elements(element, "WGS84BoundingBox"):
            lower = first_text(child, "LowerCorner")
            upper = first_text(child, "UpperCorner")
            if lower and upper:
                try:
                    x0, y0 = (float(v) for v in lower.split()[:2])
                    x1, y1 = (float(v) for v in upper.split()[:2])
                    bbox = (x0, y0, x1, y1)
                except ValueError:  # pragma: no cover - malformed service
                    bbox = None
            break
        caps.layers.append(LayerInfo(name=name, title=first_text(element, "Title"),
                                     crs=crs_values, formats=formats, bbox_wgs84=bbox,
                                     abstract=first_text(element, "Abstract")))
    for element in iter_elements(root, "Operation"):
        op_name = element.attrib.get("name", "")
        if op_name:
            caps.operations[op_name] = ""
    return caps


def _parse_wms(root: ElementTree.Element, version: str, size: int) -> Capabilities:
    caps = Capabilities(service="WMS", version=version, title=_service_title(root),
                        abstract=first_text(root, "Abstract"), raw_size=size)
    for element in iter_elements(root, "GetMap"):
        caps.formats = [child.text.strip() for child in iter_elements(element, "Format")
                        if child.text]
        break
    for element in iter_elements(root, "Layer"):
        name = ""
        for child in element:
            if local_name(child.tag) == "Name" and child.text:
                name = child.text.strip()
                break
        if not name:
            continue
        crs_values = [child.text.strip() for tag in ("CRS", "SRS")
                      for child in element if local_name(child.tag) == tag and child.text]
        title = ""
        for child in element:
            if local_name(child.tag) == "Title" and child.text:
                title = child.text.strip()
                break
        queryable = element.attrib.get("queryable", "1") not in ("0", "false", "False")
        caps.layers.append(LayerInfo(name=name, title=title, crs=crs_values,
                                     queryable=queryable))
    return caps


def _parse_wmts(root: ElementTree.Element, version: str, size: int) -> Capabilities:
    caps = Capabilities(service="WMTS", version=version, title=_service_title(root),
                        abstract=first_text(root, "Abstract"), raw_size=size)
    for element in iter_elements(root, "Layer"):
        identifier = first_text(element, "Identifier")
        if not identifier:
            continue
        formats = [child.text.strip() for child in iter_elements(element, "Format")
                   if child.text]
        matrix_sets = [first_text(child, "TileMatrixSet")
                       for child in iter_elements(element, "TileMatrixSetLink")]
        caps.layers.append(LayerInfo(name=identifier, title=first_text(element, "Title"),
                                     crs=[m for m in matrix_sets if m], formats=formats))
    return caps
