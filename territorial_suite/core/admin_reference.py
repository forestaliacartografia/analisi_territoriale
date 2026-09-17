"""Municipality reference table (ISTAT code, name, cadastral code, province, region).

Italian territorial services speak three different dialects for the same municipality: the
ISTAT code, the name, and the cadastral code (``D612``). The bundled table translates
between them offline, which lets the plugin resolve administrative units from a cadastral
answer without an extra service call - and lets the user pick a municipality by name.

See ``resources/reference/README.md`` for the provenance of the data.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import log
from .paths import resources_dir
from .registry import normalise

REFERENCE_FILE = "comuni_istat.csv"
ATTRIBUTION = "Istat - Elenco codici e denominazioni delle unita territoriali (CC BY 4.0)"


@dataclass(frozen=True)
class Municipality:
    """One row of the reference table."""

    istat: str
    name: str
    cadastral: str
    province: str
    province_abbr: str
    region: str
    nuts3: str = ""

    def as_dict(self) -> Dict[str, str]:
        """Return a JSON-friendly dictionary."""
        return {
            "istat": self.istat, "name": self.name, "cadastral": self.cadastral,
            "province": self.province, "province_abbr": self.province_abbr,
            "region": self.region, "nuts3": self.nuts3,
        }


class MunicipalityIndex:
    """In-memory index over the bundled municipality table."""

    _instance: Optional["MunicipalityIndex"] = None

    def __init__(self, rows: List[Municipality]) -> None:
        self._rows = rows
        self._by_istat = {row.istat: row for row in rows}
        self._by_cadastral = {row.cadastral: row for row in rows if row.cadastral}
        self._by_name: Dict[str, List[Municipality]] = {}
        for row in rows:
            self._by_name.setdefault(normalise(row.name), []).append(row)

    @classmethod
    def instance(cls) -> "MunicipalityIndex":
        """Return the shared index, loading the table on first use."""
        if cls._instance is None:
            cls._instance = cls.load()
        return cls._instance

    @classmethod
    def load(cls) -> "MunicipalityIndex":
        """Read the bundled CSV. A missing table degrades to an empty index."""
        path = resources_dir() / "reference" / REFERENCE_FILE
        rows: List[Municipality] = []
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                for record in csv.DictReader(handle, delimiter=";"):
                    istat = (record.get("istat") or "").strip()
                    if not istat:
                        continue
                    rows.append(Municipality(
                        istat=istat,
                        name=(record.get("name") or "").strip(),
                        cadastral=(record.get("cadastral") or "").strip().upper(),
                        province=(record.get("province") or "").strip(),
                        province_abbr=(record.get("province_abbr") or "").strip().upper(),
                        region=(record.get("region") or "").strip(),
                        nuts3=(record.get("nuts3") or "").strip(),
                    ))
        except OSError as exc:  # pragma: no cover - packaging error
            log.warning(f"Municipality reference table unavailable: {exc}")
        return cls(rows)

    # ------------------------------------------------------------------ lookups

    def by_cadastral(self, code: str) -> Optional[Municipality]:
        """Look up a municipality by cadastral code (``D612``)."""
        return self._by_cadastral.get((code or "").strip().upper())

    def by_istat(self, code: str) -> Optional[Municipality]:
        """Look up a municipality by ISTAT code, tolerating missing leading zeros."""
        cleaned = (code or "").strip()
        if not cleaned:
            return None
        found = self._by_istat.get(cleaned)
        if found is None and cleaned.isdigit():
            found = self._by_istat.get(cleaned.zfill(6))
        return found

    def by_name(self, name: str, *, province: str = "") -> List[Municipality]:
        """Look up municipalities by name (accent and case insensitive)."""
        candidates = list(self._by_name.get(normalise(name), []))
        if province:
            wanted = normalise(province)
            filtered = [row for row in candidates
                        if wanted in (normalise(row.province), normalise(row.province_abbr))]
            if filtered:
                return filtered
        return candidates

    def search(self, text: str, *, limit: int = 20) -> List[Municipality]:
        """Prefix search used by the municipality picker in the GUI."""
        needle = normalise(text)
        if not needle:
            return []
        exact = [row for row in self._rows if normalise(row.name) == needle]
        prefix = [row for row in self._rows
                  if normalise(row.name).startswith(needle) and row not in exact]
        return (exact + prefix)[:limit]

    def region_of(self, cadastral_code: str) -> str:
        """Return the region name for a cadastral code, or an empty string."""
        row = self.by_cadastral(cadastral_code)
        return row.region if row else ""

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def available(self) -> bool:
        """Whether the reference table could be loaded."""
        return bool(self._rows)
