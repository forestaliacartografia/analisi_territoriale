"""Orthophoto engine: provider choice, fallback, attribution and credentials.

The rule being tested is the one the terms of use impose: imagery is never shown without
its credit line, a provider that cannot be used is never silently replaced, and a secret
never lands in the settings file.
"""

from __future__ import annotations

import json
import os
import unittest

from qgis.core import QgsRectangle

from territorial_suite.core import credentials, settings
from territorial_suite.core.registry import DataSource, DataSourceRegistry
from territorial_suite.engines.cartography.imagery import (
    AUTOMATIC,
    GOOGLE,
    ImageryEngine,
)
from tests.support import requires_network

FLORENCE = QgsRectangle(11.250, 43.766, 11.262, 43.774)

PROBE_SETTING = "cartography.probe_imagery"


class ProbeOff(unittest.TestCase):
    """Base class: the one-tile probe is a network call, switched off by default here."""

    def setUp(self):
        self.engine = ImageryEngine()
        self._previous = settings.get(PROBE_SETTING, True)
        settings.set_value(PROBE_SETTING, False)
        self.addCleanup(lambda: settings.set_value(PROBE_SETTING, self._previous))


class TestProviderSelection(ProbeOff):

    def test_the_catalogue_declares_the_providers(self):
        """No URL lives in the engine: the providers come from the descriptors."""
        ids = {source.id for source in self.engine.providers()}
        self.assertIn("esri.basemap.world_imagery", ids)
        for source in self.engine.providers():
            self.assertTrue(source.is_operational, source.id)
            self.assertTrue(source.url.startswith("http"), source.id)

    def test_google_is_shipped_but_not_enabled_without_a_key(self):
        source = DataSourceRegistry.instance().get("google.maptiles.satellite")
        self.assertIsNotNone(source)
        self.assertFalse(source.enabled)
        self.assertIn("developers.google.com", source.documentation_url)
        # the endpoints are declared by the descriptor, not by the client
        for key in ("session_url", "viewport_url", "tiles_url"):
            self.assertTrue(source.query.get(key, "").startswith("https://"), key)

    def test_requesting_google_without_a_key_falls_back_and_says_so(self):
        choice = self.engine.resolve(FLORENCE, preference=GOOGLE)
        self.assertTrue(choice.available)
        self.assertNotEqual(choice.provider, GOOGLE)
        self.assertTrue(choice.fallback_used)
        self.assertTrue(any("chiave API" in attempt for attempt in choice.attempts),
                        choice.attempts)

    def test_explaining_an_unavailable_provider(self):
        self.assertIn("chiave API", self.engine.why_unavailable(GOOGLE))
        self.assertEqual(self.engine.why_unavailable(AUTOMATIC), "")
        self.assertIn("non presente nel catalogo",
                      self.engine.why_unavailable("provider_inesistente"))

    def test_automatic_picks_the_best_available(self):
        choice = self.engine.resolve(FLORENCE, preference=AUTOMATIC)
        self.assertTrue(choice.available)
        self.assertFalse(choice.fallback_used)
        self.assertEqual(choice.provider, "esri")

    def test_imagery_is_never_offered_without_a_credit_line(self):
        choice = self.engine.resolve(FLORENCE)
        self.assertTrue(choice.credit_line().strip())
        self.assertIn("Esri", choice.credit_line())

    def test_provenance_records_what_was_really_used(self):
        choice = self.engine.resolve(FLORENCE, preference=GOOGLE)
        provenance = self.engine.provenance(choice)
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance.source_id, choice.source_id)
        self.assertIn("Provider richiesto", provenance.notes)

    def test_choice_payload_is_json_serialisable(self):
        payload = self.engine.resolve(FLORENCE).as_dict()
        json.dumps(payload)
        for key in ("imagery_provider", "imagery_source", "source_url", "attribution",
                    "license", "date"):
            self.assertIn(key, payload)

    def test_no_fallback_when_the_caller_forbids_it(self):
        settings.set_value(PROBE_SETTING, True)
        engine = ImageryEngine()
        # a descriptor pointing at a host that cannot answer
        broken = DataSource.from_dict({
            "id": "test.imagery.broken", "name": "Rotta", "type": "XYZ",
            "url": "https://example.invalid/{z}/{x}/{y}.png", "category": "imagery",
            "tags": ["ortofoto"], "query": {"provider": "broken"},
        })
        engine.providers = lambda: [broken]
        choice = engine.resolve(FLORENCE, preference="broken", allow_fallback=False)
        self.assertFalse(choice.available)
        self.assertTrue(choice.attempts)


