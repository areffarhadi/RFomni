# Changelog

## Unreleased

- Tasks page: change the classes (add, remove, rename, edit as a list) and the prompt of any task, including built-in ones.
  Changed and new tasks move to their own column behind a vertical line, with Edit / Test / Reset to default / Delete;
  the editor shows the default next to each change. Editors offer the current classes right away.
- Test on a sample: dry-run a task (or unsaved edits) on 1–10 random files of the project; results per file with
  playback, shuffle, and "Run on all files". Nothing is written to the annotations.
- Forced alignment: every word now has its own border. Dragging moves only the highlighted word (its phones follow)
  and stops at a touching neighbour; Alt or the new **Linked** switch moves a shared border of two words together.
- **Auto-play** after moving a border is a labelled on/off switch in the timeline toolbar, also toggled with `L`.
- Speakers & overlap: diarization now gives speaker turns that may overlap (one lane per speaker in the editor);
  new tasks **Pure speech** (one speaker only, per speaker) and **Overlap** (labelled with the speaker pair);
  *Rebuild from the speaker turns* after corrections; **Speaker clips** export (WAV per speaker, overlaps per pair).
- Spoof & deepfake tasks: audio spoof (per segment), video deepfake (per window), image spoof, manipulated regions (boxes).
- **Statistics** tab: dataset make-up, label distributions (count or duration), review progress, how often reviewers
  kept or changed the model output, speaker / overlap figures, warnings, CSV download.
- **Share links**: view-only or editing links to one project, limited to some tiers if wanted, with optional downloads
  and expiry; work on the local network, or on the internet through a Cloudflare quick tunnel. Shared links run on a
  separate gated listener; edits through a link are tagged with its name.
- Showcase: new `studio_debate.wav` with three people talking over each other (`scripts/make_pseudo.py --add debate`).
- The editor no longer overflows on narrower screens; the timeline toolbar wraps instead.

## v0.2.0 "Mosaic" (2026-10-20)

- **Image regions**: new task outputs `boxes` and `keypoints`, with built-in tasks Faces (box + expression),
  People (body boxes), Objects (labelled boxes) and Pose (COCO-17 keypoints).
- Image editor with an editable overlay: move / resize / draw / relabel / delete boxes, drag keypoints,
  mark them occluded or not visible, place missing ones, add a skeleton for a missed person; zoom and pan;
  arrow-key nudging; undo / redo. Corrections keep the original model geometry and can be restored.
- Prompt tasks can produce boxes or keypoints (the prompt asks for 0-1000 coordinates).
- COCO JSON export (per image and per project), geometry in CSV and the training manifest.
- Showcase: new full-body `pose_park.png` and pseudo boxes / keypoints with deliberate mistakes to fix.
- Pose: add single points to a skeleton (P or "+ Point"), name them or use them as a missing COCO point;
  edit the connections per person (Shift + drag between two points, "connect to…", click a line + Delete).
  Extra points and edited skeletons are exported to COCO (`extra_keypoints`, per-annotation `skeleton`).
- Boxes can stay unlabelled: new boxes start without a label; "No label" option in the picker and the panel;
  pressing a label's number again clears it. Unlabelled boxes export as face / person / object.

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
