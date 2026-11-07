import importlib.util
import io
import itertools
import math
import struct
import wave
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "pg_tone", Path(__file__).parents[1] / "custom_components/polyglot_assist/tone.py")
tone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tone)

ALL = [dict(source="preset", count=c, contour=co, flow=f, pitch=p, timbre=tb)
       for c, co, f, p, tb in itertools.product(tone.COUNTS, tone.CONTOURS, tone.FLOWS, tone.PITCHES, tone.TIMBRES)]


def _read(wav: bytes) -> tuple[int, list[float]]:
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth()) == (1, 2)
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    return rate, [v / 32768 for (v,) in struct.iter_unpack("<h", frames)]


@pytest.mark.parametrize("t", ALL, ids=lambda t: "-".join(str(v) for v in t.values()))
def test_every_combination_is_a_short_clean_wav(t):
    rate, s = _read(tone.preset_wav(t))
    assert rate == tone.RATE
    assert len(s) / rate <= 0.9  # incl. lead-in, bell tail and trailing silence
    assert max(abs(x) for x in s) <= 0.9
    assert all(abs(x) < 1e-3 for x in s[: int(0.07 * rate)])  # lead-in silence: the start is never clipped
    assert abs(s[-1]) < 1e-3  # no click at the end


def _note_freqs(samples: list[float], n: int) -> list[float]:
    """Zero-crossing rate per note of a 'separate' tone (notes split at the silent gaps)."""
    notes, cur, quiet = [], [], 0
    for x in samples:
        if abs(x) < 1e-4:
            quiet += 1
            if quiet > 200 and cur:
                notes.append(cur)
                cur = []
        else:
            quiet = 0
            cur.append(x)
    if cur:
        notes.append(cur)
    out = []
    for note in notes[:n]:
        crossings = sum(1 for a, b in zip(note, note[1:]) if a < 0 <= b)
        out.append(crossings / (len(note) / tone.RATE))
    return out


@pytest.mark.parametrize("count", [2, 3])
def test_contour_is_audible(count):
    up = _note_freqs(tone.synthesize(dict(source="preset", count=count, contour="up", timbre="soft")), count)
    down = _note_freqs(tone.synthesize(dict(source="preset", count=count, contour="down", timbre="soft")), count)
    same = _note_freqs(tone.synthesize(dict(source="preset", count=count, contour="same", timbre="soft")), count)
    assert len(up) == len(down) == len(same) == count
    assert all(a < b for a, b in zip(up, up[1:]))
    assert all(a > b for a, b in zip(down, down[1:]))
    assert max(same) - min(same) < 0.03 * max(same)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_separate_has_count_notes_and_glide_has_no_gap(count):
    sep = tone.synthesize(dict(source="preset", count=count, flow="separate", timbre="soft"))
    assert len(_note_freqs(sep, 9)) == count
    glide = tone.synthesize(dict(source="preset", count=count, flow="glide", contour="up"))
    win = int(0.01 * tone.RATE)
    inner = glide[win: len(glide) - win * 5]
    for i in range(0, len(inner) - win, win):
        assert math.sqrt(sum(x * x for x in inner[i:i + win]) / win) > 0.01, i  # never silent inside


def test_volume_scales_the_peak():
    loud = max(abs(x) for x in tone.synthesize(dict(source="preset", volume=1.0)))
    quiet = max(abs(x) for x in tone.synthesize(dict(source="preset", volume=0.3)))
    assert loud == pytest.approx(0.89, abs=0.01)
    assert quiet == pytest.approx(0.89 * 0.3, abs=0.01)


def test_normalize_defaults_and_rejects_nonsense():
    assert tone.normalize(None)["source"] == "classic"  # nothing changes without the user
    n = tone.normalize(dict(count=7, contour="sideways", pitch="ultra", volume="loud", source="custom"))
    assert n["count"] == 2 and n["contour"] == "up" and n["pitch"] == "mid" and n["volume"] == 0.6
    assert n["source"] == "classic"  # custom without a stored file hash
    assert tone.normalize(dict(volume=5))["volume"] == 1.0
    assert tone.normalize(dict(source="custom", custom_hash="../../etc"))["source"] == "classic"


