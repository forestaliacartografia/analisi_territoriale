"""Cache manager, settings and taxonomy."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from territorial_suite.core import settings, taxonomy
from territorial_suite.core.cache import CacheManager, make_key, quantise_bbox
from territorial_suite.core.paths import ensure_within, safe_filename


class TestCacheKeys(unittest.TestCase):
    def test_key_is_stable_and_order_independent(self):
        first = make_key("src", bbox="1,2,3,4", crs="EPSG:4326")
        second = make_key("src", crs="EPSG:4326", bbox="1,2,3,4")
        self.assertEqual(first, second)

    def test_key_changes_with_parameters(self):
        self.assertNotEqual(make_key("src", bbox="1,2,3,4"), make_key("src", bbox="1,2,3,5"))
        self.assertNotEqual(make_key("a", bbox="1,2,3,4"), make_key("b", bbox="1,2,3,4"))

    def test_bbox_quantisation_groups_nearby_requests(self):
        first = quantise_bbox(680012.0, 4848003.0, 680511.0, 4848502.0, 100.0)
        second = quantise_bbox(680040.0, 4848050.0, 680520.0, 4848520.0, 100.0)
        self.assertEqual(first, second)

    def test_bbox_quantisation_disabled_with_zero_grid(self):
        self.assertIn("680012.000000", quantise_bbox(680012.0, 1.0, 2.0, 3.0, 0.0))


class TestCacheManager(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = CacheManager(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def payload(self, name: str = "payload.gpkg", size: int = 128) -> Path:
        path = Path(self._tmp.name) / name
        path.write_bytes(b"x" * size)
        return path

    def test_put_then_get(self):
        key = make_key("src", bbox="1")
        self.cache.put(key, self.payload(), source_id="src", area_id="a1")
        entry = self.cache.get(key)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source_id, "src")
        self.assertEqual(entry.area_id, "a1")
        self.assertGreater(entry.size_bytes, 0)

    def test_get_returns_none_for_unknown_key(self):
        self.assertIsNone(self.cache.get("missing"))

    def test_expired_entry_is_not_returned(self):
        key = make_key("src", bbox="2")
        self.cache.put(key, self.payload("old.gpkg"), ttl_seconds=-1)
        self.assertIsNone(self.cache.get(key))

    def test_entry_is_dropped_when_payload_disappears(self):
        key = make_key("src", bbox="3")
        path = self.payload("volatile.gpkg")
        self.cache.put(key, path)
        path.unlink()
        self.assertIsNone(self.cache.get(key))

    def test_invalidate_by_source(self):
        self.cache.put(make_key("a", bbox="1"), self.payload("a.gpkg"), source_id="a")
        self.cache.put(make_key("b", bbox="1"), self.payload("b.gpkg"), source_id="b")
        removed = self.cache.invalidate(source_id="a")
        self.assertEqual(removed, 1)
        self.assertEqual(len(list(self.cache.entries())), 1)

    def test_invalidate_by_area(self):
        self.cache.put(make_key("a", bbox="1"), self.payload("a.gpkg"), area_id="area-1")
        self.cache.put(make_key("b", bbox="1"), self.payload("b.gpkg"), area_id="area-2")
        self.assertEqual(self.cache.invalidate(area_id="area-1"), 1)

    def test_purge_expired(self):
        self.cache.put(make_key("a", bbox="1"), self.payload("a.gpkg"), ttl_seconds=-1)
        self.cache.put(make_key("b", bbox="1"), self.payload("b.gpkg"), ttl_seconds=3600)
        self.assertEqual(self.cache.purge_expired(), 1)
        self.assertEqual(len(list(self.cache.entries())), 1)

    def test_quota_eviction_keeps_recent_entries(self):
        settings.set_value("cache.quota_mb", 0.0001)   # ~100 bytes
        try:
            self.cache.put(make_key("a", bbox="1"), self.payload("a.gpkg", 200))
            time.sleep(0.01)
            self.cache.put(make_key("b", bbox="1"), self.payload("b.gpkg", 50))
            self.assertLessEqual(self.cache.size_bytes(), 200)
        finally:
            settings.reset("cache.quota_mb")

    def test_clear_empties_store(self):
        self.cache.put(make_key("a", bbox="1"), self.payload("a.gpkg"))
        self.cache.clear()
        self.assertEqual(self.cache.size_bytes(), 0)
        self.assertEqual(list(self.cache.entries()), [])

    def test_path_for_is_sanitised(self):
        path = self.cache.path_for("vector", "../../evil", "abc", ".gpkg")
        self.assertTrue(str(path).startswith(str(self.cache.root)))
        self.assertNotIn("..", str(path.relative_to(self.cache.root)))
        self.assertEqual(path.name, "abc.gpkg")


class TestPathSafety(unittest.TestCase):
    def test_safe_filename_blocks_traversal(self):
        self.assertNotIn("/", safe_filename("../../etc/passwd"))
        self.assertNotIn("\\", safe_filename("..\\windows\\system32"))
        self.assertNotIn("..", safe_filename("..".join(["a", "b"])))
        self.assertEqual(safe_filename(""), "untitled")

    def test_ensure_within_accepts_child_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertTrue(str(ensure_within(root, Path("sub/file.txt"))).startswith(str(root.resolve())))
            with self.assertRaises(ValueError):
                ensure_within(root, Path("..") / "outside.txt")


class TestSettings(unittest.TestCase):
    def test_defaults_are_loaded(self):
        self.assertEqual(settings.default("network.retries"), 3)
        self.assertIsInstance(settings.get("cartography.scales"), list)

    def test_missing_key_returns_fallback(self):
        self.assertEqual(settings.get("does.not.exist", "fallback"), "fallback")

    def test_override_round_trip_and_reset(self):
        settings.set_value("network.timeout_s", 99)
        self.assertEqual(settings.get("network.timeout_s"), 99)
        settings.reset("network.timeout_s")
        self.assertEqual(settings.get("network.timeout_s"), settings.default("network.timeout_s"))

    def test_override_is_coerced_to_default_type(self):
        settings.set_value("network.retries", "5")
        try:
            value = settings.get("network.retries")
            self.assertIsInstance(value, int)
            self.assertEqual(value, 5)
        finally:
            settings.reset("network.retries")

    def test_section_and_snapshot(self):
        network = settings.section("network")
        self.assertIn("timeout_s", network)
        snapshot = settings.snapshot()
        self.assertIn("cartography", snapshot)
        self.assertNotIn("_comment", snapshot)


class TestTaxonomy(unittest.TestCase):
    def test_knowledge_categories_cover_the_twelve_macro_areas(self):
        tax = taxonomy.Taxonomy.load()
        knowledge = tax.knowledge()
        self.assertGreaterEqual(len(knowledge), 12)
        ids = {category.id for category in knowledge}
        for expected in ("legal_administrative", "nature_conservation", "landscape_cultural",
                         "hydro_geomorphological", "forest_vegetation", "water",
                         "infrastructure", "risk", "landuse_planning", "anthropic_pressure",
                         "cadastral", "environmental_assessment"):
            self.assertIn(expected, ids)

    def test_categories_are_ordered(self):
        tax = taxonomy.Taxonomy.load()
        orders = [category.order for category in tax.all()]
        self.assertEqual(orders, sorted(orders))

    def test_labels_have_italian_fallback(self):
        tax = taxonomy.Taxonomy.load()
        self.assertTrue(tax.label("water", "it"))
        self.assertTrue(tax.label("water", "en"))
        self.assertEqual(tax.label("unknown_id", "it"), "unknown_id")

    def test_support_categories_are_separate(self):
        tax = taxonomy.Taxonomy.load()
        support_ids = {category.id for category in tax.support()}
        self.assertIn("terrain", support_ids)
        self.assertNotIn("terrain", {c.id for c in tax.knowledge()})


if __name__ == "__main__":
    unittest.main()
