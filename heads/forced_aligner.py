"""Emergent forced aligner of the Qwen3-Omni mix87 thinker, as a sidecar readout.

No trained weights. The thinker's own decoder attention (LLM layer 42 head 21, plus the
ranked char-level heads behind it) is read with the §9g decoder of
docs/PAPER_FORCED_ALIGNMENT_FINDING.md:

    1. teacher-force the transcript one token per CHARACTER after the ASR prompt,
    2. average the attention MATRICES of the top-K char-level heads (rows=chars, cols=audio),
    3. monotone DTW over the row-normalised posterior,
    4. sub-token read-off (centroid / boundary crossing) and within-word regression,
    5. one global offset (fitted on TIMIT dev, never on a benchmark).

    al = ThinkerAligner(thinker, processor, device)
    al.align(wav)                      # transcript from the thinker's own ASR
    al.align(wav, "the cat sat")       # forced alignment of a given transcript
    al.locate(wav, "hate")             # word localisation (spans + scores), None = abstain

The thinker may be loaded with any attention implementation; the teacher-forced pass flips the
text stack to ``eager`` (attention weights are only returned there) and restores it after.
"""
from __future__ import annotations

import contextlib
import difflib
import re
from dataclasses import asdict, dataclass, replace
from typing import Sequence

import numpy as np
import torch

SR = 16000
AUDIO_TOKENS_PER_SEC = 13.0
ASR_INSTRUCTION = "Please transcribe the speech."     # the instruction the TIMIT dump used

# Ranked char-level heads, in the order of clock_probe/timit/attn_dump/heads.json. K=2 is
# L42H21 + L22H4 (the §9g best arm).
DEFAULT_HEADS: tuple[tuple[int, int], ...] = (
    (42, 21), (22, 4), (10, 4), (34, 6), (42, 17), (22, 6), (30, 12), (18, 20))


@dataclass(frozen=True)
class DecodeCfg:
    K: int = 2                 # how many ranked heads' matrices to average
    norm: str = "row"          # 'row' | 'rowcol'
    gamma: float = 2.0         # posterior sharpening (no measured effect; kept for parity)
    base: str = "cross"        # 'grid' | 'centroid' | 'parab' | 'cross'
    agg: str = "fitr"          # 'first' | 'fit' | 'fitr'  (within-word regression)
    offset: float = 0.024      # seconds subtracted from every start; fitted on TIMIT dev
    end: str = "next"          # word-end estimator: 'next' | 'lastchar'
    w: int = 2                 # centroid half-window, audio tokens

    def name(self) -> str:
        return f"K{self.K}_{self.norm}_g{self.gamma:g}_{self.base}_{self.agg}_{self.end}"


# §9g best arm on TIMIT human word starts: 42 ms MAE / 35 ms median (offset 24 ms).
TIMIT_BEST = DecodeCfg()
# §9i recommendation for long-form (no regression, no crossing).
LONGFORM = DecodeCfg(K=2, base="centroid", agg="first", offset=0.0)


# ------------------------------------------------------------------ decoding (pure numpy)

def dtw_path(logp: np.ndarray) -> np.ndarray:
    """Monotone path maximising sum_j logp[j, i_j] with i non-decreasing in j.

    Vectorised, but identical to clock_probe/decode_dtw.dtw_path (strict '>' tie-break, so the
    earliest index achieving a running max wins).
    """
    T, A = logp.shape
    D = np.empty((T, A), np.float64)
    back = np.empty((T, A), np.int64)
    ar = np.arange(A)
    D[0] = logp[0]
    for j in range(1, T):
        prev = D[j - 1]
        pm = np.maximum.accumulate(prev)
        new = np.empty(A, bool)
        new[0] = True
        new[1:] = prev[1:] > pm[:-1]
        back[j] = np.maximum.accumulate(np.where(new, ar, 0))
        D[j] = logp[j] + pm
    out = np.empty(T, np.int64)
    i = int(np.argmax(D[T - 1]))
    out[T - 1] = i
    for j in range(T - 1, 0, -1):
        i = int(back[j][i])
        out[j - 1] = i
    return out


