"""The capabilities parser is the plugin's XML front door, and it faces the network.

A GetCapabilities document is fetched from whatever host a source descriptor names, so it
is untrusted input. ``xml.etree`` refuses *external* entities (verified below), but it
happily expands *internal* ones, and a handful of nested declarations is enough to turn a
few hundred bytes into an out-of-memory error. OGC capabilities are schema-based and never
carry a DTD, so the parser refuses the construct outright.

These tests are offline: no service is contacted.
"""

from __future__ import annotations

import unittest

from territorial_suite.core.errors import SourceSchemaError
from territorial_suite.services.ogc import capabilities

GOOD = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" version="2.0.0">
  <FeatureTypeList>
    <FeatureType>
      <Name>ms:rt_idrogeol.areeboscate.2016.rt.poly</Name>
      <Title>Aree boscate 2016</Title>
      <DefaultCRS>urn:ogc:def:crs:EPSG::3003</DefaultCRS>
    </FeatureType>
  </FeatureTypeList>
</wfs:WFS_Capabilities>"""

#: Three levels of nesting only: enough to prove expansion happens, small enough to stay
#: harmless if the guard ever regresses and the document is actually parsed.
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
]>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">&lol2;</wfs:WFS_Capabilities>"""

EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">&xxe;</wfs:WFS_Capabilities>"""


class TestHonestDocumentsStillWork(unittest.TestCase):
    """The hardening must not cost us a single real service."""

    def test_a_normal_capabilities_document_parses(self):
        parsed = capabilities.parse(GOOD)
        self.assertIn("ms:rt_idrogeol.areeboscate.2016.rt.poly", parsed.layer_names)

    def test_a_document_merely_mentioning_doctype_in_text_is_not_refused(self):
        """The guard looks for a declaration, not for the word: a service that describes
        its own metadata must not be rejected for using the word in an abstract."""
        payload = GOOD.replace(b"<Title>Aree boscate 2016</Title>",
                               b"<Title>Formato DOCTYPE ed ENTITY non usati</Title>")
        self.assertTrue(capabilities.parse(payload).layer_names)


class TestUntrustedDocumentsAreRefused(unittest.TestCase):
    def test_an_entity_expansion_bomb_is_refused_before_parsing(self):
        with self.assertRaises(SourceSchemaError) as caught:
            capabilities.parse(BILLION_LAUGHS)
        self.assertIn("DTD", str(caught.exception))

    def test_an_external_entity_is_refused(self):
        with self.assertRaises(SourceSchemaError):
            capabilities.parse(EXTERNAL_ENTITY)

    def test_the_refusal_explains_itself(self):
        """A source that fails must say why, so the user can tell a hostile document from
        a broken one."""
        try:
            capabilities.parse(BILLION_LAUGHS)
        except SourceSchemaError as exc:
            self.assertTrue(getattr(exc, "detail", ""), "l'errore non spiega il motivo")

    def test_a_malformed_document_is_still_a_schema_error_not_a_crash(self):
        with self.assertRaises(SourceSchemaError):
            capabilities.parse(b"<wfs:WFS_Capabilities>non chiuso")


if __name__ == "__main__":
    unittest.main()
