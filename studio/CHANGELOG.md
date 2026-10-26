# Changelog

## v0.6.4 "Atelier" (2026-10-26)

The site on its own domain.

- **labelomni.com**: a Cloudflare Tunnel (`stack/configs/cloudflared_labelomni.yml`, the stack's eighth service
  `tunnel`) serves the studio at https://labelomni.com and the chat at https://chat.labelomni.com, with a real
  certificate and nothing opened on the firewall.
- **Landing page first**: the bare domain opens the landing page; its links open the studio as `/?app`. The LAN
  address (port 8710) still opens the studio directly.
- **Links between studio and chat** use `chat.<domain>` / `<domain>` behind the domain and the ports on the LAN.
- **Voice chat**: every reply was saved twice in the chat, and a reply of several sentences kept only the last one.
  Both came from the chat page's handling of the agent's events; it now saves each reply once and in full.
- **Voice debugging**: `OMNI_MIC_DEBUG_DIR` records each call's microphone and logs its level once a second;
  `OMNI_RTVI_DEBUG=1` logs every event the agent sends to the browser. Both are off unless set.

## v0.6.3 "Atelier" (2026-10-26)

One sign-in for the whole site.

- **Sign in once**: the sign-in page first asks whether you are signed in already (on the studio's page or on the
  chat) and, if so, goes straight on to the Studio, the Chat, Task sharing, Subjective tests or Data collection; the
  form only shows when you are not. Once you are signed in, the landing page links go straight to the apps.
- **Chat top bar**: shows your name as soon as the chat signs you in (it said "Sign in" until a reload), and its
  *Sign out* now ends the session for the chat and the studio together.
- **Home** instead of *About* in the studio's top bar, for the link back to the landing page.

## v0.6.2 "Atelier" (2026-10-25)

Text, a fourth kind of data next to audio, video and images.

- **Text documents**: a .txt or .md file is one document; a .csv, .tsv or .jsonl table becomes one document per row
  (uploaded or linked), keeping the other columns (an id, a gold label) with the document.
- **14 text tasks** in a new *Text* category (and a *Text essentials* preset), run by RFomni: sentiment, emotion,
  topic, language, offensive language, intent, keywords, summary, English translation, writing quality and
  correction for the whole document; named entities, personal information and opinions about aspects as marked words
  (character offsets). Entities use the model's trained short prompt. The task editor and *Write it with AI* offer
  text and the new "each marked stretch of text" granularity.
- **Text editor**: the document with its marks and labels, select words to mark them, a card per task on the right,
  keys as in the other editors (Tab, A, R, 1 to 9, Delete), right-to-left text shown correctly.
- File list (Text filter, word counts), statistics (words), the assistant and export (JSON, CSV and a manifest with one
  line per document; timing formats off for text-only projects) know about text. Long documents are read in pieces.
- Model replies: entities in every JSON shape the model writes (also for the audio NER task), long text answers
  written as one JSON object per paragraph, and near-JSON with a stray quote are all read correctly now.
- Measured on samples of public test sets (60 each): language 97%, sentiment (SST-2) 95%, intent (Banking77) 82%,
  topic (AG News) 77%, offensive 68%, emotion 62%, entities (CoNLL-2003) 57% F1.

## v0.6.1 "Atelier" (2026-10-25)

- **Landing page back to its v0.5.1 design**, as the owner preferred it: the "AI" orb, the numbered section labels,
  the headings, the screenshots and the card pictures are as they were (only the version line is new). The sign-in
  page, which reuses the landing orb, shows it the same way. Everything else from v0.6.0 stays.

## v0.6.0 "Atelier" (2026-10-25)

The whole website checked against the tells of machine-made pages (Hallmark design checks, plus a fresh-context
review) and set right, keeping every link, control and hook. The chat page is unchanged.

- **Tasks page**: the task library is a table, one row per task in category groups, the name a button that opens
  its details; the tiers in a project are a numbered list in editor order (still drag to reorder) instead of a wall
  of chips; changed and new tasks are plain rows; the empty state is a sentence and its button. Run buttons across the
  studio use a play icon instead of sparkles.
- **Studio**: one accent for every project tab instead of a colour per tab; the assistant takes the theme's accent,
  solid, instead of a violet-to-sky gradient with glows; projects without a picture get a flat cover; labels in sentence
  case, meta lines with commas instead of middle dots; disabled buttons, focus rings and field edges made clear; the
  light theme's "on" switches are coloured again.
