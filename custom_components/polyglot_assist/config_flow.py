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
    CONF_CHAT_LOG_TOOLS,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGE_HINT,
    CONF_INTENTS_FILE,
    CONF_SOURCE,
    CONF_TRANSLATION_AI_TASK,
    DEFAULT_INTENTS_FILE,
    DOMAIN,
    SOURCE_CATALOG,
    SOURCE_YAML,
)
from .corpus import validate
from .guard import GuardError, async_guarded_update, write_lock
from .loader import MatcherLoader
from .source import IntentSource, SourceError

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
        self._loaded = False
        self._original_intent_name: str | None = None
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
                "language_hint": "Fallback language hint",
                "chat_log": "Hits in conversation history",
                "source": "Intents source (YAML file or catalog)",
                "translation": "Translation proposals (AI task)",
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
            return self.async_create_entry(title="", data=dict(self._entry.options))

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
    # Fallback language hint (opt-in)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_language_hint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_options = {**self._entry.options, CONF_LANGUAGE_HINT: user_input[CONF_LANGUAGE_HINT]}
            self.hass.config_entries.async_update_entry(self._entry, options=new_options)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            # An OptionsFlow result's data REPLACES entry.options.
            return self.async_create_entry(title="", data=new_options)

        current = self._entry.options.get(CONF_LANGUAGE_HINT, False)
        schema = vol.Schema(
            {
                vol.Required(CONF_LANGUAGE_HINT, default=current): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="language_hint", data_schema=schema)

    # ─────────────────────────────────────────────────────────────────
    # ChatLog recording of hits (opt-in)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_chat_log(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_options = {
                **{k: v for k, v in self._entry.options.items() if k != "fallback_llm_apis"},
                CONF_CHAT_LOG_TOOLS: user_input[CONF_CHAT_LOG_TOOLS],
            }
            self.hass.config_entries.async_update_entry(self._entry, options=new_options)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CHAT_LOG_TOOLS,
                    default=self._entry.options.get(CONF_CHAT_LOG_TOOLS, False),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="chat_log", data_schema=schema)

    # ─────────────────────────────────────────────────────────────────
    # Intents source: YAML file or catalog (.storage)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Switch YAML ↔ catalog without ever losing edits of the other side."""
        errors: dict[str, str] = {}
        placeholders = {"err": ""}
        if user_input is not None and self._runtime is not None:
            async with write_lock(self._runtime):
                return await self._source_switch(user_input, errors, placeholders)
        if user_input is not None:
            return await self._source_switch(user_input, errors, placeholders)
        return self._source_form(errors, placeholders)

    async def _source_switch(self, user_input, errors, placeholders) -> ConfigFlowResult:
        wanted = user_input[CONF_SOURCE]
        choice = user_input.get("when_different", "ask")
        src = self._source
        current = src.kind
        try:
            if wanted == current:
                target = await src.async_load()
            elif wanted == SOURCE_CATALOG:
                yaml_data = await src.async_load_yaml()
                catalog = await src.async_load_catalog()
                if catalog is None or (catalog != yaml_data and choice == "use_yaml"):
                    if catalog is not None:
                        await src.async_export_yaml()  # keep the replaced catalog as a file
                    await src.async_save_catalog(yaml_data)  # verified write
                    catalog = await src.async_load_catalog()
                elif catalog != yaml_data and choice != "keep_other":
                    errors["base"] = "sources_differ"
                target = catalog
            else:  # catalog → yaml
                catalog = await src.async_load_catalog()
                yaml_data = await src.async_load_yaml()
                if catalog is not None and catalog != yaml_data:
                    if choice == "ask":
                        errors["base"] = "sources_differ"
                    else:
                        await src.async_export_yaml()  # keep the catalog edits as a file
                target = yaml_data
            if not errors:
                await MatcherLoader.load_data(self.hass, target, "switch-test")
        except (SourceError, ValueError, yaml.YAMLError) as err:
            _LOGGER.error("Switching source failed: %s", err)
            errors["base"] = "import_failed"
            placeholders["err"] = str(err)[:200]
        if not errors:
            new_options = {**self._entry.options, CONF_SOURCE: wanted}
            self.hass.config_entries.async_update_entry(self._entry, options=new_options)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data=new_options)
        return self._source_form(errors, placeholders)

    def _source_form(self, errors, placeholders) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOURCE, default=self._entry.options.get(CONF_SOURCE, SOURCE_YAML)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[SOURCE_YAML, SOURCE_CATALOG], translation_key="source"
                    )
                ),
                vol.Optional("when_different", default="ask"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["ask", "use_yaml", "keep_other"],
                        translation_key="when_different",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="source", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    # ─────────────────────────────────────────────────────────────────
    # Translation proposals: which AI Task entity to use
    # ─────────────────────────────────────────────────────────────────

    async def async_step_translation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_options = {**self._entry.options}
            if user_input.get(CONF_TRANSLATION_AI_TASK):
                new_options[CONF_TRANSLATION_AI_TASK] = user_input[CONF_TRANSLATION_AI_TASK]
            else:
                new_options.pop(CONF_TRANSLATION_AI_TASK, None)
            self.hass.config_entries.async_update_entry(self._entry, options=new_options)
            return self.async_create_entry(title="", data=new_options)

        current = self._entry.options.get(CONF_TRANSLATION_AI_TASK)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TRANSLATION_AI_TASK,
                    description={"suggested_value": current} if current else None,
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
            }
        )
        return self.async_show_form(step_id="translation", data_schema=schema)

    # ─────────────────────────────────────────────────────────────────
    # Edit YAML (raw view)
    # ─────────────────────────────────────────────────────────────────

    async def async_step_edit_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
            # Write to the active source (YAML file or catalog), guarded
            def _replace(data: dict) -> None:
                data.clear()
                data.update(parsed)

            error = await self._guarded_write(_replace)
            if error:
                return self.async_show_form(
                    step_id="edit_yaml",
                    data_schema=self._yaml_schema(new_yaml),
                    errors={"base": "refused"},
                    description_placeholders={"err": error},
                )
            return self.async_create_entry(title="", data=dict(self._entry.options))

        current_yaml = await self._source.async_load_text()
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
            self._original_intent_name = self._current_intent_name
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

            # Keep every other field (safety, llm_equivalent, translation, …)
            updated_intent: dict[str, Any] = dict(self._current_intent_data)
            for key, value in (
                ("description", description),
                ("response_intent", response_intent),
            ):
                if value:
                    updated_intent[key] = value
                else:
                    updated_intent.pop(key, None)
            if new_sentences:
                updated_intent["sentences"] = new_sentences
            else:
                updated_intent.pop("sentences", None)

            if new_lang_to_add and new_lang_to_add not in new_sentences:
                # Add an empty bucket in memory only and re-enter the form
                updated_intent.setdefault("sentences", {})[new_lang_to_add] = []
                self._current_intent_data = updated_intent
                return await self.async_step_edit_intent()

            name = self._current_intent_name
            original = self._original_intent_name

            def _apply(data: dict) -> None:
                intents = data.setdefault("intents", {})
                if original is None and name in intents:
                    raise GuardError("exists", f"an intent named {name} already exists")
                intents[name] = updated_intent

            error = await self._guarded_write(_apply)
            if error:
                self._current_intent_data = updated_intent
                return self._edit_intent_form(existing_langs, error)
            return self.async_create_entry(title="", data=dict(self._entry.options))

        return self._edit_intent_form(existing_langs, None)

    def _edit_intent_form(self, existing_langs: list[str], error: str | None) -> ConfigFlowResult:
        sentences_by_lang = self._current_intent_data.get("sentences", {}) or {}
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
            errors={"base": "refused"} if error else None,
            description_placeholders={
                "intent_name": self._current_intent_name,
                "existing_langs": ", ".join(existing_langs) or "(none)",
                "err": error or "",
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

            # Staged in memory; saved (guarded) once sentences were entered
            self._current_intent_name = name
            self._original_intent_name = None
            self._current_intent_data = {
                "description": description or f"{name} intent",
                "sentences": {initial_lang: []},
                "response_intent": name,
            }
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
        return self.async_create_entry(title="", data=dict(self._entry.options))

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    @property
    def _runtime(self) -> dict[str, Any] | None:
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

    @property
    def _source(self) -> IntentSource:
        # Works while the entry is not loaded (e.g. a broken catalog): this
        # flow is the way to repair it.
        runtime = self._runtime
        return runtime["source"] if runtime else IntentSource(self.hass, self._entry)

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            self._intents_data = await self._source.async_load()
        except SourceError:
            self._intents_data = {"intents": {}}
        self._intents_data.setdefault("intents", {})
        self._loaded = True

    async def _guarded_write(self, mutate) -> str | None:
        """Write through the regression guard; returns an error text or None."""
        runtime = self._runtime
        try:
            if runtime is None:
                # Entry not loaded: plain write, then let the entry load again
                try:
                    data = await self._source.async_load()
                except SourceError:
                    data = {"intents": {}}
                before = set(validate(data))
                mutate(data)
                new = [p for p in validate(data) if p not in before]
                if new:
                    return "the change has errors: " + "; ".join(new[:5])
                await self._source.async_save(data)
                await self.hass.config_entries.async_reload(self._entry.entry_id)
                return None
            await async_guarded_update(self.hass, runtime, mutate)
        except GuardError as err:
            details = err.details.get("problems") or [
                f"{r['text']!r}: expected {r['expected']['intent']}, got "
                f"{(r['got'] or {}).get('intent')}"
                for r in err.details.get("regressions", [])
            ]
            return f"{err} " + "; ".join(details[:5])
        except SourceError as err:
            return str(err)
        return None

    async def _reload_matcher(self) -> None:
        if self._runtime is None or not self.hass.services.has_service(DOMAIN, "reload"):
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return
        await self.hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
