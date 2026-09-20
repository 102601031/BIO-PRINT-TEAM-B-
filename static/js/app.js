/* BioPrint front-end — Register + Enroll page.
 * Live login testing lives on its own page: /dummy-login
 * (see static/js/dummy_login.js). Both pages share the raw capture
 * logic in static/js/capture.js.
 */

const ROUNDS_REQUIRED = Number(document.getElementById("rounds-required")?.textContent || 5);

const enrollState = {
  username: null,
};

// ---------------------------------------------------------------- tabs --
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.add("hidden"));
    btn.classList.add("active");
    document.querySelector(`.panel[data-panel="${btn.dataset.tab}"]`).classList.remove("hidden");
    resetStage();
    resetEnrollUI();
  });
});

function resetStage() {
  BioCapture.hide();
  document.getElementById("result-dashboard").classList.add("hidden");
  document.getElementById("stage-empty").classList.remove("hidden");
}

// A fresh registration (or switching tabs) must never show a leftover
// round tracker or status message from a previously-viewed account.
function resetEnrollUI() {
  enrollState.username = null;
  document.getElementById("enroll-round-tracker").innerHTML = "";
  setStatus("enroll-status", "", "");
}

// ------------------------------------------------------------ register --
document.getElementById("btn-register").addEventListener("click", async () => {
  const username = document.getElementById("reg-username").value.trim();
  const password = document.getElementById("reg-password").value;
  const el = document.getElementById("reg-status");
  el.className = "status-line";
  el.textContent = "creating account…";

  const res = await postJSON("/api/register", { username, password });
  if (res.ok) {
    el.className = "status-line ok";
    el.textContent = `account created — switch to Enroll to build your behavioral baseline.`;
    resetEnrollUI();                                  // never inherit stale enroll state
    document.getElementById("enroll-username").value = username;
    document.getElementById("reg-password").value = "";
  } else {
    el.className = "status-line err";
    el.textContent = res.error || "registration failed.";
  }
});

// -------------------------------------------------------------- enroll --
document.getElementById("btn-start-enroll").addEventListener("click", startEnrollRound);
document.getElementById("enroll-username").addEventListener("change", () => {
  resetEnrollUI();   // typing a *different* username always starts from empty
  startEnrollRound();
});

async function startEnrollRound() {
  const username = document.getElementById("enroll-username").value.trim();
  if (!username) return;

  const status = await getJSON(`/api/user/${encodeURIComponent(username)}/status`);
  if (!status.exists) {
    setStatus("enroll-status", "no such user — register first.", "err");
    return;
  }
  if (status.enrolled) {
    setStatus("enroll-status", "already enrolled — a baseline can only be recorded once. Head to the Dummy Login page to test it.", "ok");
    renderRoundTracker(status.rounds_required, status.rounds_required);
    return;
  }

  enrollState.username = username;
  const round = status.samples_collected + 1;
  renderRoundTracker(status.samples_collected, status.rounds_required);
  BioCapture.begin({
    title: "Enrollment",
    sub: `round ${round} of ${status.rounds_required} — behave naturally, this teaches BioPrint your baseline.`,
  });
}

function renderRoundTracker(done, total) {
  const track = document.getElementById("enroll-round-tracker");
  track.innerHTML = "";
  for (let i = 0; i < total; i++) {
    const dot = document.createElement("div");
    dot.className = "round-dot" + (i < done ? " done" : i === done ? " current" : "");
    track.appendChild(dot);
  }
}

document.getElementById("btn-submit-capture").addEventListener("click", async () => {
  if (!BioCapture.isReady() || !enrollState.username) return;
  BioCapture.lock();

  const payload = { username: enrollState.username, ...BioCapture.getPayload() };
  const res = await postJSON("/api/enroll/sample", payload);
  if (!res.ok) { setStatus("enroll-status", res.error, "err"); return; }

  renderRoundTracker(res.round, res.rounds_required);

  if (res.complete) {
    const fin = await postJSON("/api/enroll/finalize", { username: enrollState.username });
    if (fin.ok) {
      setStatus("enroll-status", `baseline complete (${fin.sample_count} samples) — redirecting to the Dummy Login page…`, "ok");
      resetStage();
      setTimeout(() => {
        window.location.href = `/dummy-login?username=${encodeURIComponent(enrollState.username)}`;
      }, 1200);
    } else {
      setStatus("enroll-status", fin.error, "err");
    }
  } else {
    setStatus("enroll-status", `sample ${res.round} of ${res.rounds_required} captured — next round starting…`, "ok");
    setTimeout(startEnrollRound, 700);
  }
});

// -------------------------------------------------------------- helpers --
function setStatus(id, text, cls) {
  const el = document.getElementById(id);
  el.className = "status-line " + (cls || "");
  el.textContent = text;
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}
async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
