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
from homeassistant.core import HomeAssistant, callback
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


INNER_OPTION = "polyglot_inner_tts"  # part of the cache key: changing the inner TTS must not replay old audio


def _silence_wav() -> bytes:
    import io  # noqa: PLC0415
    import wave  # noqa: PLC0415

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 2205)  # 0.1 s
    return buf.getvalue()


class PolyglotToneTTS(TextToSpeechEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "tone"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tone_tts"
        self._chime: bytes | None = None
        self._unsub_state = None

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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._bind()

        async def _options_changed(_hass: HomeAssistant, _entry: ConfigEntry) -> None:
            self._bind()  # another inner TTS may have been chosen
            self.async_write_ha_state()

        self.async_on_remove(self._entry.add_update_listener(_options_changed))
        self.async_on_remove(self._unbind)

    @callback
    def _unbind(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None

    @callback
    def _bind(self) -> None:
        """Follow the state of the CURRENT inner TTS (it may load later or go away)."""
        from homeassistant.helpers.event import async_track_state_change_event  # noqa: PLC0415

        self._unbind()
        if self._inner_id:
            self._unsub_state = async_track_state_change_event(
                self.hass, [self._inner_id], lambda _event: self.async_write_ha_state()
            )

    @property
    def available(self) -> bool:
        inner = self._inner()
        return inner is not None and getattr(inner, "available", True) is not False

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
        return [*(getattr(inner, "supported_options", None) or []), INNER_OPTION]

    @property
    def default_options(self) -> dict[str, Any] | None:
        inner = self._inner()
        return {**(getattr(inner, "default_options", None) or {}), INNER_OPTION: self._inner_id or ""}

    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        inner = self._inner()
        get = getattr(inner, "async_get_supported_voices", None)
        return get(language) if callable(get) else None

    # ── audio ──────────────────────────────────────────────────────────────
    async def _tone(self) -> tuple[str, bytes]:
        if self._chime is None:
            self._chime = await self.hass.async_add_executor_job(CHIME_FILE.read_bytes)
        return "wav", self._chime

    async def _inner_stream(self, language: str, options: dict[str, Any], text_gen: AsyncGenerator[str]) -> TTSAudioResponse:
        """Ask the inner TTS directly: its own format comes back and HA converts once."""
        inner = self._inner()
        if inner is None:
            raise HomeAssistantError("Polyglot Assist (tone): choose the speech output behind the tone in Polyglot's options")
        opts = {k: v for k, v in (options or {}).items() if k != INNER_OPTION}
        if isinstance(inner, TextToSpeechEntity):
            return await inner.internal_async_stream_tts_audio(TTSAudioRequest(language, opts, text_gen))
        message = "".join([chunk async for chunk in text_gen])  # legacy provider
        extension, data = await inner.async_get_tts_audio(message, language, opts)
        if extension is None or data is None:
            raise HomeAssistantError(f"No TTS from {self._inner_id}")

        async def data_gen() -> AsyncGenerator[bytes]:
            yield data

        return TTSAudioResponse(extension, data_gen())

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> tuple[str, bytes]:
        if message.strip() == CHIME_MARKER:
            return await self._tone()
        if not message.strip():
            return "wav", _silence_wav()

        async def one() -> AsyncGenerator[str]:
            yield message

        response = await self._inner_stream(language, options, one())
        return response.extension, b"".join([chunk async for chunk in response.data_gen])

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        """Stream through to the real TTS; only a text that is exactly the marker becomes the tone."""
        gen = request.message_gen
        head = ""
        ended = False
        # read while the text could still be the marker (Polyglot sends it as one piece)
        while len(head.strip()) <= len(CHIME_MARKER) and CHIME_MARKER.startswith(head.strip()):
            try:
                head += await gen.__anext__()
            except StopAsyncIteration:
                ended = True
                break
        if ended and head.strip() in (CHIME_MARKER, ""):
            ext, data = await self._tone() if head.strip() else ("wav", _silence_wav())

            async def fixed() -> AsyncGenerator[bytes]:
                yield data

            return TTSAudioResponse(ext, fixed())

        async def text_gen() -> AsyncGenerator[str]:
            if head:  # never read the marker aloud
                yield head.replace(CHIME_MARKER, "", 1) if head.lstrip().startswith(CHIME_MARKER) else head
            if not ended:
                async for chunk in gen:
                    yield chunk

        return await self._inner_stream(request.language, request.options, text_gen())
