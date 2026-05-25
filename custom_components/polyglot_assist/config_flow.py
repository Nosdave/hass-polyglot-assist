"""Config-flow for Polyglot Assist.

Minimal v0.1: ask the user for
- fallback_agent: the conversation agent to proxy unmatched utterances to
  (any HA-registered conversation entity-id, e.g. ``conversation.skye_harris_…``)
- intents_file: path to the YAML defining multilang sentences per intent

Future enhancements (options-flow): hot-reload toggle, language filter,
log-verbosity.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_FALLBACK_AGENT,
    CONF_INTENTS_FILE,
    DEFAULT_INTENTS_FILE,
    DOMAIN,
)


class PolyglotAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI configuration flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title="Polyglot Assist",
                data={
                    CONF_FALLBACK_AGENT: user_input[CONF_FALLBACK_AGENT],
                    CONF_INTENTS_FILE: user_input[CONF_INTENTS_FILE],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_FALLBACK_AGENT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="conversation")
                ),
                vol.Required(
                    CONF_INTENTS_FILE, default=DEFAULT_INTENTS_FILE
                ): selector.TextSelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "intents_default": DEFAULT_INTENTS_FILE,
            },
        )
