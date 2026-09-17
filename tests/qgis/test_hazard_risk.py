"""Hazard is not risk, and the code has to make that impossible to blur.

Every test here defends one sentence a dossier must never print:

* a risk figure that no source computed, filled in from the hazard next to it;
* a "class" where the source published only geometry and the layer name;
* a percentage over the whole polygon instead of the part inside the area;
* an aggregated level that has replaced the authority's own code.
"""

from __future__ import annotations

import unittest

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

from territorial_suite.core.gaps import DataGap
from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSource, DataSourceRegistry
from territorial_suite.engines import hazard_risk
from territorial_suite.engines.hazard_risk import (
    HazardRiskEngine,
    Kind,
    ThemeOutcome,
    normalised_label,
    rule_for,
)

AREA = ProjectArea.from_rectangle(QgsRectangle(11.240, 43.760, 11.260, 43.775),
                                  "EPSG:4326", name="Firenze")


def classed_layer(rows, field="category"):
    """A layer shaped like a hazard source: one polygon per (class, rectangle)."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "hazard", "memory")
    layer.dataProvider().addAttributes([QgsField(field, QMetaType.Type.QString)])
    layer.updateFields()
    features = []
    for value, rect in rows:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromRect(rect))
        feature[field] = value
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def pgra_source():
    """A descriptor shaped like the PGRA one, without touching the network."""
    return DataSource.from_dict({
        "id": "pcn.alluvioni.ite.hph", "name": "Aree allagabili - elevata probabilita",
        "type": "WFS", "url": "http://example.invalid/ogc", "layer": "ITE2018_Estensione_HPH",
        "category": "flood_hazard", "crs": ["EPSG:4326"],
        "fields": {"class": "category"},
    })


def pai_source(layer="RN.PAI.PERICOLOSITA.FRANA_01", category="landslide_hazard"):
    """A descriptor shaped like PAI, which publishes its classes as attributes."""
    return DataSource.from_dict({
        "id": "pcn.pai.pericolosita.frana_01", "name": "PAI - pericolosita frana",
        "type": "WFS", "url": "http://example.invalid/ogc", "layer": layer,
        "category": category, "crs": ["EPSG:4326"],
        "fields": {"class": "pericolosita"},
    })


def catalogue_source():
    """The landslide catalogue: geometry and nothing else, verified.

    Used where the point is a source that publishes no attributes at all. PAI used to
    be that source, until the richer /ms_ogc/wfs/ endpoint was probed and turned out to
    publish its classes properly.
    """
    return DataSource.from_dict({
        "id": "pcn.frane.poligonali", "name": "Catalogo frane - aree in frana",
        "type": "WFS", "url": "http://example.invalid/ogc",
        "layer": "RN.CATALOGO_FRANE.POLIGONALI",
        "category": "landslide_inventory", "crs": ["EPSG:4326"],
    })


class RuleCase(unittest.TestCase):
    def setUp(self):
        hazard_risk.reload_rules()
        self.addCleanup(hazard_risk.reload_rules)


class TestTheFourKindsStayApart(RuleCase):
    def test_every_kind_exists_and_reads_differently(self):
        labels = {k: k.label_it for k in Kind}
        self.assertEqual(len(set(labels.values())), 4)
        self.assertIn("pericolosita", Kind.HAZARD.label_it)
        self.assertIn("rischio", Kind.RISK.label_it)
        self.assertIn("inventario", Kind.INVENTORY.label_it)

    def test_an_inventory_is_not_probabilistic(self):
        self.assertFalse(Kind.INVENTORY.is_probabilistic)
        self.assertFalse(Kind.SUSCEPTIBILITY.is_probabilistic)
        self.assertTrue(Kind.HAZARD.is_probabilistic)

    def test_the_configured_themes_declare_their_kind(self):
        themes = HazardRiskEngine(registry=DataSourceRegistry()).themes()
        self.assertEqual(themes["flood_hazard"]["kind"], "hazard")
        self.assertEqual(themes["flood_risk"]["kind"], "risk")
        self.assertEqual(themes["landslide_hazard"]["kind"], "hazard")
        self.assertEqual(themes["landslide_risk"]["kind"], "risk")
        self.assertEqual(themes["landslide_inventory"]["kind"], "inventory")

    def test_hazard_and_risk_never_share_a_category(self):
        themes = HazardRiskEngine(registry=DataSourceRegistry()).themes()
        hazard = set(themes["flood_hazard"]["categories"])
        risk = set(themes["flood_risk"]["categories"])
        self.assertEqual(hazard & risk, set())


class TestRiskIsNeverDerivedFromHazard(RuleCase):
    """The rule the whole engine exists for."""

    def setUp(self):
        super().setUp()
        self.registry = DataSourceRegistry()
        self.registry.load()
        self.engine = HazardRiskEngine(registry=self.registry)

    def test_a_theme_with_no_source_is_not_determinable(self):
        self.engine.sources_for = lambda theme: []
        outcome = self.engine.run_theme(AREA, "flood_risk")
        self.assertFalse(outcome.determinable)
        self.assertIn(DataGap.NO_DATA.value, outcome.gaps)
        self.assertEqual(outcome.classes, [])

    def test_that_case_says_it_is_not_deduced_from_other_themes(self):
        self.engine.sources_for = lambda theme: []
        outcome = self.engine.run_theme(AREA, "flood_risk")
        self.assertIn("non viene dedotto", " ".join(outcome.warnings))
        self.assertIn("non determinabile", outcome.statement())

    def test_a_hazard_result_does_not_populate_the_risk_theme(self):
        """Hazard answers, risk does not: the two outcomes must stay independent."""
        def only_hazard(theme):
            return [pgra_source()] if theme == "flood_hazard" else []

        self.engine.sources_for = only_hazard
        layer = classed_layer(
            [("HighProbabilityHazard", QgsRectangle(11.240, 43.760, 11.260, 43.775))])

        class Fetched:
            truncated = False
            lost_features = 0

            def layer(self, _name):
                return built

        built = layer
        self.engine._fetch = lambda *a, **k: Fetched()
        outcome = self.engine.run(AREA, themes=["flood_hazard", "flood_risk"])
        hazard = outcome.theme("flood_hazard")
        risk = outcome.theme("flood_risk")
        self.assertTrue(hazard.present)
        self.assertFalse(risk.determinable)
        self.assertEqual(risk.classes, [])

    def test_a_truncated_download_is_declared_not_averaged_away(self):
        """5000 features arriving because the cap says 5000 is not "all of them"."""
        class Capped:
            truncated = True
            lost_features = 0

            def layer(self, _name):
                return classed_layer([("HighProbabilityHazard",
                                       QgsRectangle(11.240, 43.760, 11.260, 43.775))])

        self.engine.sources_for = lambda theme: [pgra_source()]
        self.engine._fetch = lambda *a, **k: Capped()
        outcome = self.engine.run_theme(AREA, "flood_hazard")
        self.assertIn(DataGap.PARTIAL_COVERAGE.value, outcome.gaps)
        self.assertIn("troncata", " ".join(outcome.warnings))
        self.assertTrue(outcome.present, "il dato scaricato resta utilizzabile")

    def test_features_the_service_announced_but_did_not_deliver_are_declared(self):
        class Lossy:
            truncated = False
            lost_features = 7

            def layer(self, _name):
                return classed_layer([("HighProbabilityHazard",
                                       QgsRectangle(11.240, 43.760, 11.260, 43.775))])

        self.engine.sources_for = lambda theme: [pgra_source()]
        self.engine._fetch = lambda *a, **k: Lossy()
        outcome = self.engine.run_theme(AREA, "flood_hazard")
        self.assertIn(DataGap.PARTIAL_COVERAGE.value, outcome.gaps)
        self.assertIn("7 feature", " ".join(outcome.warnings))

    def test_a_view_only_source_cannot_answer_a_theme(self):
        wms = DataSource.from_dict({
            "id": "x.wms", "name": "solo consultabile", "type": "WMS",
            "url": "http://example.invalid", "layer": "a", "category": "flood_risk"})
        self.engine.sources_for = lambda theme: [wms]
        outcome = self.engine.run_theme(AREA, "flood_risk")
        self.assertFalse(outcome.determinable)
        self.assertIn(DataGap.VIEW_ONLY.value, outcome.gaps)
        self.assertIn("non sono calcolabili", " ".join(outcome.warnings))


class TestClassification(RuleCase):
    def setUp(self):
        super().setUp()
        self.engine = HazardRiskEngine(registry=DataSourceRegistry())

    def test_the_official_scenario_is_read_from_the_attribute(self):
        west = QgsRectangle(11.240, 43.760, 11.250, 43.775)
        east = QgsRectangle(11.250, 43.760, 11.260, 43.775)
        layer = classed_layer([("HighProbabilityHazard", west),
                               ("LowProbabilityHazard", east)])
        classes = self.engine._classify(layer, AREA, pgra_source(),
                                        rule_for(pgra_source()), 1_000_000.0)
        codes = {c.official_code for c in classes}
        self.assertEqual(codes, {"HighProbabilityHazard", "LowProbabilityHazard"})
        for row in classes:
            self.assertEqual(row.derived_from, "attribute")

    def test_the_normalised_level_is_a_second_value_not_a_replacement(self):
        layer = classed_layer([("HighProbabilityHazard",
                                QgsRectangle(11.240, 43.760, 11.260, 43.775))])
        row = self.engine._classify(layer, AREA, pgra_source(),
                                    rule_for(pgra_source()), 1_000_000.0)[0]
        self.assertEqual(row.official_code, "HighProbabilityHazard")
        self.assertEqual(row.normalised, "high")
        self.assertIn("Elevata", row.official_label)

    def test_classes_are_not_collapsed_into_one(self):
        rects = [("HighProbabilityHazard", QgsRectangle(11.240, 43.760, 11.246, 43.775)),
                 ("MediumProbabilityHazard", QgsRectangle(11.246, 43.760, 11.253, 43.775)),
                 ("LowProbabilityHazard", QgsRectangle(11.253, 43.760, 11.260, 43.775))]
        classes = self.engine._classify(classed_layer(rects), AREA, pgra_source(),
                                        rule_for(pgra_source()), 1_000_000.0)
        self.assertEqual(len(classes), 3)

    def test_only_the_part_inside_the_area_is_measured(self):
        huge = QgsRectangle(11.0, 43.6, 11.5, 44.0)
        total = 1_000_000.0
        classes = self.engine._classify(
            classed_layer([("HighProbabilityHazard", huge)]), AREA,
            pgra_source(), rule_for(pgra_source()), total)
        area_geom = AREA.geometry_in(AREA.work_crs)
        from territorial_suite.core import measure

        expected = measure.area_m2(area_geom, AREA.work_crs)
        self.assertAlmostEqual(classes[0].area_m2, expected, delta=expected * 0.02)


class TestASourceWithoutAttributesIsHonestAboutIt(RuleCase):
    """The landslide catalogue publishes geometry only. The result must say so.

    PAI used to be the example here. Probing the /ms_ogc/wfs/ endpoint named in the
    brief showed it publishes pericolosita, rischio, the basin authority, the plan and
    the decree, so the earlier conclusion held only for the endpoint that had been
    tried - and the tests moved to a source where the property is real.
    """

    def setUp(self):
        super().setUp()
        self.engine = HazardRiskEngine(registry=DataSourceRegistry())

    def test_the_class_is_declared_as_coming_from_the_layer(self):
        source = catalogue_source()
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "pai", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(
            QgsRectangle(11.240, 43.760, 11.260, 43.775)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        classes = self.engine._classify(layer, AREA, source, rule_for(source), 1e6)
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0].derived_from, "layer")
        self.assertEqual(classes[0].official_code, source.layer)
        # "observed" e non "unclassified": un fenomeno censito e' un fatto osservato,
        # e la nomenclatura lo distingue da una classe che la fonte non pubblica.
        self.assertEqual(classes[0].normalised, "observed")

    def test_unclassified_is_not_a_low_level(self):
        self.assertIn("non pubblicata", normalised_label("unclassified"))
        order = hazard_risk.rules()["normalised_levels"]["order"]
        self.assertEqual(order[0], "unclassified")

    def test_the_limitation_of_the_source_is_carried_into_the_result(self):
        registry = DataSourceRegistry()
        registry.load()
        engine = HazardRiskEngine(registry=registry)
        engine.sources_for = lambda theme: [catalogue_source()]
        # La fonte non risponde: cio' che conta qui e' che la limitazione dichiarata dal
        # descrittore arrivi comunque nel risultato.
        engine._fetch = lambda *a, **k: None
        outcome = engine.run_theme(AREA, "landslide_inventory")
        self.assertTrue(outcome.limitations)
        self.assertIn("sola geometria", " ".join(outcome.limitations))


class TestTheSynthesis(RuleCase):
    def test_the_matrix_has_one_row_per_theme(self):
        outcome = hazard_risk.HazardRiskOutcome(area_m2=1e6)
        outcome.themes = [ThemeOutcome(theme="flood_hazard", kind=Kind.HAZARD),
                          ThemeOutcome(theme="flood_risk", kind=Kind.RISK)]
        rows = outcome.matrix()
        self.assertEqual([r["theme"] for r in rows], ["flood_hazard", "flood_risk"])
        self.assertEqual([r["kind"] for r in rows], ["hazard", "risk"])

    def test_a_non_determinable_theme_reports_presence_as_unknown_not_false(self):
        """«Non lo so» e «non c'e'» non sono la stessa riga di tabella."""
        theme = ThemeOutcome(theme="flood_risk", kind=Kind.RISK,
                             gaps=[DataGap.NO_DATA.value])
        outcome = hazard_risk.HazardRiskOutcome(themes=[theme])
        self.assertIsNone(outcome.matrix()[0]["present"])

    def test_the_outcome_serialises_for_the_dossier(self):
        import json

        theme = ThemeOutcome(theme="flood_hazard", kind=Kind.HAZARD, area_m2=1e6)
        outcome = hazard_risk.HazardRiskOutcome(area_m2=1e6, themes=[theme])
        payload = outcome.as_dict()
        json.dumps(payload)
        for key in ("area_m2", "themes", "matrix", "warnings"):
            self.assertIn(key, payload)


class TestArchitecture(RuleCase):
    def test_no_endpoint_lives_in_the_engine(self):
        for name in ("engine.py", "model.py"):
            source = (plugin_dir() / "engines" / "hazard_risk" / name).read_text(
                encoding="utf-8")
            self.assertNotIn("http://", source)
            self.assertNotIn("https://", source)

    def test_the_engine_holds_no_http_client_of_its_own(self):
        source = (plugin_dir() / "engines" / "hazard_risk" / "engine.py").read_text(
            encoding="utf-8")
        for forbidden in ("import requests", "urllib.request", "QNetworkAccessManager"):
            self.assertNotIn(forbidden, source)

    def test_the_classes_come_from_configuration(self):
        source = (plugin_dir() / "engines" / "hazard_risk" / "engine.py").read_text(
            encoding="utf-8")
        for literal in ("HighProbabilityHazard", "RN.PAI.", "P3", "P4"):
            self.assertNotIn(literal, source,
                             f"«{literal}» deve vivere in configurazione")


if __name__ == "__main__":
    unittest.main()
