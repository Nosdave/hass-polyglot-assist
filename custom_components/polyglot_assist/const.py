"""Constants for the Polyglot Assist integration."""
from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN: Final = "polyglot_assist"

# Config keys
CONF_FALLBACK_AGENT: Final = "fallback_agent"
CONF_INTENTS_FILE: Final = "intents_file"
CONF_LANGUAGES: Final = "languages"
# Options (opt-in, default off)
CONF_LANGUAGE_HINT: Final = "fallback_language_hint"
# Record hits in the ChatLog as tool calls (needs llm_equivalent per intent)
CONF_CHAT_LOG_TOOLS: Final = "chat_log_tools"
# AI Task entity used for translation/name PROPOSALS (pick one without tools)
CONF_TRANSLATION_AI_TASK: Final = "translation_ai_task"

# Intents source: YAML file (default) or catalog in .storage
CONF_SOURCE: Final = "source"
SOURCE_YAML: Final = "yaml"
SOURCE_CATALOG: Final = "catalog"

# Per-intent safety level
SAFETY_NORMAL: Final = "normal"
SAFETY_SENSITIVE: Final = "sensitive"
SAFETY_DISABLED: Final = "disabled"

# Defaults
DEFAULT_INTENTS_FILE: Final = "polyglot_assist/intents.yaml"
DEFAULT_LANGUAGES: Final = ["de", "en", "fr"]
DEFAULT_TESTS_FILE: Final = "polyglot_assist/tests.yaml"

# Service names
SERVICE_RELOAD: Final = "reload"
SERVICE_TEST_MATCH: Final = "test_match"
SERVICE_RUN_TESTS: Final = "run_tests"
SERVICE_IMPORT_YAML: Final = "import_yaml"
SERVICE_EXPORT_YAML: Final = "export_yaml"

# Dispatcher signal: matcher (re)loaded for an entry → f"{SIGNAL_LOADED}_{entry_id}"
SIGNAL_LOADED: Final = "polyglot_assist_loaded"

# Event names (for observability)
EVENT_MATCH: Final = "polyglot_assist_match"
EVENT_MISS_PROXIED: Final = "polyglot_assist_miss_proxied"

# Reserved slot key — always injected with the matched language
SLOT_LANG: Final = "lang"
# Full original sentence, so handlers (e.g. wildcard intents) can pass it on unchanged
SLOT_UTTERANCE: Final = "utterance"


def device_info(entry: "ConfigEntry") -> "DeviceInfo":
    """One service device per entry, so entities get clear names."""
    from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo  # noqa: PLC0415

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name=entry.title or "Polyglot Assist",
        manufacturer="Polyglot Assist",
    )
