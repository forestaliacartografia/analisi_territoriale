"""Map styling engine.

Styles are configuration, not code: ``config/styles/styles.json`` holds colours, widths and
label fields; a ``.qml`` file with the style name (shipped or in the user profile) always
wins, so a user can replace any style with one exported from QGIS without touching Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QPainter

from ...core import log, settings
from ...core.paths import config_dir, user_dir
from ...core.taxonomy import Taxonomy

_CACHE: Optional[Dict[str, Any]] = None


def definitions() -> Dict[str, Any]:
    """Load (and cache) the style configuration."""
    global _CACHE
    if _CACHE is None:
        path = config_dir() / "styles" / "styles.json"
        try:
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
            log.warning(f"Cannot read styles.json: {exc}")
            _CACHE = {"styles": {}, "category_defaults": {}, "raster": {}, "ramps": {}}
        user_file = user_dir() / "styles" / "styles.json"
        if user_file.exists():
            try:
                overlay = json.loads(user_file.read_text(encoding="utf-8"))
                for section in ("styles", "category_defaults", "raster", "ramps", "presets"):
                    _CACHE.setdefault(section, {}).update(overlay.get(section, {}))
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring invalid user styles.json: {exc}")
    return _CACHE


def reload_definitions() -> None:
    """Drop the cached configuration (after the user edits the styles)."""
    global _CACHE
    _CACHE = None


def qml_path(style_name: str) -> Optional[Path]:
    """Return the ``.qml`` file for a style name, if one exists."""
    for folder in (user_dir() / "styles", config_dir() / "styles"):
        candidate = folder / f"{style_name}.qml"
        if candidate.exists():
            return candidate
    return None


def style_for_category(category: str) -> str:
    """Return the default style name for a taxonomy category.

    Sub-categories inherit the style of their dossier macro-category unless they declare
    one of their own, so adding a category never leaves a layer unstyled.
    """
    defaults = definitions().get("category_defaults", {})
    for candidate in Taxonomy.instance().ancestry(category) or [category]:
        if candidate in defaults:
            return defaults[candidate]
    return ""


def _color(value: str, fallback: str = "#000000") -> QColor:
    """Parse a colour written as ``#RGB``, ``#RRGGBB`` or ``#RRGGBBAA``.

    Qt reads a nine-character ``#XXXXXXXX`` string as ``#AARRGGBB``, while every web-style
    palette (and this configuration) writes ``#RRGGBBAA``. Parsing the alpha ourselves
    avoids the classic "everything turned yellow" surprise.
    """
    text = (value or fallback or "").strip()
    if len(text) == 9 and text.startswith("#"):
        try:
            red = int(text[1:3], 16)
            green = int(text[3:5], 16)
            blue = int(text[5:7], 16)
            alpha = int(text[7:9], 16)
            return QColor(red, green, blue, alpha)
        except ValueError:
            pass
    colour = QColor(text)
    if not colour.isValid():
        colour = QColor(fallback)
    return colour


def _color_string(value: str, fallback: str = "#000000") -> str:
    """Return a colour as ``r,g,b,a``, the form QGIS symbol properties expect."""
    colour = _color(value, fallback)
    return f"{colour.red()},{colour.green()},{colour.blue()},{colour.alpha()}"


def _pen_style(name: str) -> str:
    return {"solid": "solid", "dash": "dash", "dot": "dot",
            "dash_dot": "dash dot", "dashdot": "dash dot"}.get(str(name).lower(), "solid")


def build_symbol(spec: Dict[str, Any], geometry_type: Qgis.GeometryType,
                 *, line_scale: float = 1.0):
    """Build a QGIS symbol from a style definition."""
    if geometry_type == Qgis.GeometryType.Polygon:
        return QgsFillSymbol.createSimple({
            "color": _color_string(spec.get("fill", "#00000000")),
            "outline_color": _color_string(spec.get("stroke", "#555555")),
            "outline_width": str(float(spec.get("stroke_width", 0.4)) * line_scale),
            "outline_style": _pen_style(spec.get("stroke_style", "solid")),
        })
    if geometry_type == Qgis.GeometryType.Line:
        return QgsLineSymbol.createSimple({
            "color": _color_string(spec.get("stroke", "#555555")),
            "width": str(float(spec.get("stroke_width", 0.4)) * line_scale),
            "line_style": _pen_style(spec.get("stroke_style", "solid")),
            "capstyle": "round",
            "joinstyle": "round",
        })
    return QgsMarkerSymbol.createSimple({
        "name": spec.get("marker", "circle"),
        "color": _color_string(spec.get("fill", "#555555")),
        "outline_color": _color_string(spec.get("stroke", "#ffffff")),
        "outline_width": "0.2",
        "size": str(float(spec.get("size", 2.0))),
    })


def apply_labels(layer: QgsVectorLayer, spec: Dict[str, Any]) -> None:
    """Enable labelling when the style declares a label field present in the layer."""
    field_name = spec.get("label_field")
    if not field_name:
        return
    names = {field.name().lower(): field.name() for field in layer.fields()}
    resolved = names.get(str(field_name).lower())
    if not resolved:
        return
    settings = QgsPalLayerSettings()
    settings.fieldName = resolved
    text_format = QgsTextFormat()
    text_format.setSize(float(spec.get("label_size", 8)))
    text_format.setColor(_color(spec.get("label_color", "#333333")))
    buffer = text_format.buffer()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor("#ffffff"))
    text_format.setBuffer(buffer)
    settings.setFormat(text_format)
    if layer.geometryType() == Qgis.GeometryType.Line:
        settings.placement = Qgis.LabelPlacement.Line
    elif layer.geometryType() == Qgis.GeometryType.Polygon:
        settings.placement = Qgis.LabelPlacement.OverPoint
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def apply_vector_style(layer: QgsVectorLayer, style_name: str = "", *,
                       category: str = "", preset: str = "professional") -> str:
    """Style a vector layer. Returns the style name actually applied."""
    if layer is None or not layer.isValid():
        return ""
    name = style_name or style_for_category(category)
    config = definitions()
    if not name:
        name = {Qgis.GeometryType.Polygon: "generic_constraint",
                Qgis.GeometryType.Line: "generic_line",
                Qgis.GeometryType.Point: "generic_point"}.get(layer.geometryType(), "")
    path = qml_path(name)
    if path is not None:
        message, ok = layer.loadNamedStyle(str(path))
        if ok:
            layer.triggerRepaint()
            return name
        log.warning(f"Cannot load style {path.name}: {message}")
    spec = config.get("styles", {}).get(name)
    if not spec:
        return ""
    line_scale = float(config.get("presets", {}).get(preset, {}).get("line_scale", 1.0))
    symbol = build_symbol(spec, layer.geometryType(), line_scale=line_scale)
    if symbol is not None:
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    apply_labels(layer, spec)
    if "opacity" in spec:
        layer.setOpacity(float(spec["opacity"]))
    layer.triggerRepaint()
    return name


def ramp_spec(ramp_name: str) -> Dict[str, Any]:
    """Return the definition of a colour ramp in its normalised object form.

    A ramp is written either as a plain list of stops with values in 0-1, stretched over
    the band range, or as ``{"type": "discrete", "domain": [...], "items": [...]}`` with
    absolute values. The second form is what slope and aspect need: a 30 % slope must get
    the same colour on every sheet, whatever the local minimum and maximum happen to be.
    """
    raw = definitions().get("ramps", {}).get(ramp_name)
    if isinstance(raw, dict):
        return {"type": str(raw.get("type", "discrete")).lower(),
                "domain": list(raw.get("domain", [])),
                "items": list(raw.get("items", []))}
    stops = raw if isinstance(raw, list) and raw else [
        {"value": 0.0, "color": "#000000"}, {"value": 1.0, "color": "#ffffff"}]
    return {"type": "normalised", "domain": [0.0, 1.0], "items": stops}


def _ramp_items(ramp_name: str, minimum: float, maximum: float) -> List:
    """Build colour-ramp shader items for a band spanning ``minimum``-``maximum``."""
    spec = ramp_spec(ramp_name)
    items = []
    if spec["type"] == "normalised":
        span = (maximum - minimum) or 1.0
        for stop in spec["items"]:
            value = minimum + float(stop.get("value", 0.0)) * span
            items.append(QgsColorRampShader.ColorRampItem(
                value, _color(stop.get("color", "#000000")), f"{value:.0f}"))
        return items
    for stop in spec["items"]:
        value = float(stop.get("value", 0.0))
        label = str(stop.get("label", "")) or f"{value:.0f}"
        items.append(QgsColorRampShader.ColorRampItem(
            value, _color(stop.get("color", "#000000")), label))
    return items


def ramp_for_style(style_name: str) -> str:
    """Return the colour ramp a raster style uses.

    The two names differ on purpose: the *style* is the theme ("dem", "slope") while the
    *ramp* is the palette ("terrain", "slope"). Reading the ramp from the style keeps the
    baked composite and the on-screen layer using the same colours.
    """
    spec = definitions().get("raster", {}).get(style_name, {})
    return str(spec.get("ramp", "")) or style_name


def has_ramp(ramp_name: str) -> bool:
    """Whether a ramp is actually defined in the configuration.

    :func:`ramp_spec` degrades a typo to a black-to-white ramp, which is acceptable for a
    live layer the user can re-style but not for a composite written to disk: a grey file
    looks like a legitimate hillshade and nothing says it is wrong.
    """
    return bool(definitions().get("ramps", {}).get(ramp_name))


def ramp_lookup(ramp_name: str) -> List:
    """Return ``[(upper_bound, (r, g, b)), ...]`` used to bake a composite raster."""
    spec = ramp_spec(ramp_name)
    table = []
    for stop in spec["items"]:
        colour = _color(stop.get("color", "#000000"))
        table.append((float(stop.get("value", 0.0)),
                      (colour.red(), colour.green(), colour.blue())))
    return sorted(table, key=lambda entry: entry[0])


#: Prefix of the styles that name an already-flattened relief composite.
SHADED_PREFIX = "shaded"


def apply_raster_style(layer: QgsRasterLayer, style_name: str) -> str:
    """Style a raster layer (DEM, slope, aspect, hillshade, baked composite)."""
    if layer is None or not layer.isValid():
        return ""
    if style_name.startswith(SHADED_PREFIX):
        # The colours are already burnt into the pixels: applying a single-band renderer
        # to an RGB composite would turn it grey.
        layer.setOpacity(1.0)
        layer.triggerRepaint()
        return style_name
    path = qml_path(style_name)
    if path is not None:
        message, ok = layer.loadNamedStyle(str(path))
        if ok:
            layer.triggerRepaint()
            return style_name
        log.warning(f"Cannot load raster style {path.name}: {message}")
    spec = definitions().get("raster", {}).get(style_name)
    if not spec:
        return ""
    provider = layer.dataProvider()
    try:
        stats = provider.bandStatistics(1, QgsRasterBandStats.Stats.All)
        minimum, maximum = float(stats.minimumValue), float(stats.maximumValue)
    except Exception:  # pragma: no cover - defensive
        minimum, maximum = 0.0, 1.0
    if style_name == "hillshade":
        renderer = QgsSingleBandGrayRenderer(provider, 1)
        layer.setRenderer(renderer)
    else:
        ramp_name = spec.get("ramp", "gray")
        definition = ramp_spec(ramp_name)
        discrete = definition["type"] == "discrete"
        if discrete and definition["domain"]:
            minimum, maximum = (float(definition["domain"][0]),
                                float(definition["domain"][-1]))
        shader = QgsRasterShader()
        ramp_shader = QgsColorRampShader(minimum, maximum)
        ramp_shader.setColorRampType(QgsColorRampShader.Type.Discrete if discrete
                                     else QgsColorRampShader.Type.Interpolated)
        ramp_shader.setColorRampItemList(_ramp_items(ramp_name, minimum, maximum))
        shader.setRasterShaderFunction(ramp_shader)
        layer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, 1, shader))
    layer.setOpacity(float(spec.get("opacity", 1.0)))
    blend = str(spec.get("blend", "")).lower()
    if blend:
        _apply_blend(layer, blend)
    layer.triggerRepaint()
    return style_name


#: Blend modes a style may request, by their configuration name.
_BLEND_MODES = {"multiply": "Multiply", "normal": "SourceOver", "overlay": "Overlay",
                "screen": "Screen", "darken": "Darken"}


def _apply_blend(layer: QgsRasterLayer, blend: str) -> bool:
    """Set a layer blend mode by name. Returns whether it could be applied."""
    name = _BLEND_MODES.get(blend)
    if name is None:
        log.warning(f"Modalita' di fusione sconosciuta: {blend}")
        return False
    mode = getattr(QPainter.CompositionMode, f"CompositionMode_{name}", None)
    if mode is None:  # pragma: no cover - Qt naming differences
        log.warning(f"Modalita' di fusione non disponibile in questa build: {blend}")
        return False
    layer.setBlendMode(mode)
    return True


def apply_shaded_relief(colour_layer: QgsRasterLayer, hillshade_layer: QgsRasterLayer, *,
                        opacity: Optional[float] = None) -> bool:
    """Lay a hillshade over a coloured raster, multiplying the two.

    This is what makes a DEM, a slope or an aspect map readable: the colour carries the
    value, the shading carries the shape. It applies **on the canvas**; print and PDF
    export often drop layer blend modes, which is why the terrain engine also bakes a
    flattened composite for the layouts.

    :returns: whether the blend mode could really be set.
    """
    if colour_layer is None or hillshade_layer is None:
        return False
    if not colour_layer.isValid() or not hillshade_layer.isValid():
        return False
    spec = definitions().get("raster", {}).get("hillshade", {})
    value = settings.get("terrain.hillshade_opacity", spec.get("opacity", 0.55))         if opacity is None else opacity
    hillshade_layer.setOpacity(max(0.0, min(1.0, float(value))))
    colour_layer.setBlendMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    return _apply_blend(hillshade_layer, str(spec.get("blend", "multiply")).lower())


def apply_style(layer, style_name: str = "", *, category: str = "",
                preset: str = "professional") -> str:
    """Style any layer, dispatching on its type."""
    if isinstance(layer, QgsRasterLayer):
        return apply_raster_style(layer, style_name or category)
    if isinstance(layer, QgsVectorLayer):
        return apply_vector_style(layer, style_name, category=category, preset=preset)
    return ""


def available_styles() -> List[str]:
    """Return every style name the engine can apply."""
    names = set(definitions().get("styles", {}))
    names.update(definitions().get("raster", {}))
    for folder in (config_dir() / "styles", user_dir() / "styles"):
        if folder.exists():
            names.update(path.stem for path in folder.glob("*.qml"))
    return sorted(names)
