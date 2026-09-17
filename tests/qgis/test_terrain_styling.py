"""P0.4 — DEM, slope and aspect must read as relief, not as flat colour.

Two halves, and both are needed:

* **on the canvas** the hillshade multiplies over the coloured raster;
* **on paper** that blend mode is gone — QGIS drops layer blend modes in several export
  paths — so the terrain engine bakes a flattened composite and the layout prints that.

The tests build a real synthetic DEM (a tilted, dented surface) so slope, aspect and
hillshade all have something to say, then check the numbers rather than the appearance.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt.QtGui import QPainter

from territorial_suite.core import settings
from territorial_suite.core.models import LayerRef
from territorial_suite.engines.cartography import styling
from territorial_suite.engines.project_layers import STYLE_PROPERTY, LayerApplier
from territorial_suite.engines.terrain import SHADED_STYLE_PREFIX, bake_shaded_relief

SIZE = 40
PIXEL = 10.0
ORIGIN_X, ORIGIN_Y = 680000.0, 4849000.0


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def write_dem(path: Path) -> Path:
    """A tilted surface with a bump: every aspect sector occurs somewhere."""
    import numpy

    gdal = _gdal()
    rows, cols = numpy.mgrid[0:SIZE, 0:SIZE]
    centre = SIZE / 2.0
    tilt = 400.0 + rows * 2.0 + cols * 1.0
    bump = 60.0 * numpy.exp(-(((rows - centre) ** 2 + (cols - centre) ** 2) / 40.0))
    elevation = (tilt + bump).astype("float32")

    dataset = gdal.GetDriverByName("GTiff").Create(str(path), SIZE, SIZE, 1,
                                                   gdal.GDT_Float32)
    try:
        dataset.SetGeoTransform((ORIGIN_X, PIXEL, 0.0, ORIGIN_Y, 0.0, -PIXEL))
        from osgeo import osr

        reference = osr.SpatialReference()
        reference.ImportFromEPSG(32632)
        dataset.SetProjection(reference.ExportToWkt())
        band = dataset.GetRasterBand(1)
        band.WriteArray(elevation)
        band.SetNoDataValue(-9999.0)
        band = None
        dataset.FlushCache()
    finally:
        dataset = None
    return path


def derive(dem: Path, folder: Path, algorithm: str) -> Path:
    gdal = _gdal()
    out = folder / f"{algorithm}.tif"
    result = gdal.DEMProcessing(str(out), str(dem), algorithm, computeEdges=True)
    result.FlushCache()
    result = None
    return out


class TerrainFixture(unittest.TestCase):
    """A real DEM plus its derivatives, built once per test case."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.dem = write_dem(self.folder / "dem.tif")
        self.hillshade = derive(self.dem, self.folder, "hillshade")
        self.slope = derive(self.dem, self.folder, "slope")
        self.aspect = derive(self.dem, self.folder, "aspect")


class TestColourRamps(unittest.TestCase):
    def test_aspect_has_the_eight_cardinal_sectors_and_wraps(self):
        """The old two-stop grey ramp could not distinguish north from south."""
        spec = styling.ramp_spec("aspect")
        self.assertEqual(spec["type"], "discrete")
        self.assertEqual(spec["domain"], [0.0, 360.0])
        labels = [item["label"] for item in spec["items"]]
        for sector in ("N", "NE", "E", "SE", "S", "SO", "O", "NO"):
            self.assertIn(sector, labels)
        # North wraps: the first and the last class share a colour.
        self.assertEqual(spec["items"][0]["color"], spec["items"][-1]["color"])
        self.assertEqual(spec["items"][-1]["value"], 360.0)
        colours = {item["color"] for item in spec["items"]}
        self.assertGreaterEqual(len(colours), 8, "rampa esposizione non distinguibile")

    def test_slope_classes_are_absolute_so_sheets_are_comparable(self):
        spec = styling.ramp_spec("slope")
        self.assertEqual(spec["type"], "discrete")
        bounds = [item["value"] for item in spec["items"]]
        self.assertEqual(bounds, sorted(bounds))
        self.assertIn(30.0, bounds)

    def test_a_ramp_lookup_is_ordered_rgb(self):
        table = styling.ramp_lookup("aspect")
        self.assertEqual(len(table), 9)
        for _bound, colour in table:
            self.assertEqual(len(colour), 3)
            self.assertTrue(all(0 <= channel <= 255 for channel in colour))


