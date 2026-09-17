"""Prints that survive being closed.

Until now a sheet existed only as long as the layout the builder had just made. Anything
the user wanted to change meant building it again from scratch, and the settings that
produced last week's Tavola 03 were gone the moment the dialog closed.

A print is therefore recorded: an identifier, the specification it was built from, when it
was made, when it was last touched, and which version it is at. The record lives in the
**QGIS project**, the same place the project areas live, so it travels with the file the
user actually saves and shares. Nothing here writes to a private directory the recipient
of a project would not receive.

What is stored is the **recipe, not the drawing**. Rebuilding from the recipe is what
makes "update this sheet with the new data" possible at all: a stored PDF could only ever
be reprinted, never refreshed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from qgis.core import QgsPrintLayout, QgsProject

from ...core import log
from ...core.constants import PROP_LAYOUT_TEMPLATE, PROP_PRINT_REGISTRY
from ...core.errors import LayoutError
from ...core.project_area import ProjectArea
from .layout import LayoutBuilder, MapSpec

#: How many superseded versions of a print are kept. Enough to undo a mistake, not so
#: many that a project file grows without bound.
HISTORY_LIMIT = 10


def _now() -> str:
    """Timestamp in UTC, ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PrintRecord:
    """One print: what it is, how it was made, and how it got to this version."""

    print_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    subtitle: str = ""
    template: str = ""
    theme: str = ""
    area_id: str = ""
    #: Serialised :class:`MapSpec`: the recipe the sheet is rebuilt from.
    spec: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    version: int = 1
    #: Ids of the sources that fed the sheet, for provenance.
    sources: List[str] = field(default_factory=list)
    #: Last QA outcome, so a list can show which sheets are in trouble without
    #: rebuilding every one of them.
    qa_level: str = ""
    qa_summary: str = ""
    #: Superseded versions, newest last. Each entry is a previous ``as_dict()``.
    history: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PrintRecord":
        """Rebuild from :meth:`as_dict` output, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})

    def map_spec(self) -> MapSpec:
        """The specification this print is rebuilt from."""
        known = {f for f in MapSpec.__dataclass_fields__}
        spec = MapSpec(**{k: v for k, v in (self.spec or {}).items() if k in known})
        spec.template = self.template or spec.template
        if self.title:
            spec.title = self.title
        if self.subtitle:
            spec.subtitle = self.subtitle
        return spec

    def snapshot(self) -> Dict[str, Any]:
        """A copy of this version, without its own history (that would nest forever)."""
        payload = self.as_dict()
        payload.pop("history", None)
        return payload

    @property
    def label(self) -> str:
        """What the list shows: the title, or the identifier when there is none."""
        return self.title or f"Stampa {self.print_id}"


class PrintManager:
    """Create, reopen, update, duplicate and version the prints of a project."""

    def __init__(self, project: Optional[QgsProject] = None) -> None:
        self.project = project or QgsProject.instance()

    # ------------------------------------------------------------------ persistence

    @staticmethod
    def _entry(key: str) -> tuple:
        scope, _, name = key.partition("/")
        return scope, name

    def records(self) -> List[PrintRecord]:
        """Every print stored in the project, newest change first."""
        raw, ok = self.project.readEntry(*self._entry(PROP_PRINT_REGISTRY), "")
        if not ok or not raw:
            return []
        try:
            payload = json.loads(raw)
        except ValueError:  # pragma: no cover - corrupted project
            log.warning("Il registro delle stampe e' illeggibile ed e' stato ignorato")
            return []
        if not isinstance(payload, list):
            return []
        found = [PrintRecord.from_dict(item) for item in payload if isinstance(item, dict)]
        return sorted(found, key=lambda r: r.updated_at, reverse=True)

    def get(self, print_id: str) -> Optional[PrintRecord]:
        """One print by its identifier."""
        return next((r for r in self.records() if r.print_id == print_id), None)

    def _write(self, records: List[PrintRecord]) -> None:
        self.project.writeEntry(*self._entry(PROP_PRINT_REGISTRY),
                                json.dumps([r.as_dict() for r in records],
                                           ensure_ascii=False))
        self.project.setDirty(True)

    def save(self, record: PrintRecord) -> PrintRecord:
        """Store a print, replacing any earlier record with the same identifier."""
        records = [r for r in self.records() if r.print_id != record.print_id]
        records.append(record)
        self._write(records)
        return record

    def remove(self, print_id: str) -> bool:
        """Delete a print. Returns whether anything was deleted."""
        records = self.records()
        remaining = [r for r in records if r.print_id != print_id]
        if len(remaining) == len(records):
            return False
        self._write(remaining)
        return True

    # ------------------------------------------------------------------ operations

    def create(self, area: ProjectArea, spec: MapSpec, *,
               add_to_project: bool = True) -> tuple:
        """Build a sheet and record it.

        :returns: ``(record, layout)``.
        """
        layout = LayoutBuilder(self.project).build(area, spec,
                                                   add_to_project=add_to_project)
        record = PrintRecord(
            title=spec.title or layout.name(),
            subtitle=spec.subtitle,
            template=spec.template,
            area_id=area.id,
            spec=self._spec_payload(spec),
        )
        self._attach(record, layout)
        self._record_qa(record, layout)
        return self.save(record), layout

    def open(self, print_id: str, area: ProjectArea, *,
             add_to_project: bool = True) -> tuple:
        """Rebuild a stored print so it can be looked at or edited.

        :raises LayoutError: when the print is not in this project.
        """
        record = self.get(print_id)
        if record is None:
            raise LayoutError(f"La stampa «{print_id}» non esiste in questo progetto.")
        layout = LayoutBuilder(self.project).build(area, record.map_spec(),
                                                   add_to_project=add_to_project)
        self._attach(record, layout)
        return record, layout

    def update(self, print_id: str, area: ProjectArea, *,
               spec: Optional[MapSpec] = None,
               add_to_project: bool = True) -> tuple:
        """Rebuild a print, keeping its identity and bumping its version.

        The previous version is kept: a print is not overwritten beyond recovery, which
        matters because "update with the new data" is exactly the operation that can turn
        a good sheet into a broken one.

        :raises LayoutError: when the print is not in this project.
        """
        record = self.get(print_id)
        if record is None:
            raise LayoutError(f"La stampa «{print_id}» non esiste in questo progetto.")
        record.history.append(record.snapshot())
        del record.history[:-HISTORY_LIMIT]
        if spec is not None:
            record.spec = self._spec_payload(spec)
            record.template = spec.template or record.template
            if spec.title:
                record.title = spec.title
            if spec.subtitle:
                record.subtitle = spec.subtitle
        record.version += 1
        record.updated_at = _now()
        layout = LayoutBuilder(self.project).build(area, record.map_spec(),
                                                   add_to_project=add_to_project)
        self._attach(record, layout)
        self._record_qa(record, layout)
        return self.save(record), layout

    def duplicate(self, print_id: str, *, title: str = "") -> PrintRecord:
        """Copy a print into a new, independent one at version 1.

        :raises LayoutError: when the print is not in this project.
        """
        record = self.get(print_id)
        if record is None:
            raise LayoutError(f"La stampa «{print_id}» non esiste in questo progetto.")
        copy = PrintRecord.from_dict(record.as_dict())
        copy.print_id = uuid.uuid4().hex[:12]
        copy.title = title or f"{record.label} (copia)"
        copy.version = 1
        copy.history = []
        copy.created_at = copy.updated_at = _now()
        return self.save(copy)

    def restore(self, print_id: str, version: int) -> PrintRecord:
        """Bring a superseded version back as the current one.

        The version being replaced is pushed onto the history in its turn, so restoring
        is itself undoable.

        :raises LayoutError: when the print or the version does not exist.
        """
        record = self.get(print_id)
        if record is None:
            raise LayoutError(f"La stampa «{print_id}» non esiste in questo progetto.")
        wanted = next((h for h in record.history if h.get("version") == version), None)
        if wanted is None:
            available = sorted({h.get("version") for h in record.history})
            raise LayoutError(f"La versione {version} non e' fra quelle conservate "
                              f"({available or 'nessuna'}).")
        history = list(record.history) + [record.snapshot()]
        restored = PrintRecord.from_dict(wanted)
        restored.history = history[-HISTORY_LIMIT:]
        restored.version = record.version + 1
        restored.updated_at = _now()
        return self.save(restored)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _spec_payload(spec: MapSpec) -> Dict[str, Any]:
        """The parts of a specification worth storing, as plain JSON."""
        payload = asdict(spec)
        # The warnings belong to one build, not to the recipe.
        payload.pop("warnings", None)
        return payload

    def _attach(self, record: PrintRecord, layout: QgsPrintLayout) -> None:
        """Mark a layout as belonging to a stored print."""
        from ...core.constants import PROP_PRINT_ID

        layout.setCustomProperty(PROP_PRINT_ID, record.print_id)
        layout.setCustomProperty(PROP_LAYOUT_TEMPLATE, record.template)
        layout.setName(f"{record.label}")

    def _record_qa(self, record: PrintRecord, layout: QgsPrintLayout) -> None:
        """Store the last QA outcome on the record, without blocking anything here."""
        from .export import ExportCenter

        report = ExportCenter.validate(layout)
        if report is None:
            record.qa_level, record.qa_summary = "", ""
            return
        record.qa_level = report.level
        record.qa_summary = report.summary()
        record.theme = report.theme


def print_id_of(layout: QgsPrintLayout) -> str:
    """The stored print a layout belongs to, if any."""
    from ...core.constants import PROP_PRINT_ID

    return str(layout.customProperty(PROP_PRINT_ID, "") or "")
