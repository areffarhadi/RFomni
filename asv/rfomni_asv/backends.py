"""Two ways to turn speech into a voiceprint, behind one interface.

The weights are WeSpeaker's ``voxblink2_samresnet34_ft`` (SimAM-ResNet34, 256-d) either
way; the backends differ only in what you have to install to run them.

``onnx``   onnxruntime + torchaudio's Kaldi fbank. This is the default because it is the
           one a colleague can install without fighting wespeaker's dependency tree
           (s3prl still imports ``distutils``, removed in Python 3.12).
``torch``  wespeaker's own loader. Keep it when you already have that environment, or
           when you want to swap in a different WeSpeaker checkpoint.

The two agree to within float noise — ``tests/test_backends.py`` asserts cosine > 0.999
between them on the same audio, and it measures 1.0.

FEATURES. Both paths reproduce wespeaker's ``compute_features``: 80-bin Kaldi fbank,
25 ms frames, 10 ms shift, Hamming window, then cepstral mean normalisation over time.
CMN is why the input scale does not matter — a gain change is a constant offset in the
log-mel domain and the mean subtraction removes it (measured: doubling the amplitude
moves the embedding by nothing, cosine 1.0000). The asymmetry is that attenuating a
signal also requantises it, and *that* costs a little: a quarter amplitude scores 0.949
against the original. So a quiet microphone is worth fixing at the microphone, not here.
"""

from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
#: Where the checkpoints live. Defaults to the copy inside this repository; set
#: RFOMNI_ASV_MODELS to read them from a shared directory instead, so several
#: checkouts on one box share one copy rather than each carrying 578 MiB.
MODELS_ROOT = Path(
    os.environ.get("RFOMNI_ASV_MODELS")
    or Path(__file__).resolve().parent.parent / "models"
)


@dataclass(frozen=True)
class ModelSpec:
    """A checkpoint this repo ships, with the numbers that were measured for it.

    ``threshold`` is not a hyperparameter to taste — it is where that model's false
    accepts drop into the range an owner gate needs, measured on VoxCeleb1-O. It differs
    per model because the score distributions do, so selecting a model has to carry its
    threshold with it or the gate silently changes behaviour.
    """

    name: str
    directory: str
    dim: int
    threshold: float
    #: backends this checkpoint can run on, in preference order
    backends: tuple[str, ...]
    onnx_file: str = "model.onnx"
    #: Which feature frontend this checkpoint was trained with. Getting this wrong does
    #: not raise — it returns near-identical embeddings for everyone — so it belongs in
    #: the spec rather than in the caller's head. See FRONTENDS.
    frontend: str = "kaldi_fbank"
    # measured: benchmarks/RESULTS.md, 4 CPU threads, full VoxCeleb1-O
    eer_pct: float = 0.0
    mindcf: float = 0.0
    cpu_s_per_s: float = 0.0
    #: Sustained batch throughput on an RTX 6000 Ada via the torch backend. 0 where the
    #: checkpoint has no GPU weights here. A live agent calling it sporadically sees
    #: ~16 ms per utterance rather than this figure — see benchmarks/RESULTS.md.
    gpu_s_per_s: float = 0.0
    summary: str = ""

    @property
    def path(self) -> Path:
        return MODELS_ROOT / self.directory


#: Everything selectable through SPK_MODEL.
REGISTRY: dict[str, ModelSpec] = {
    "samresnet34": ModelSpec(
        name="samresnet34",
        directory="voxblink2_samresnet34_ft",
        dim=256,
        threshold=0.45,
        backends=("onnx", "torch"),
        eer_pct=0.415,
        mindcf=0.030,
        cpu_s_per_s=27.7,
        gpu_s_per_s=232.0,
        summary="WeSpeaker voxblink2_samresnet34_ft — the accurate one, and the default",
    ),
    "ecapa512": ModelSpec(
        name="ecapa512",
        directory="voxceleb_ecapa512_lm",
        dim=192,
        threshold=0.50,
        backends=("onnx",),
        eer_pct=0.883,
        mindcf=0.109,
        cpu_s_per_s=327.0,
        summary="WeSpeaker voxceleb_ECAPA512_LM — 11.8x faster on CPU, looser gate",
    ),
    "ecapa1024": ModelSpec(
        name="ecapa1024",
        directory="voxblink2_ecapa1024",
        dim=192,
        threshold=0.45,
        backends=("onnx",),
        frontend="voxblink2_mel",
        eer_pct=0.675,
        mindcf=0.053,
        cpu_s_per_s=128.0,
        summary="VoxBlink2 ECAPA-TDNN 1024 — 4.6x faster than the default, close accuracy",
    ),
}