def posterior(A: np.ndarray, norm: str = "row", gamma: float = 1.0) -> np.ndarray:
    P = A.astype(np.float64)
    if norm == "rowcol":
        P = P / np.clip(P.sum(0, keepdims=True), 1e-9, None)
    P = np.clip(P, 1e-12, None) ** gamma
    return P / np.clip(P.sum(1, keepdims=True), 1e-9, None)


def read_times(P, idx, times, dec, w=2):
    """Per-character time from the DTW path (clock_probe/decode_lab.read_times)."""
    tok = float(times[1] - times[0]) if len(times) > 1 else 1.0
    n, A = P.shape
    out = np.empty(n, float)
    for j in range(n):
        i = int(idx[j])
        if dec == "grid":
            out[j] = times[i]
        elif dec == "centroid":
            lo, hi = max(0, i - w), min(A, i + w + 1)
            s = P[j, lo:hi]
            out[j] = float((s * times[lo:hi]).sum() / s.sum()) if s.sum() > 0 else times[i]
        elif dec == "parab":
            if 0 < i < A - 1:
                a, b, c = (np.log(P[j, i + d]) for d in (-1, 0, 1))
                den = a - 2 * b + c
                d = 0.0 if abs(den) < 1e-12 else 0.5 * (a - c) / den
                out[j] = times[i] + float(np.clip(d, -1, 1)) * tok
            else:
                out[j] = times[i]
        else:                                        # 'cross': boundary between char j-1 and j
            if j == 0:
                out[j] = times[i]; continue
            lo, hi = int(idx[j - 1]), i
            if hi <= lo:
                out[j] = times[i]; continue
            d = P[j - 1, lo:hi + 1] - P[j, lo:hi + 1]
            s = np.flatnonzero((d[:-1] > 0) & (d[1:] <= 0))
            if not len(s):
                out[j] = 0.5 * (times[lo] + times[hi]); continue
            k = int(s[-1]); f = d[k] / max(d[k] - d[k + 1], 1e-12)
            out[j] = times[lo + k] + float(np.clip(f, 0, 1)) * tok
    return out


def utt_slope(t: np.ndarray) -> float:
    n = len(t)
    return float((t[-1] - t[0]) / max(n - 1, 1)) if n > 1 else 0.0


def fit_starts(t, c2w, nwords, mode, global_slope=None):
    """Word start per word (clock_probe/decode_lab2.fit_starts, plus 'first')."""
    p = np.full(nwords, np.nan)
    for wi in range(nwords):
        j = np.flatnonzero(c2w == wi)
        if not len(j):
            continue
        y = t[j]
        if mode == "first" or len(j) == 1:
            p[wi] = y[0]; continue
        x = np.arange(len(j), dtype=float)
        if mode == "fitr" and global_slope is not None:
            p[wi] = float(np.mean(y - global_slope * x))
        else:
            s, b = np.polyfit(x, y, 1)
            p[wi] = float(np.clip(b, y[0] - 3 * max(s, 0) - 0.05, y[0] + 0.05))
    return p