def test_hash_is_stable_and_changes_with_every_setting():
    base = tone.normalize(dict(source="preset"))
    seen = {tone.tone_hash(base)}
    assert tone.tone_hash(dict(base)) == tone.tone_hash(base)
    for key, other in (("count", 3), ("contour", "down"), ("flow", "glide"), ("pitch", "high"),
                       ("timbre", "bell"), ("volume", 0.9)):
        h = tone.tone_hash({**base, key: other})
        assert h not in seen, key
        seen.add(h)
    assert tone.tone_hash(dict(source="classic")) not in seen
    assert tone.tone_hash(dict(source="custom", custom_hash="0123456789ab")) not in seen


def _wav(seconds: float, rate=22050, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 5))) for i in range(n * channels)))
    return buf.getvalue()


def test_upload_checks():
    with pytest.raises(tone.ToneFileError, match="type"):
        tone.check_custom(b"RIFF....WAVE", "exe")
    with pytest.raises(tone.ToneFileError, match="empty"):
        tone.check_custom(b"", "wav")
    with pytest.raises(tone.ToneFileError, match="too_big"):
        tone.check_custom(b"RIFF" + b"\0" * (tone.MAX_CUSTOM_BYTES + 1), "wav")
    with pytest.raises(tone.ToneFileError, match="format"):
        tone.check_custom(b"<html>not audio</html>", "mp3")
    tone.check_custom(b"ID3\x04" + b"\0" * 20, "mp3")
    tone.check_custom(b"OggS" + b"\0" * 20, "ogg")
    tone.check_custom(b"fLaC" + b"\0" * 20, "flac")


def test_without_ffmpeg_wav_is_cut_and_normalised_other_formats_refused():
    out = tone.to_tone_wav(_wav(6.0), "wav", None)
    rate, s = _read(out)
    assert len(s) / rate == pytest.approx(tone.MAX_CUSTOM_WAV_S, abs=0.01)
    assert max(abs(x) for x in s) == pytest.approx(0.89, abs=0.01)
    with pytest.raises(tone.ToneFileError, match="convert"):
        tone.to_tone_wav(_wav(1.0, channels=2), "wav", None)
    with pytest.raises(tone.ToneFileError, match="convert"):
        tone.to_tone_wav(b"OggS" + b"\0" * 20, "ogg", None)
    with pytest.raises(tone.ToneFileError, match="silent"):
        tone.to_tone_wav(_silent(0.5), "wav", None)


def _silent(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * int(seconds * 22050))
    return buf.getvalue()


def test_storage_paths_cannot_escape(tmp_path):
    p = tone.write_custom(str(tmp_path), "../../evil/ID", _wav(0.2))
    assert p.parent == tmp_path / "polyglot_assist" / "tones"
    assert p.name == "evilID.wav"
    assert tone.read_custom(str(tmp_path), "../../evil/ID")[0] == "wav"
    assert tone.delete_custom(str(tmp_path), "../../evil/ID") is True
    assert tone.read_custom(str(tmp_path), "../../evil/ID") is None
    with pytest.raises(tone.ToneFileError):
        tone.custom_path(str(tmp_path), "../..")


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_with_ffmpeg_any_format_becomes_a_short_mono_wav(tmp_path):
    import shutil
    import subprocess

    src = tmp_path / "long.wav"
    src.write_bytes(_wav(8.0, rate=44100, channels=2))
    mp3 = tmp_path / "long.mp3"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-i", str(src), str(mp3)], check=True)
    out = tone.to_tone_wav(mp3.read_bytes(), "mp3", shutil.which("ffmpeg"))
    rate, s = _read(out)
    assert rate == tone.RATE
    assert len(s) / rate <= tone.MAX_CUSTOM_WAV_S + 0.05
    with pytest.raises(tone.ToneFileError, match="format"):
        tone.to_tone_wav(b"ID3\x04" + b"\0" * 400, "mp3", shutil.which("ffmpeg"))