- **Landing page**: no numbered section labels or italic tails on headings; a speech waveform in the hero orb instead
  of the letters "AI"; current screenshots of the studio (all three modes) instead of v0.2.0 ones, real crops on the
  feature cards and a project overview instead of an invented terminal log; the "nine agents" list and the coloured
  prompt border are gone, and sending a prompt opens the studio; the grid, crosshairs, glows and footer line art removed.
- **Public pages** (task sharing, listening tests, data collection, sign-in): solid cards and one solid button colour
  instead of frosted glass, colour blobs and gradients; an owner's accent fills the buttons with readable ink.

## v0.5.1 "Agora" (2026-10-25)

A check of the whole website (every page at desktop and phone size, in the three themes, with the axe accessibility
checker and walks through labelling, listening, recording and the dialogs) and the fixes it led to.

- **Right-to-left pages**: bilingual text ("فارسی · English") keeps each language's direction, so punctuation stays
  where it belongs; in questions the second language goes on its own line under the first. Instructions keep each
  paragraph's direction. The dropdown arrow on right-to-left pages is drawn correctly.
- **Phones**: the studio's top bar shrinks to icons instead of being cut off; in a project the file list and an open
  file each take the whole screen (a correction link opened on a phone now shows the editor). The recording page puts
  the clip length above its buttons.