class TestEmptyCatalogue(ProbeOff):
    def test_no_imagery_is_reported_not_faked(self):
        engine = ImageryEngine()
        engine.providers = lambda: []
        choice = engine.resolve(FLORENCE)
        self.assertFalse(choice.available)
        self.assertIn("nessuna sorgente", " ".join(choice.attempts).lower())
        self.assertIsNone(engine.provenance(choice))


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.variable = credentials.env_variable(credentials.GOOGLE_MAPS)
        self.previous = os.environ.get(self.variable)
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is None:
            os.environ.pop(self.variable, None)
        else:
            os.environ[self.variable] = self.previous

    def test_environment_variable_is_read_first(self):
        os.environ[self.variable] = "chiave-di-prova"
        self.assertEqual(credentials.get(credentials.GOOGLE_MAPS), "chiave-di-prova")
        self.assertTrue(credentials.available(credentials.GOOGLE_MAPS))
        self.assertIn("variabile d'ambiente",
                      credentials.describe(credentials.GOOGLE_MAPS))

    def test_a_missing_secret_is_empty_not_an_error(self):
        os.environ.pop(self.variable, None)
        self.assertEqual(credentials.get("secret_che_non_esiste"), "")
        self.assertEqual(credentials.describe("secret_che_non_esiste"), "non configurata")

    def test_the_secret_is_never_written_into_the_settings(self):
        """Only an opaque auth-config id may be stored; never the key itself."""
        os.environ.pop(self.variable, None)
        key = credentials.AUTH_CONFIG_SETTING.format(name=credentials.GOOGLE_MAPS)
        settings.set_value(key, "abc123")
        snapshot = json.dumps(settings.snapshot())
        self.assertNotIn("chiave-di-prova", snapshot)
        settings.set_value(key, "")

    def test_google_client_refuses_to_work_without_a_key(self):
        from territorial_suite.core.errors import SourceUnavailableError
        from territorial_suite.services.google_tiles import GoogleTilesClient

        os.environ.pop(self.variable, None)
        source = DataSourceRegistry.instance().require("google.maptiles.satellite")
        client = GoogleTilesClient(source, api_key="")
        self.assertFalse(client.configured)
        with self.assertRaises(SourceUnavailableError):
            client.session()

    def test_a_descriptor_without_endpoints_is_rejected(self):
        from territorial_suite.core.errors import SourceUnavailableError
        from territorial_suite.services.google_tiles import GoogleTilesClient

        source = DataSource.from_dict({
            "id": "test.google.incomplete", "name": "Incompleta", "type": "XYZ",
            "url": "https://example.org/{z}/{x}/{y}", "category": "imagery",
            "query": {"provider": "google"}})
        client = GoogleTilesClient(source, api_key="finta")
        with self.assertRaises(SourceUnavailableError):
            client.session()


class TestTileProbe(unittest.TestCase):
    """A valid XYZ layer proves nothing: only a real tile does."""

    @requires_network
    def test_a_real_provider_answers_with_an_image(self):
        engine = ImageryEngine()
        source = engine.providers()[0]
        engine.probe_tile(source, source.url, FLORENCE, 14)     # must not raise

    @requires_network
    def test_a_dead_host_is_reported_instead_of_being_drawn_blank(self):
        from territorial_suite.core.errors import SourceUnavailableError

        engine = ImageryEngine()
        broken = DataSource.from_dict({
            "id": "test.imagery.dead", "name": "Morta", "type": "XYZ",
            "url": "https://example.invalid/{z}/{x}/{y}.png", "category": "imagery",
            "tags": ["ortofoto"], "query": {"provider": "dead", "retries": 1}})
        with self.assertRaises(SourceUnavailableError):
            engine.probe_tile(broken, broken.url, FLORENCE, 14)


if __name__ == "__main__":
    unittest.main()