def decode(attn: np.ndarray, c2w: np.ndarray, times: np.ndarray, nwords: int,
           cfg: DecodeCfg = TIMIT_BEST) -> dict:
    """attn: (H, n_char, n_audio) in DEFAULT_HEADS order. Returns per-word start/end/score."""
    A = attn[:cfg.K].astype(np.float64).mean(0) if cfg.K > 1 else attn[0].astype(np.float64)
    P = posterior(A, cfg.norm, cfg.gamma)
    idx = dtw_path(np.log(P))
    times = times.astype(np.float64)
    t = read_times(P, idx, times, cfg.base, cfg.w)
    starts = fit_starts(t, c2w, nwords, cfg.agg, utt_slope(t)) - cfg.offset
    # per-char confidence: posterior mass within +-w tokens of the path
    nA = P.shape[1]
    conf = np.array([P[j, max(0, idx[j] - cfg.w): min(nA, idx[j] + cfg.w + 1)].sum()
                     for j in range(len(idx))])
    tc = read_times(P, idx, times, "centroid", cfg.w)
    slope = max(utt_slope(t), 1.0 / AUDIO_TOKENS_PER_SEC / 4)
    ends = np.full(nwords, np.nan)
    ends_last = np.full(nwords, np.nan)
    scores = np.full(nwords, np.nan)
    for wi in range(nwords):
        j = np.flatnonzero(c2w == wi)
        if not len(j):
            continue
        scores[wi] = float(conf[j].mean())
        last = float(tc[j[-1]]) + 0.5 * slope - cfg.offset
        nxt = starts[wi + 1] if wi + 1 < nwords else np.nan
        ends_last[wi] = max(min(last, nxt) if not np.isnan(nxt) else last, starts[wi] + 0.02)
        if cfg.end == "next" and not np.isnan(nxt):
            ends[wi] = nxt
        else:
            ends[wi] = ends_last[wi]
        ends[wi] = max(ends[wi], starts[wi] + 0.02)
    return {"starts": starts, "ends": ends, "ends_last": ends_last, "scores": scores, "path": idx}


# ------------------------------------------------------------------ text helpers

_PUNCT = re.compile(r"[^\w'\s]+", re.UNICODE)


def normalize_words(text: str) -> list[str]:
    """Lowercase, drop punctuation except in-word apostrophes, split."""
    t = _PUNCT.sub(" ", text.lower().replace("’", "'"))
    out = []
    for w in t.split():
        w = w.strip("'")
        if w:
            out.append(w)
    return out