DEFAULT_MODEL = os.environ.get("SPK_MODEL", "samresnet34")
#: Kept for callers that predate the registry.
DEFAULT_MODEL_DIR = str(REGISTRY["samresnet34"].path)


def get_spec(name: str | None = None) -> ModelSpec:
    name = (name or DEFAULT_MODEL).lower()
    if name not in REGISTRY:
        raise ValueError(f"unknown model {name!r} — choose from {sorted(REGISTRY)}")
    return REGISTRY[name]


def _install_distutils_shim() -> None:
    """s3prl, pulled in transitively by wespeaker, still does ``from distutils.util
    import strtobool``, which Python 3.12 removed. Provide just that symbol rather than
    pinning setuptools back to a version that ships the shim."""
    if "distutils" in sys.modules:
        return
    d, u = types.ModuleType("distutils"), types.ModuleType("distutils.util")

    def strtobool(value: str) -> int:
        v = str(value).strip().lower()
        if v in ("y", "yes", "t", "true", "on", "1"):
            return 1
        if v in ("n", "no", "f", "false", "off", "0"):
            return 0
        raise ValueError(f"invalid truth value {value!r}")

    u.strtobool = strtobool
    d.util = u
    sys.modules["distutils"], sys.modules["distutils.util"] = d, u


def _to_float_mono(pcm: bytes, sr: int) -> np.ndarray:
    """PCM16 mono bytes -> float32 at 16 kHz.

    Linear interpolation is enough for resampling here: this model was trained on 16 kHz
    and callers already hand us 16 kHz in every deployment we run. It exists so an odd
    sample rate degrades instead of crashing.
    """
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if sr != SAMPLE_RATE:
        n = int(round(len(x) * SAMPLE_RATE / sr))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def _unit(v: np.ndarray) -> np.ndarray:
    """Unit-norm, so cosine similarity downstream is a plain dot product."""
    v = np.asarray(v).reshape(-1).astype(np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _kaldi_fbank(x: np.ndarray) -> np.ndarray:
    """WeSpeaker's frontend: 80-bin Kaldi fbank, 25/10 ms, Hamming, then CMN."""
    import torch
    import torchaudio.compliance.kaldi as kaldi

    feat = kaldi.fbank(
        torch.from_numpy(x).unsqueeze(0),
        num_mel_bins=80,
        frame_length=25,
        frame_shift=10,
        sample_frequency=SAMPLE_RATE,
        window_type="hamming",
    )
    feat = feat - feat.mean(dim=0)
    return feat.unsqueeze(0).numpy()          # [1, T, 80]


_MEL = None


def _voxblink2_mel(x: np.ndarray) -> np.ndarray:
    """VoxBlink2's frontend, which is a different animal from the WeSpeaker one.

    From ScriptsForVoxBlink2: the waveform is std-normalised and pre-emphasised in the
    *dataset*, then the model applies a torchaudio MelSpectrogram with default Hann window
    and no f_min/f_max, log(x + 1e-6), and mean subtraction over time.

    Feeding this model a Kaldi fbank instead does not error; it returns embeddings whose
    cosine is 0.999 for genuine and impostor pairs alike.
    """
    import torch
    from torchaudio import transforms

    global _MEL
    if _MEL is None:
        _MEL = transforms.MelSpectrogram(
            sample_rate=SAMPLE_RATE, n_fft=512, win_length=400, hop_length=160, n_mels=80
        )
    v = x.astype(np.float64)
    sd = float(np.std(v))
    if sd != 0:
        v = (v - np.mean(v)) / sd
    v = np.append(v[0], v[1:] - 0.97 * v[:-1]).astype(np.float32)   # preemphasis(0.97)
    with torch.inference_mode():
        f = _MEL(torch.from_numpy(v).unsqueeze(0))
        f = torch.log(f + 1e-6)
        f = f - f.mean(axis=2, keepdim=True)
    return f.numpy()                          # [1, 80, T]


FRONTENDS = {"kaldi_fbank": _kaldi_fbank, "voxblink2_mel": _voxblink2_mel}


class Embedder:
    """Common surface: ``embed(pcm, sr) -> unit-norm 256-d vector``."""

    name = "base"
    dim = 256
    #: Set by load_embedder when the checkpoint came from the registry. It is what lets
    #: /health tell a client which threshold this model was measured at.
    spec: "ModelSpec | None" = None

    def embed(self, pcm: bytes, sr: int = SAMPLE_RATE) -> np.ndarray:
        raise NotImplementedError

    def info(self) -> dict:
        return {"backend": self.name, "dim": self.dim}

    def spec_info(self) -> dict:
        if self.spec is None:
            return {}
        return {
            "model_name": self.spec.name,
            "threshold": self.spec.threshold,
            "eerPct": self.spec.eer_pct,
        }


class OnnxEmbedder(Embedder):
    """onnxruntime graph fed by torchaudio's Kaldi fbank.

    The exported graph takes features, not audio, so the frontend has to match what the
    checkpoint was trained with exactly — see the module docstring.
    """

    name = "onnx"

    def __init__(
        self,
        model_dir: str = DEFAULT_MODEL_DIR,
        device: str = "cpu",
        model_file: str | None = None,
        threads: int = 0,
        frontend: str = "kaldi_fbank",
    ):
        import onnxruntime as ort

        model_file = model_file or os.environ.get("SPK_ONNX_FILE", "model.onnx")
        path = Path(model_dir) / model_file
        if not path.exists():
            raise FileNotFoundError(f"no {model_file} in {model_dir}")
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if device.startswith("cuda")
            else ["CPUExecutionProvider"]
        )
        available = set(ort.get_available_providers())
        self._providers = [p for p in providers if p in available] or ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        # THREADS. onnxruntime's default is every core, and on a big box that is actively
        # worse: measured on VoxCeleb1-O, 128 cores gave 124 ms/utt against 100 ms at 32,
        # because these tensors are small enough that synchronisation costs more than the
        # extra parallelism buys.
        #
        # The default here is 4, which is a deployment choice rather than a peak-speed one:
        # this block is meant to sit beside a large model in a live service, so it should
        # take a small, predictable slice of the machine. 4 threads embeds a 2.5 s barge-in
        # window in ~98 ms and a 4 s utterance in ~153 ms — comfortably real time — while
        # leaving the rest of the box alone. Raise SPK_THREADS for batch work; scaling is
        # close to linear up to about 8. See benchmarks/RESULTS.md.
        threads = threads or int(os.environ.get("SPK_THREADS", "4"))
        if threads > 0:
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(path), so, providers=self._providers)
        self._input = self._sess.get_inputs()[0].name
        self.dim = int(self._sess.get_outputs()[0].shape[-1])
        self.model_dir = model_dir
        self.model_file = model_file
        self.threads = threads
        self.frontend = frontend
        self._fe = FRONTENDS[frontend]

    def _fbank(self, x: np.ndarray) -> np.ndarray:
        """This model's frontend. Named _fbank for the benchmarks that already call it."""
        return self._fe(x)

    def embed(self, pcm: bytes, sr: int = SAMPLE_RATE) -> np.ndarray:
        feats = self._fe(_to_float_mono(pcm, sr))
        out = self._sess.run(None, {self._input: feats})[0]
        return _unit(out)

    def info(self) -> dict:
        return {
            "backend": self.name,
            "model": f"wespeaker/voxblink2_samresnet34_ft (onnx: {self.model_file})",
            "model_dir": self.model_dir,
            "dim": self.dim,
            "providers": self._providers,
            "threads": self.threads or "default",
            "frontend": self.frontend,
        }


