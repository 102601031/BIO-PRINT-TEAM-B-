/* BioPrint front-end — Dummy Login page.
 * Two explicit steps:
 *   1. Password is checked FIRST (via /api/login/password). A wrong
 *      password stops here with a clear error and lets the user retry —
 *      it never reaches the behavior capture step.
 *   2. Only once the password is confirmed correct does the live
 *      behavioral check (step 2, using capture.js) begin.
 * The final /api/login/verify call re-checks the password server-side
 * too (defense in depth), but the UI gate above is what keeps a wrong
 * password from ever triggering a behavior capture attempt.
 */

const loginState = { username: null, password: null, passwordVerified: false };

// Pre-fill the username if we arrived here right after enrollment.
const params = new URLSearchParams(window.location.search);
if (params.get("username")) {
  document.getElementById("login-username").value = params.get("username");
  loadHistory(params.get("username"));
}

const usernameInput = document.getElementById("login-username");
const passwordInput = document.getElementById("login-password");
const verifyBtn = document.getElementById("btn-login-password");

verifyBtn.addEventListener("click", verifyPassword);
passwordInput.addEventListener("keydown", (e) => { if (e.key === "Enter") verifyPassword(); });

async function verifyPassword() {
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  const el = document.getElementById("login-status");

  if (!username || !password) {
    el.className = "status-line err";
    el.textContent = "enter username and password.";
    return;
  }

  verifyBtn.disabled = true;
  el.className = "status-line";
  el.textContent = "checking password…";

  const res = await postJSON("/api/login/password", { username, password });

  if (!res.ok) {
    verifyBtn.disabled = false;
    el.className = "status-line err";
    el.textContent = "no account with that username.";
    return;
  }

  if (!res.password_ok) {
    // Wrong password — stop here. No behavior capture is triggered.
    verifyBtn.disabled = false;
    el.className = "status-line err";
    el.textContent = "Incorrect password. Please try again.";
    passwordInput.value = "";
    passwordInput.focus();
    passwordInput.classList.add("shake");
    setTimeout(() => passwordInput.classList.remove("shake"), 400);
    return;
  }

  if (!res.enrolled) {
    verifyBtn.disabled = false;
    el.className = "status-line err";
    el.textContent = "Password correct, but this account hasn't finished enrollment yet — go register/enroll first.";
    return;
  }

  // Password confirmed correct — now, and only now, unlock step 2.
  el.className = "status-line ok";
  el.textContent = "Password verified.";
  loginState.username = username;
  loginState.password = password;
  loginState.passwordVerified = true;

  usernameInput.disabled = true;
  passwordInput.disabled = true;
  verifyBtn.disabled = true;

  document.getElementById("verified-username-label").textContent = username;
  document.getElementById("step2-panel").classList.remove("hidden");

  BioCapture.begin({
    title: "Live behavior check",
    sub: "one round — this is compared against the enrolled baseline for " + username,
  });
  loadHistory(username);
}

document.getElementById("btn-switch-account").addEventListener("click", resetLoginForm);

function resetLoginForm() {
  loginState.username = null;
  loginState.password = null;
  loginState.passwordVerified = false;

  usernameInput.disabled = false;
  passwordInput.disabled = false;
  passwordInput.value = "";
  verifyBtn.disabled = false;
  document.getElementById("login-status").textContent = "";
  document.getElementById("login-status").className = "status-line";
  document.getElementById("step2-panel").classList.add("hidden");

  BioCapture.hide();
  document.getElementById("result-dashboard").classList.add("hidden");
  document.getElementById("stage-empty").classList.remove("hidden");
  usernameInput.focus();
}

document.getElementById("btn-submit-capture").addEventListener("click", async () => {
  if (!BioCapture.isReady() || !loginState.passwordVerified) return;
  BioCapture.lock();

  const payload = {
    username: loginState.username,
    password: loginState.password,
    ...BioCapture.getPayload(),
  };
  const res = await postJSON("/api/login/verify", payload);
  loginState.lastPayload = payload;          // kept in case the user adds this round to their baseline
  BioCapture.hide();
  renderResult(res);
  loadHistory(loginState.username);
});