- **Accessibility**: names for the editor's text boxes, score inputs and dropdowns, dialog titles, table columns,
  scrollable tables and the assistant's conversation (keyboard scrolling); heading levels in order on every page and
  a main heading on the item, editor and task-design screens; small text that was too faint (project hint, the
  light theme's *Live* badge, grey text on selected rows in the green theme) now has enough contrast.
- **Wording**: the public pages say "1 person" and "1,695 items".

## v0.5.0 "Agora" (2026-10-25)

The platform's public side: three pages anyone can open without an account, each with a session ID to continue
later, and the owner's side for each in the studio.

- **Task sharing** (`/tasks/`, landing *Task sharing*): shared labelling tasks. The owner uploads images, video, audio
  or text (.txt, .csv / .tsv, .jsonl), or adds a server folder, and describes the task to the **design agent**, which
  writes the page (instructions, questions: one answer, several answers, a rating, free text; look; how many people per
  item) with a live preview; everything can be edited by hand. *Go live* gives a link; *List it on the public page*
  shows it on `/tasks/`. People label one item at a time (keys 1–9 and Enter, phones too). Items can be in **groups**
  (e.g. languages) that people choose first, with `{group}` in the wording; *0 people per item* means everyone labels
  every item. Items can be listed before their media arrives. Results: progress, answer counts, agreement, people;
  CSV and JSONL exports. `scripts/import_native_speaker.py` brings in the native-speaker labelling site (39 languages,
  1,695 speakers; earlier labels kept; running it again keeps answers).
- **Correction links**: share a project the model already labelled, from the Shared tasks page, so people fix the labels
  in the editor. Each person gets a session ID and every change is signed with it (`meta.edited_by`,
  `meta.edited_session`); the page lists who changed how much. With downloads off there is no download button or
  save-as menu on the media, and the server refuses clips opened on their own and every export.
- **Subjective tests** (`/listen/`, landing *Subjective test*): listening tests (shared tasks of kind *listening*).
  Listener questions before starting (e.g. native speaker? device?; nothing identifying is required), practice clips
  not scored, rating only after a clip has played to the end, never the same sentence twice in a row, back to change an
  answer, a closing comment, Persian right-to-left pages. The owner adds the blind clip-to-system key privately and gets
  the MOS ± 95% interval per scale and per system, filtered by listener answers. `scripts/import_listening_test.py`
  brings in the Persian TTS test of QwenOmni-Talker (60 clips + 2 practice; naturalness, pronunciation, accent).
- **Data collection** (`/collect/`, landing *Data collection*): projects that ask people to record. Sentences per
  language, ways to read them (e.g. emotions, each with an acting hint), consent, optional speaker questions, 16 or
  48 kHz. The recording page (`/c/<code>`, https for the microphone) records the raw microphone (no noise suppression,
  echo cancellation or gain), shows the waveform to listen to and trim (or *Trim silence*), record again and submit;
  right-to-left sentences are shown as such. The owner plays, accepts or rejects recordings and exports a ZIP (audio by
  language and way, plus metadata.csv). `scripts/make_emotion_collection.py` creates *Multilingual emotional speech*
  (en, de, fr, es, fa, zh; neutral, happy, sad, angry, surprised).
- The landing page's platform menu now opens all four parts; the studio's top bar has *Shared tasks*, *Subjective
  tests* and *Data collection*.
- During development the studio runs without sign-in (`OMNI_AUTH=off`); the sign-in page then steps aside.
- The platform is called **RFomni** throughout (voice messages `rfomni-*`, no other name in the interface).

## v0.4.0 "Compass" (2026-10-25)

- **The model is connected.** Labels come from **RFomni**, the fine-tuned annotation model served by vLLM-omni
  (`backend/engine/omni.py`, now the default engine; `OMNI_ENGINE=mock` keeps the placeholder engine). Trained tasks use
  their short trained prompt and are grouped per speech region; every other task, and any task whose prompt you
  edited, is sent as its prompt plus output spec, so edits on the Tasks page are what the model sees. Tasks the trained
  **heads** answer (speakers and overlap, speaker count, voice gender, audio spoof, phonemes, end of turn, body parts,
  pose, face spoof and deepfake, head pose, video deepfake) go to the heads service (`engine/heads.py`), with Silero
  VAD for speech regions. Tasks nothing can answer yet are reported once per run instead of filled with made-up values,
  and Stop takes effect between model calls.
- **New tasks:** Number of speakers, End of turn, Body part detection and Head pose (48 built-in tasks).
- **Assistant.** A glass chat panel in the bottom-right corner (`Ctrl+/`), in the style of the sign-in card.
  - It answers from the open project and the page you are on: the tasks chosen, grouped by audio, video and image,
    the ones that could be added, file counts and run status. Every reply is written by the model.
  - It walks you through project, data, tasks, test, run and review. Every change (create or open a project, link
    data, add or remove tasks, write or edit a task, test, run) appears on an **approval card**: Approve, Reject, or
    ask for something different first. **Stop** cancels a run.
  - It opens the right page and **points at** the part it talks about (a ring and a note, clear of the panel), and
    filters the task library ("show me only the audio tasks"). Parts are named with `data-guide` attributes.
  - **Voice**: the microphone button talks to the same assistant through the voice agent; spoken approvals need the
    registered voice.
  - The **chat page** lists it as the *Omni Studio Assistant* model, with the same plan as the panel.
  - When the studio is updated while a tab is open, the panel offers to reload.
- **Write it with AI.** The New prompt task form has a box at the top: describe what you want ("does the speaker
  sound tired?") and the model writes a short prompt, clear non-overlapping classes, output and granularity in the
  style of the built-in tasks; **Improve** changes it as you ask ("add a class for energetic") or tidies it. The
  assistant uses the same writer, and you can revise a drafted task in the chat before approving it. Existing
  prompts change only when you ask about the prompt.
- **Sign-in is real.** Accounts are the chat's (Open WebUI): the sign-in page signs in there and keeps the session in
  the shared cookie, so one sign-in covers the studio and the chat on the same address. The studio needs a signed-in
  user, new accounts wait for approval, and server folders and the internet tunnel are for administrators.
  An account menu in the top bar shows who is signed in, opens the chat and signs out. `OMNI_AUTH=off` restores the
  single local user.
- The studio can serve **https** on the network (`OMNI_STUDIO_HOST`, `OMNI_STUDIO_SSL_CERT` / `_KEY`), so the
  microphone works from other computers.
- The model is shown by name, **RFomni**, in the top bar and in the assistant's answers.

## v0.3.0 "Aurora" (2026-10-24)

- **Landing page, new look** (inspired by the Aigent Framer template): a floating header bar with corner marks;
  a full hero with a turning sphere of light grains and "AI" written in grains inside it, which part around the
  cursor (`orb.js`), over a grid with softly lit cells; a strip of the formats Omni Studio reads and writes.
  A **live feed** of labels from the example project drifts by in two rows. Sections are numbered tags
  ("01 — The editor") with the heading on the left and the text on the right, inside a frame of guide lines with
  "+" marks. The editor demo has its own section; a **What is Omni Studio?** paragraph lights up word by word
  as you scroll. New closing call to action, and a larger footer (product, resources, contact, version) over
  rolling contour lines (`terrain.js`). All three modes (Day, Night, Green), phones and reduced motion supported.
- The **Video & action** section and `dust.js` are removed from the landing page.
- **Platform menu in the header.** The bar's links are now the parts of the platform: *Omni Studio*, *Chat* (the agent
  chat, Open WebUI), and *Task sharing*, *Subjective test* and *Data collection* marked *Soon*. Studio and Chat go
  through the sign-in page, which has a *Continue to Studio / Chat* switch (preset by `?next=studio|chat`, remembered)
  and opens the chosen app. On phones the links fold into a menu button.
- **Sign-in page** at `/login/`: a frosted glass card over soft drifting lights with the orb as the light source
  (inspired by the Glass Login Framer template), *Sign in* / *Create an account* on the same card, show / hide password,
  Google and GitHub marked *coming soon*, all three modes and phones. For now every submit opens the studio, even with
  empty fields; real accounts come later. The landing page's *Open the studio* buttons now go through it.
- The landing page no longer claims a local-only, no-account product: the *Run it locally* section is now
  **Get started** (sign in, add data, start labelling), and the FAQ asks *Who can see my data?* and *Do I need an account?*
- The website (landing page and studio) no longer names a specific model; it speaks of "the model".
- The landing page no longer links to the GitHub repository (no *View on GitHub*, docs, changelog or issue links);
  those places now point into the platform (*Chat with the agent*, *Ask in the chat*, a *Platform* footer column).
- `scripts/make_pseudo.py` also runs on Linux: espeak-ng voices when macOS `say` is missing, Liberation / DejaVu fonts.
- **Landing page** at `/landing/` (*About* in the studio's top bar, on every page; its *Open the studio* buttons return
  to the page you last had open in the studio, and the two pages blur into each other): animated editor demo, word-by-word headlines,
  a tilted screenshot that straightens as you scroll, a speakers/overlap diagram whose bars draw in one by one
  each time it comes into view, workflow, "Run it locally" and FAQ.
  A **task pile**: all 44 tasks drop into a box as pills coloured by group; grab and throw them, click one to make it
  jump, tap the floor, or throw them all up (`pile.js`, a small physics simulation written for the page).
  A **Your own tasks** section: "Can't find your task? Describe it." A prompt box with a running colour border writes,
  sends and erases example prompts (a Persian translation, accents from {en-US, en-GB, en-CA}, describing the scene,
  laughter over speech, read or spontaneous speech, people on screen); visitors can type their own. Below it nine
  agents are always at work, each with its own moving orb and shimmering label: listening, reasoning, searching,
  fetching data, connecting, drafting a response, generating output, synthesizing sources, idle (`ask.js`).
  Plain HTML/CSS/JS, no libraries, nothing loaded from the internet; respects reduced motion.
  Day / Night / Green switch in its header (shared with the app), with app screenshots in each mode.
  Fonts: Instrument Serif and Poppins are included (SIL OFL); Satoshi is downloaded per install by
  `scripts/get_fonts.py` (run by `start.sh`), because its license does not allow sharing the files in a repository.
- **Themes**: the theme button in the top bar opens a menu with Day, Night and the new **Green** theme
  (deep greens #051F20 to #235347, sage accent #8EB69B, mint text #DAF1DE). Forest is checked for WCAG AA contrast
  (text, secondary text, buttons, input borders). Primary buttons and switches now take their text colour from the
  theme instead of fixed white, and inputs have their own border token.
- **Project folders** on the Projects page: each project is a folder with its first picture (or the colours of its
  tasks) on the back and a frosted front with name, counts and progress. Resting on it (or focusing it) leans the
  front forward and fans out a few of its files: thumbnails for images and videos, real waveforms for audio, and
  "+N more". The projects list now includes a cover and up to four preview files per project.
- **Motion**: the project rail is a floating dock; the active tab is a coloured bead on its edge (each tab has its own
  colour, per theme) and the dock's outline wraps around it. The bead glides to the next tab on a soft spring
  (run by the browser's compositor, so it stays smooth while the page loads), with a glow in the tab colour. Pages blur out and the next one comes into
  focus, sliding in from the direction of the tab (also between the overview and files, file to file, and Projects);
  segmented controls get a sliding highlight. `frontend/js/motion.js`, no library; off when the system asks for reduced motion.
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