class TorchEmbedder(Embedder):
    """wespeaker's own loader — needs the wespeaker package and its dependencies."""

    name = "torch"

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR, device: str = "cuda:0"):
        _install_distutils_shim()
        import torch
        from wespeaker.cli.speaker import load_model

        if device.startswith("cuda") and not torch.cuda.is_available():
            device = "cpu"
        self._torch = torch
        self._model = load_model(model_dir)
        self._model.set_device(device)
        self.device = device
        self.model_dir = model_dir

    def embed(self, pcm: bytes, sr: int = SAMPLE_RATE) -> np.ndarray:
        x = _to_float_mono(pcm, sr)
        with self._torch.inference_mode():
            # extract_embedding_from_pcm does not move its input, so hand it a tensor
            # that is already on the model's device.
            t = self._torch.from_numpy(x).unsqueeze(0).to(self.device)
            e = self._model.extract_embedding_from_pcm(t, SAMPLE_RATE)
        if e is None:
            raise RuntimeError("no speech found in the clip")
        return _unit(e.cpu().numpy())

    def info(self) -> dict:
        return {
            "backend": self.name,
            "model": "wespeaker/voxblink2_samresnet34_ft",
            "model_dir": self.model_dir,
            "dim": self.dim,
            "device": self.device,
        }


