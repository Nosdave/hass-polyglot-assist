"""Constants for the Polyglot Assist integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "polyglot_assist"

# Config keys
CONF_FALLBACK_AGENT: Final = "fallback_agent"
CONF_INTENTS_FILE: Final = "intents_file"
CONF_LANGUAGES: Final = "languages"

# Defaults
DEFAULT_INTENTS_FILE: Final = "polyglot_assist/intents.yaml"
DEFAULT_LANGUAGES: Final = ["de", "en", "fr"]

# Service names
SERVICE_RELOAD: Final = "reload"
SERVICE_TEST_MATCH: Final = "test_match"

# Event names (for observability)
EVENT_MATCH: Final = "polyglot_assist_match"
EVENT_MISS_PROXIED: Final = "polyglot_assist_miss_proxied"

# Reserved slot key — always injected with the matched language
SLOT_LANG: Final = "lang"
