"""Dossier rendering and package manifest."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qgis.core import QgsRectangle

from territorial_suite.core.models import (
    AdminUnit,
    AdminUnits,
    Alert,
    AlertLevel,
    AnalysisReport,
    CadastralRow,
    EvidenceLevel,
    FeatureHit,
    Provenance,
    SlopeClass,
    SourceResult,
    SourceStatus,
    TerrainStats,
)
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.report import ReportEngine, ReportOptions


def sample_report() -> AnalysisReport:
    """A report with data in every section."""
    area = ProjectArea.from_rectangle(QgsRectangle(680000, 4848000, 681000, 4849000),
                                      "EPSG:32632", name="Area di test")
    provenance = Provenance(source_id="eu.eea.natura2000.sci",
                            source_name="Natura 2000 - SIC/ZSC",
                            authority="European Environment Agency",
                            url="https://example.org", license="EEA re-use policy",
                            attribution="EEA", update_frequency="annual",
                            evidence_level=EvidenceLevel.DECLARATORY, official=True)
    present = SourceResult(
        source_id="eu.eea.natura2000.sci", source_name="Natura 2000 - SIC/ZSC",
        category="nature_conservation", status=SourceStatus.ONLINE, present=True,
        feature_count=1, intersect_area_m2=250000.0, intersect_pct=25.0,
        min_distance_m=0.0, provenance=provenance,
        hits=[FeatureHit(fid="1", label="Sito di prova", intersect_area_m2=250000.0,
                         intersect_pct=25.0)])
    failed = SourceResult(
        source_id="it.regione.vincoli", source_name="Vincoli regionali",
        category="landscape_cultural", status=SourceStatus.OFFLINE,
        error="servizio non raggiungibile",
        provenance=Provenance(source_id="it.regione.vincoli", authority="Regione"))
    return AnalysisReport(
        area=area.as_dict(),
        results=[present, failed],
        alerts=[Alert(level=AlertLevel.CHECK_REQUIRED, code="n2k",
                      title="Sito Natura 2000 interessato",
                      detail="Verificare presso l'ente competente.",
                      category="nature_conservation",
                      evidence_level=EvidenceLevel.DECLARATORY,
                      legal_reference="D.P.R. 357/1997")],
        cadastre=[CadastralRow(municipality="Firenze", istat_code="048017",
                               cadastral_code="D612", sheet="169", parcel="10",
                               national_ref="D612_016900.10", area_cadastral_m2=1200.0,
                               area_intersect_m2=600.0, intersect_pct=50.0)],
        terrain=TerrainStats(source_id="dem", cell_size_m=10.0, elevation_min=24.0,
                             elevation_max=187.0, elevation_mean=93.0,
                             elevation_median=90.0, elevation_range=163.0,
                             slope_mean_pct=22.0, slope_max_pct=71.0,
                             slope_classes=[SlopeClass(lower=0, upper=5, area_pct=12.0,
                                                       area_m2=120000.0)],
                             aspect_histogram={"N": 20.0, "S": 30.0},
                             provenance=Provenance(source_id="dem", source_name="DEM",
                                                   attribution="Open data")),
        admin=AdminUnits(municipalities=[AdminUnit(name="Firenze", istat_code="048017",
                                                   cadastral_code="D612",
                                                   province_code="FI",
                                                   region_name="Toscana",
                                                   area_share_pct=100.0)],
                         provinces=[AdminUnit(name="Firenze", level="province")],
                         regions=[AdminUnit(name="Toscana", level="region")],
                         resolved=True),
        warnings=["Una fonte non era disponibile"],
        plugin_version="0.1.0", qgis_version="3.40",
    )


class TestReportHtml(unittest.TestCase):
    def setUp(self):
        self.report = sample_report()
        self.engine = ReportEngine(self.report, ReportOptions(author="Tecnico",
                                                              organisation="Studio"))
        self.html = self.engine.to_html()

    def test_contains_the_main_sections(self):
        for heading in ("Area di progetto", "Localizzazione amministrativa", "Segnalazioni",
                        "Catasto", "Natura", "Dati altimetrici", "Fonti",
                        "Metadati dell'elaborazione"):
            self.assertIn(heading, self.html)

    def test_states_the_nature_of_the_data(self):
        self.assertIn("Natura del dato", self.html)
        self.assertIn(EvidenceLevel.DECLARATORY.label_it, self.html)

    def test_reports_missing_sources_explicitly(self):
        self.assertIn("Fonti non disponibili", self.html)
        self.assertIn("Vincoli regionali", self.html)

    def test_says_cadastral_areas_are_graphic(self):
        self.assertIn("grafiche", self.html)

    def test_includes_the_disclaimer(self):
        self.assertIn("non costituiscono accertamento", self.html)

    def test_empty_categories_are_declared(self):
        self.assertIn("Nessuna sorgente configurata o disponibile", self.html)

    def test_html_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.engine.write_html(Path(folder) / "r.html")
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 2000)

    def test_pdf_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.engine.write_pdf(Path(folder) / "r.pdf")
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)


class TestReportTables(unittest.TestCase):
    def setUp(self):
        self.engine = ReportEngine(sample_report())

    def test_cadastral_rows_have_a_header(self):
        rows = self.engine.cadastral_rows()
        self.assertEqual(rows[0][0], "Comune")
        self.assertEqual(rows[1][4], "10")

    def test_sources_rows_include_failures(self):
        rows = self.engine.sources_rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("servizio non raggiungibile" in str(cell)
                            for row in rows for cell in row))

    def test_alerts_rows(self):
        rows = self.engine.alerts_rows()
        self.assertEqual(rows[1][0], AlertLevel.CHECK_REQUIRED.label_it)

    def test_csv_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.engine.write_csv(Path(folder) / "c.csv", self.engine.cadastral_rows())
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("Comune;", content)
            self.assertIn("Firenze", content)

    def test_xlsx_export_or_csv_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.engine.write_xlsx(Path(folder) / "t.xlsx")
            if path is None:
                self.assertTrue((Path(folder) / "t_catasto.csv").exists())
            else:
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 1000)


class TestPackageManifest(unittest.TestCase):
    def test_package_layout_and_manifest(self):
        from territorial_suite.engines.package import FOLDERS, PackageBuilder

        report = sample_report()
        area = ProjectArea.from_dict(report.area)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            result = PackageBuilder(area, report).build(Path(folder), include_project=False)
            for name in FOLDERS:
                self.assertTrue((result.root / name).is_dir(), name)
            manifest = json.loads((result.root / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertIn("sources", manifest)
            self.assertIn("disclaimer", manifest)
            self.assertTrue(any(entry["id"] == "eu.eea.natura2000.sci"
                                for entry in manifest["sources"]))
            self.assertTrue((result.root / "reports" / "analysis.json").exists())
            self.assertTrue((result.root / "imagery" / "SERVIZI.txt").exists())


if __name__ == "__main__":
    unittest.main()
