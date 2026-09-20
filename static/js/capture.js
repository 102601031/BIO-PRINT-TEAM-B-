/* capture.js — shared behavioral capture widget.
 * Used by both index.html (Enroll) and dummy_login.html (Dummy Login),
 * so both surfaces genuinely run the identical capture code rather
 * than two copies that could drift apart.
 *
 * Public API: window.BioCapture
 *   .begin({ title, sub })   -> resets + shows the widget, focuses input
 *   .getPayload()            -> { keystrokes, mouse }
 *   .isReady()               -> bool (phrase typed correctly + all targets hit)
 *   .hide()                  -> hides the widget
 */

const PASSPHRASE = "the quick brown fox jumps over the lazy dog";
const TARGET_COUNT = 4;

const _cap = {
  keystrokeEvents: [],
  mouseEvents: [],
  typedOk: false,
  targetsHit: 0,
  targetSeq: [],
};

function _renderPassphrase(typed) {
  return [...PASSPHRASE].map((ch, i) => {
    const cls = i < typed.length ? "char-ok" : "char-pending";
    return `<span class="${cls}">${ch === " " ? "&nbsp;" : ch}</span>`;
  }).join("");
}

function _setupMouseArena() {
  const arena = document.getElementById("mouse-arena");
  arena.innerHTML = '<div class="arena-hint" id="arena-hint">finish typing the phrase to unlock targets</div>';
  _cap.targetSeq = [];

  const rect = { w: arena.clientWidth || 500, h: 230 };
  for (let i = 0; i < TARGET_COUNT; i++) {
    _cap.targetSeq.push({
      x: 30 + Math.random() * (rect.w - 60),
      y: 30 + Math.random() * (rect.h - 60),
    });
  }

  arena.onmousemove = (e) => {
    if (!_cap.typedOk) return;
    const r = arena.getBoundingClientRect();
    _cap.mouseEvents.push({
      type: "move", x: e.clientX - r.left, y: e.clientY - r.top, t: performance.now(),
    });
  };
}

function _renderNextTarget() {
  const arena = document.getElementById("mouse-arena");
  const hintEl = document.getElementById("arena-hint");
  if (hintEl) hintEl.remove();

  if (_cap.targetsHit >= TARGET_COUNT) {
    document.getElementById("btn-submit-capture").disabled = false;
    return;
  }
  const pos = _cap.targetSeq[_cap.targetsHit];
  const t = document.createElement("div");
  t.className = "arena-target";
  t.style.left = pos.x + "px";
  t.style.top = pos.y + "px";
  t.textContent = _cap.targetsHit + 1;

  t.addEventListener("mousedown", (e) => {
    const r = arena.getBoundingClientRect();
    _cap.mouseEvents.push({ type: "down", x: e.clientX - r.left, y: e.clientY - r.top, t: performance.now() });
  });
  t.addEventListener("mouseup", (e) => {
    const r = arena.getBoundingClientRect();
    _cap.mouseEvents.push({ type: "up", x: e.clientX - r.left, y: e.clientY - r.top, t: performance.now() });
    t.classList.add("hit");
    _cap.targetsHit += 1;
    setTimeout(() => { t.remove(); _renderNextTarget(); }, 120);
  });
  arena.appendChild(t);
}

function _maybeUnlockTargets() {
  if (_cap.typedOk && document.getElementById("arena-hint")) {
    const arena = document.getElementById("mouse-arena");
    if (_cap.targetsHit === 0 && arena.querySelectorAll(".arena-target").length === 0) {
      _renderNextTarget();
    }
  }
}

// Wire the passphrase input once the DOM is ready. Both host pages
// include an element with this exact id.
document.addEventListener("DOMContentLoaded", () => {
  const passInput = document.getElementById("passphrase-input");
  if (!passInput) return;

  passInput.addEventListener("keydown", (e) => {
    if (e.key.length === 1 || e.key === "Backspace") {
      _cap.keystrokeEvents.push({ key: e.key, type: "down", t: performance.now() });
    }
  });
  passInput.addEventListener("keyup", (e) => {
    if (e.key.length === 1 || e.key === "Backspace") {
      _cap.keystrokeEvents.push({ key: e.key, type: "up", t: performance.now() });
    }
  });
  passInput.addEventListener("input", (e) => {
    document.getElementById("passphrase-text").innerHTML = _renderPassphrase(e.target.value);
    _cap.typedOk = e.target.value === PASSPHRASE;
    _maybeUnlockTargets();
  });

  // The whole point of this field is to *measure* real keyboard timing —
  // paste, drag-drop, and autofill all skip the keyboard entirely and
  // would corrupt (or trivially spoof) the behavioral signal, so they're
  // blocked outright rather than just discouraged.
  const blockNonKeyboardEntry = (e) => {
    e.preventDefault();
    _flashPasteWarning();
  };
  passInput.addEventListener("paste", blockNonKeyboardEntry);
  passInput.addEventListener("drop", blockNonKeyboardEntry);
  passInput.addEventListener("contextmenu", (e) => e.preventDefault()); // no right-click paste
  passInput.setAttribute("oncopy", "return false");
  passInput.setAttribute("oncut", "return false");
});

function _flashPasteWarning() {
  const hint = document.getElementById("paste-warning");
  if (!hint) return;
  hint.classList.add("show");
  clearTimeout(_flashPasteWarning._t);
  _flashPasteWarning._t = setTimeout(() => hint.classList.remove("show"), 2200);
}

window.BioCapture = {
  begin({ title, sub }) {
    document.getElementById("stage-empty")?.classList.add("hidden");
    document.getElementById("result-dashboard")?.classList.add("hidden");
    document.getElementById("capture-widget").classList.remove("hidden");
    document.getElementById("capture-title").textContent = title;
    document.getElementById("capture-sub").textContent = sub;

    _cap.keystrokeEvents = [];
    _cap.mouseEvents = [];
    _cap.typedOk = false;
    _cap.targetsHit = 0;

    document.getElementById("passphrase-text").innerHTML = _renderPassphrase("");
    document.getElementById("paste-warning")?.classList.remove("show");
    const input = document.getElementById("passphrase-input");
    input.disabled = false;
    input.value = "";
    input.focus();

    document.getElementById("btn-submit-capture").disabled = true;
    _setupMouseArena();
  },

  getPayload() {
    return { keystrokes: _cap.keystrokeEvents, mouse: _cap.mouseEvents };
  },

  isReady() {
    return _cap.typedOk && _cap.targetsHit >= TARGET_COUNT;
  },

  lock() {
    document.getElementById("passphrase-input").disabled = true;
    document.getElementById("btn-submit-capture").disabled = true;
  },

  hide() {
    document.getElementById("capture-widget").classList.add("hidden");
  },
};
