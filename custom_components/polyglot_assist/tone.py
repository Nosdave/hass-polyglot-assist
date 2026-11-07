"""Confirmation tones: a small synthesiser and the user's own tone file.

Pure Python (``math``/``wave``), fast enough for low-end hardware: a tone is
at most ~0.7 s at 22.05 kHz, i.e. ~15k samples, generated once and cached.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

RATE = 22050
LEAD_S = 0.08  # some satellites clip the first milliseconds of a stream

COUNTS = (1, 2, 3)
CONTOURS = ("up", "down", "same")
FLOWS = ("separate", "glide")
PITCHES = {"low": 440.0, "mid": 660.0, "high": 880.0}
TIMBRES = ("soft", "bright", "bell")
SOURCES = ("classic", "preset", "custom")  # classic = the tone shipped before 0.9.1 (sounds/chime.wav)
CLASSIC_FILE = Path(__file__).parent / "sounds" / "chime.wav"
DEFAULT_TONE: dict[str, Any] = {
    "source": "classic", "count": 2, "contour": "up", "flow": "separate",
    "pitch": "mid", "timbre": "soft", "volume": 0.6,
}

# own tone file: any of these is converted ONCE to a short mono WAV (ffmpeg) and stored as <entry>.wav
CUSTOM_EXTS = ("wav", "mp3", "ogg", "flac")
MAX_CUSTOM_BYTES = 512 * 1024
MAX_CUSTOM_WAV_S = 4.0  # longer files are cut at upload

_STEPS = (1.0, 1.26, 1.5)  # root, major third, fifth
NOTE_S, GAP_S, GLIDE_NOTE_S = 0.11, 0.06, 0.16
ATTACK_S, RELEASE_S = 0.008, 0.04
BELL_TAIL_S = 0.25  # a bell must ring out after its last note


def normalize(tone: dict[str, Any] | None) -> dict[str, Any]:
    """Fill defaults and clamp; unknown values fall back to the default."""
    t = {**DEFAULT_TONE, **(tone or {})}
    out = dict(DEFAULT_TONE)
    out["source"] = t["source"] if t["source"] in SOURCES else DEFAULT_TONE["source"]
    out["count"] = t["count"] if t["count"] in COUNTS else DEFAULT_TONE["count"]
    out["contour"] = t["contour"] if t["contour"] in CONTOURS else DEFAULT_TONE["contour"]
    out["flow"] = t["flow"] if t["flow"] in FLOWS else DEFAULT_TONE["flow"]
    out["pitch"] = t["pitch"] if t["pitch"] in PITCHES else DEFAULT_TONE["pitch"]
    out["timbre"] = t["timbre"] if t["timbre"] in TIMBRES else DEFAULT_TONE["timbre"]
    ch = t.get("custom_hash")
    if isinstance(ch, str) and len(ch) == 12 and all(c in "0123456789abcdef" for c in ch):
        out["custom_hash"] = ch
    elif out["source"] == "custom":
        out["source"] = DEFAULT_TONE["source"]  # no stored file known
    try:
        out["volume"] = round(min(1.0, max(0.2, float(t["volume"]))), 2)
    except (TypeError, ValueError):
        out["volume"] = DEFAULT_TONE["volume"]
    return out


def frequencies(tone: dict[str, Any]) -> list[float]:
    """The pitch of each note (a single gliding note still moves: root ↔ fifth)."""
    base = PITCHES[tone["pitch"]]
    n = tone["count"]
    if tone["contour"] == "same":
        return [base] * n
    steps = {1: [1.0, 1.5], 2: [1.0, 1.5], 3: [1.0, 1.26, 1.5]}[n]
    if tone["contour"] == "down":
        steps = steps[::-1]  # start high, end on the root
    return [base * s for s in steps]


def _voice(phase: float, timbre: str, t_note: float) -> float:
    """One sample of the instrument at ``phase`` (radians), ``t_note`` s into the note."""
    if timbre == "bright":
        return 0.7 * math.sin(phase) + 0.2 * math.sin(2 * phase) + 0.1 * math.sin(3 * phase)
    if timbre == "bell":
        decay = math.exp(-t_note * 9.0)
        return decay * (0.75 * math.sin(phase) + 0.25 * math.sin(2.76 * phase) * math.exp(-t_note * 14.0))
    return math.sin(phase)


def _env(t: float, length: float) -> float:
    a = min(1.0, t / ATTACK_S) if ATTACK_S else 1.0
    r = min(1.0, max(0.0, (length - t) / RELEASE_S))
    return a * r


def synthesize(tone: dict[str, Any] | None) -> list[float]:
    """The tone as float samples in [-1, 1] (without the lead-in silence)."""
    tone = normalize(tone)
    freqs = frequencies(tone)
    out: list[float] = []
    if tone["flow"] == "separate" or (tone["contour"] == "same" and tone["count"] == 1):
        notes = freqs[: tone["count"]] if tone["count"] > 1 else [PITCHES[tone["pitch"]]]
        for i, f in enumerate(notes):
            length = NOTE_S + (BELL_TAIL_S if tone["timbre"] == "bell" and i == len(notes) - 1 else 0.0)
            n = int(length * RATE)
            phase, step = 0.0, 2 * math.pi * f / RATE
            for k in range(n):
                t = k / RATE
                out.append(_env(t, length) * _voice(phase, tone["timbre"], t))
                phase += step
            if i < len(notes) - 1:
                out.extend([0.0] * int(GAP_S * RATE))
    else:
        # one continuous tone; frequency glides (log) through the sequence, phase never jumps
        length = GLIDE_NOTE_S * max(2, tone["count"])
        n = int(length * RATE)
        phase = 0.0
        segs = max(1, len(freqs) - 1)
        for k in range(n):
            t = k / RATE
            x = k / max(1, n - 1)
            if len(freqs) > 1 and tone["contour"] != "same":
                pos = x * segs
                i = min(segs - 1, int(pos))
                frac = pos - i
                frac = frac * frac * (3 - 2 * frac)  # ease between notes
                f = freqs[i] * (freqs[i + 1] / freqs[i]) ** frac
            else:
                f = freqs[0]
            amp = _env(t, length)
            if tone["contour"] == "same" and tone["count"] > 1:
                # soft pulses without gaps ("wah-wah")
                amp *= 0.55 - 0.45 * math.cos(2 * math.pi * tone["count"] * x)
            out.append(amp * _voice(phase, tone["timbre"], t * 0.5))  # bell decays slower over the glide
            phase += 2 * math.pi * f / RATE
    peak = max((abs(s) for s in out), default=0.0) or 1.0
    gain = 0.89 * tone["volume"] / peak  # ≤ -1 dBFS at full volume
    return [s * gain for s in out]


def to_wav(samples: list[float]) -> bytes:
    lead = [0.0] * int(LEAD_S * RATE)
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in lead + samples + [0.0] * 220)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)
    return buf.getvalue()


def preset_wav(tone: dict[str, Any] | None) -> bytes:
    return to_wav(synthesize(tone))


def data_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def tone_hash(tone: dict[str, Any] | None) -> str:
    """Stable short hash of what will sound — part of the TTS cache key."""
    t = normalize(tone)
    if t["source"] == "custom":
        return "c" + t["custom_hash"][:11]
    if t["source"] == "classic":
        return "classic"
    return hashlib.sha256(json.dumps({k: v for k, v in t.items() if k not in ("source", "custom_hash")},
                                      sort_keys=True).encode()).hexdigest()[:12]


_CACHE: dict[tuple[str, str], tuple[str, bytes]] = {}


async def async_tone_audio(hass: Any, entry_id: str, tone: dict[str, Any] | None) -> tuple[str, bytes]:
    """(extension, bytes) of the tone that will sound; the own file falls back to the preset."""
    t = normalize(tone)
    key = (entry_id, tone_hash(t))
    if key in _CACHE:
        return _CACHE[key]
    result: tuple[str, bytes] | None = None
    if t["source"] == "custom":
        found = await hass.async_add_executor_job(read_custom, hass.config.config_dir, entry_id)
        if found is not None and data_hash(found[1]) == t["custom_hash"]:
            result = found
    if result is None and t["source"] == "classic":
        result = ("wav", await hass.async_add_executor_job(CLASSIC_FILE.read_bytes))
    if result is None:
        result = ("wav", await hass.async_add_executor_job(preset_wav, {**t, "source": "preset"}))
    if len(_CACHE) > 16:
        _CACHE.clear()
    _CACHE[key] = result
    return result


# ── own tone file ─────────────────────────────────────────────────────────
class ToneFileError(ValueError):
    """The uploaded file cannot be a tone (``args[0]`` is a short code)."""


def check_custom(data: bytes, ext: str) -> None:
    if ext not in CUSTOM_EXTS:
        raise ToneFileError("type")
    if not data:
        raise ToneFileError("empty")
    if len(data) > MAX_CUSTOM_BYTES:
        raise ToneFileError("too_big")
    head = data[:12]
    ok = {
        "wav": head[:4] == b"RIFF" and head[8:12] == b"WAVE",
        "mp3": head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0),
        "ogg": head[:4] == b"OggS",
        "flac": head[:4] == b"fLaC",
    }[ext]
    if not ok:
        raise ToneFileError("format")


def tones_dir(config_dir: str) -> Path:
    return Path(config_dir) / "polyglot_assist" / "tones"


def _safe(entry_id: str) -> str:
    safe = "".join(c for c in entry_id if c.isalnum())
    if not safe:
        raise ToneFileError("entry")
    return safe


def custom_path(config_dir: str, entry_id: str) -> Path:
    return tones_dir(config_dir) / f"{_safe(entry_id)}.wav"


def to_tone_wav(data: bytes, ext: str, ffmpeg: str | None) -> bytes:
    """Any accepted file → mono 22.05 kHz 16-bit WAV, at most MAX_CUSTOM_WAV_S, peak-normalised (executor).

    Converting once at upload means a long or odd file never reaches playback,
    and only one well-known format is ever played or previewed.
    """
    import subprocess  # noqa: PLC0415

    check_custom(data, ext)
    if ffmpeg:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argument list, data via stdin
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", {"wav": "wav", "mp3": "mp3", "ogg": "ogg", "flac": "flac"}[ext],
                 "-i", "pipe:0", "-t", str(MAX_CUSTOM_WAV_S), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"],
                input=data, capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise ToneFileError("convert") from err
        if proc.returncode != 0 or not proc.stdout:
            raise ToneFileError("format")
        pcm = proc.stdout
    else:
        if ext != "wav":
            raise ToneFileError("convert")  # without ffmpeg only plain 16-bit mono WAV
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise ToneFileError("convert")
            rate = w.getframerate()
            pcm = w.readframes(int(MAX_CUSTOM_WAV_S * rate))
        samples = [v / 32768 for (v,) in struct.iter_unpack("<h", pcm[: len(pcm) // 2 * 2])]
        return _wav_at(samples, rate)
    samples = [v / 32768 for (v,) in struct.iter_unpack("<h", pcm[: len(pcm) // 2 * 2])]
    return _wav_at(samples, RATE)


def _wav_at(samples: list[float], rate: int) -> bytes:
    if not samples or max(abs(s) for s in samples) < 1e-3:
        raise ToneFileError("silent")
    peak = max(abs(s) for s in samples)
    gain = 0.89 / peak
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * gain)) * 32767)) for s in samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return buf.getvalue()


def write_custom(config_dir: str, entry_id: str, wav: bytes) -> Path:
    """Store the converted tone atomically (executor)."""
    base = tones_dir(config_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = custom_path(config_dir, entry_id)
    tmp = target.with_suffix(".wav.tmp")
    tmp.write_bytes(wav)
    tmp.replace(target)
    return target


def delete_custom(config_dir: str, entry_id: str) -> bool:
    p = custom_path(config_dir, entry_id)
    if p.is_file():
        p.unlink()
        return True
    return False


def read_custom(config_dir: str, entry_id: str) -> tuple[str, bytes] | None:
    p = custom_path(config_dir, entry_id)
    return ("wav", p.read_bytes()) if p.is_file() else None
