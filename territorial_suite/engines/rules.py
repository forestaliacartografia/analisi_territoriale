"""Declarative rule engine producing territorial alerts.

Rules live in ``config/rules/*.json`` (plus user overrides) and are pure data::

    {"id": "...", "when": {"category": "water", "operation": "distance_lt",
                           "threshold": 150, "unit": "m"},
     "level": "CHECK_REQUIRED", "title": "...", "detail": "...",
     "legal_reference": "..."}

**The engine never states a legal conclusion.** ``level`` is a semantic classification
(INFO / ATTENTION / CHECK_REQUIRED / CARTOGRAPHIC_ISSUE), ``legal_reference`` is quoted as
a *reference declared by the source*, and every alert carries the evidence level of the
data it is based on, so the reader always knows whether it rests on a map or on an act.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core import log, measure
from ..core.errors import ConfigError
from ..core.models import Alert, AlertLevel, AnalysisReport, EvidenceLevel, SourceResult
from ..core.paths import config_dir, user_dir
from ..core.taxonomy import Taxonomy

SUBJECT_SOURCE = "source"
SUBJECT_AREA = "area"
SUBJECT_CADASTRE = "cadastre"
SUBJECT_TERRAIN = "terrain"


@dataclass
class Rule:
    """One declarative rule."""

    id: str
    title: str
    level: AlertLevel = AlertLevel.INFO
    detail: str = ""
    category: str = ""
    #: ``subtree`` (default) also matches the sub-categories; ``exact`` only this one.
    match: str = "subtree"
    source_id: str = ""
    subject: str = SUBJECT_SOURCE
    operation: str = "intersects"
    threshold: float = 0.0
    unit: str = ""
    # JSON key "field"; renamed here so it does not shadow dataclasses.field.
    attribute: str = ""
    values: List[Any] = field(default_factory=list)
    legal_reference: str = ""
    enabled: bool = True
    priority: int = 100

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Rule":
        """Parse a rule descriptor."""
        data = dict(payload or {})
        when = dict(data.get("when", {}))
        rule_id = str(data.get("id", "")).strip()
        if not rule_id:
            raise ConfigError("Rule without id")
        try:
            level = AlertLevel(str(data.get("level", "INFO")).upper())
        except ValueError:
            log.warning(f"Rule {rule_id}: unknown level '{data.get('level')}', using INFO")
            level = AlertLevel.INFO
        return cls(
            id=rule_id,
            title=str(data.get("title", rule_id)),
            level=level,
            detail=str(data.get("detail", "")),
            category=str(when.get("category", "")),
            match=str(when.get("match", "subtree")).strip().lower() or "subtree",
            source_id=str(when.get("source_id", "")),
            subject=str(when.get("subject", SUBJECT_SOURCE)),
            operation=str(when.get("operation", "intersects")),
            threshold=float(when.get("threshold", 0) or 0),
            unit=str(when.get("unit", "")),
            attribute=str(when.get("field", "")),
            values=list(when.get("values", [])),
            legal_reference=str(data.get("legal_reference", "")),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 100)),
        )

    def matches_source(self, result: SourceResult) -> bool:
        """Whether this rule applies to a given source result."""
        if self.source_id:
            return result.source_id == self.source_id or \
                result.source_id.startswith(self.source_id)
        if self.category:
            if self.match == "exact":
                # Used by the generic rule of a macro-category, so that it does not fire
                # again next to the specific rules of its sub-categories.
                return result.category == self.category
            # A rule written for a dossier macro-category otherwise covers the
            # sub-categories filed under it: "aree naturalistiche" must fire on a
            # national park.
            return self.category in Taxonomy.instance().ancestry(result.category)
        return False


# --------------------------------------------------------------------------- operations


def _op_intersects(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    if result.present:
        return {"count": result.feature_count, "pct": result.intersect_pct,
                "area": result.intersect_area_m2}
    return None


def _op_not_present(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    return {} if result.ok and not result.present else None


def _op_area_pct_gt(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    if result.present and result.intersect_pct > rule.threshold:
        return {"pct": result.intersect_pct, "threshold": rule.threshold}
    return None


def _op_area_m2_gt(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    if result.present and result.intersect_area_m2 > rule.threshold:
        return {"area": result.intersect_area_m2, "threshold": rule.threshold}
    return None


def _op_distance_lt(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    distance = result.min_distance_m
    if result.present or distance is None:
        return None
    if distance < rule.threshold:
        return {"distance": distance, "threshold": rule.threshold}
    return None


def _op_within_or_near(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    """Intersecting *or* closer than the threshold - the usual "setback" question."""
    if result.present:
        return {"distance": 0.0, "threshold": rule.threshold, "count": result.feature_count}
    distance = result.min_distance_m
    if distance is not None and distance < rule.threshold:
        return {"distance": distance, "threshold": rule.threshold,
                "count": result.feature_count}
    return None


def _op_count_gt(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    if result.feature_count > rule.threshold:
        return {"count": result.feature_count, "threshold": rule.threshold}
    return None


def _op_unavailable(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    return {"error": result.error} if not result.ok else None


def _op_attribute_in(rule: Rule, result: SourceResult) -> Optional[Dict[str, Any]]:
    if not rule.attribute or not rule.values:
        return None
    wanted = {str(value).lower() for value in rule.values}
    for hit in result.hits:
        value = hit.attributes.get(rule.attribute)
        if value is not None and str(value).lower() in wanted:
            return {"value": value, "label": hit.label}
    return None


SOURCE_OPERATIONS: Dict[str, Callable[[Rule, SourceResult], Optional[Dict[str, Any]]]] = {
    "intersects": _op_intersects,
    "not_present": _op_not_present,
    "area_pct_gt": _op_area_pct_gt,
    "area_m2_gt": _op_area_m2_gt,
    "distance_lt": _op_distance_lt,
    "within_or_near": _op_within_or_near,
    "count_gt": _op_count_gt,
    "unavailable": _op_unavailable,
    "attribute_in": _op_attribute_in,
}


class RuleEngine:
    """Loads rules and evaluates them against an analysis report."""

    def __init__(self, rules: Optional[List[Rule]] = None) -> None:
        self.rules = rules or []

    @classmethod
    def load(cls) -> "RuleEngine":
        """Load built-in rules, then user rules (same id overrides)."""
        rules: Dict[str, Rule] = {}
        for folder in (config_dir() / "rules", user_dir() / "rules"):
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.json")):
                if path.name.startswith("_"):
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    log.warning(f"Invalid rule file {path.name}: {exc}")
                    continue
                items = payload if isinstance(payload, list) else payload.get("rules", [])
                for item in items:
                    try:
                        rule = Rule.from_dict(item)
                    except ConfigError as exc:
                        log.warning(f"{path.name}: {exc}")
                        continue
                    rules[rule.id] = rule
        return cls(sorted(rules.values(), key=lambda r: (r.priority, r.id)))

    # ------------------------------------------------------------------ evaluation

    def evaluate(self, report: AnalysisReport) -> List[Alert]:
        """Return the alerts raised by the rules for this report."""
        alerts: List[Alert] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            try:
                if rule.subject == SUBJECT_SOURCE:
                    alerts.extend(self._evaluate_source_rule(rule, report))
                elif rule.subject == SUBJECT_AREA:
                    alerts.extend(self._evaluate_area_rule(rule, report))
                elif rule.subject == SUBJECT_CADASTRE:
                    alerts.extend(self._evaluate_cadastre_rule(rule, report))
                elif rule.subject == SUBJECT_TERRAIN:
                    alerts.extend(self._evaluate_terrain_rule(rule, report))
                else:
                    log.warning(f"Rule {rule.id}: unknown subject '{rule.subject}'")
            except Exception as exc:  # pragma: no cover - a rule must never break a run
                log.exception(f"Rule {rule.id} failed", exc)
        return sorted(alerts, key=lambda alert: (-alert.level.rank, alert.title))

    def _evaluate_source_rule(self, rule: Rule, report: AnalysisReport) -> List[Alert]:
        handler = SOURCE_OPERATIONS.get(rule.operation)
        if handler is None:
            log.warning(f"Rule {rule.id}: unknown operation '{rule.operation}'")
            return []
        alerts: List[Alert] = []
        for result in report.results:
            if not rule.matches_source(result):
                continue
            values = handler(rule, result)
            if values is None:
                continue
            alerts.append(Alert(
                level=rule.level,
                code=rule.id,
                title=self._format(rule.title, result, values),
                detail=self._format(rule.detail, result, values),
                category=result.category or rule.category,
                evidence_level=result.evidence_level,
                source_ids=[result.source_id],
                values=values,
                legal_reference=rule.legal_reference,
            ))
        return alerts

    def _evaluate_area_rule(self, rule: Rule, report: AnalysisReport) -> List[Alert]:
        admin = report.admin
        area_m2 = float(report.area.get("area_m2", 0.0) or 0.0)
        values: Optional[Dict[str, Any]] = None
        if rule.operation == "admin_unresolved" and not admin.resolved:
            values = {"note": admin.note}
        elif rule.operation == "multi_municipality" and admin.is_multi_municipality:
            values = {"count": len(admin.municipalities),
                      "names": [unit.name for unit in admin.municipalities]}
        elif rule.operation == "area_ha_gt" and area_m2 / 10_000.0 > rule.threshold:
            values = {"area": area_m2, "threshold": rule.threshold}
        elif rule.operation == "no_source_available" and not report.results:
            values = {}
        if values is None:
            return []
        return [Alert(level=rule.level, code=rule.id,
                      title=self._format(rule.title, None, values),
                      detail=self._format(rule.detail, None, values),
                      category=rule.category, evidence_level=EvidenceLevel.CARTOGRAPHIC,
                      values=values, legal_reference=rule.legal_reference)]

    def _evaluate_cadastre_rule(self, rule: Rule, report: AnalysisReport) -> List[Alert]:
        rows = report.cadastre
        values: Optional[Dict[str, Any]] = None
        if rule.operation == "multi_municipality":
            municipalities = {row.municipality for row in rows}
            if len(municipalities) > 1:
                values = {"count": len(municipalities), "names": sorted(municipalities)}
        elif rule.operation == "multi_sheet":
            sheets = {(row.municipality, row.sheet) for row in rows}
            if len(sheets) > max(1, rule.threshold):
                values = {"count": len(sheets)}
        elif rule.operation == "parcels_gt" and len(rows) > rule.threshold:
            values = {"count": len(rows), "threshold": rule.threshold}
        elif rule.operation == "no_parcels" and not rows:
            values = {}
        if values is None:
            return []
        return [Alert(level=rule.level, code=rule.id,
                      title=self._format(rule.title, None, values),
                      detail=self._format(rule.detail, None, values),
                      category="cadastral", evidence_level=EvidenceLevel.DECLARATORY,
                      values=values, legal_reference=rule.legal_reference)]

    def _evaluate_terrain_rule(self, rule: Rule, report: AnalysisReport) -> List[Alert]:
        terrain = report.terrain
        if terrain is None or not terrain.available:
            return []
        values: Optional[Dict[str, Any]] = None
        if rule.operation == "slope_mean_gt" and terrain.slope_mean_pct is not None \
                and terrain.slope_mean_pct > rule.threshold:
            values = {"value": terrain.slope_mean_pct, "threshold": rule.threshold}
        elif rule.operation == "slope_max_gt" and terrain.slope_max_pct is not None \
                and terrain.slope_max_pct > rule.threshold:
            values = {"value": terrain.slope_max_pct, "threshold": rule.threshold}
        elif rule.operation == "slope_class_pct_gt":
            share = sum(item.area_pct for item in terrain.slope_classes
                        if item.lower >= float(rule.attribute or 0))
            if share > rule.threshold:
                values = {"value": share, "threshold": rule.threshold, "class": rule.attribute}
        elif rule.operation == "elevation_range_gt" and terrain.elevation_range is not None \
                and terrain.elevation_range > rule.threshold:
            values = {"value": terrain.elevation_range, "threshold": rule.threshold}
        if values is None:
            return []
        return [Alert(level=rule.level, code=rule.id,
                      title=self._format(rule.title, None, values),
                      detail=self._format(rule.detail, None, values),
                      category="terrain", evidence_level=EvidenceLevel.CARTOGRAPHIC,
                      values=values, legal_reference=rule.legal_reference)]

    # ------------------------------------------------------------------ formatting

    @staticmethod
    def _format(template: str, result: Optional[SourceResult],
                values: Dict[str, Any]) -> str:
        """Fill the placeholders of a rule message."""
        if not template:
            return ""
        tokens = {
            "source": result.source_name if result else "",
            "count": values.get("count", ""),
            "threshold": values.get("threshold", ""),
            "names": ", ".join(values.get("names", [])) if values.get("names") else "",
            "error": values.get("error", ""),
            "note": values.get("note", ""),
            "value": values.get("value", ""),
            "class": values.get("class", ""),
            "label": values.get("label", ""),
        }
        if "pct" in values:
            tokens["pct"] = f"{float(values['pct']):.1f}"
        if "area" in values:
            tokens["area"] = measure.format_area(float(values["area"]))
        if "distance" in values:
            tokens["distance"] = measure.format_distance(float(values["distance"]))
        try:
            return template.format(**tokens)
        except (KeyError, IndexError, ValueError):  # pragma: no cover - author error
            return template


def load_rule_files() -> List[Path]:
    """Return the rule files that would be loaded (used by the settings dialog)."""
    paths: List[Path] = []
    for folder in (config_dir() / "rules", user_dir() / "rules"):
        if folder.exists():
            paths.extend(sorted(p for p in folder.glob("*.json") if not p.name.startswith("_")))
    return paths