def load_embedder(
    backend: str | None = None,
    model_dir: str | None = None,
    device: str | None = None,
    model: str | None = None,
) -> Embedder:
    """Build the embedder for ``SPK_MODEL`` on ``SPK_BACKEND``.

    ``model`` picks a registered checkpoint (``samresnet34``, ``ecapa512``); ``model_dir``
    overrides the path for something unregistered. Backend is ``onnx``, ``torch`` or
    ``auto``; ``auto`` prefers whatever the model lists first that actually imports.

    A backend the checkpoint does not ship is refused rather than silently downgraded —
    ecapa512 has no PyTorch weights here, and falling back would change which model is
    answering without saying so.
    """
    device_env = device or os.environ.get("SPK_DEVICE", "")

    if model_dir or os.environ.get("SPK_MODEL_DIR"):
        # Unregistered path: no spec, so no threshold or measured numbers travel with it.
        path = model_dir or os.environ["SPK_MODEL_DIR"]
        backend = (backend or os.environ.get("SPK_BACKEND", "onnx")).lower()
        if backend == "auto":
            backend = "onnx"
        if backend == "onnx":
            return OnnxEmbedder(path, device_env or "cpu")
        return TorchEmbedder(path, device_env or "cuda:0")

    spec = get_spec(model)
    backend = (backend or os.environ.get("SPK_BACKEND", "auto")).lower()

    if backend == "auto":
        for candidate in spec.backends:
            try:
                return _build(candidate, spec, device_env)
            except Exception as exc:  # noqa: BLE001 - try the next backend
                logger_msg = f"{spec.name}: {candidate} backend unavailable ({exc})"
                print(f"[rfomni-asv] {logger_msg}")
        raise RuntimeError(f"no usable backend for {spec.name} (tried {spec.backends})")

    if backend not in spec.backends:
        raise ValueError(
            f"model {spec.name!r} has no {backend!r} backend in this repo "
            f"(it ships {', '.join(spec.backends)})"
        )
    return _build(backend, spec, device_env)


def _build(backend: str, spec: ModelSpec, device: str) -> Embedder:
    if backend == "onnx":
        e = OnnxEmbedder(
            str(spec.path), device or "cpu",
            model_file=spec.onnx_file, frontend=spec.frontend,
        )
    elif backend == "torch":
        e = TorchEmbedder(str(spec.path), device or "cuda:0")
    else:
        raise ValueError(f"unknown backend {backend!r} (expected onnx, torch or auto)")
    e.spec = spec
    return e
