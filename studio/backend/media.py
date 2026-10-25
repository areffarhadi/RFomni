"""Media probing and signal rendering (waveform peaks, spectrogram tiles, video frames).

Audio is decoded once with PyAV (bundled FFmpeg, no system install needed) to 16 kHz mono,
cached as int16 .npy next to the project, and kept in a small in-memory LRU.
"""
import io
import threading
import wave
from collections import OrderedDict
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageOps

from .config import AUDIO_EXT, HOP, IMAGE_EXT, N_FFT, SR, TEXT_EXT, TEXT_MAX_BYTES, VIDEO_EXT

BROWSER_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac"}
BROWSER_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1", "hevc"}


def modality_of(path: Path):
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in TEXT_EXT:
        return "text"
    return None


def read_text(path: Path) -> str:
    """A text document as str: UTF-8 (a BOM is dropped), undecodable bytes replaced, line ends normalised."""
    raw = Path(path).read_bytes()
    if len(raw) > TEXT_MAX_BYTES:
        raise ValueError(f"text file larger than {TEXT_MAX_BYTES // 1_000_000} MB")
    return raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def probe(path: Path) -> dict:
    """Return modality + technical metadata. Raises ValueError for unsupported files."""
    path = Path(path)
    mod = modality_of(path)
    if mod is None:
        raise ValueError(f"unsupported file type: {path.suffix}")
    info = {"modality": mod, "size": path.stat().st_size, "duration": None}
    if mod == "text":
        txt = read_text(path)
        if not txt.strip():
            raise ValueError("empty text file")
        info.update(chars=len(txt), words=len(txt.split()), lines=txt.count("\n") + 1, has_audio=False, playable=True)
        return info
    if mod == "image":
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            info.update(width=im.width, height=im.height, format=(im.format or path.suffix[1:]).lower())
        return info
    with av.open(str(path)) as c:
        dur = float(c.duration) / av.time_base if c.duration else None
        a = c.streams.audio[0] if c.streams.audio else None
        v = c.streams.video[0] if c.streams.video else None
        if a is not None:
            info.update(sample_rate=a.codec_context.sample_rate, channels=a.codec_context.channels,
                        audio_codec=a.codec_context.name)
            if dur is None and a.duration:
                dur = float(a.duration * a.time_base)
        info["has_audio"] = a is not None
        if mod == "video":
            if v is None:
                info["modality"] = "audio"
            else:
                info.update(width=v.codec_context.width, height=v.codec_context.height,
                            fps=round(float(v.average_rate), 3) if v.average_rate else None,
                            video_codec=v.codec_context.name)
                if dur is None and v.duration:
                    dur = float(v.duration * v.time_base)
        if info["modality"] == "audio" and a is None:
            raise ValueError("no audio stream")
    info["duration"] = round(dur, 3) if dur else None
    if info["modality"] == "audio":
        info["playable"] = path.suffix.lower() in BROWSER_AUDIO
    else:
        info["playable"] = info.get("video_codec") in BROWSER_VIDEO_CODECS
    return info


