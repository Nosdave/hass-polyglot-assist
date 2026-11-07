"""TTS entity "Polyglot Assist (with tone)": the confirmation tone with ANY TTS.

Home Assistant plays its own acknowledge sound only when its built-in agent
answered and every target is in the satellite's area — a custom agent such as
Polyglot cannot ask for it. This entity sits in front of the TTS you already
use: every text is passed on to that TTS unchanged (streaming included);
only Polyglot's tone marker is answered with a short tone.

Choose it as speech output of a voice assistant and pick the real TTS in
Polyglot's options ("Speech output behind the tone").
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from homeassistant.components import tts
from homeassistant.components.tts import TextToSpeechEntity, TTSAudioRequest, TTSAudioResponse, Voice
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CHIME_MARKER, CONF_CHIME_TTS, DOMAIN

CHIME_FILE = Path(__file__).parent / "sounds" / "chime.wav"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PolyglotToneTTS(hass, entry)])


def inner_tts_error(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """Why ``entity_id`` cannot be the speech output behind the tone, or None."""
    if not entity_id:
        return "missing"
    if not entity_id.startswith("tts."):
        return "not_tts"
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform == DOMAIN:
        return "is_polyglot"  # would forward to itself
    return None


class PolyglotToneTTS(TextToSpeechEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "tone"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tone_tts"
        self._chime: bytes | None = None

    # ── the TTS behind the tone ────────────────────────────────────────────
    @property
    def _inner_id(self) -> str | None:
        inner = self._entry.options.get(CONF_CHIME_TTS)
        return inner if inner_tts_error(self.hass, inner) is None else None

    def _inner(self) -> Any | None:
        if not self._inner_id:
            return None
        from homeassistant.components.tts.helper import get_engine_instance  # noqa: PLC0415

        return get_engine_instance(self.hass, self._inner_id)

    @property
    def available(self) -> bool:
        return self._inner() is not None

    @property
    def supported_languages(self) -> list[str]:
        inner = self._inner()
        return list(getattr(inner, "supported_languages", None) or ["en"])

    @property
    def default_language(self) -> str:
        inner = self._inner()
        return getattr(inner, "default_language", None) or self.supported_languages[0]

    @property
    def supported_options(self) -> list[str] | None:
        inner = self._inner()
        return getattr(inner, "supported_options", None)

    @property
    def default_options(self) -> dict[str, Any] | None:
        inner = self._inner()
        return getattr(inner, "default_options", None)

    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        inner = self._inner()
        get = getattr(inner, "async_get_supported_voices", None)
        return get(language) if callable(get) else None

    # ── audio ──────────────────────────────────────────────────────────────
    async def _tone(self) -> tuple[str, bytes]:
        if self._chime is None:
            self._chime = await self.hass.async_add_executor_job(CHIME_FILE.read_bytes)
        return "wav", self._chime

    def _forward(self, language: str, options: dict[str, Any]) -> Any:
        if not self._inner_id:
            raise HomeAssistantError("Polyglot Assist (tone): choose the speech output behind the tone in Polyglot's options")
        return tts.async_create_stream(self.hass, self._inner_id, language=language, options=dict(options or {}))

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> tuple[str, bytes]:
        if message.strip() == CHIME_MARKER:
            return await self._tone()
        stream = self._forward(language, options)
        stream.async_set_message(message)
        return stream.extension, b"".join([chunk async for chunk in stream.async_stream_result()])

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        """Stream through to the real TTS; the marker is checked on the start of the text only."""
        gen = request.message_gen
        head = ""
        ended = False
        # read until the text can no longer be the marker (it is never streamed in pieces by the LLM)
        while len(head.strip()) <= len(CHIME_MARKER) and CHIME_MARKER.startswith(head.strip()):
            try:
                head += await gen.__anext__()
            except StopAsyncIteration:
                ended = True
                break
        if head.strip() == CHIME_MARKER and not ended:
            # the marker alone means tone; anything after it means: speak everything
            async for chunk in gen:
                head += chunk
                if chunk.strip():
                    break
            else:
                ended = True
        if head.strip() == CHIME_MARKER and ended:
            ext, data = await self._tone()

            async def tone_gen() -> AsyncGenerator[bytes]:
                yield data

            return TTSAudioResponse(ext, tone_gen())

        async def text_gen() -> AsyncGenerator[str]:
            if head:  # never read the marker aloud
                yield head.replace(CHIME_MARKER, "", 1) if head.lstrip().startswith(CHIME_MARKER) else head
            if not ended:
                async for chunk in gen:
                    yield chunk

        stream = self._forward(request.language, request.options)
        stream.async_set_message_stream(text_gen())
        return TTSAudioResponse(stream.extension, stream.async_stream_result())