class TestShadedComposite(TerrainFixture):
    def test_the_composite_keeps_the_dem_grid(self):
        out = bake_shaded_relief(self.slope, self.hillshade,
                                 self.folder / "slope_shaded.tif",
                                 ramp="slope", opacity=0.55)
        layer = QgsRasterLayer(str(out), "slope shaded", "gdal")
        self.assertTrue(layer.isValid())
        source = QgsRasterLayer(str(self.slope), "slope", "gdal")
        self.assertEqual(layer.width(), source.width())
        self.assertEqual(layer.height(), source.height())
        self.assertEqual(layer.crs().authid(), source.crs().authid())
        self.assertAlmostEqual(layer.extent().xMinimum(),
                               source.extent().xMinimum(), delta=0.01)
        self.assertAlmostEqual(layer.extent().yMaximum(),
                               source.extent().yMaximum(), delta=0.01)
        self.assertEqual(layer.bandCount(), 4, "RGB + alfa attesi")

    def test_the_composite_is_really_shaded_and_really_coloured(self):
        import numpy

        flat = bake_shaded_relief(self.aspect, self.hillshade,
                                  self.folder / "aspect_flat.tif",
                                  ramp="aspect", opacity=0.0)
        shaded = bake_shaded_relief(self.aspect, self.hillshade,
                                    self.folder / "aspect_shaded.tif",
                                    ramp="aspect", opacity=1.0)
        gdal = _gdal()
        flat_data = gdal.Open(str(flat)).ReadAsArray().astype("float64")
        shaded_data = gdal.Open(str(shaded)).ReadAsArray().astype("float64")
        # Shading darkens: multiplying by grey/255 can never brighten a pixel.
        self.assertTrue((shaded_data[:3] <= flat_data[:3] + 1).all())
        self.assertLess(shaded_data[:3].mean(), flat_data[:3].mean())
        # Colour survives: the three channels are not equal, so it is not grey.
        red, green, blue = flat_data[0], flat_data[1], flat_data[2]
        self.assertGreater(float(numpy.abs(red - green).mean()), 1.0,
                           "il composito e' grigio: la rampa non e' stata applicata")
        self.assertGreater(float(numpy.abs(green - blue).mean()), 1.0)

    def test_mismatched_grids_are_refused_instead_of_producing_nonsense(self):
        from territorial_suite.core.errors import EngineError

        other = write_dem(self.folder / "other.tif")
        gdal = _gdal()
        small = self.folder / "small.tif"
        warped = gdal.Warp(str(small), str(other), width=SIZE // 2, height=SIZE // 2)
        warped.FlushCache()
        warped = None
        with self.assertRaises(EngineError):
            bake_shaded_relief(self.slope, small, self.folder / "bad.tif", ramp="slope")

    def test_an_unknown_ramp_is_an_error(self):
        from territorial_suite.core.errors import EngineError

        with self.assertRaises(EngineError):
            bake_shaded_relief(self.slope, self.hillshade, self.folder / "x.tif",
                               ramp="rampa_che_non_esiste")


class TestCanvasBlending(TerrainFixture):
    def setUp(self):
        super().setUp()
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def test_multiply_is_applied_to_the_hillshade(self):
        colour = QgsRasterLayer(str(self.slope), "Pendenza", "gdal")
        shade = QgsRasterLayer(str(self.hillshade), "Ombreggiatura", "gdal")
        self.assertTrue(styling.apply_shaded_relief(colour, shade))
        self.assertEqual(shade.blendMode(),
                         QPainter.CompositionMode.CompositionMode_Multiply)
        self.assertEqual(colour.blendMode(),
                         QPainter.CompositionMode.CompositionMode_SourceOver)
        self.assertAlmostEqual(shade.opacity(),
                               float(settings.get("terrain.hillshade_opacity", 0.55)),
                               places=3)

    def test_an_invalid_layer_is_refused_not_ignored(self):
        broken = QgsRasterLayer("/non/esiste.tif", "rotta", "gdal")
        good = QgsRasterLayer(str(self.slope), "Pendenza", "gdal")
        self.assertFalse(styling.apply_shaded_relief(good, broken))
        self.assertFalse(styling.apply_shaded_relief(None, good))

    def test_the_applier_blends_the_whole_terrain_group(self):
        applier = LayerApplier(self.project)
        layers = []
        for name, path, style in (("DEM", self.dem, "dem"),
                                  ("Pendenza", self.slope, "slope"),
                                  ("Esposizione", self.aspect, "aspect"),
                                  ("Ombreggiatura", self.hillshade, "hillshade")):
            layer = applier.add_layer_ref(LayerRef(
                name=name, uri=str(path), provider="gdal", category="terrain",
                group="Terrain", style=style, is_raster=True))
            self.assertIsNotNone(layer, name)
            layers.append(layer)
        self.assertEqual(applier.shade_terrain(layers), 3)
        hillshade = layers[-1]
        self.assertEqual(hillshade.blendMode(),
                         QPainter.CompositionMode.CompositionMode_Multiply)

    def test_a_baked_composite_keeps_its_own_colours(self):
        """Applying a single-band renderer to an RGB composite would turn it grey."""
        out = bake_shaded_relief(self.dem, self.hillshade,
                                 self.folder / "dem_shaded.tif", ramp="terrain")
        layer = QgsRasterLayer(str(out), "Rilievo ombreggiato", "gdal")
        before = type(layer.renderer()).__name__
        applied = styling.apply_raster_style(layer, f"{SHADED_STYLE_PREFIX}dem")
        self.assertTrue(applied.startswith("shaded"))
        self.assertEqual(type(layer.renderer()).__name__, before)
        self.assertAlmostEqual(layer.opacity(), 1.0, places=3)


class TestLayoutUsesTheComposite(TerrainFixture):
    def setUp(self):
        super().setUp()
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def test_the_slope_sheet_prints_the_composite_not_the_raw_slope(self):
        from territorial_suite.engines.cartography.layout import (
            LayoutBuilder, MapSpec, get_template)

        applier = LayerApplier(self.project)
        raw = applier.add_layer_ref(LayerRef(
            name="Pendenza", uri=str(self.slope), provider="gdal", category="terrain",
            group="Terrain", style="slope", is_raster=True))
        composite_path = bake_shaded_relief(self.slope, self.hillshade,
                                            self.folder / "slope_shaded.tif",
                                            ramp="slope")
        shaded = applier.add_layer_ref(LayerRef(
            name="Rilievo ombreggiato (pendenza)", uri=str(composite_path),
            provider="gdal", category="terrain", group="Terrain",
            style=f"{SHADED_STYLE_PREFIX}slope", is_raster=True))
        self.assertIsNotNone(shaded)
        self.assertEqual(shaded.customProperty(STYLE_PROPERTY, ""), "shaded_slope")

        builder = LayoutBuilder(self.project)
        chosen = builder.layers_for(get_template("slope_map"), MapSpec(template="slope_map"))
        self.assertIn(shaded, chosen, "la tavola non usa il composito ombreggiato")
        self.assertNotIn(raw, chosen, "la pendenza grezza verrebbe stampata sopra")

    def test_without_a_composite_the_raw_theme_is_still_printed(self):
        from territorial_suite.engines.cartography.layout import (
            LayoutBuilder, MapSpec, get_template)

        applier = LayerApplier(self.project)
        raw = applier.add_layer_ref(LayerRef(
            name="Pendenza", uri=str(self.slope), provider="gdal", category="terrain",
            group="Terrain", style="slope", is_raster=True))
        chosen = LayoutBuilder(self.project).layers_for(
            get_template("slope_map"), MapSpec(template="slope_map"))
        self.assertIn(raw, chosen)


if __name__ == "__main__":
    unittest.main()
