"""Rules for the fallback agent (where sentences go that Polyglot does not know)."""
from __future__ import annotations

from contextvars import ContextVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

# Set while Polyglot hands a sentence to its fallback. If a sentence comes
# back to a Polyglot agent and would be handed on again, it is a loop
# (Polyglot → other proxy agent → Polyglot …) and is refused.
IN_FALLBACK: ContextVar[bool] = ContextVar("polyglot_assist_in_fallback", default=False)


def is_polyglot_agent(hass: HomeAssistant, agent_id: str | None) -> bool:
    """True if ``agent_id`` is a Polyglot Assist agent (this or another entry)."""
    if not agent_id:
        return False
    entry = er.async_get(hass).async_get(agent_id)
    return entry is not None and entry.platform == DOMAIN


def fallback_error(hass: HomeAssistant, agent_id: str | None) -> str | None:
    """Error key for config/option flows and the panel, or None if usable."""
    if not agent_id:
        return "fallback_missing"
    if is_polyglot_agent(hass, agent_id):
        return "fallback_is_polyglot"
    return None
