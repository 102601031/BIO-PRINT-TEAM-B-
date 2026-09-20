# BioPrint — Behavior-Based Login Security

A working prototype for **Event 2: BioPrint**. Authenticates users by
*how* they type and move the mouse, not just what they type — a
correct password alone is never enough to log in.

## Run it

**Easiest:** double-click `run_bioprint.bat` (Windows) or run `./run_bioprint.sh`
(Mac/Linux). It installs dependencies and opens the app in your browser
automatically.

**Manual:**
```bash
pip install -r requirements.txt --break-system-packages   # or use a venv
python app.py
```

Open **http://127.0.0.1:5000**. The SQLite database (`bioprint.db`) is
created automatically on first run.


## Pages

- **`/`** — Register + Enroll. Create an account, then complete 5
  behavioral-baseline rounds. Enrollment is **one-time only** — once
  finalized, an account cannot be re-enrolled (enforced server-side).
  Finishing enrollment automatically redirects to the Dummy Login page.
- **`/dummy-login`** — the standalone login-testing page (the "Dummy
  Login Page" deliverable). Enter credentials for an already-enrolled
  account and run the live behavior check independently of the
  registration flow.

## How to try it

1. Go to **`/`**, **Register** a username + password.
2. Switch to **Enroll**, click **Start / resume enrollment**, and
   complete 5 short rounds: type the on-screen phrase and click 4
   targets in order. Behave a little differently each round — that's
   what teaches the system your *normal* range of variation.
3. On completion you're redirected to **`/dummy-login`** automatically.
   Sign in there in two explicit steps:
   - **Step 1 — password.** A wrong password stops immediately with an
     "Incorrect password" message and lets you retry — it never
     triggers the behavior capture step.
   - **Step 2 — live behavior check**, unlocked only once the password
     is confirmed correct. Repeat the typing + clicking task once. You'll see:
   - **ACCESS GRANTED / BLOCKED**
   - A confidence score (0–100) with the accept threshold marked
   - A per-signal breakdown (which behaviors deviated, and by how many
     standard deviations from your baseline)
   - A plain-language explanation
4. Try logging in with the **right password** but **deliberately typing
   much faster/slower or moving the mouse very differently** — it gets
   blocked even though the password is correct.
5. Try pasting into the passphrase field (Ctrl+V, right-click paste, or
   drag-drop) — it's blocked outright, with a visible warning, since a
   pasted phrase has no real keystroke timing to measure.
6. Open the browser console and try scripting a login (e.g. dispatching
   synthetic `mousemove`/`keydown` events, or replaying a canned event
   log) — the bot detector flags perfectly straight mouse paths,
   zero-jitter movement, and machine-uniform keystroke timing. (In
   testing, this also correctly catches browser-automation tools like
   Playwright/Selenium driving the page.)

## Architecture

```
app.py                    Flask routes (register / enroll / dummy-login / verify)
database.py                SQLite persistence (users, samples, profiles, attempt log)
behavioral_engine.py        Feature extraction, profile building, scoring, bot detection
templates/index.html        Register + Enroll page
templates/dummy_login.html  Standalone login-testing page
static/js/capture.js        Shared behavior-capture widget (used by both pages)
static/js/app.js            Register + Enroll page logic
static/js/dummy_login.js    Dummy Login page logic
static/css/style.css        Styling
make_report.py              Generates report.pdf (the technical report deliverable)
run_bioprint.bat / .sh      One-click launchers
```

### How the fingerprint works (Core Requirements)

- **Enrollment flow** (custom-built): 5 rounds of typing a fixed
  phrase + clicking 4 targets, so behavior is sampled across natural
  round-to-round variation rather than a single snapshot.
- **Behavioral fingerprinting engine** (`behavioral_engine.py`):
  extracts 12 numeric features independent of the password —
  keystroke dwell/flight times, typing speed, backspace rate, mouse
  velocity/acceleration, path straightness, click-hold time, and
  mouse jitter. Enrollment samples are reduced to a per-feature
  **(mean, std)** profile — a lightweight, fully-transparent
  statistical model (no black-box ML dependency required).
- **Live authentication check**: a login sample's features are
  compared to the profile with a weighted RMS z-score (a diagonal
  Mahalanobis-style distance), mapped to a 0–100 confidence score.
  Below the threshold → blocked, **even with the correct password**,
  and with **no OTP or secondary verification** involved anywhere.
- **Bot / non-human detection** is a fully independent check (not
  password-aware, not profile-aware): near-zero mouse movement,
  perfectly straight paths, zero directional jitter, or machine-exact
  keystroke timing all flag traffic as non-human.
- **Working demo**: the UI itself is the demo — genuine logins are
  accepted, mimicked/inconsistent behavior and scripted input are
  blocked, all visible live.

### Stretch goals implemented

- **Adaptive profiles**: every *accepted* login nudges the profile's
  mean/std with an exponential moving average, so slow genuine drift
  (new mouse, tired hands) doesn't lock the real user out over time.
- **Confidence score + live dashboard**: shown after every attempt.
- **Explainability**: a human-readable summary lists exactly which
  behavioral signals triggered a block, and by how much.
- Multi-input support (touchpad / mouse / touch) works as-is since the
  browser reports pointer coordinates uniformly — a future pass could
  add pressure/touch-radius features for touch-specific fingerprinting.

## Notes / things to extend for production

- This is a development server (`app.run(debug=True)`); front it with
  gunicorn/uwsgi + a real WSGI deployment for anything beyond a demo.
- The passphrase is fixed for simplicity; a production system would
  likely use the user's *own* free-typed input (e.g. their normal
  login form) rather than a shared phrase.
- Feature weights and the accept threshold (`ACCEPT_THRESHOLD` in
  `app.py`) are tunable constants — in a real deployment you'd tune
  these against a labeled dataset of genuine vs. impostor attempts.
