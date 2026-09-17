"""P0.1 / P0.2 — plugin identity: visible name, stable technical id, real icon.

The two halves of the identity pull in opposite directions and both must hold:

* the **visible** name is what the user reads, and it is "Analisi territoriale";
* the **technical** id is what QGIS projects, settings and saved Processing models refer
  to, and it must never move. Renaming it to match the label would silently break every
  project that already carries the plugin's layer properties.
"""

from __future__ import annotations

import configparser
import struct
import unittest
from pathlib import Path

from territorial_suite.core import constants
from territorial_suite.core.paths import plugin_dir, resources_dir

VISIBLE_NAME = "Analisi territoriale"
TECHNICAL_ID = "territorial_suite"
ICON_FILE = "analisi_territoriale.png"


def metadata() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(plugin_dir() / "metadata.txt", encoding="utf-8")
    return parser


class TestVisibleName(unittest.TestCase):
    def test_the_plugin_is_called_analisi_territoriale(self):
        self.assertEqual(constants.PLUGIN_NAME, VISIBLE_NAME)
        self.assertEqual(metadata()["general"]["name"], VISIBLE_NAME)

    def test_the_log_channel_and_layer_group_follow_the_visible_name(self):
        self.assertEqual(constants.LOG_CHANNEL, VISIBLE_NAME)
        self.assertEqual(constants.GROUP_ROOT, VISIBLE_NAME)

    def test_the_processing_group_is_the_visible_name(self):
        from territorial_suite.processing.algs.area import AreaStatisticsAlgorithm

        self.assertEqual(AreaStatisticsAlgorithm().group(), VISIBLE_NAME)

    def test_the_old_product_name_is_gone_from_user_facing_strings(self):
        """No shipped string may still read "Territorial Suite" to the user."""
        offenders = []
        for path in sorted(plugin_dir().rglob("*.py")):
            if path.name == "credentials.py":
                # Frozen in P0: its default label names an entry that may already exist
                # in a user's authentication database.
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if "Territorial Suite" not in stripped:
                    continue
                # Module docstrings and comments describe the package, not the UI.
                if stripped.startswith(("#", '"""', "'''", "*")):
                    continue
                offenders.append(f"{path.relative_to(plugin_dir())}:{number}: {stripped}")
        self.assertEqual(offenders, [], "\n".join(offenders))


class TestTechnicalIdIsFrozen(unittest.TestCase):
    """These are the values that would break existing projects if they moved."""

    def test_plugin_id_and_everything_derived_from_it(self):
        self.assertEqual(constants.PLUGIN_ID, TECHNICAL_ID)
        self.assertEqual(constants.SETTINGS_ROOT, f"plugins/{TECHNICAL_ID}")
        for key in (constants.PROP_PROJECT_AREA, constants.PROP_PROJECT_AREA_LIST,
                    constants.PROP_SOURCE_OVERRIDES, constants.PROP_ONBOARDING_DONE,
                    constants.PROP_LAYER_PROVENANCE, constants.PROP_LAYER_SOURCE_ID,
                    constants.PROP_LAYER_AREA_ID, constants.PROP_LAYER_CATEGORY):
            self.assertTrue(key.startswith(f"{TECHNICAL_ID}/"), key)

    def test_the_processing_provider_keeps_its_id(self):
        from territorial_suite.processing.provider import TerritorialSuiteProvider

        provider = TerritorialSuiteProvider()
        self.assertEqual(provider.id(), TECHNICAL_ID)
        self.assertEqual(provider.name(), VISIBLE_NAME)

    def test_the_python_package_was_not_renamed(self):
        self.assertEqual(plugin_dir().name, TECHNICAL_ID)


class TestIcon(unittest.TestCase):
    def path(self) -> Path:
        return resources_dir() / "icons" / ICON_FILE

    def test_the_supplied_icon_is_installed(self):
        self.assertTrue(self.path().is_file(), f"{ICON_FILE} mancante")

    def test_metadata_points_at_it(self):
        self.assertEqual(metadata()["general"]["icon"], f"resources/icons/{ICON_FILE}")

    def test_it_is_a_square_png_of_a_usable_size(self):
        data = self.path().read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", "non e' un PNG")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual(width, height, f"icona non quadrata: {width}x{height}")
        self.assertTrue(24 <= width <= 128, f"lato fuori intervallo: {width}")

    def test_qt_can_actually_load_it(self):
        from qgis.PyQt.QtGui import QIcon, QImage

        image = QImage(str(self.path()))
        self.assertFalse(image.isNull(), "Qt non riesce a leggere l'icona")
        self.assertFalse(QIcon(str(self.path())).isNull())

    def test_the_toolbar_action_uses_it(self):
        """The dock toggle is the action the user looks for in the toolbar."""
        source = (plugin_dir() / "plugin.py").read_text(encoding="utf-8")
        self.assertIn(f'icon="{ICON_FILE}"', source)
        self.assertNotIn('icon="territorial_suite.svg"', source)


if __name__ == "__main__":
    unittest.main()
