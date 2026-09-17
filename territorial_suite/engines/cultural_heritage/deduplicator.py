"""Conservative deduplication of cultural records across sources.

The same asset is often published by several SITAP layers: the national art. 136 view and
the regional clone, the Vincoli in Rete inventory and the listed-assets table. Showing it
three times inflates the dossier; merging it carelessly destroys the reason it is
trustworthy - *which* authority published *what*.

So the rule here is deliberately timid. Two records are merged only when the evidence is
unambiguous:

* they carry the same non-empty **act code**, or
* their names match after normalisation **and** they sit in the same municipality.

Anything less certain is left as two records. The merged record keeps the provenance of
every contributor in ``merged_with`` and counts them in ``source_count``, and the winner
is the record whose source declares the stronger evidence - never an average of the two.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Dict, List, Tuple

from .model import UNKNOWN, CulturalAsset

#: Order of :class:`EvidenceLevel` values, strongest last.
_EVIDENCE_RANK = {"cartographic": 0, "declaratory": 1, "verified_act": 2}

#: Words dropped before comparing names: they carry no identifying power.
_NOISE = {"il", "lo", "la", "i", "gli", "le", "di", "del", "della", "dei", "degli",
          "delle", "da", "in", "con", "su", "per", "e", "ed", "a", "al", "alla",
          "immobile", "denominato", "denominata", "complesso", "area", "zona"}


def normalise_name(text: str) -> str:
    """Return a comparison key: accents, case, punctuation and filler words removed."""
    if not text or text == UNKNOWN:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in ascii_text).split()
             if w not in _NOISE]
    return " ".join(words)


def _signature(asset: CulturalAsset) -> Tuple[str, str]:
    """The identity used for merging: act code first, then name plus municipality."""
    code = (asset.act.code or "").strip()
    if code and code != UNKNOWN:
        return ("code", f"{asset.category}|{code}")
    name = normalise_name(asset.name)
    if not name:
        return ("", "")
    municipality = normalise_name(asset.municipality)
    return ("name", f"{asset.category}|{name}|{municipality}")


def _stronger(left: CulturalAsset, right: CulturalAsset) -> CulturalAsset:
    """Pick the record to keep: stronger evidence, then the one that intersects."""
    left_rank = (_EVIDENCE_RANK.get(left.evidence_level, 0), int(left.intersects),
                 left.intersect_area_m2, int(left.act.present))
    right_rank = (_EVIDENCE_RANK.get(right.evidence_level, 0), int(right.intersects),
                  right.intersect_area_m2, int(right.act.present))
    return left if left_rank >= right_rank else right


def _identity(asset: CulturalAsset) -> str:
    """A key identifying a record byte for byte, ignoring the service's own row id.

    ``spatial.analyse_layer`` already drops ``fid`` and ``gml_id`` from the attributes, so
    two records with the same attributes, the same name and the same measured geometry are
    the same feature delivered twice.
    """
    payload = json.dumps(asset.attributes, sort_keys=True, default=str)
    return "|".join((asset.source_id, asset.name, payload,
                     f"{asset.intersect_area_m2:.1f}",
                     f"{(asset.distance_m or 0.0):.1f}"))


class CulturalHeritageDeduplicator:
    """Merges records that provably describe the same asset."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.merged_count = 0
        #: Exact duplicates a single service returned more than once.
        self.repeated_count = 0
        self.repeating_sources: List[str] = []

    def drop_repeats(self, assets: List[CulturalAsset]) -> List[CulturalAsset]:
        """Remove rows a service published twice.

        Several SITAP views do this: the same polygon comes back with two different
        ``gml_id`` values. Keeping both would double the count of assets in the dossier
        (the intersected *surface* is safe, because it is measured on the union).
        """
        seen = set()
        unique: List[CulturalAsset] = []
        for asset in assets:
            key = _identity(asset)
            if key in seen:
                self.repeated_count += 1
                if asset.source_id not in self.repeating_sources:
                    self.repeating_sources.append(asset.source_id)
                continue
            seen.add(key)
            unique.append(asset)
        return unique

    def run(self, assets: List[CulturalAsset]) -> List[CulturalAsset]:
        """Return the deduplicated list, preserving the provenance of every contributor."""
        self.merged_count = 0
        self.repeated_count = 0
        self.repeating_sources = []
        if not self.enabled or len(assets) < 2:
            return list(assets)
        assets = self.drop_repeats(assets)

        buckets: Dict[str, CulturalAsset] = {}
        singles: List[CulturalAsset] = []
        order: List[str] = []
        for asset in assets:
            kind, key = _signature(asset)
            if not kind:
                singles.append(asset)
                continue
            existing = buckets.get(key)
            if existing is None:
                buckets[key] = asset
                order.append(key)
                continue
            if existing.source_id == asset.source_id:
                # The same source listing the same name twice is two real records
                # (two decrees on the same villa, for instance): never collapse those.
                singles.append(asset)
                continue
            buckets[key] = self._merge(existing, asset)
            self.merged_count += 1

        return [buckets[key] for key in order] + singles

    @staticmethod
    def _merge(left: CulturalAsset, right: CulturalAsset) -> CulturalAsset:
        """Keep the stronger record and record what was folded into it."""
        winner = _stronger(left, right)
        loser = right if winner is left else left
        winner.merged_with = sorted(set(winner.merged_with + loser.merged_with +
                                        [loser.global_id]))
        # ``global_id`` is "<source id>#<feature id>", so the contributing services are
        # recoverable from the merged ids without carrying a second list around.
        contributors = {winner.source_id} | {
            merged.split("#", 1)[0] for merged in winner.merged_with if merged}
        winner.source_count = len(contributors)
        # The weaker record may still be the one that carries the act reference.
        if not winner.act.present and loser.act.present:
            winner.act = loser.act
        for field_name in ("municipality", "province", "region", "asset_type"):
            if getattr(winner, field_name) in ("", UNKNOWN):
                value = getattr(loser, field_name)
                if value not in ("", UNKNOWN):
                    setattr(winner, field_name, value)
        winner.findings = sorted(set(winner.findings) | set(loser.findings))
        return winner
