"""Measure hazard and risk over the area, one theme at a time, without mixing them.

The engine reads the themes from configuration, asks the registry which sources answer
each one, measures the intersection with the area and classifies it. Nothing here knows
an endpoint: the sources come from the registry and the classes from
``config/rules/hazard_classes.json``.

The single behaviour worth stating twice: when a theme has no source that can answer it,
the theme is reported as **not determinable**, naming what was consulted. It is never
filled in from a neighbouring theme. A risk inferred from a hazard is a number nobody
computed, presented as if somebody had.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from qgis.core import QgsVectorLayer

from ...core import crs as crs_utils
from ...core import geometry as geom_utils
from ...core import log, measure, settings
from ...core.errors import SourceError
from ...core.feedback import ChildFeedback, Feedback, NullFeedback
from ...core.gaps import DataGap
from ...core.paths import config_dir, user_dir
from ...core.project_area import ProjectArea
from ...core.registry import DataSource, DataSourceRegistry
from .model import HazardClass, HazardRiskOutcome, Kind, ThemeOutcome

_RULES: Optional[Dict[str, Any]] = None


def rules() -> Dict[str, Any]:
    """Load the theme and class rules (built-in plus user overrides)."""
    global _RULES
    if _RULES is None:
        path = config_dir() / "rules" / "hazard_classes.json"
        try:
            _RULES = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
            log.warning(f"Cannot read hazard_classes.json: {exc}")
            _RULES = {"themes": {}, "rules": [], "normalised_levels": {}}
        user_file = user_dir() / "rules" / "hazard_classes.json"
        if user_file.exists():
            try:
                overlay = json.loads(user_file.read_text(encoding="utf-8"))
                for key in ("themes", "normalised_levels"):
                    if overlay.get(key):
                        _RULES.setdefault(key, {}).update(overlay[key])
                if overlay.get("rules"):
                    _RULES["rules"] = list(overlay["rules"]) + list(_RULES.get("rules", []))
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring an invalid hazard_classes.json: {exc}")
    return _RULES


def reload_rules() -> None:
    """Forget the cached rules (tests, settings dialog)."""
    global _RULES
    _RULES = None


def rule_for(source: DataSource) -> Dict[str, Any]:
    """The classification rule that applies to a source, or an empty one."""
    for rule in rules().get("rules", []):
        prefix = (rule.get("match") or {}).get("source_prefix", "")
        if prefix and source.id.startswith(prefix):
            return rule
    return {}


def normalised_label(level: str, language: str = "it") -> str:
    """Human wording of a normalised level."""
    labels = rules().get("normalised_levels", {}).get(f"labels_{language}", {})
    return labels.get(level, level)


class HazardRiskEngine:
    """Hazard and risk over a project area, kept semantically apart."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http=None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http

    # ------------------------------------------------------------------ selection

    def themes(self) -> Dict[str, Dict[str, Any]]:
        """Every theme the configuration declares."""
        return dict(rules().get("themes", {}))

    def sources_for(self, theme: str) -> List[DataSource]:
        """Operational sources that answer one theme, best first."""
        wanted = set((self.themes().get(theme) or {}).get("categories", [theme]))
        found = [s for s in self.registry.query(operational_only=True)
                 if s.category in wanted]
        return sorted(found, key=lambda s: s.priority)

    # ------------------------------------------------------------------ entry point

    def run(self, area: ProjectArea, *, themes: Optional[Sequence[str]] = None,
            feedback: Optional[Feedback] = None,
            refresh: bool = False) -> HazardRiskOutcome:
        """Measure every requested theme. Safe in a worker thread."""
        feedback = feedback or NullFeedback()
        work_crs = area.work_crs
        total = measure.area_m2(area.geometry_in(work_crs), work_crs)
        outcome = HazardRiskOutcome(area_m2=total)
        names = list(themes) if themes else list(self.themes())
        for index, name in enumerate(names):
            if feedback.is_canceled():
                break
            child = ChildFeedback(feedback, 100.0 * index / max(len(names), 1),
                                  100.0 * (index + 1) / max(len(names), 1))
            outcome.themes.append(self.run_theme(area, name, feedback=child,
                                                 refresh=refresh, area_m2=total))
        return outcome

    def run_theme(self, area: ProjectArea, theme: str, *,
                  feedback: Optional[Feedback] = None, refresh: bool = False,
                  area_m2: Optional[float] = None) -> ThemeOutcome:
        """Measure one theme and classify what it found."""
        feedback = feedback or NullFeedback()
        work_crs = area.work_crs
        config = self.themes().get(theme, {})
        kind = Kind(config.get("kind", "hazard"))
        total = area_m2 if area_m2 is not None else measure.area_m2(
            area.geometry_in(work_crs), work_crs)
        result = ThemeOutcome(theme=theme, kind=kind, area_m2=total)

        sources = self.sources_for(theme)
        if not sources:
            # The theme has no source at all. Crucially this is *not* filled in from a
            # related theme: a risk that nobody computed must not appear as a number.
            result.gaps.append(DataGap.NO_DATA.value)
            result.warnings.append(
                f"Nessuna fonte configurata per «{config.get('label_it', theme)}»: "
                f"il dato non e' determinabile e non viene dedotto da altri temi.")
            return result

        # Every source that answers the theme is consulted, not just the first. Some
        # national datasets are partitioned - the flood layers are one per river-basin
        # district - so "the highest-priority descriptor" is the wrong district for most
        # areas, and taking it alone reports 0% where the data plainly exists.
        cap = int(settings.get("hazard_risk.max_sources_per_theme", 40))
        consulted = sources[:cap]
        if len(sources) > len(consulted):
            result.warnings.append(
                f"Consultate {len(consulted)} fonti su {len(sources)}: il risultato "
                f"potrebbe essere incompleto.")
            result.gaps.append(DataGap.PARTIAL_COVERAGE.value)

        answered = 0
        buckets: List[HazardClass] = []
        seen_gaps: List[str] = []
        for source in consulted:
            if feedback.is_canceled():
                break
            rule = rule_for(source)
            if not result.source_id:
                self._attribute(result, source, rule, kind, theme, work_crs)
            if rule.get("limitation") and rule["limitation"] not in result.limitations:
                result.limitations.append(rule["limitation"])

            if not self._is_measurable(source):
                seen_gaps.append(DataGap.VIEW_ONLY.value)
                result.warnings.append(
                    f"«{source.name}» e' consultabile ma non scaricabile: superficie e "
                    f"percentuale non sono calcolabili.")
                continue
            try:
                fetched = self._fetch(area, source, feedback, refresh)
            except SourceError as exc:
                seen_gaps.append(DataGap.SOURCE_UNAVAILABLE.value)
                result.warnings.append(f"«{source.name}» non ha risposto: {exc}")
                continue
            layer = fetched.layer(source.name) if fetched is not None else None
            if layer is None or not layer.isValid():
                seen_gaps.append(DataGap.QUERY_FAILED.value)
                continue
            answered += 1
            # A capped or lossy download turns every percentage into a share of whatever
            # arrived. The number stays useful only if it says what it is.
            if getattr(fetched, "truncated", False):
                seen_gaps.append(DataGap.PARTIAL_COVERAGE.value)
                result.warnings.append(
                    f"«{source.name}»: risposta troncata al limite di feature, le "
                    f"superfici si riferiscono alle sole feature scaricate.")
            if getattr(fetched, "lost_features", 0):
                seen_gaps.append(DataGap.PARTIAL_COVERAGE.value)
                result.warnings.append(
                    f"«{source.name}»: {fetched.lost_features} feature annunciate non "
                    f"sono state lette, il calcolo e' incompleto.")
            found = self._classify(layer, area, source, rule, total)
            if found:
                # Whichever source actually carries data names the result, so the
                # provenance points at what was measured, not at what was tried first.
                self._attribute(result, source, rule, kind, theme, work_crs)
            buckets.extend(found)

        result.classes = self._merge(buckets)
        for gap in seen_gaps:
            if gap not in result.gaps:
                result.gaps.append(gap)
        if not answered:
            if not any(g in result.gaps for g in (DataGap.SOURCE_UNAVAILABLE.value,
                                                  DataGap.VIEW_ONLY.value)):
                result.gaps.append(DataGap.QUERY_FAILED.value)
        elif not result.classes:
            result.gaps.append(DataGap.NO_FEATURE_FOUND.value)
        return result

    @staticmethod
    def _attribute(result: ThemeOutcome, source: DataSource, rule: Dict[str, Any],
                   kind: Kind, theme: str, work_crs) -> None:
        """Point the result at a source: its name, its plan and its provenance."""
        result.source_id = source.id
        result.source_name = source.name
        result.plan = rule.get("plan", "") or source.legal_reference
        result.dataset_version = source.last_verified or ""
        result.provenance = source.provenance(
            operation=f"{kind.label_it} - {theme}", crs=work_crs.authid())

    @staticmethod
    def _merge(classes: List[HazardClass]) -> List[HazardClass]:
        """Fold classes sharing an official code, however many sources produced them.

        A partitioned dataset legitimately answers the same class from two neighbouring
        districts when an area straddles a boundary, and two rows for one class would
        read as two classes.
        """
        merged: Dict[str, HazardClass] = {}
        for row in classes:
            existing = merged.get(row.official_code)
            if existing is None:
                merged[row.official_code] = row
                continue
            existing.area_m2 += row.area_m2
            existing.percentage += row.percentage
            existing.feature_count += row.feature_count
        return sorted(merged.values(), key=lambda c: c.area_m2, reverse=True)


    # ------------------------------------------------------------------ retrieval

    @staticmethod
    def _is_measurable(source: DataSource) -> bool:
        """Whether the theme can be measured rather than only looked at."""
        return source.type.value in ("WFS", "OGCAPI", "ARCGIS_FEATURE", "GEOJSON", "GPKG")

    def _fetch(self, area: ProjectArea, source: DataSource, feedback: Feedback,
               refresh: bool):
        """Download the source over the area through the existing service layer.

        The whole :class:`FetchResult` comes back, not just the layer: whether the
        response was capped or lost features changes what the percentages mean.
        """
        from ...services.ogc.wfs import WfsClient

        service_crs = source.crs[0] if source.crs else crs_utils.WGS84
        rect = area.context_bbox(service_crs)
        return WfsClient(source, http=self.http).fetch(
            rect, service_crs, area_id=area.id, feedback=feedback, refresh=refresh)

    # ------------------------------------------------------------------ classification

    @staticmethod
    def _classify(layer: QgsVectorLayer, area: ProjectArea, source: DataSource,
                  rule: Dict[str, Any], total: float) -> List[HazardClass]:
        """Group the intersected surface by the class the source publishes."""
        work_crs = area.work_crs
        area_geom = area.geometry_in(work_crs)
        field_name = rule.get("field") or (source.fields or {}).get("class", "")
        derived = rule.get("derived_from", "attribute" if field_name else "layer")
        values = rule.get("values", {})
        layer_values = rule.get("layer_values", {})
        has_field = bool(field_name) and layer.fields().indexOf(field_name) >= 0

        buckets: Dict[str, Dict[str, Any]] = {}
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception as exc:
                    log.debug(f"_classify: feature saltata "
                              f"({type(exc).__name__}: {exc})")
                    continue
            clipped = geom_utils.intersection(geometry, area_geom)
            if clipped is None or clipped.isEmpty():
                continue
            surface = measure.area_m2(clipped, work_crs)
            if surface <= 0:
                continue
            if has_field:
                code = str(feature[field_name] or "").strip()
            else:
                # No attribute: the layer itself is the only distinction the source
                # offers, and the result says so rather than implying a classification.
                code = source.layer
            entry = buckets.setdefault(code, {"area": 0.0, "count": 0})
            entry["area"] += surface
            entry["count"] += 1

        table = values if has_field else layer_values
        classes = []
        for code, entry in buckets.items():
            described = table.get(code, {})
            classes.append(HazardClass(
                official_code=code,
                official_label=described.get("label", code),
                normalised=described.get("normalised", "unclassified"),
                area_m2=entry["area"],
                percentage=measure.percentage(entry["area"], total),
                feature_count=entry["count"],
                derived_from="attribute" if has_field else derived,
            ))
        return sorted(classes, key=lambda c: c.area_m2, reverse=True)


def run(area: ProjectArea, *, registry: Optional[DataSourceRegistry] = None,
        themes: Optional[Sequence[str]] = None,
        feedback: Optional[Feedback] = None) -> HazardRiskOutcome:
    """Convenience entry point used by the GUI and by Processing."""
    return HazardRiskEngine(registry=registry).run(area, themes=themes, feedback=feedback)