document.getElementById("btn-try-again").addEventListener("click", () => {
  document.getElementById("result-dashboard").classList.add("hidden");
  BioCapture.begin({
    title: "Live behavior check",
    sub: "one round — this is compared against the enrolled baseline for " + loginState.username,
  });
});

document.getElementById("btn-add-round").addEventListener("click", async () => {
  const btn = document.getElementById("btn-add-round");
  const status = document.getElementById("add-round-status");
  if (!loginState.lastPayload) return;

  btn.disabled = true;
  status.className = "status-line";
  status.textContent = "adding this attempt to your baseline…";

  const res = await postJSON("/api/enroll/add-round", loginState.lastPayload);

  if (!res.ok) {
    status.className = "status-line err";
    status.textContent = res.error || "Could not add this round.";
    btn.disabled = false;
    return;
  }

  status.className = "status-line ok";
  status.textContent = `Baseline updated — now trained on ${res.sample_count} rounds.`;
  btn.hidden = true;
});

// -------------------------------------------------------------- result --
function renderResult(res) {
  const dash = document.getElementById("result-dashboard");
  dash.classList.remove("hidden");

  const addBtn = document.getElementById("btn-add-round");
  const addStatus = document.getElementById("add-round-status");
  addBtn.hidden = !res.accepted;
  addBtn.disabled = false;
  addStatus.textContent = "";
  addStatus.className = "status-line";

  const banner = document.getElementById("verdict-banner");
  banner.className = "verdict-banner " + (res.accepted ? "accept" : "block");
  document.getElementById("verdict-word").textContent = res.accepted ? "ACCESS GRANTED" : "ACCESS BLOCKED";

  let subParts = [];
  if (!res.password_ok) subParts.push("password did not match");
  if (res.is_bot) subParts.push("non-human input pattern detected");
  if (res.password_ok && !res.is_bot && !res.accepted) subParts.push("behavior did not match enrolled baseline");
  if (res.accepted) subParts.push("password correct, behavior matched baseline, no bot signals");
  document.getElementById("verdict-sub").textContent = subParts.join(" · ");

  const conf = res.confidence || 0;
  document.getElementById("confidence-number").textContent = conf.toFixed(1);
  document.getElementById("confidence-fill").style.width = Math.min(conf, 100) + "%";
  document.getElementById("confidence-fill").style.background = res.accepted ? "var(--signal)" : "var(--danger)";
  document.getElementById("threshold-mark").style.left = (res.threshold || 0) + "%";

  const rows = document.getElementById("breakdown-rows");
  rows.innerHTML = "";
  (res.breakdown || []).forEach(b => {
    const pct = Math.min(100, (b.z_score / 4) * 100);
    const hot = b.z_score >= 1.5;
    const row = document.createElement("div");
    row.className = "breakdown-row";
    row.innerHTML = `
      <div class="feat-name">${b.description}</div>
      <div class="z-bar-track"><div class="z-bar-fill ${hot ? "hot" : ""}" style="width:${pct}%"></div></div>
      <div class="z-val">${b.z_score.toFixed(2)}σ</div>
    `;
    rows.appendChild(row);
  });

  document.getElementById("explanation-text").textContent = (res.explanation || []).join("\n");
}

// -------------------------------------------------------------- history --
async function loadHistory(username) {
  const res = await getJSON(`/api/user/${encodeURIComponent(username)}/history`);
  if (!res.ok) return;
  const block = document.getElementById("history-block");
  const body = document.getElementById("history-body");
  body.innerHTML = "";
  res.history.forEach(h => {
    const tr = document.createElement("tr");
    const time = new Date(h.timestamp * 1000).toLocaleTimeString();
    tr.innerHTML = `
      <td>${time}</td>
      <td class="${h.password_ok ? "tag-yes" : "tag-no"}">${h.password_ok ? "ok" : "no"}</td>
      <td class="${h.is_bot ? "tag-bad" : "tag-no"}">${h.is_bot ? "yes" : "no"}</td>
      <td>${h.confidence.toFixed(0)}</td>
      <td class="${h.accepted ? "tag-yes" : "tag-bad"}">${h.accepted ? "granted" : "blocked"}</td>
    `;
    body.appendChild(tr);
  });
  block.hidden = false;
}

// -------------------------------------------------------------- helpers --
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}
async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