def decode_audio(path: Path) -> np.ndarray:
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return np.zeros(0, np.float32)
        stream = c.streams.audio[0]
        res = av.AudioResampler(format="flt", layout="mono", rate=SR)
        parts = []
        for frame in c.decode(stream):
            for o in res.resample(frame):
                parts.append(o.to_ndarray().reshape(-1))
        for o in res.resample(None):
            parts.append(o.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
class _LRU:
    def __init__(self, max_items):
        self.max = max_items
        self.data = OrderedDict()
        self.lock = threading.Lock()
        self.key_locks = {}

    def key_lock(self, key):
        with self.lock:
            return self.key_locks.setdefault(key, threading.Lock())

    def get(self, key):
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.max:
                self.data.popitem(last=False)

    def drop_prefix(self, prefix):
        with self.lock:
            for k in [k for k in self.data if k[: len(prefix)] == prefix]:
                del self.data[k]


_audio = _LRU(6)
_spec = _LRU(4)


def get_audio(key, path: Path, cache_dir: Path) -> np.ndarray:
    """16 kHz mono float32 for a media file (decoded once, then cached on disk + memory)."""
    x = _audio.get(key)
    if x is not None:
        return x
    with _audio.key_lock(key):
        x = _audio.get(key)
        if x is not None:
            return x
        f = cache_dir / "audio16k.npy"
        if f.exists() and f.stat().st_mtime >= Path(path).stat().st_mtime:
            x = np.load(f).astype(np.float32) / 32767.0
        else:
            x = decode_audio(path)
            np.save(f, np.clip(x * 32767.0, -32768, 32767).astype(np.int16))
        _audio.put(key, x)
        return x


def drop_caches(key_prefix):
    _audio.drop_prefix(key_prefix)
    _spec.drop_prefix(key_prefix)


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------
def peaks(x: np.ndarray, start: float, end: float, n: int) -> dict:
    n = int(max(1, min(n, 8192)))
    a, b = start * SR, end * SR
    mins = np.zeros(n, np.float32)
    maxs = np.zeros(n, np.float32)
    if len(x) and b > a:
        edges = np.linspace(a, b, n + 1)
        if (b - a) / n >= 2:
            e = np.clip(np.floor(edges).astype(np.int64), 0, len(x))
            lo, hi = e[:-1], e[1:]
            ok = hi > lo
            if ok.any():
                mins[ok] = _binreduce(np.minimum, x, lo[ok], hi[ok])
                maxs[ok] = _binreduce(np.maximum, x, lo[ok], hi[ok])
        else:
            centers = ((edges[:-1] + edges[1:]) / 2).astype(np.int64)
            ok = (centers >= 0) & (centers < len(x))
            mins[ok] = maxs[ok] = x[centers[ok]]
    return {"min": np.round(mins, 4).tolist(), "max": np.round(maxs, 4).tolist()}


def _binreduce(fn, x, lo, hi):
    """fn over contiguous bins [lo[i], hi[i]); reduceat runs each bin to the next start,
    so only the last bin needs its end fixed."""
    out = fn.reduceat(x, lo).astype(np.float32)
    out[-1] = fn.reduce(x[lo[-1]: hi[-1]])
    return out


def audio_peak(x: np.ndarray) -> float:
    if not len(x):
        return 1.0
    step = max(1, len(x) // 4_000_000)
    a = np.abs(x[::step])
    return float(max(1e-4, 0.5 * np.max(a) + 0.5 * np.percentile(a, 99.99)))


# ---------------------------------------------------------------------------
# Spectrogram
# ---------------------------------------------------------------------------
_WIN = np.hanning(N_FFT).astype(np.float32)


def _frames_db(x: np.ndarray, centers: np.ndarray) -> np.ndarray:
    if not len(x):
        return np.full((len(centers), N_FFT // 2 + 1), -100.0, np.float32)
    half = N_FFT // 2
    idx = centers[:, None] - half + np.arange(N_FFT)[None, :]
    valid = (idx >= 0) & (idx < len(x))
    fr = np.where(valid, x[np.clip(idx, 0, len(x) - 1)], 0.0).astype(np.float32)
    spec = np.fft.rfft(fr * _WIN, axis=1)
    p = spec.real ** 2 + spec.imag ** 2
    return (10.0 * np.log10(p + 1e-10)).astype(np.float32)


def get_full_spec(key, x: np.ndarray, cache_dir: Path):
    """Full-file 10 ms-hop power spectrogram in dB (frames x 257, float16) + reference max."""
    hit = _spec.get(key)
    if hit is not None:
        return hit
    with _spec.key_lock(key):
        hit = _spec.get(key)
        if hit is not None:
            return hit
        f = cache_dir / "spec_db.npy"
        a = cache_dir / "audio16k.npy"
        if f.exists() and a.exists() and f.stat().st_mtime >= a.stat().st_mtime:
            full = np.load(f)
        else:
            centers = np.arange(0, max(1, len(x)), HOP, dtype=np.int64)
            blocks = [
                _frames_db(x, centers[i: i + 4096]).astype(np.float16)
                for i in range(0, len(centers), 4096)
            ]
            full = np.concatenate(blocks) if blocks else np.full((1, N_FFT // 2 + 1), -100, np.float16)
            np.save(f, full)
        vmax = float(np.percentile(full.max(axis=1).astype(np.float32), 99.5)) if len(full) else 0.0
        hit = (full, vmax)
        _spec.put(key, hit)
        return hit


_MEL_CACHE = {}


def _mel_fb(n_mels: int, fmax: float) -> np.ndarray:
    k = (n_mels, round(fmax))
    if k in _MEL_CACHE:
        return _MEL_CACHE[k]

    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    nb = N_FFT // 2 + 1
    freqs = np.linspace(0, SR / 2, nb)
    mpts = np.linspace(hz2mel(0), hz2mel(fmax), n_mels + 2)
    hz = mel2hz(mpts)
    fb = np.zeros((n_mels, nb), np.float32)
    for i in range(n_mels):
        l, c, r = hz[i], hz[i + 1], hz[i + 2]
        up = (freqs - l) / max(c - l, 1e-6)
        down = (r - freqs) / max(r - c, 1e-6)
        fb[i] = np.maximum(0, np.minimum(up, down))
    _MEL_CACHE[k] = fb
    return fb


def _lut(name: str) -> np.ndarray:
    anchors = {
        "magma": ["#000004", "#1c1044", "#4f127b", "#812581", "#b5367a", "#e55064", "#fb8761", "#fec287", "#fcfdbf"],
        "viridis": ["#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c", "#28ae80", "#5ec962", "#addc30", "#fde725"],
        "inferno": ["#000004", "#1f0c48", "#550f6d", "#88226a", "#ba3655", "#e35933", "#f98e09", "#f6d746", "#fcffa4"],
        "gray": ["#ffffff", "#000000"],  # Praat style: dark = energy
        "ice": ["#06080d", "#0b2a3d", "#0e4f6b", "#1b7a8c", "#3fa7a3", "#86d0b8", "#d7f3e3"],
    }.get(name) or ["#000004", "#fcfdbf"]
    cols = np.array([[int(h[i: i + 2], 16) for i in (1, 3, 5)] for h in anchors], np.float32)
    pos = np.linspace(0, 1, len(cols))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, pos, cols[:, j]) for j in range(3)], axis=1).astype(np.uint8)


def spectrogram_png(key, x, cache_dir, start, end, w, h, scale="linear", cmap="magma",
                    fmax=8000.0, drange=70.0, fmt="png") -> bytes:
    w = int(max(1, min(w, 4096)))
    h = int(max(8, min(h, 1024)))
    fmax = float(max(500.0, min(fmax, SR / 2)))
    full, vmax = get_full_spec(key, x, cache_dir)
    nfr = len(full)
    spc = (end - start) * SR / w  # samples per column
    nbins = N_FFT // 2 + 1
    cols = np.full((w, nbins), -120.0, np.float32)
    if spc >= HOP:
        edges = np.floor(np.linspace(start * SR / HOP, end * SR / HOP, w + 1)).astype(np.int64)
        lo = np.clip(edges[:-1], 0, nfr)
        hi = np.clip(np.maximum(edges[1:], edges[:-1] + 1), 0, nfr)
        ok = hi > lo
        if ok.any():
            # mean (in dB) over the frames of each column; reduceat runs each column to the
            # next column start, so only the final column needs its end fixed
            lo_ok, hi_ok = lo[ok], hi[ok]
            base = lo_ok[0]
            chunk = full[base: hi_ok[-1]].astype(np.float32)
            red = np.add.reduceat(chunk, lo_ok - base, axis=0)
            counts = (np.append(lo_ok[1:], hi_ok[-1]) - lo_ok).astype(np.float32)
            cols[ok] = red / np.maximum(counts, 1)[:, None]
    else:
        centers = (start * SR + (np.arange(w) + 0.5) * spc).astype(np.int64)
        ok = (centers >= 0) & (centers < len(x))
        if ok.any():
            cols[ok] = _frames_db(x, centers[ok])
    if scale == "mel":
        n_mels = 128
        p = np.power(10.0, cols / 10.0)
        cols = 10.0 * np.log10(p @ _mel_fb(n_mels, fmax).T + 1e-10)
        nb = n_mels
    else:
        nb = int(round(fmax / (SR / 2) * (nbins - 1))) + 1
        cols = cols[:, :nb]
    rows = np.minimum((np.arange(h) + 0.5) * nb / h, nb - 1).astype(np.int64)
    img = cols[:, rows].T[::-1]
    norm = np.clip((img - (vmax - drange)) / drange, 0, 1)
    rgb = _lut(cmap)[(norm * 255).astype(np.uint8)]
    buf = io.BytesIO()
    if fmt == "jpeg":
        Image.fromarray(rgb, "RGB").save(buf, "JPEG", quality=88)
    else:
        Image.fromarray(rgb, "RGB").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Images, frames, thumbnails
# ---------------------------------------------------------------------------
def video_frame(path: Path, t: float, height: int = 72) -> Image.Image:
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        tb = float(v.time_base)
        start = v.start_time or 0
        target = int(max(0.0, t) / tb) + start
        c.seek(target, stream=v, backward=True, any_frame=False)
        best = None
        for i, frame in enumerate(c.decode(v)):
            best = frame
            if frame.pts is None or frame.pts >= target or i > 120:
                break
        if best is None:
            raise ValueError("no frame")
        img = best.to_image()
    if height:
        w = max(1, round(img.width * height / img.height))
        img = img.resize((w, height), Image.BILINEAR)
    return img


def jpeg_bytes(img: Image.Image, quality=82) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def thumbnail(path: Path, modality: str, duration=None, size=240) -> bytes:
    if modality == "image":
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            return jpeg_bytes(im)
    if modality == "video":
        t = min(1.0, (duration or 0) * 0.1)
        img = video_frame(path, t, height=0)
        img.thumbnail((size, size))
        return jpeg_bytes(img)
    raise ValueError("no thumbnail for audio")


def wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(x * 32767.0, -32768, 32767).astype("<i2").tobytes())
    return buf.getvalue()
