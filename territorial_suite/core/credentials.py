"""Secrets, kept out of the settings file.

Some services need a key the user owns and pays for (the Google Map Tiles API). Writing
that key into ``QgsSettings`` would leave it in clear text in the QGIS profile, so it is
stored in the QGIS **authentication database**, which is encrypted with the user's master
password, and only an opaque *auth config id* is remembered in the settings.

Reading order:

1. an environment variable, so a machine can be configured without touching the profile
   (and so tests never need the auth database);
2. the QGIS authentication database.

If neither has the key, the caller is expected to degrade gracefully - never to guess.
"""

from __future__ import annotations

import os

from . import log, settings


#: Settings key holding the *identifier* of an auth configuration (never the secret).
AUTH_CONFIG_SETTING = "credentials.{name}.auth_config_id"

#: Environment variable consulted first, e.g. ``TERRITORIAL_SUITE_GOOGLE_MAPS_KEY``.
ENV_TEMPLATE = "TERRITORIAL_SUITE_{name}_KEY"


def env_variable(name: str) -> str:
    """Name of the environment variable that can carry this secret."""
    return ENV_TEMPLATE.format(name=name.upper().replace(".", "_").replace("-", "_"))


def _auth_manager():
    try:
        from qgis.core import QgsApplication

        return QgsApplication.authManager()
    except Exception:  # pragma: no cover - outside QGIS
        return None


def get(name: str) -> str:
    """Return the secret called ``name``, or an empty string when it is not configured."""
    value = os.environ.get(env_variable(name), "").strip()
    if value:
        return value
    config_id = str(settings.get(AUTH_CONFIG_SETTING.format(name=name), "") or "").strip()
    if not config_id:
        return ""
    manager = _auth_manager()
    if manager is None:
        return ""
    try:
        from qgis.core import QgsAuthMethodConfig

        config = QgsAuthMethodConfig()
        if not manager.loadAuthenticationConfig(config_id, config, True):
            log.warning(f"Credenziale '{name}': configurazione {config_id} non leggibile "
                        f"(la password principale di QGIS potrebbe non essere stata "
                        f"inserita)")
            return ""
        return (config.configMap().get("password", "")
                or config.configMap().get("token", "")).strip()
    except Exception as exc:  # pragma: no cover - auth db locked
        log.warning(f"Credenziale '{name}' non leggibile: {exc}")
        return ""


def store(name: str, secret: str, *, label: str = "") -> str:
    """Store a secret in the QGIS authentication database.

    Returns the auth config id that was saved in the settings, or an empty string when the
    authentication database is unavailable - in which case nothing is written anywhere,
    because a secret in clear text is worse than a missing feature.
    """
    manager = _auth_manager()
    if manager is None or not secret:
        return ""
    try:
        from qgis.core import QgsAuthMethodConfig

        config = QgsAuthMethodConfig()
        config.setName(label or f"Territorial Suite - {name}")
        config.setMethod("Basic")
        config.setConfig("username", name)
        config.setConfig("password", secret)
        if not manager.storeAuthenticationConfig(config):
            log.warning(f"Credenziale '{name}' non salvata: database di autenticazione "
                        f"non disponibile")
            return ""
        settings.set_value(AUTH_CONFIG_SETTING.format(name=name), config.id())
        return config.id()
    except Exception as exc:  # pragma: no cover - auth db locked
        log.warning(f"Credenziale '{name}' non salvata: {exc}")
        return ""


def forget(name: str) -> None:
    """Remove a stored secret and the reference to it."""
    config_id = str(settings.get(AUTH_CONFIG_SETTING.format(name=name), "") or "")
    manager = _auth_manager()
    if config_id and manager is not None:
        try:
            manager.removeAuthenticationConfig(config_id)
        except Exception as exc:  # pragma: no cover - auth db locked
            log.warning(f"Credenziale '{name}' non rimossa: {exc}")
    settings.set_value(AUTH_CONFIG_SETTING.format(name=name), "")


def available(name: str) -> bool:
    """Whether a secret is configured, without returning it."""
    return bool(get(name))


def describe(name: str) -> str:
    """Where the secret comes from, for the settings dialog."""
    if os.environ.get(env_variable(name), "").strip():
        return f"variabile d'ambiente {env_variable(name)}"
    if str(settings.get(AUTH_CONFIG_SETTING.format(name=name), "") or ""):
        return "database di autenticazione di QGIS"
    return "non configurata"


#: Name of the secret used by the Google Map Tiles provider.


GOOGLE_MAPS = "google_maps"
