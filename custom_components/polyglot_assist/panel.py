"""Sidebar panel "Polyglot" (admin only)."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_URL = "polyglot-assist"
STATIC_URL = f"/{DOMAIN}_static"
_FILE = "polyglot-panel.js"


async def async_register(hass: HomeAssistant, version: str) -> None:
    """Serve the panel module and add the sidebar entry (once)."""
    if hass.data.get(f"{DOMAIN}_panel"):
        return
    folder = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(folder), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="polyglot-assist-panel",
        sidebar_title="Polyglot",
        sidebar_icon="mdi:translate",
        module_url=f"{STATIC_URL}/{_FILE}?v={version}",
        require_admin=True,
        config={"domain": DOMAIN},
    )
    hass.data[f"{DOMAIN}_panel"] = True


def async_unregister(hass: HomeAssistant) -> None:
    if hass.data.pop(f"{DOMAIN}_panel", None):
        frontend.async_remove_panel(hass, PANEL_URL)
