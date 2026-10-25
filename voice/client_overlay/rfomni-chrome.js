/**
 * RFOMNI chrome overlay — mode switcher, memory indicator, mic level meter,
 * and goal-speaker recognition. V5: the owner's voiceprint is taken from the
 * conversation itself, so there is no Register button and nothing is announced —
 * the "Only my voice" controls simply become available once a voice is known.
 * Uses window.__RFOMNI_CLIENT__ set by the patched prebuilt UI.
 */
(function () {
  const MODES = [
    { id: "assistant", label: "Assistant", hint: "Spoken conversation with short-term memory." },
    { id: "translate_en", label: "Translate → EN", hint: "Any language → English speech." },
    { id: "annotator", label: "Annotator", hint: "Full ASR/LID/EMO… + EN translation if not English." },
  ];

  // Two situations: unrestricted, or the fixed set of languages the room is known to
  // speak. The set narrows ASR to those candidates and bounds the spoken reply to them.
  const LANGS = [
    { id: "auto", label: "Any language",
      hint: "No restriction — the model detects the language itself." },
    { id: "set", label: "EN · DE · FR · ES · PT",
      hint: "Everyone speaks one of these five; anything else is refused." },
  ];

  let mode = "assistant";
  let forceLang = "auto";
  // `probed` flips once the agent has reported gate status, which happens on connect.
  let gate = { probed: false, available: false, enrolled: false, gate: false, arming: false,
               takes: 0, bargeGateSupported: false, bargeGate: false, bargeGuarding: false,
               diarizeSupported: false, diarizeAvailable: false, diarize: false,
               rejections: 0,
               // V5: set by an agent that captures the owner by itself.
               autoSupported: false, autoEnroll: false };
  let rejectFlash = 0;
  let memory = { used: 0, max: 12 };
  let micLevel = 0;
  let micMeter = null;
  let micMeterRaf = 0;
  let micBtn = null;

  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (k === "className") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2).toLowerCase(), v);
      else n.setAttribute(k, v);
    });
    (kids || []).forEach((c) => n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }

  function sendClient(type, payload) {
    const c = window.__RFOMNI_CLIENT__;
    if (!c) return;
    try {
      if (typeof c.sendClientMessage === "function") c.sendClientMessage(type, payload || {});
      else if (c.transport && typeof c.transport.sendMessage === "function") {
        c.transport.sendMessage({ type: "client-message", data: { t: type, d: payload || {} } });
      }
    } catch (e) {
      console.warn("[RFOMNI] " + type + " failed", e);
    }
  }

  function renderGate() {
    const box = document.getElementById("rfomni-gate-box");
    if (!box) return;
    // Until the agent reports status we cannot know whether the embedder is reachable.
    // Show the controls disabled rather than hidden, so the feature is not mistaken for
    // missing before the call starts; hide them only once an agent says it has no gate.
    const idle = !gate.probed;
    box.style.display = idle || gate.available ? "flex" : "none";
    const btn = document.getElementById("rfomni-enroll");
    const tgl = document.getElementById("rfomni-gate-toggle");
    const pill = document.getElementById("rfomni-gate-stat");
    // With silent capture running there is nothing to press: the button would invite
    // the user to do a job the agent is already doing, and pressing it would replace a
    // clean multi-turn voiceprint with a single short take. It comes back if the agent
    // reports capture switched off.
    const auto = !!(gate.autoSupported && gate.autoEnroll);
    if (btn) {
      btn.style.display = auto ? "none" : "";
      btn.disabled = idle;
      btn.textContent = gate.arming ? "Speak now…" : gate.enrolled ? "Add take" : "Register my voice";
      btn.classList.toggle("on", !!gate.arming);
      btn.title = idle
        ? "Connect first, then this records your next spoken turn as the voiceprint"
        : "Record your next spoken turn as the voiceprint (needs ~5-8 s of speech)";
    }
    if (tgl) {
      tgl.disabled = idle || !gate.enrolled;
      tgl.classList.toggle("on", !!gate.gate);
      tgl.title = gate.enrolled
        ? "Answer only the registered voice"
        : auto ? "Available once the agent has heard enough of one voice"
               : "Register a voice first";
    }
    const diar = document.getElementById("rfomni-diarize-toggle");
    if (diar) {
      // Hidden unless the agent has a diarizer it can actually reach.
      diar.style.display = idle || (gate.diarizeSupported && gate.diarizeAvailable) ? "" : "none";
      diar.disabled = idle || !gate.enrolled;
      diar.classList.toggle("on", !!gate.diarize);
      diar.title = gate.enrolled
        ? "Separate overlapping speakers before deciding, instead of scoring the mixture"
        : auto ? "Available once the agent has heard enough of one voice"
               : "Register a voice first";
    }
    const barge = document.getElementById("rfomni-barge-toggle");
    if (barge) {
      // Hidden against an agent that cannot gate barge-in.
      barge.style.display = idle || gate.bargeGateSupported ? "" : "none";
      barge.disabled = idle || !gate.enrolled;
      barge.classList.toggle("on", !!gate.bargeGate);
      barge.title = gate.enrolled
        ? "Only the registered voice may interrupt the agent"
        : auto ? "Available once the agent has heard enough of one voice"
               : "Register a voice first";
    }
    const lamp = document.getElementById("rfomni-reject");
    if (lamp) {
      const n = gate.rejections || 0;
      lamp.style.display = n > 0 ? "inline-flex" : "none";
      lamp.textContent = n > 1 ? `● ${n}` : "●";
      lamp.classList.toggle("hot", Date.now() - rejectFlash < 2500);
    }
    if (pill) {
      if (idle) pill.textContent = auto ? "Connect to start" : "Connect to register";
      else if (!gate.enrolled) pill.textContent = auto ? "Listening" : "Not registered";
      else {
        let s = `${gate.takes} take${gate.takes === 1 ? "" : "s"}`;
        if (gate.consistency != null) s += ` · agree ${gate.consistency}`;
        if (gate.lastScore != null) s += ` · last ${gate.lastScore}`;
        if (gate.lastBargeScore != null) s += ` · barge ${gate.lastBargeScore}`;
        if (gate.lastSpeakers != null) s += ` · ${gate.lastSpeakers} spk`;
        pill.textContent = s;
      }
      pill.classList.toggle("muted", !gate.enrolled);
    }
  }

  function sendMode(m) {
    mode = m;
    document.querySelectorAll(".rfomni-mode").forEach((b) => b.classList.toggle("on", b.dataset.m === m));
    const hint = document.getElementById("rfomni-mode-hint");
    const found = MODES.find((x) => x.id === m);
    if (hint && found) hint.textContent = found.hint;
    const c = window.__RFOMNI_CLIENT__;
    if (!c) return;
    try {
      if (typeof c.sendClientMessage === "function") c.sendClientMessage("set-mode", { mode: m });
      else if (c.transport && typeof c.transport.sendMessage === "function") {
        c.transport.sendMessage({ type: "client-message", data: { t: "set-mode", d: { mode: m } } });
      }
    } catch (e) {
      console.warn("[RFOMNI] set-mode failed", e);
    }
  }

  function sendLang(lang) {
    forceLang = lang;
    document.querySelectorAll(".rfomni-lang").forEach((b) => b.classList.toggle("on", b.dataset.l === lang));
    const c = window.__RFOMNI_CLIENT__;
    if (!c) return;
    try {
      if (typeof c.sendClientMessage === "function") c.sendClientMessage("set-lang", { lang });
      else if (c.transport && typeof c.transport.sendMessage === "function") {
        c.transport.sendMessage({ type: "client-message", data: { t: "set-lang", d: { lang } } });
      }
    } catch (e) {
      console.warn("[RFOMNI] set-lang failed", e);
    }
  }

  function renderMemory() {
    const m = document.getElementById("rfomni-memory");
    if (!m) return;
    m.textContent = `Memory ${memory.used}/${memory.max}`;
    m.title = "Short-term conversation turns kept in the Omni speak prompt";
  }

  function parseLevel(lvl) {
    if (typeof lvl === "number") return lvl;
    if (lvl && typeof lvl === "object") {
      if (typeof lvl.audioLevel === "number") return lvl.audioLevel;
      if (typeof lvl.value === "number") return lvl.value;
    }
    return 0;
  }

  function findMicButton() {
    const panels = document.querySelectorAll('[data-slot="panel"]');
    for (const panel of panels) {
      const header = panel.querySelector('[data-slot="panel-title"]');
      if (!header || !/devices/i.test(header.textContent || "")) continue;
      const btns = panel.querySelectorAll("button");
      for (const btn of btns) {
        const text = (btn.textContent || "").toLowerCase();
        const hasMicLabel = text.includes("microphone") || text.includes("mic");
        const hasDropdown = btn.closest('[data-slot="panel-content"]') && btn.querySelector("svg");
        if (hasMicLabel || (hasDropdown && btn.className.includes("rounded-e-none"))) {
          return btn;
        }
      }
      const group = panel.querySelector('[class*="buttongroup"], [class*="button-group"]');
      if (group) {
        const main = group.querySelector("button");
        if (main) return main;
      }
    }
    return null;
  }

  function drawMicMeter() {
    if (!micMeter) return;
    const canvas = micMeter.querySelector("canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    const bars = 10;
    const gap = 2;
    const barW = Math.max(2, Math.floor((w - gap * (bars - 1)) / bars));
    const target = Math.min(1, Math.max(0, micLevel)) * 255;
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < bars; i++) {
      const wave = 0.55 + 0.45 * Math.abs(Math.sin(i * 0.9 + performance.now() / 100));
      const val = target * wave;
      const smoothKey = "s" + i;
      const prev = micMeter._smooth?.[smoothKey] || 0;
      const next = val < 1 ? Math.max(prev - 8, 0) : prev + (val - prev) * 0.75;
      if (!micMeter._smooth) micMeter._smooth = {};
      micMeter._smooth[smoothKey] = next;
      const barH = Math.max(2, (next / 255) * (h - 2));
      const x = i * (barW + gap);
      const y = (h - barH) / 2;
      ctx.fillStyle = "currentColor";
      ctx.fillRect(x, y, barW, barH);
    }
    micMeterRaf = requestAnimationFrame(drawMicMeter);
  }

  function attachMicMeter(btn) {
    if (!btn || btn.querySelector(".rfomni-mic-meter")) return;
    micBtn = btn;
    const wrap = el("span", { className: "rfomni-mic-meter", title: "Mic level" });
    const canvas = el("canvas", { width: "36", height: "20" });
    wrap.appendChild(canvas);
    btn.appendChild(wrap);
    micMeter = wrap;
    if (!micMeterRaf) micMeterRaf = requestAnimationFrame(drawMicMeter);
  }

  function ensureMicMeter() {
    const btn = findMicButton();
    if (btn) attachMicMeter(btn);
  }

  function mount() {
    if (document.getElementById("rfomni-chrome")) return;
    const bar = el("div", { id: "rfomni-chrome", className: "rfomni-chrome" }, [
      el("div", { className: "rfomni-modes" }, MODES.map((m) =>
        el("button", {
          className: "rfomni-mode" + (m.id === mode ? " on" : ""),
          "data-m": m.id,
          type: "button",
          onClick: () => sendMode(m.id),
          text: m.label,
        })
      )),
      el("div", { className: "rfomni-langs" }, [
        el("span", { className: "rfomni-pill muted", text: "Languages" }),
        ...LANGS.map((l) =>
          el("button", {
            className: "rfomni-lang" + (l.id === forceLang ? " on" : ""),
            "data-l": l.id,
            type: "button",
            title: l.hint,
            onClick: () => sendLang(l.id),
            text: l.label,
          })
        ),
      ]),
      el("div", { id: "rfomni-gate-box", className: "rfomni-gate" }, [
        el("span", { className: "rfomni-pill muted", text: "Speaker" }),
        el("button", {
          id: "rfomni-enroll",
          className: "rfomni-mode",
          type: "button",
          title: "Record your next spoken turn as the voiceprint (needs ~5-8 s of speech)",
          onClick: () => {
            gate.arming = true;
            renderGate();
            sendClient("enroll-voice", {});
          },
          text: "Register my voice",
        }),
        el("button", {
          id: "rfomni-gate-toggle",
          className: "rfomni-mode",
          type: "button",
          onClick: () => sendClient("set-gate", { on: !gate.gate }),
          text: "Only my voice",
        }),
        el("button", {
          id: "rfomni-diarize-toggle",
          className: "rfomni-mode",
          type: "button",
          style: "display:none",
          onClick: () => sendClient("set-diarize", { on: !gate.diarize }),
          text: "Separate speakers",
        }),
        el("button", {
          id: "rfomni-barge-toggle",
          className: "rfomni-mode",
          type: "button",
          onClick: () => sendClient("set-barge-gate", { on: !gate.bargeGate }),
          text: "Only my barge-in",
        }),
        el("button", {
          id: "rfomni-gate-reset",
          className: "rfomni-mode",
          type: "button",
          title: "Forget the voiceprint — the agent starts listening for one again",
          onClick: () => sendClient("reset-voice", {}),
          text: "Clear",
        }),
        el("span", { id: "rfomni-reject", className: "rfomni-reject", title: "Someone other than the registered speaker was refused", text: "" }),
        el("span", { id: "rfomni-gate-stat", className: "rfomni-pill muted", text: "Not registered" }),
      ]),
      el("div", { className: "rfomni-meta" }, [
        el("span", { id: "rfomni-memory", className: "rfomni-pill", text: "Memory 0/12" }),
        el("span", { id: "rfomni-vision", className: "rfomni-pill muted", text: "Cam off" }),
      ]),
      el("p", { id: "rfomni-mode-hint", className: "rfomni-hint", text: MODES[0].hint }),
    ]);
    document.body.appendChild(bar);
    renderMemory();
    renderGate();
  }

  function ensureMicOn(c) {
    if (!c || typeof c.enableMic !== "function") return;
    try {
      c.enableMic(true);
    } catch (e) {
      console.warn("[RFOMNI] enableMic failed", e);
    }
  }

  function wireClient(c) {
    if (!c || c.__rfomniWired) return;
    c.__rfomniWired = true;
    window.__RFOMNI_CLIENT__ = c;
    ensureMicOn(c);
    const onMsg = (msg) => {
      const data = msg?.data ?? msg;
      if (!data || typeof data !== "object") return;
      if (data.type === "rfomni-memory" || data.memoryTurns != null) {
        memory.used = data.memoryTurns ?? data.used ?? memory.used;
        memory.max = data.maxTurns ?? data.max ?? memory.max;
        renderMemory();
      }
      if (data.type === "rfomni-mode" && data.mode) {
        mode = data.mode;
        document.querySelectorAll(".rfomni-mode").forEach((b) => b.classList.toggle("on", b.dataset.m === mode));
      }
      if (data.type === "rfomni-lang" && data.lang) {
        forceLang = data.lang;
        document.querySelectorAll(".rfomni-lang").forEach((b) => b.classList.toggle("on", b.dataset.l === forceLang));
      }
      if (data.type === "rfomni-gate") {
        if ((data.rejections || 0) > (gate.rejections || 0)) rejectFlash = Date.now();
        gate = Object.assign(gate, data, { probed: true });
        renderGate();
        // Repaint shortly after so the lamp cools down on its own.
        setTimeout(renderGate, 2600);
      }
      if (data.type === "rfomni-error" && data.error) {
        const pill = document.getElementById("rfomni-gate-stat");
        if (pill) pill.textContent = String(data.error).slice(0, 60);
        gate.arming = false;
        renderGate();
      }
      if (data.type === "rfomni-vision") {
        const v = document.getElementById("rfomni-vision");
        if (v) {
          v.textContent = data.enabled ? "Cam on" : "Cam off";
          v.classList.toggle("muted", !data.enabled);
        }
      }
    };
    try {
      if (typeof c.on === "function") {
        ["localAudioLevel", "LocalAudioLevel"].forEach((ev) => {
          try {
            c.on(ev, (lvl) => {
              micLevel = parseLevel(lvl);
            });
          } catch (_) {}
        });
        ["serverMessage", "server-message", "ServerMessage"].forEach((ev) => {
          try { c.on(ev, onMsg); } catch (_) {}
        });
        ["Connected", "TransportStateChanged"].forEach((ev) => {
          try {
            c.on(ev, (state) => {
              if (ev === "Connected" || state === "ready") ensureMicOn(c);
            });
          } catch (_) {}
        });
      }
    } catch (e) {
      console.warn("[RFOMNI] wire client", e);
    }
    setTimeout(() => {
      ensureMicOn(c);
      sendMode(mode);
      ensureMicMeter();
    }, 400);
    setTimeout(() => {
      ensureMicOn(c);
      ensureMicMeter();
    }, 1500);
  }

  const t0 = Date.now();
  const timer = setInterval(() => {
    mount();
    ensureMicMeter();
    if (window.__RFOMNI_CLIENT__) {
      wireClient(window.__RFOMNI_CLIENT__);
    }
    if (Date.now() - t0 > 120000) clearInterval(timer);
  }, 300);

  window.__RFOMNI_WIRE__ = wireClient;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
