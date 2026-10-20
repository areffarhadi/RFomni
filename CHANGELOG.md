# Changelog

## v0.1.0 "Tessera" (2026-10-20)

First version of the rebuilt studio. UI-first: annotation runs use a mock engine until the model is connected.

- **Projects** as plain folders: data, selected tasks, one JSON annotation file per media file.
- **Data**: upload files or whole mixed folders (drag & drop), or link local folders without copying.
  Audio, video and images are detected automatically; decoding uses PyAV, no system ffmpeg needed.
- **Tasks**: 33 built-in annotation and captioning tasks, plus prompt tasks written in plain language
  with output type (text, label, tags, score), granularity and a preview of the exact model prompt.
- **Run** tasks on all, selected or un-annotated files in the background (fill empty tiers, re-run
  unreviewed tiers, or replace everything).
- **Tier editor** for audio and video: waveform and/or spectrogram, video frame strip, overview,
  one channel per task with parent/child tiers (ASR, words, phones). Create, resize, split, merge,
  label hotkeys, undo/redo, autosave, live transcript with word highlighting.
- **Forced alignment on the signal**: word or phone boundaries drawn on the waveform and spectrogram;
  drag a boundary to move it together with the neighbouring word, the phones inside and a coinciding
  utterance edge, then hear the result. Original model times are kept for evaluation.
- **Channel order** per project: drag tier headers to reorder; applies to every file and to exports.
- **Image editor** with one field per task.
- **Review queue** of the least-confident segments across the project.
- **Import**: ELAN .eaf, Praat TextGrid, vLLM-omni pipeline JSON, native JSON.
  **Export**: JSON, ELAN, TextGrid, SRT, WebVTT, RTTM, CSV and a JSONL training manifest.
- `scripts/make_pseudo.py` builds a showcase project from synthetic speech, video and images.
