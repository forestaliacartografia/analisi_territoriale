"""Territorial knowledge taxonomy.

The macro-categories of the *Quadro Conoscitivo Territoriale* are data, not code: they are
read from ``config/taxonomy.json`` and can be extended by the user with a file of the same
shape in ``<profile>/territorial_suite/taxonomy.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from . import log
from .errors import ConfigError
from .paths import config_dir, user_dir

KNOWLEDGE = "knowledge"
SUPPORT = "support"


@dataclass(frozen=True)
class Category:
    """One entry of the territorial taxonomy."""

    id: str
    order: int = 999
    kind: str = KNOWLEDGE
    parent: str = ""
    label_en: str = ""
    label_it: str = ""
    report_section: str = ""
    description_it: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def label(self, language: str = "en") -> str:
        """Return the human label in the requested language, with fallbacks."""
        if language.lower().startswith("it") and self.label_it:
            return self.label_it
        return self.label_en or self.label_it or self.id


class Taxonomy:
    """Loaded taxonomy, cached process-wide."""

    _instance: Optional["Taxonomy"] = None

    def __init__(self, categories: List[Category]) -> None:
        self._by_id = {cat.id: cat for cat in categories}
        self._ordered = sorted(categories, key=lambda c: (c.order, c.id))

    @classmethod
    def instance(cls) -> "Taxonomy":
        """Return the shared taxonomy, loading it on first use."""
        if cls._instance is None:
            cls._instance = cls.load()
        return cls._instance

    @classmethod
    def reload(cls) -> "Taxonomy":
        """Force a reload from disk (used after the user edits the config)."""
        cls._instance = cls.load()
        return cls._instance

    @classmethod
    def load(cls) -> "Taxonomy":
        """Load built-in categories and merge user-provided ones."""
        categories: Dict[str, Category] = {}
        for path in (config_dir() / "taxonomy.json", user_dir() / "taxonomy.json"):
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError) as exc:
                if path.parent == config_dir():
                    raise ConfigError("Cannot read taxonomy.json", detail=str(exc)) from exc
                log.warning(f"Ignoring invalid user taxonomy.json: {exc}")
                continue
            for raw in payload.get("categories", []):
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                known = {f.name for f in Category.__dataclass_fields__.values()}
                extra = {k: v for k, v in raw.items() if k not in known}
                categories[raw["id"]] = Category(
                    id=raw["id"],
                    order=int(raw.get("order", 999)),
                    kind=raw.get("kind", KNOWLEDGE),
                    parent=str(raw.get("parent", "")),
                    label_en=raw.get("label_en", ""),
                    label_it=raw.get("label_it", ""),
                    report_section=raw.get("report_section", ""),
                    description_it=raw.get("description_it", ""),
                    extra=extra,
                )
        if not categories:
            raise ConfigError("Taxonomy is empty")
        return cls(list(categories.values()))

    def all(self) -> List[Category]:
        """Return every category, ordered."""
        return list(self._ordered)

    def knowledge(self) -> List[Category]:
        """Return the top-level categories that make up the territorial dossier.

        Sub-categories exist to describe *what a source maps* precisely; the dossier keeps
        its twelve sections, so they are deliberately not returned here.
        """
        return [c for c in self._ordered if c.kind == KNOWLEDGE and not c.parent]

    def support(self) -> List[Category]:
        """Return operational (non-dossier) categories."""
        return [c for c in self._ordered if c.kind == SUPPORT]

    def get(self, category_id: str) -> Optional[Category]:
        """Return a category by id, or ``None``."""
        return self._by_id.get(category_id)

    def label(self, category_id: str, language: str = "en") -> str:
        """Return the label of ``category_id``, falling back to the raw id."""
        cat = self._by_id.get(category_id)
        return cat.label(language) if cat else category_id

    def ids(self) -> List[str]:
        """Return every known category id, ordered."""
        return [c.id for c in self._ordered]

    # ------------------------------------------------------------------ hierarchy

    def children(self, category_id: str) -> List[Category]:
        """Return the direct children of a category, ordered."""
        return [c for c in self._ordered if c.parent == category_id]

    def descendants(self, category_id: str) -> List[Category]:
        """Return every category below ``category_id`` at any depth."""
        found: List[Category] = []
        frontier = [category_id]
        seen = {category_id}
        while frontier:
            current = frontier.pop()
            for child in self.children(current):
                if child.id in seen:          # a malformed config must not loop forever
                    continue
                seen.add(child.id)
                found.append(child)
                frontier.append(child.id)
        return sorted(found, key=lambda c: (c.order, c.id))

    def ancestry(self, category_id: str) -> List[str]:
        """Return ``[category_id, parent, grandparent, ...]`` up to the root."""
        chain: List[str] = []
        current = category_id
        while current and current not in chain:
            chain.append(current)
            category = self._by_id.get(current)
            current = category.parent if category else ""
        return chain

    def root_of(self, category_id: str) -> str:
        """Return the dossier macro-category a (possibly nested) category belongs to."""
        chain = self.ancestry(category_id)
        return chain[-1] if chain else category_id

    def expand(self, category_ids: Iterable[str]) -> List[str]:
        """Return the requested categories plus all their descendants.

        Engines keep asking for ``landscape_cultural``; the catalogue answers with the
        landscape *and* archaeological *and* UNESCO sources filed underneath it.
        """
        wanted: List[str] = []
        for category_id in category_ids:
            if not category_id or category_id in wanted:
                continue
            wanted.append(category_id)
            for child in self.descendants(category_id):
                if child.id not in wanted:
                    wanted.append(child.id)
        return wanted
