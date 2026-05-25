"""Config-flow + Options-flow for Polyglot Assist.

Initial config-flow asks for:
- fallback_agent: the conversation agent to proxy unmatched utterances to
- intents_file: path to the YAML defining multilang sentences per intent

OptionsFlow provides ongoing editing without removing+re-adding the
integration:
- Change fallback agent
- Edit intents.yaml as raw text (YAML view)
- Visual editor: pick an intent → edit per-language sentences
- Add a new language to an intent
- Add a brand-new intent
- Reload matcher (rebuilds Hassil intents from disk without HA restart)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_FALLBACK_AGENT,
    CONF_INTENTS_FILE,
    DEFAULT_INTENTS_FILE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


# Languages the user can pick from when adding a new language to an intent.
# Codes follow BCP-47 short form (ISO-639-1).
_COMMON_LANGUAGES = [
    "de", "en", "fr", "es", "it", "nl", "pt", "pl", "cs", "sv", "da",
    "no", "fi", "ru", "tr", "ja", "ko", "zh", "ar",
]


class PolyglotAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial integration setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
            description_placeholders={"intents_default": DEFAULT_INTENTS_FILE},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        return PolyglotAssistOptionsFlow(config_entry)


class PolyglotAssistOptionsFlow(OptionsFlow):
    """Visual + YAML editor for intents, plus fallback-agent reconfig."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # The newer HA OptionsFlow base class auto-populates self.config_entry;
        # we don't store it explicitly to remain forward-compatible.
        self._entry = config_entry
        self._intents_data: dict[str, Any] = {}
        self._intents_path: Path | None = None
        self._current_intent_name: str | None = None
        self._current_intent_data: dict[str, Any] = {}

    # ─────────────────────────────────────────────────────────────────
    # Menu
    # ─────────────────────────────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "change_fallback": "Change fallback agent",
                "edit_visual": "Edit intents (visual)",
                "edit_yaml": "Edit intents (YAML)",
                "add_intent": "Add a new intent",
                "reload": "Reload matcher",
            },
        )

    # ─────────────────────────────────────────────────────────────────
    # Change fallback agent
    # ─────────────────────────────────────────────────────────────────

    async def async_step_change_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_data = {**self._entry.data, CONF_FALLBACK_AGENT: user_input[CONF_FALLBACK_AGENT]}
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data={})

        current = self._entry.data.get(CONF_FALLBACK_AGENT, "")
        schema = vol.Schema(
            {
                vol.Required(CONF_FALLBACK_AGENT, default=current): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="conversation")
                ),
            }
        )
        return self.async_show_form(step_id="change_fallback", data_schema=schema)

    # ─────────────────────────────────────────────────────────────────
    # Edit YAML (raw view)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_edit_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._ensure_loaded()
        assert self._intents_path is not None

        if user_input is not None:
            new_yaml = user_input["yaml_content"]
            # Validate
            try:
                parsed = yaml.safe_load(new_yaml)
                if not isinstance(parsed, dict) or "intents" not in parsed:
                    raise ValueError("Top-level 'intents:' key missing")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("YAML validation failed: %s", err)
                return self.async_show_form(
                    step_id="edit_yaml",
                    data_schema=self._yaml_schema(new_yaml),
                    errors={"base": "invalid_yaml"},
                    description_placeholders={"err": str(err)[:200]},
                )
            # Write
            await self.hass.async_add_executor_job(
                self._intents_path.write_text, new_yaml, "utf-8"
            )
            await self._reload_matcher()
            return self.async_create_entry(title="", data={})

        current_yaml = await self.hass.async_add_executor_job(
            self._intents_path.read_text, "utf-8"
        )
        return self.async_show_form(
            step_id="edit_yaml",
            data_schema=self._yaml_schema(current_yaml),
        )

    @staticmethod
    def _yaml_schema(default_value: str) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("yaml_content", default=default_value): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )

    # ─────────────────────────────────────────────────────────────────
    # Edit visual — step 1: pick an intent
    # ─────────────────────────────────────────────────────────────────

    async def async_step_edit_visual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._ensure_loaded()
        intent_names = sorted(self._intents_data.get("intents", {}).keys())

        if not intent_names:
            return self.async_abort(reason="no_intents")

        if user_input is not None:
            self._current_intent_name = user_input["intent_name"]
            self._current_intent_data = (
                self._intents_data["intents"].get(self._current_intent_name, {}) or {}
            )
            return await self.async_step_edit_intent()

        schema = vol.Schema(
            {
                vol.Required("intent_name"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=intent_names,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="edit_visual", data_schema=schema)

    # ─────────────────────────────────────────────────────────────────
    # Edit intent — per-language sentence textareas
    # ─────────────────────────────────────────────────────────────────

    async def async_step_edit_intent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._ensure_loaded()
        assert self._current_intent_name is not None

        sentences_by_lang: dict[str, list[str]] = (
            self._current_intent_data.get("sentences", {}) or {}
        )
        # Order: existing langs alphabetical, plus a "+ add" slot
        existing_langs = sorted(sentences_by_lang.keys())

        if user_input is not None:
            # Save: parse textareas back into sentence lists
            new_sentences: dict[str, list[str]] = {}
            for lang in existing_langs:
                key = f"sentences_{lang}"
                raw = user_input.get(key, "")
                lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                if lines:
                    new_sentences[lang] = lines

            description = user_input.get("description", "").strip()
            response_intent = user_input.get("response_intent", "").strip()
            new_lang_to_add = (user_input.get("add_language") or "").strip().lower()

            updated_intent: dict[str, Any] = {}
            if description:
                updated_intent["description"] = description
            if new_sentences:
                updated_intent["sentences"] = new_sentences
            if response_intent:
                updated_intent["response_intent"] = response_intent

            self._intents_data["intents"][self._current_intent_name] = updated_intent

            if new_lang_to_add and new_lang_to_add not in new_sentences:
                # Add empty bucket — user can populate next iteration
                self._intents_data["intents"][self._current_intent_name].setdefault(
                    "sentences", {}
                )[new_lang_to_add] = []
                await self._save_intents()
                # Re-enter the edit step so the new lang's textarea appears
                self._current_intent_data = (
                    self._intents_data["intents"][self._current_intent_name]
                )
                return await self.async_step_edit_intent()

            await self._save_intents()
            await self._reload_matcher()
            return self.async_create_entry(title="", data={})

        # Build dynamic schema with one textarea per existing language
        schema_dict: dict = {}
        schema_dict[vol.Optional(
            "description", default=self._current_intent_data.get("description", "")
        )] = selector.TextSelector()
        for lang in existing_langs:
            default = "\n".join(sentences_by_lang.get(lang, []))
            schema_dict[vol.Optional(f"sentences_{lang}", default=default)] = (
                selector.TextSelector(selector.TextSelectorConfig(multiline=True))
            )
        schema_dict[vol.Optional(
            "response_intent",
            default=self._current_intent_data.get(
                "response_intent", self._current_intent_name
            ),
        )] = selector.TextSelector()
        schema_dict[vol.Optional("add_language", default="")] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[lc for lc in _COMMON_LANGUAGES if lc not in existing_langs],
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )

        return self.async_show_form(
            step_id="edit_intent",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "intent_name": self._current_intent_name,
                "existing_langs": ", ".join(existing_langs) or "(none)",
            },
        )

    # ─────────────────────────────────────────────────────────────────
    # Add new intent
    # ─────────────────────────────────────────────────────────────────

    async def async_step_add_intent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._ensure_loaded()

        if user_input is not None:
            name = user_input["new_intent_name"].strip()
            description = user_input.get("description", "").strip()
            initial_lang = user_input.get("initial_language", "de")

            if not name or not name.replace("_", "").isalnum():
                return self.async_show_form(
                    step_id="add_intent",
                    data_schema=self._add_intent_schema(),
                    errors={"new_intent_name": "invalid_name"},
                )

            if name in (self._intents_data.get("intents") or {}):
                return self.async_show_form(
                    step_id="add_intent",
                    data_schema=self._add_intent_schema(),
                    errors={"new_intent_name": "name_exists"},
                )

            self._intents_data.setdefault("intents", {})[name] = {
                "description": description or f"{name} intent",
                "sentences": {initial_lang: []},
                "response_intent": name,
            }
            await self._save_intents()
            # Open the new intent in the edit-intent flow
            self._current_intent_name = name
            self._current_intent_data = self._intents_data["intents"][name]
            return await self.async_step_edit_intent()

        return self.async_show_form(
            step_id="add_intent", data_schema=self._add_intent_schema()
        )

    def _add_intent_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("new_intent_name"): selector.TextSelector(),
                vol.Optional("description", default=""): selector.TextSelector(),
                vol.Optional("initial_language", default="de"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_COMMON_LANGUAGES,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
            }
        )

    # ─────────────────────────────────────────────────────────────────
    # Reload matcher only (no data change)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_reload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._reload_matcher()
        return self.async_create_entry(title="", data={})

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        if self._intents_path is not None:
            return
        intents_file = self._entry.data.get(CONF_INTENTS_FILE, DEFAULT_INTENTS_FILE)
        path = Path(intents_file)
        if not path.is_absolute():
            path = Path(self.hass.config.path(intents_file))
        self._intents_path = path
        if path.is_file():
            raw = await self.hass.async_add_executor_job(path.read_text, "utf-8")
            self._intents_data = yaml.safe_load(raw) or {"intents": {}}
        else:
            self._intents_data = {"intents": {}}

    async def _save_intents(self) -> None:
        assert self._intents_path is not None

        def _do_save(path: Path, data: dict) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            new_yaml = yaml.safe_dump(
                data, allow_unicode=True, sort_keys=False, indent=2
            )
            path.write_text(new_yaml, "utf-8")

        await self.hass.async_add_executor_job(
            _do_save, self._intents_path, self._intents_data
        )
        return

    async def _reload_matcher(self) -> None:
        await self.hass.services.async_call(
            DOMAIN, "reload", {}, blocking=True
        )
