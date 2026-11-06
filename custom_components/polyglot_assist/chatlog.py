"""Record Tier-1 hits in the shared ChatLog as an LLM-visible tool call.

Why: after a Polyglot hit the fallback LLM sees nothing of it, so a follow-up
like "and the piano too" lacks context. Measured on a local Qwen model with a
real prompt (follow-ups after two hits):

* no entry (default):                       19/40 correct
* assistant TEXT only:                       8/40 correct, 22/40 claimed
                                             success without calling a tool
* tool call with an OFFERED tool name:      30/40 correct
* tool call with the bare Polyglot intent:   12/18 calls with an invalid name

So hits are only recorded as a tool call whose name resolves uniquely to a
tool the fallback agent really offers (its LLM APIs are read from its own
configuration; ``llm_equivalent`` per intent),
never as plain text and never with an unresolvable name. Opt-in.

intents.yaml::

    LightZoneOnOff:
      llm_equivalent:
        tool: HassTurnOn            # suffix of an offered tool name
        args: {name: "{{ zone_text }}"}   # templates; slot values and <slot>_text
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.conversation import ConversationInput
from homeassistant.components.conversation.chat_log import (
    AssistantContent,
    ChatLog,
    ToolResultContent,
)
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent, llm
from homeassistant.helpers.template import Template

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_TOOL_CACHE_TTL = 300.0
_tool_cache: dict[tuple[Any, ...], tuple[float, list[str]]] = {}


@callback
def clear_tool_cache() -> None:
    _tool_cache.clear()


def resolve_tool_name(wanted: str, offered: list[str]) -> str | None:
    """Exact name, else the unique offered name ending in ``__<wanted>``."""
    if wanted in offered:
        return wanted
    hits = [n for n in offered if n.endswith(f"__{wanted}")]
    return hits[0] if len(hits) == 1 else None


@callback
def fallback_llm_apis(hass: HomeAssistant, agent_id: str) -> list[str]:
    """LLM API ids the fallback conversation entity really uses.

    Read from its config (sub)entry (``llm_hass_api``) so the recorded tool
    name is exactly one the fallback offers. Unknown → [] (nothing recorded).
    """
    entry = er.async_get(hass).async_get(agent_id)
    if entry is None or entry.config_entry_id is None:
        return []
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None:
        return []
    sources: list[Any] = []
    if entry.config_subentry_id and entry.config_subentry_id in config_entry.subentries:
        sources.append(config_entry.subentries[entry.config_subentry_id].data)
    sources += [config_entry.options, config_entry.data]
    existing = {api.id for api in llm.async_get_apis(hass)}
    for src in sources:
        apis = src.get(CONF_LLM_HASS_API)
        if apis:
            ids = [apis] if isinstance(apis, str) else [str(a) for a in apis]
            # like the conversation integrations: drop APIs that no longer exist
            # (this also changes whether tool names carry a namespace)
            return [a for a in ids if a in existing]
    return []


async def _offered_tools(
    hass: HomeAssistant, api_ids: list[str], user_input: ConversationInput
) -> list[str]:
    key = (tuple(sorted(api_ids)), user_input.device_id)
    cached = _tool_cache.get(key)
    if cached and time.monotonic() - cached[0] < _TOOL_CACHE_TTL:
        return cached[1]
    api = await llm.async_get_api(hass, list(api_ids), user_input.as_llm_context(DOMAIN))
    names = [tool.name for tool in api.tools]
    _tool_cache[key] = (time.monotonic(), names)
    return names


def _render_args(
    hass: HomeAssistant, args: dict[str, Any], slots: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    variables: dict[str, Any] = {}
    for name, slot in slots.items():
        variables[name] = slot.get("value")
        if "text" in slot:
            variables[f"{name}_text"] = slot.get("text")
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            rendered = Template(value, hass).async_render(variables, parse_result=False).strip()
            if not rendered:
                return None  # an undefined variable — better no record than a wrong one
            out[key] = rendered
        else:
            out[key] = value
    return out


async def async_record_hit(
    hass: HomeAssistant,
    *,
    user_input: ConversationInput,
    chat_log: ChatLog,
    intent_cfg: dict[str, Any],
    slots: dict[str, dict[str, Any]],
    response: intent.IntentResponse,
    fallback_agent_id: str,
) -> bool:
    """Add tool call + result + spoken answer to the ChatLog. Never raises.

    All content objects are built first and added only together, so a
    failure can never leave a tool call without its result in the log
    (OpenAI-compatible servers reject such a history).
    """
    equivalent = intent_cfg.get("llm_equivalent")
    if not isinstance(equivalent, dict) or not equivalent.get("tool"):
        return False
    try:
        api_ids = fallback_llm_apis(hass, fallback_agent_id)
        if not api_ids:
            return False
        offered = await _offered_tools(hass, api_ids, user_input)
        tool_name = resolve_tool_name(str(equivalent["tool"]), offered)
        if tool_name is None:
            _LOGGER.debug(
                "llm_equivalent %s does not resolve to one offered tool — not recorded",
                equivalent["tool"],
            )
            return False
        args = _render_args(hass, dict(equivalent.get("args") or {}), slots)
        if args is None:
            return False
        agent_id = user_input.agent_id
        tool_input = llm.ToolInput(tool_name=tool_name, tool_args=args, external=True)
        failed = response.response_type == intent.IntentResponseType.ERROR
        contents: list[Any] = [
            AssistantContent(agent_id=agent_id, content=None, tool_calls=[tool_input]),
            ToolResultContent(
                agent_id=agent_id,
                tool_call_id=tool_input.id,
                tool_name=tool_name,
                result=llm.ToolResult(data=llm.IntentResponseDict(response), error=failed),
            ),
        ]
        speech = response.speech.get("plain", {}).get("speech", "")
        if speech:
            contents.append(AssistantContent(agent_id=agent_id, content=speech))
    except Exception:  # noqa: BLE001 — recording must never break a hit
        _LOGGER.exception("Polyglot Assist: preparing the ChatLog record failed")
        return False
    for content in contents:
        chat_log.async_add_assistant_content_without_tools(content)
    return True
