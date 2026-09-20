"""
app.py — BioPrint: Behavior-Based Login Security

Flask backend gluing together:
  database.py           (SQLite persistence)
  behavioral_engine.py  (feature extraction, scoring, bot detection)

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Hosting (e.g. a free-tier PaaS):
    gunicorn app:app
Environment variables (all optional):
    PORT             port for `python app.py`            (default 5000)
    HOST             bind address for `python app.py`    (default 127.0.0.1)
    BIOPRINT_DB      path of the SQLite file — point it at a persistent disk
    BIOPRINT_DEBUG   set to 1 for Flask debug mode (never on a public host)
"""

import os
import re
import sqlite3

from flask import Flask, request, jsonify, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash

import database as db
import behavioral_engine as be

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024     # reject request bodies over 2 MB

# Create the tables at import time so this works under gunicorn as well as
# `python app.py` (previously it only ran under __main__).
db.init_db()

ENROLLMENT_ROUNDS = 5          # how many samples we require during enrollment
ACCEPT_THRESHOLD = 58          # confidence (0-100) required to accept, purely on behavior
ADAPTIVE_UPDATES = True        # let accepted logins gently refine the profile

MAX_EVENTS = 6000              # per event list (keystrokes / mouse) in one request
MIN_ENROLL_MOUSE_MOVES = 5     # an enrollment round with less movement than this is unusable
MAX_PASSWORD_LEN = 256
# Usernames end up in URL paths (/api/user/<username>/status), so a "/" would
# make the account unreachable. Keep them to a safe character set.
USERNAME_RE = re.compile(r"[\w.@-]{1,64}")


# ----------------------------------------------------------------- helpers --

def _bad(message, code=400, **extra):
    return jsonify(ok=False, error=message, **extra), code