def clean_asr(text: str) -> str:
    """Strip language tags / special markers some fine-tunes emit before the transcript:
    '<asr_text>', 'language English', '[English]\n', and bracketed sound tags '[Music]'."""
    t = re.sub(r"<[^>]{1,40}>", " ", text)
    t = re.sub(r"^\s*language\s+\w+\s*", "", t, flags=re.I)
    t = re.sub(r"\[[^\]\n]{1,30}\]", " ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


# The mix87 fine-tune often answers a transcription request with a speaker / scene CAPTION
# ("a male speaker with a high pitched voice delivers his words ... clean environment",
# "[English] Applause.", "mood neutral emotion sadness"). Teacher-forcing a caption aligns
# nothing, so such outputs are retried with the next instruction. Patterns written from the
# TIMIT failures of asr_instruction_timit.json -- never from AIR-Bench.
_CAPTION_STRONG = re.compile(
    r"delivers (his|her|their) (words|speech)|\bspeaker with\b|\blow quality\b|\bin mono\b|"
    r"\b(clean|noisy|quiet|reverberant) environment\b|\b(high|low|medium)[- ]pitched\b|"
    r"\b(is|are) heard\b|^\s*(fe)?male (speech|speaker|voice)\b|^\s*(mood|emotion)\b|"
    r"^\s*(a|an|the|two|several|some)\s+(\w+\s+){0,2}(man|woman|men|women|male|female|person|"
    r"people|child|children|kid|girl|boy|adult|crowd|speaker)\s+(\w+\s+){0,2}"
    r"(speaks|speaking|talks|talking|is heard|are heard|says|laughs|sings)\b|"
    r"^\s*(music|applause|laughter|silence|noise|neutral|speech|singing|cheering)\W*$", re.I)
_CAPTION_WEAK = re.compile(r"\b(pitched|environment|accent|mood|emotion|voice|speaker|recording|"
                           r"background|pace|tone|audio|sound)\b", re.I)


def caption_like(text: str) -> bool:
    """True for a caption / label / degenerate loop instead of a transcript."""
    t = text.strip()
    if not t:
        return True
    w = normalize_words(t)
    if len(w) >= 6 and max(w.count(x) for x in set(w)) > 0.5 * len(w):
        return True                                   # 'الله الله الله ...' repetition loop
    return bool(_CAPTION_STRONG.search(t)) or len(set(m.lower() for m in _CAPTION_WEAK.findall(t))) >= 3


# Fallback order = TIMIT WER order (asr_instruction_timit.json), chosen off-benchmark.
ASR_CHAIN = ("Transcribe the audio verbatim. Output only the spoken words.",
             "Transcribe the audio verbatim. Output only the spoken words; do not describe "
             "the speaker, the voice or the recording.",
             "Transform the speech into a text document as transcriptions.")


# Spoken-form canonicalisation used only when MATCHING (the teacher-forced text is untouched):
# the standard abbreviations an English ASR writes for words a reference spells out.
_ABBR = {"mr": "mister", "mrs": "missus", "dr": "doctor"}


def _canon(w: str) -> str:
    return _ABBR.get(w, w)


def match_query(words: Sequence[str], query: str, min_ratio: float = 0.8) -> list[tuple[int, int, float]]:
    """(first_word, last_word, similarity) for every occurrence of the query in `words`.

    Exact token-sequence matches first; if none, the best fuzzy window(s) with character
    similarity >= min_ratio (an ASR spelling variant), else nothing (caller abstains).
    """
    q = [_canon(w) for w in normalize_words(query)]
    words = [_canon(w) for w in words]
    if not q or not words:
        return []
    n = len(q)
    exact = [(i, i + n - 1, 1.0) for i in range(len(words) - n + 1)
             if list(words[i:i + n]) == q]
    if exact:
        return exact
    qs = " ".join(q)
    cand = []
    for i in range(len(words) - n + 1):
        r = difflib.SequenceMatcher(None, qs, " ".join(words[i:i + n])).ratio()
        if r >= min_ratio:
            cand.append((i, i + n - 1, round(r, 3)))
    if not cand:
        return []
    best = max(c[2] for c in cand)
    return [c for c in cand if c[2] >= best - 1e-9]


# ------------------------------------------------------------------ the live aligner

def set_audio_window(processor, seconds: float):
    """Widen WhisperFeatureExtractor beyond its 30 s default (README trap #1). Returns undo dict."""
    fe = processor.feature_extractor
    old = {"chunk_length": fe.chunk_length, "n_samples": fe.n_samples,
           "nb_max_frames": fe.nb_max_frames}
    n = max(int(seconds) + 2, old["chunk_length"])
    fe.chunk_length = n
    fe.n_samples = n * fe.sampling_rate
    fe.nb_max_frames = fe.n_samples // fe.hop_length
    return old


def restore_audio_window(processor, old):
    fe = processor.feature_extractor
    for k, v in old.items():
        setattr(fe, k, v)


class ThinkerAligner:
    """Forced alignment read off the thinker's own attention. Shares the caller's model."""

    def __init__(self, thinker, processor, device, heads=DEFAULT_HEADS,
                 cfg: DecodeCfg = TIMIT_BEST, instruction: str = ASR_INSTRUCTION,
                 score_threshold: float | None = None):
        self.thinker, self.processor, self.device = thinker, processor, torch.device(device)
        self.heads = [tuple(h) for h in heads]
        self.cfg = cfg
        self.instruction = instruction
        self.score_threshold = score_threshold
        self.layers = thinker.model.layers

    # -- attention implementation toggle ---------------------------------------
    @contextlib.contextmanager
    def _eager(self):
        th = self.thinker
        cfgs = {id(c): c for c in [th.config.text_config, th.model.config]
                + [l.self_attn.config for l in self.layers]}
        old = {k: c._attn_implementation for k, c in cfgs.items()}
        try:
            for c in cfgs.values():
                c._attn_implementation = "eager"
            yield
        finally:
            for k, c in cfgs.items():
                c._attn_implementation = old[k]

    def _to_dev(self, batch):
        return {k: (v.to(self.device, self.thinker.dtype) if torch.is_tensor(v) and v.is_floating_point()
                    else v.to(self.device) if torch.is_tensor(v) else v) for k, v in batch.items()}

    def _prefix_batch(self, wav, instruction: str | None = None):
        messages = [{"role": "user", "content": [{"type": "audio", "audio": "P"},
                                                 {"type": "text", "text": instruction or self.instruction}]}]
        prefix = self.processor.apply_chat_template(messages, add_generation_prompt=True,
                                                    tokenize=False)
        old = set_audio_window(self.processor, len(wav) / SR)
        try:
            batch = self.processor(text=[prefix], audio=[wav], sampling_rate=SR,
                                   return_tensors="pt", padding=True)
        finally:
            restore_audio_window(self.processor, old)
        return self._to_dev(batch)

    # -- ASR -------------------------------------------------------------------
    @torch.no_grad()
    def transcribe(self, wav: np.ndarray, max_new_tokens: int = 384,
                   instruction: str | None = None) -> str:
        batch = self._prefix_batch(wav, instruction)
        out = self.thinker.generate(**batch, max_new_tokens=max_new_tokens, do_sample=False,
                                    use_cache=True)
        txt = self.processor.batch_decode(out[:, batch["input_ids"].shape[1]:],
                                          skip_special_tokens=True)[0]
        return clean_asr(txt)

    def transcribe_robust(self, wav: np.ndarray, max_new_tokens: int = 384,
                          chain=ASR_CHAIN) -> tuple[str, int, list[str]]:
        """First non-caption output along `chain`. Returns (text, chain index, all tries);
        index -1 / text '' when every instruction produced a caption (caller abstains)."""
        tries = []
        for k, ins in enumerate(chain):
            t = self.transcribe(wav, max_new_tokens, ins)
            tries.append(t)
            if t.strip() and not caption_like(t):
                return t, k, tries
        return "", -1, tries

    # -- one teacher-forced pass -----------------------------------------------
    @torch.no_grad()
    def attention(self, wav: np.ndarray, words: Sequence[str]):
        """(H, n_char, n_audio) float16 attention for self.heads, char->word map, token times."""
        tok = self.processor.tokenizer
        batch = self._prefix_batch(wav)
        ids = batch["input_ids"][0]
        char_ids, c2w = [], []
        for wi, w in enumerate(words):
            for ch in (" " if wi else "") + w:
                t = tok(ch, add_special_tokens=False)["input_ids"]
                if t:
                    char_ids += t
                    c2w += [wi] * len(t)
        ext = torch.cat([ids, torch.tensor(char_ids, device=self.device, dtype=ids.dtype)])
        batch["input_ids"] = ext.unsqueeze(0)
        if batch.get("attention_mask") is not None:
            batch["attention_mask"] = torch.ones_like(ext).unsqueeze(0)
        apos = (ext == self.thinker.config.audio_token_id).nonzero(as_tuple=True)[0]
        tpos = torch.arange(len(ids), len(ext), device=self.device)
        by_layer: dict[int, list[int]] = {}
        for l, h in self.heads:
            by_layer.setdefault(l, []).append(h)
        store = {}

        def mk(li):
            def hook(mod, inp, o):
                w = o[1]
                if w is None:
                    return
                for h in by_layer[li]:
                    store[(li, h)] = w[0, h][tpos][:, apos].float().cpu().numpy()
            return hook

        hs = [self.layers[li].self_attn.register_forward_hook(mk(li)) for li in by_layer]
        try:
            with self._eager():
                self.thinker(**batch, use_cache=False)
        finally:
            for x in hs:
                x.remove()
        if len(store) != len(self.heads):
            raise RuntimeError("attention weights not returned (eager toggle failed?)")
        A = np.stack([store[h] for h in self.heads]).astype(np.float16)
        times = ((np.arange(len(apos)) + 0.5) / AUDIO_TOKENS_PER_SEC).astype(np.float32)
        return A, np.asarray(c2w, np.int32), times

    # -- public API (spec §6 contract) ------------------------------------------
    def align(self, wav: np.ndarray, transcript: str | None = None,
              cfg: DecodeCfg | None = None, cache: dict | None = None) -> dict | None:
        """Word timings. Returns None to abstain (clip < 0.5 s, empty transcript, low score)."""
        cfg = cfg or self.cfg
        if len(wav) < SR // 2:
            return None
        asr = transcript is None
        if asr:
            transcript = self.transcribe_robust(wav)[0]
        words = normalize_words(transcript)
        if not words:
            return None
        A, c2w, times = self.attention(wav, words)
        if cache is not None:
            cache.update(attn=A, c2w=c2w, times=times, words=words, transcript=transcript)
        return self.from_attention(A, c2w, times, words, cfg, transcript=transcript,
                                   transcript_source="asr" if asr else "given")

    def from_attention(self, A, c2w, times, words, cfg: DecodeCfg | None = None,
                       transcript: str = "", transcript_source: str = "given") -> dict | None:
        return alignment_from_attention(A, c2w, times, words, cfg or self.cfg, self.heads,
                                        self.score_threshold, transcript, transcript_source)

    def locate(self, wav: np.ndarray, query: str, transcript: str | None = None,
               alignment: dict | None = None, min_ratio: float = 0.8) -> dict | None:
        """Spans where `query` is spoken. None = abstain (query absent from the transcript)."""
        al = alignment or self.align(wav, transcript)
        return locate_in_alignment(al, query, min_ratio)


# TIMIT-dev 5th percentile of the per-word score under TIMIT_BEST (timit_offline_check.json).
# Reported next to every score as the reference point; weakly predictive (Spearman -0.11).
SCORE_REFERENCE = 0.76


def alignment_from_attention(A, c2w, times, words, cfg: DecodeCfg = TIMIT_BEST,
                             heads=DEFAULT_HEADS, score_threshold: float | None = None,
                             transcript: str = "", transcript_source: str = "given") -> dict | None:
    """Spec §6 align() dict from one cached teacher-forced pass."""
    d = decode(A, c2w, times, len(words), cfg)
    out = []
    for i, w in enumerate(words):
        s, e, el, sc = d["starts"][i], d["ends"][i], d["ends_last"][i], d["scores"][i]
        if np.isnan(s):
            continue
        out.append({"word": w, "start": round(float(max(s, 0.0)), 3),
                    "end": round(float(e), 3), "end_last": round(float(el), 3),
                    "score": round(float(sc), 3)})
    if not out:
        return None
    mean = float(np.mean([o["score"] for o in out]))
    if score_threshold is not None and mean < score_threshold:
        return None
    return {"words": out, "frame_rate": AUDIO_TOKENS_PER_SEC, "language": None,
            "mean_score": round(mean, 3), "score_threshold": score_threshold,
            "score_reference": SCORE_REFERENCE,
            "transcript": transcript, "transcript_source": transcript_source,
            "decoder": asdict(cfg), "heads": [list(h) for h in list(heads)[:cfg.K]]}


def locate_in_alignment(al: dict | None, query: str, min_ratio: float = 0.8) -> dict | None:
    if not al:
        return None
    words = [w["word"] for w in al["words"]]
    hits = match_query(words, query, min_ratio)
    if not hits:
        return None
    spans = []
    for a, b, r in hits:
        ws = al["words"][a:b + 1]
        spans.append({"start": ws[0]["start"], "end": ws[-1]["end"],
                      "score": round(float(np.mean([w["score"] for w in ws])), 3),
                      "match": r, "text": " ".join(w["word"] for w in ws)})
    return {"spans": spans, "n_occurrences": len(spans), "query": query}
