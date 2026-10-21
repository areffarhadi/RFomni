import { h, ico } from '../util.js';
import { openModal } from '../ui.js';

const mod = navigator.platform.includes('Mac') ? '⌘' : 'Ctrl';
const GROUPS = [
  ['Playback', [['Space', 'Play / pause'], ['Shift Space  or  P', 'Play selection or selected segment / word'], ['← / →', 'Back / forward 1 s (Shift: 5 s)'], ['F', 'Follow playhead on/off']]],
  ['Navigate', [['Tab / Shift Tab', 'Next / previous segment in tier'], ['↑ / ↓', 'Tier above / below'], ['N', 'Next low-confidence segment'], ['[ / ]', 'Previous / next file']]],
  ['Edit', [['Enter', 'Edit value (or create from selection)'], ['1 – 9', 'Pick label'], ['R', 'Toggle reviewed'], ['A', 'Accept: reviewed + next'],
    ['S', 'Split at playhead'], ['M', 'Merge with next'], ['Delete', 'Delete segment'], [`${mod} Z`, 'Undo'], [`${mod} Shift Z`, 'Redo'], [`${mod} S`, 'Save now']]],
  ['Timeline', [['+ / −', 'Zoom in / out'], ['0', 'Fit whole file'], ['Z', 'Zoom to segment'], [`${mod} / pinch + scroll`, 'Zoom at pointer'], ['Shift + scroll', 'Scroll in time'],
    ['Drag on empty tier', 'Create a segment'], ['Drag segment edge', 'Adjust boundary (snaps)'], ['Alt + drag segment', 'Move segment'], ['Drag on waveform', 'Select a time range']]],
  ['Forced alignment on the signal', [['Drag a line on waveform / spectrogram', 'Move that word\'s own border (the highlighted word; its phones follow)'],
    ['Alt + drag a line', 'Move the shared border of two touching words together'],
    ['Click a word on the waveform', 'Select it'], ['Double-click a word', 'Play it'], ['Boundaries ▾ (toolbar)', 'Choose the tier: words, phones, segments or off'],
    ['L  or  ▶ Auto-play (toolbar)', 'Switch auto-play after moving a border on / off'], ['🔗 Linked (toolbar)', 'Off: each word has its own border · On: touching words share it']]],
];

// A key spec like "Shift Space  or  P" or "Alt + drag segment" becomes separate <kbd> keys,
// with "or", "/" and "+" as quiet separators and mouse gestures as plain text.
const KEY = /^(Space|Shift|Tab|Enter|Delete|Alt|Ctrl|Esc|⌘|[A-Z0-9]|[←→↑↓[\]+−])$/;
const GLYPH = { '▶': 'play', '🔗': 'link' };  // toolbar buttons: show their icon, not a glyph

function combo(spec) {
  const out = [];
  const sep = (t) => out.push(h('span.keys-sep', t));
  spec.split(/ {2}or {2}/).forEach((alt, i) => {
    if (i) sep('or');
    alt.split(' / ').forEach((part, j) => {
      if (j) sep('/');
      part.split(' + ').forEach((chunk, k) => {
        if (k) sep('+');
        let words = [];
        const flush = () => { if (words.length) out.push(h('span', words.join(' '))); words = []; };
        for (const tok of chunk.split(' ').filter(Boolean)) {
          if (KEY.test(tok)) { flush(); out.push(h('kbd', tok)); }
          else if (GLYPH[tok]) { flush(); out.push(ico(GLYPH[tok], 12)); }
          else words.push(tok);
        }
        flush();
      });
    });
  });
  return h('span.keys-combo', out);
}

export function showShortcuts() {
  if (document.querySelector('.modal.keys-modal')) return;
  openModal({
    title: 'Keyboard & mouse', width: 760, className: 'keys-modal',
    // groups whose gestures are long sentences get the full width
    body: h('div.keys', GROUPS.map(([g, rows]) => h('div.keys-group' + (rows.some(([k]) => k.length > 24) ? '.wide' : ''),
      h('h4', g), rows.map(([k, d]) => h('div.keys-row', h('span.keys-desc', d), combo(k)))))),
  });
}
