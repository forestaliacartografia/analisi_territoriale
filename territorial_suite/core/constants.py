"""Stable identifiers shared across the whole plugin.

Only *identity* constants belong here. Every tunable value (URLs, thresholds, scales,
class breaks, TTLs) lives in ``config/`` and is read through :mod:`core.settings`.
"""

from __future__ import annotations

#: User-visible name. The technical id below is deliberately different and
#: must never follow it: QgsSettings keys, layer custom properties and
#: Processing algorithm ids are built from PLUGIN_ID and renaming them would
#: break existing projects, saved models and user settings.
PLUGIN_NAME = "Analisi territoriale"
PLUGIN_ID = "territorial_suite"
PLUGIN_VERSION = "0.1.1"

#: QgsMessageLog channel used by :mod:`core.log`.
LOG_CHANNEL = PLUGIN_NAME

#: Root of the plugin settings tree inside QgsSettings.
SETTINGS_ROOT = f"plugins/{PLUGIN_ID}"

#: QgsProject custom-property keys.
PROP_PROJECT_AREA = f"{PLUGIN_ID}/project_area"
PROP_PROJECT_AREA_LIST = f"{PLUGIN_ID}/project_areas"
PROP_SOURCE_OVERRIDES = f"{PLUGIN_ID}/source_overrides"
PROP_ONBOARDING_DONE = f"{PLUGIN_ID}/onboarding_done"

#: QgsMapLayer custom-property keys (data provenance, see :mod:`core.provenance`).
PROP_LAYER_PROVENANCE = f"{PLUGIN_ID}/provenance"
PROP_LAYER_SOURCE_ID = f"{PLUGIN_ID}/source_id"
PROP_LAYER_AREA_ID = f"{PLUGIN_ID}/area_id"
PROP_LAYER_CATEGORY = f"{PLUGIN_ID}/category"

#: Layout custom property: the template a sheet was built from. The sheet QA
#: needs it to know which contract the title was supposed to honour.
PROP_LAYOUT_TEMPLATE = f"{PLUGIN_ID}/layout_template"

#: Layout custom property: the stored print a layout belongs to.
PROP_PRINT_ID = f"{PLUGIN_ID}/print_id"

#: Project entry holding the register of prints. Prints live in the project the
#: user saves and shares, not in a private directory the recipient would not get.
PROP_PRINT_REGISTRY = f"{PLUGIN_ID}/prints"

#: Layer-tree group names created by the plugin.
GROUP_ROOT = PLUGIN_NAME
GROUP_AREA = "Project area"
GROUP_CONSTRAINTS = "Constraints"
GROUP_CADASTRE = "Cadastre"
GROUP_TERRAIN = "Terrain"
GROUP_DATA = "Data"
GROUP_IMAGERY = "Imagery"

#: File extension of the portable project-area file.
AREA_FILE_SUFFIX = ".tsa.json"

#: Name of the serialised analysis result inside an exported package.
ANALYSIS_FILE = "analysis.json"

#: Distinct identifier written in layer metadata to recognise plugin outputs.
METADATA_IDENTIFIER_PREFIX = "territorial-suite"
