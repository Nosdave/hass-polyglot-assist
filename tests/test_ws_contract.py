"""The panel's this.ws() always sends entry_id — every such command must accept it."""
import re
from pathlib import Path

BASE = Path(__file__).parents[1] / "custom_components/polyglot_assist"


def _schemas() -> dict[str, str]:
    src = (BASE / "websocket.py").read_text("utf-8")
    out = {}
    for m in re.finditer(r'vol\.Required\("type"\): "polyglot_assist/([a-z_/]+)"', src):
        start = src.rfind("websocket_command(", 0, m.start())
        # the decorator ends where the decorated function starts
        ends = [i for i in (src.find("\ndef ", m.end()), src.find("\nasync def ", m.end()), src.find("\n@", m.end())) if i != -1]
        out[m.group(1)] = src[start:min(ends)]
    return out


def test_panel_ws_commands_accept_entry_id():
    panel = (BASE / "frontend/polyglot-panel.js").read_text("utf-8")
    used = set(re.findall(r'this\.ws\("([a-z_/]+)"', panel))
    schemas = _schemas()
    assert used, "no commands found"
    missing = sorted(c for c in used if c not in schemas)
    assert not missing, f"panel calls unknown commands: {missing}"
    without = sorted(c for c in used if 'vol.Optional("entry_id")' not in schemas[c])
    assert not without, f"these commands reject entry_id: {without}"