def _json_body():
    """The request body as a dict, or None if it isn't a JSON object."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _username(data):
    v = data.get("username")
    return v.strip() if isinstance(v, str) else ""


def _password(data):
    v = data.get("password")
    return v if isinstance(v, str) else ""


def _password_ok(user, password):
    return len(password) <= MAX_PASSWORD_LEN and check_password_hash(user["password_hash"], password)


def _parse_events(data):
    """Validate and clean the keystroke/mouse lists in a request body.
    Returns (keystrokes, mouse, error_message_or_None)."""
    lists = []
    for name in ("keystrokes", "mouse"):
        raw = data.get(name)
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return None, None, f"'{name}' must be a list."
        if len(raw) > MAX_EVENTS:
            return None, None, f"Too many '{name}' events."
        lists.append(raw)
    return be.clean_keystrokes(lists[0]), be.clean_mouse(lists[1]), None


# ---------------------------------------------------------- error handling --
# The front-end always calls r.json(); make sure /api/* never answers with an
# HTML error page (which would throw in the browser and freeze the UI).

@app.errorhandler(HTTPException)
def handle_http_error(e):
    if request.path.startswith("/api/"):
        message = "Request too large." if e.code == 413 else (e.description or e.name)
        return jsonify(ok=False, error=message), e.code
    return e


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception("Unhandled error on %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Internal server error."), 500
    return "Internal server error", 500


# ------------------------------------------------------------------- pages --

@app.route("/")
def index():
    return render_template("index.html", rounds=ENROLLMENT_ROUNDS)


@app.route("/dummy-login")
def dummy_login():
    """The standalone 'Dummy Login Page' deliverable: a page whose only
    job is to run the live authentication demo (password + behavior).
    Registration and enrollment happen on '/'; this page is where a
    judge or teammate tests logins against an already-enrolled account."""
    return render_template("dummy_login.html", threshold=ACCEPT_THRESHOLD)


# --------------------------------------------------------------- register --

@app.route("/api/register", methods=["POST"])
def api_register():
    data = _json_body()
    if data is None:
        return _bad("Invalid request body.")
    username = _username(data)
    password = _password(data)

    if not username or not password:
        return _bad("Username and password are required.")
    if not USERNAME_RE.fullmatch(username):
        return _bad("Username may only contain letters, numbers and . _ - @ (max 64 characters).")
    if len(password) < 4:
        return _bad("Password must be at least 4 characters.")
    if len(password) > MAX_PASSWORD_LEN:
        return _bad(f"Password must be at most {MAX_PASSWORD_LEN} characters.")

    if db.get_user(username):
        return _bad("That username is already taken.", 409)

    try:
        db.create_user(username, generate_password_hash(password))
    except sqlite3.IntegrityError:          # two registrations racing for the same name
        return _bad("That username is already taken.", 409)
    return jsonify(ok=True)


@app.route("/api/user/<username>/status")
def api_user_status(username):
    user = db.get_user(username)
    if not user:
        return jsonify(exists=False)
    samples = db.get_enrollment_samples(user["id"])
    return jsonify(
        exists=True,
        enrolled=bool(user["enrolled"]),
        samples_collected=len(samples),
        rounds_required=ENROLLMENT_ROUNDS,
    )


# -------------------------------------------------------------- enrollment --

@app.route("/api/enroll/sample", methods=["POST"])
def api_enroll_sample():
    data = _json_body()
    if data is None:
        return _bad("Invalid request body.")
    user = db.get_user(_username(data))
    if not user:
        return _bad("Unknown user — register first.", 404)
    if user["enrolled"]:
        return _bad("This user is already enrolled. "
                    "A baseline can only be recorded once per account.", 409)

    keystrokes, mouse, err = _parse_events(data)
    if err:
        return _bad(err)
    if not be.phrase_matches(keystrokes):
        return _bad("The typed text didn't match the phrase — please redo this round.")
    if sum(1 for e in mouse if e["type"] == "move") < MIN_ENROLL_MOUSE_MOVES:
        return _bad("Not enough mouse movement was captured — please redo this round.")

    existing = db.get_enrollment_samples(user["id"])
    if len(existing) >= ENROLLMENT_ROUNDS:
        return _bad("All enrollment rounds are already captured — finalize enrollment.", 409)

    features = be.extract_features(keystrokes, mouse)
    round_index = len(existing) + 1
    db.add_enrollment_sample(user["id"], round_index, features)

    return jsonify(
        ok=True,
        round=round_index,
        rounds_required=ENROLLMENT_ROUNDS,
        features=features,
        complete=round_index >= ENROLLMENT_ROUNDS,
    )


@app.route("/api/enroll/finalize", methods=["POST"])
def api_enroll_finalize():
    data = _json_body()
    if data is None:
        return _bad("Invalid request body.")
    user = db.get_user(_username(data))
    if not user:
        return _bad("Unknown user — register first.", 404)
    if user["enrolled"]:
        return _bad("This user is already enrolled.", 409)

    samples = db.get_enrollment_samples(user["id"])
    if len(samples) < 3:
        return _bad("Not enough enrollment samples yet.")

    profile = be.build_profile(samples)
    db.save_profile(user["id"], profile, len(samples))
    db.mark_enrolled(user["id"])

    return jsonify(ok=True, profile=profile, sample_count=len(samples))


# ------------------------------------------------------------------ login --

@app.route("/api/login/password", methods=["POST"])
def api_login_password():
    """Step 1 of login: the password itself. Kept separate from the
    behavioral check so the two signals are genuinely independent, as
    required — a correct password alone must never be sufficient."""
    data = _json_body()
    if data is None:
        return _bad("Invalid request body.")

    user = db.get_user(_username(data))
    if not user:
        return jsonify(ok=False, password_ok=False, error="Unknown user."), 404

    return jsonify(
        ok=True,
        password_ok=_password_ok(user, _password(data)),
        enrolled=bool(user["enrolled"]),
    )


@app.route("/api/login/verify", methods=["POST"])
def api_login_verify():
    """Step 2 of login: live behavioral check. This is the core of
    BioPrint — even a correct password can be BLOCKED here."""
    data = _json_body()
    if data is None:
        return _bad("Invalid request body.")
    username = _username(data)

    user = db.get_user(username)
    if not user:
        return _bad("Unknown user.", 404)

    keystrokes, mouse, err = _parse_events(data)
    if err:
        return _bad(err)

    password_ok = _password_ok(user, _password(data))
    profile_row = db.get_profile(user["id"])

    # The typed text must actually be the required phrase; the server never
    # used to look at which keys were recorded.
    phrase_ok = be.phrase_matches(keystrokes)

    features = be.extract_features(keystrokes, mouse)
    is_bot, bot_reasons = be.detect_bot(keystrokes, mouse, features)

    if profile_row:
        confidence, breakdown = be.score_attempt(profile_row["features"], features)
    else:
        confidence, breakdown = 0.0, []

    # Decision: password must be correct AND the phrase must match AND
    # behavior must clear the threshold AND the traffic must not look like
    # a bot/script/replay.
    bad_features = be.count_bad_features(breakdown)
    too_many_bad = bad_features >= be.MAX_BAD_FEATURES

    accepted = bool(password_ok and phrase_ok
                    and confidence >= ACCEPT_THRESHOLD and not is_bot
                    and not too_many_bad)

    explanation = be.explain(confidence, breakdown, is_bot, bot_reasons,
                             accepted, ACCEPT_THRESHOLD, phrase_ok=phrase_ok)
    if too_many_bad:
        explanation.insert(0, f"{bad_features} behavioral signals were far outside the "
                              f"enrolled baseline — blocked automatically.")

    attempt_id = db.log_attempt(
        user_id=user["id"], username_tried=username, password_ok=password_ok,
        is_bot=is_bot, confidence=confidence, accepted=accepted,
        features=features, explanation=explanation,
    )

    if accepted and ADAPTIVE_UPDATES and profile_row:
        updated = be.update_profile_adaptive(profile_row["features"], features)
        db.save_profile(user["id"], updated, profile_row["sample_count"] + 1)

    return jsonify(
        ok=True,
        attempt_id=attempt_id,
        password_ok=password_ok,
        phrase_ok=phrase_ok,
        bad_features=bad_features,
        is_bot=is_bot,
        bot_reasons=bot_reasons,
        confidence=confidence,
        threshold=ACCEPT_THRESHOLD,
        accepted=accepted,
        breakdown=breakdown,
        explanation=explanation,
    )


@app.route("/api/attempt/<int:attempt_id>")
def api_attempt(attempt_id):
    attempt = db.get_attempt(attempt_id)
    if not attempt:
        return _bad("Not found", 404)
    return jsonify(ok=True, attempt=attempt)


@app.route("/api/user/<username>/history")
def api_user_history(username):
    user = db.get_user(username)
    if not user:
        return _bad("Unknown user.", 404)
    return jsonify(ok=True, history=db.get_attempts_for_user(user["id"]))


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("BIOPRINT_DEBUG") == "1",
    )
