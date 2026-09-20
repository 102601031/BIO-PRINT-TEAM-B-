"""
behavioral_engine.py — the actual "BioPrint" logic.

Everything here is deliberately dependency-light (pure Python + a tiny
amount of arithmetic) so the statistical approach is fully transparent
and easy to defend/explain: this is a from-scratch behavioral biometric
model, not a wrapper around a black-box library.

Pipeline
--------
1. extract_features(keystroke_events, mouse_events)
      raw browser events  ->  a flat dict of numeric behavioral features

2. build_profile(list_of_feature_dicts)
      several enrollment samples -> per-feature (mean, std) profile

3. score_attempt(profile, features)
      one login sample vs. the profile -> confidence score (0-100)
      + a per-feature z-score breakdown for the dashboard

4. detect_bot(keystroke_events, mouse_events, features)
      independent, password-agnostic check for scripted / replayed /
      non-human input

5. explain(...)
      turns the numbers above into a human-readable list of reasons
"""

import difflib
import math
import statistics

EPS = 1e-6

# The phrase users type during enrollment and login. Keep in sync with
# PASSPHRASE in static/js/capture.js. The server re-checks it against the
# recorded keystrokes so a payload that doesn't correspond to the phrase
# is rejected.
PASSPHRASE = "the quick brown fox jumps over the lazy dog"
PHRASE_MATCH_MIN = 0.90      # tolerance for the odd mid-string edit (difflib ratio)

# --- mouse stroke segmentation -------------------------------------------
MAX_GAP_MS = 200.0           # a pause longer than this ends a stroke
MIN_DT_MS = 1.0              # samples closer than this in time are dropped
MIN_STROKE_POINTS = 4        # a stroke needs at least this many points
MIN_STROKE_PX = 20.0         # straightness/jitter only measured on strokes this long
MAX_STROKE_STRAIGHTNESS = 5.0

# --- profile statistics ----------------------------------------------------
Z_CAP = 4.0                  # one wild feature can't dominate the score
BAD_FEATURE_Z = 2.5          # a feature counts as "wrong" when it is this many std devs off
MAX_BAD_FEATURES = 3         # this many wrong features => automatic block

# Human-readable descriptions used by the dashboard / explanation engine.
FEATURE_INFO = {
    "ks_mean_dwell":      "how long keys are held down",
    "ks_std_dwell":       "consistency of key-hold duration",
    "ks_mean_flight":     "pause between releasing one key and pressing the next",
    "ks_std_flight":      "rhythm consistency between keystrokes",
    "ks_typing_speed":    "overall typing speed",
    "ks_backspace_rate":  "how often corrections/backspaces are made",
    "ms_avg_velocity":    "average mouse movement speed",
    "ms_std_velocity":    "variability of mouse movement speed",
    "ms_avg_accel":       "average mouse acceleration/deceleration",
    "ms_straightness":    "how curved vs. straight mouse paths are",
    "ms_avg_click_hold":  "how long mouse buttons are held on click",
    "ms_jitter":          "small involuntary hand tremor in the path",
}


# ------------------------------------------------------------ sanitizing --
# Events arrive from the browser and are untrusted. These helpers drop
# anything malformed so the extractors below can never crash on bad input.

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean_keystrokes(events):
    """Keep only well-formed {"key","type","t"} events, sorted by time."""
    out = []
    if not isinstance(events, list):
        return out
    for e in events:
        if not isinstance(e, dict):
            continue
        k, typ, t = e.get("key"), e.get("type"), e.get("t")
        if not isinstance(k, str) or not (0 < len(k) <= 16):
            continue
        if typ not in ("down", "up") or not _is_num(t):
            continue
        out.append({"key": k, "type": typ, "t": float(t)})
    out.sort(key=lambda ev: ev["t"])          # stable: keeps down-before-up on ties
    return out


def clean_mouse(events):
    """Keep only well-formed mouse events, sorted by time. 'move' events
    must carry x/y; 'down'/'up' keep x/y only if both are valid numbers."""
    out = []
    if not isinstance(events, list):
        return out
    for e in events:
        if not isinstance(e, dict):
            continue
        typ, t = e.get("type"), e.get("t")
        if typ not in ("move", "down", "up") or not _is_num(t):
            continue
        has_xy = _is_num(e.get("x")) and _is_num(e.get("y"))
        if typ == "move" and not has_xy:
            continue
        ev = {"type": typ, "t": float(t)}
        if has_xy:
            ev["x"], ev["y"] = float(e["x"]), float(e["y"])
        out.append(ev)
    out.sort(key=lambda ev: ev["t"])
    return out


# ------------------------------------------------------------ phrase check --

def typed_text(keystroke_events):
    """Rebuild what was typed from the key-down events (Backspace deletes
    the previous character)."""
    buf = []
    for e in clean_keystrokes(keystroke_events):
        if e["type"] != "down":
            continue
        if e["key"] == "Backspace":
            if buf:
                buf.pop()
        elif len(e["key"]) == 1:
            buf.append(e["key"])
    return "".join(buf)


def phrase_matches(keystroke_events, expected=PASSPHRASE, min_ratio=PHRASE_MATCH_MIN):
    """True if the recorded keystrokes spell (approximately) the required phrase."""
    typed = typed_text(keystroke_events)
    if not typed:
        return False
    return difflib.SequenceMatcher(None, typed, expected).ratio() >= min_ratio


# ------------------------------------------------------------- keystroke --

def extract_keystroke_features(events):
    """
    events: list of {"key": str, "type": "down"|"up", "t": float(ms)}
    Returns dwell/flight-based features. Robust to missing/partial/malformed data.
    """
    events = clean_keystrokes(events)
    if not events:
        return _zeroed(["ks_mean_dwell", "ks_std_dwell", "ks_mean_flight",
                         "ks_std_flight", "ks_typing_speed", "ks_backspace_rate"])

    dwell_times = []
    flight_times = []
    last_up_t = None
    backspaces = 0
    char_count = 0            # printable characters only (Backspace is not a character)
    pending = {}              # stack per key so repeated keys are handled correctly

    for ev in events:
        k, typ, t = ev["key"], ev["type"], ev["t"]
        if typ == "down":
            pending.setdefault(k, []).append(t)
            if last_up_t is not None:
                flight_times.append(max(0.0, t - last_up_t))
            # count on key-DOWN only (counting up as well doubled the rate)
            if k.lower() in ("backspace", "delete"):
                backspaces += 1
            else:
                char_count += 1
        else:  # "up"
            stack = pending.get(k)
            if stack:
                down_t = stack.pop()
                dwell_times.append(max(0.0, t - down_t))
            last_up_t = t

    total_time_s = max((events[-1]["t"] - events[0]["t"]) / 1000.0, EPS)
    typing_speed = char_count / total_time_s

    return {
        "ks_mean_dwell": _mean(dwell_times),
        "ks_std_dwell": _std(dwell_times),
        "ks_mean_flight": _mean(flight_times),
        "ks_std_flight": _std(flight_times),
        "ks_typing_speed": typing_speed,
        "ks_backspace_rate": backspaces / max(char_count, 1),
    }


# ----------------------------------------------------------------- mouse --

def _split_strokes(events):
    """
    Split the (time-sorted, cleaned) mouse events into strokes: runs of
    points from one click to the next. A stroke also ends at any pause
    longer than MAX_GAP_MS (e.g. the cursor left the arena and came back),
    so gaps never masquerade as slow movement. Each stroke is a list of
    (x, y, t_ms).
    """
    strokes, cur = [], []

    def flush():
        nonlocal cur
        if len(cur) >= MIN_STROKE_POINTS:
            strokes.append(cur)
        cur = []

    def add(pt):
        if cur:
            dt = pt[2] - cur[-1][2]
            if dt > MAX_GAP_MS:
                flush()
            elif dt < MIN_DT_MS:
                return                      # duplicate timestamp
        cur.append(pt)

    for e in events:
        typ = e["type"]
        pt = (e["x"], e["y"], e["t"]) if "x" in e else None
        if typ == "move":
            add(pt)
        elif typ == "down":
            if pt:
                add(pt)                     # the stroke ends at the click
            flush()
        elif typ == "up":
            flush()
            if pt:
                cur.append(pt)              # the next stroke starts where the click released
    flush()
    return strokes


def extract_mouse_features(events):
    """
    events: list of {"x": float, "y": float, "t": float(ms), "type": "move"|"down"|"up"}
    Movement features are computed PER STROKE (click to click) and then
    pooled/averaged, instead of over one long path covering every target.
    """
    events = clean_mouse(events)
    strokes = _split_strokes(events)
    if not strokes:
        return _zeroed(["ms_avg_velocity", "ms_std_velocity", "ms_avg_accel",
                         "ms_straightness", "ms_avg_click_hold", "ms_jitter"])

    vels, accels, straights, jitters = [], [], [], []
    for s in strokes:
        step_d, step_v, angles = [], [], []
        for (x0, y0, t0), (x1, y1, t1) in zip(s, s[1:]):
            dt = (t1 - t0) / 1000.0
            d = math.hypot(x1 - x0, y1 - y0)
            step_d.append(d)
            step_v.append(d / dt)
            if d > 0:
                angles.append(math.atan2(y1 - y0, x1 - x0))
        vels.extend(step_v)
        accels.extend(step_v[i] - step_v[i - 1] for i in range(1, len(step_v)))

        net = math.hypot(s[-1][0] - s[0][0], s[-1][1] - s[0][1])
        if net >= MIN_STROKE_PX:
            straights.append(min(sum(step_d) / net, MAX_STROKE_STRAIGHTNESS))
            # jitter: how much local direction wobbles (real hands never move
            # in a perfectly smooth vector; scripted/injected paths often do)
            if len(angles) >= 2:
                jitters.append(_mean([abs(_angle_diff(angles[i], angles[i - 1]))
                                      for i in range(1, len(angles))]))

    # click hold time = mousedown -> mouseup, matched in order
    downs = [e["t"] for e in events if e["type"] == "down"]
    ups = [e["t"] for e in events if e["type"] == "up"]
    holds = [max(0.0, u - d) for d, u in zip(downs, ups)]

    return {
        "ms_avg_velocity": _mean(vels),
        "ms_std_velocity": _std(vels),
        "ms_avg_accel": _mean([abs(a) for a in accels]),
        "ms_straightness": _mean(straights),
        "ms_avg_click_hold": _mean(holds),
        "ms_jitter": _mean(jitters),
    }


def extract_features(keystroke_events, mouse_events):
    feats = {}
    feats.update(extract_keystroke_features(keystroke_events))
    feats.update(extract_mouse_features(mouse_events))
    return feats


# ---------------------------------------------------------------- profile --

# Minimum plausible standard deviation per feature: (relative to the mean,
# absolute). Five enrollment rounds under-estimate how much a person varies
# between sessions (and n=5 std estimates are noisy), so the std is never
# allowed to fall below max(relative * |mean|, absolute). Units: ms, px/s,
# chars/s, radians.
#
# THIS IS THE MAIN TUNING DIAL. Larger floors -> fewer genuine users locked
# out, but "close" impostors get in more easily (in synthetic tests, floors of
# 10-15% let a typist who was 20% different on every timing feature through
# about two times in three). These are conservative starting points; re-tune
# them together with ACCEPT_THRESHOLD in app.py on real genuine/impostor
# attempts.
FEATURE_FLOORS = {
    "ks_mean_dwell":     (0.08, 2.5),
    "ks_std_dwell":      (0.12, 2.0),
    "ks_mean_flight":    (0.08, 4.0),
    "ks_std_flight":     (0.12, 4.0),
    "ks_typing_speed":   (0.08, 0.15),
    "ks_backspace_rate": (0.00, 0.03),
    "ms_avg_velocity":   (0.12, 12.0),
    "ms_std_velocity":   (0.12, 12.0),
    "ms_avg_accel":      (0.12, 25.0),
    "ms_straightness":   (0.03, 0.02),   # a ratio >= 1, so its natural spread is small
    "ms_avg_click_hold": (0.08, 4.0),
    "ms_jitter":         (0.12, 0.03),
}
DEFAULT_FLOOR = (0.15, 0.01)


def _std_floor(feature, mean):
    rel, absolute = FEATURE_FLOORS.get(feature, DEFAULT_FLOOR)
    return max(abs(mean) * rel, absolute)


def build_profile(sample_list):
    """
    sample_list: list of feature dicts (one per enrollment round).
    Returns {feature: {"mean": .., "std": ..}}. The std is the SAMPLE
    standard deviation (n-1) with a per-feature floor, so a
    perfectly-consistent enrollment (or n=1) never causes division blow-up
    or an unrealistically tight profile.
    """
    if not sample_list:
        return {}
    profile = {}
    for k in FEATURE_INFO:
        vals = [s.get(k, 0.0) for s in sample_list]
        mean = _mean(vals)
        std = _sample_std(vals)
        profile[k] = {"mean": mean, "std": max(std, _std_floor(k, mean))}
    return profile


def update_profile_adaptive(profile, new_features, alpha=0.12):
    """Stretch goal: exponential moving average so genuine behavioral
    drift (new mouse, new desk, tired hands) doesn't slowly lock a real
    user out. Only ever called after an ACCEPTED login."""
    updated = {}
    for k, stats in profile.items():
        new_val = new_features.get(k, stats["mean"])
        new_mean = (1 - alpha) * stats["mean"] + alpha * new_val
        deviation = abs(new_val - stats["mean"])
        new_std = (1 - alpha) * stats["std"] + alpha * max(deviation, stats["std"] * 0.3)
        updated[k] = {"mean": new_mean, "std": max(new_std, _std_floor(k, new_mean))}
    return updated


# ------------------------------------------------------------------ score --

# relative importance of each signal in the aggregate confidence score
FEATURE_WEIGHTS = {
    "ks_mean_dwell": 1.2, "ks_std_dwell": 0.8, "ks_mean_flight": 1.2,
    "ks_std_flight": 0.8, "ks_typing_speed": 1.0, "ks_backspace_rate": 0.5,
    "ms_avg_velocity": 1.0, "ms_std_velocity": 0.8, "ms_avg_accel": 0.8,
    "ms_straightness": 1.2, "ms_avg_click_hold": 0.7, "ms_jitter": 1.0,
}


def score_attempt(profile, features):
    """
    Returns (confidence 0-100, per_feature list of dicts sorted by
    how much each feature deviated from baseline).
    """
    if not profile:
        return 0.0, []

    breakdown = []
    weighted_sq_sum = 0.0
    weight_sum = 0.0
    for k, stats in profile.items():
        val = features.get(k, 0.0)
        z = abs(val - stats["mean"]) / stats["std"]          # raw, shown in the breakdown
        z_used = min(z, Z_CAP)                                # capped, used in the aggregate
        w = FEATURE_WEIGHTS.get(k, 1.0)
        weighted_sq_sum += w * (z_used ** 2)
        weight_sum += w
        breakdown.append({
            "feature": k,
            "description": FEATURE_INFO.get(k, k),
            "value": val,
            "baseline_mean": stats["mean"],
            "z_score": z,
        })

    # weighted RMS z-score across all features (a Mahalanobis-style
    # distance under a diagonal/independent-feature assumption)
    rms_z = math.sqrt(weighted_sq_sum / max(weight_sum, EPS))

    # map distance -> confidence: 0 std away = 100, 1 ≈ 82, 2 ≈ 46, 3 ≈ 17
    # (the accept threshold of 55 sits at an RMS distance of about 1.75)
    confidence = max(0.0, 100.0 * math.exp(-0.5 * (rms_z / 1.6) ** 2))

    breakdown.sort(key=lambda d: d["z_score"], reverse=True)
    return round(confidence, 1), breakdown


def count_bad_features(breakdown):
    """How many features deviate by at least BAD_FEATURE_Z standard deviations."""
    return sum(1 for b in breakdown if b["z_score"] >= BAD_FEATURE_Z)


# ------------------------------------------------------------- bot check --

def detect_bot(keystroke_events, mouse_events, features):
    """
    Independent of the password and of any enrolled profile: flags input
    that doesn't look like it came from a human hand at all, e.g. a
    script driving the DOM or a replayed/canned event log.
    """
    reasons = []

    mouse_events = clean_mouse(mouse_events)
    moves = [e for e in mouse_events if e["type"] == "move"]

    # 1. No mouse movement at all before a click -> synthetic event dispatch
    if len(moves) < 3:
        reasons.append("virtually no natural mouse movement was captured")

    # 2. Perfectly straight-line mouse strokes (straightness ~ 1.0) are a
    #    classic signature of programmatically generated coordinates.
    #    ms_straightness is now the mean over individual strokes, so the
    #    cut-off is tighter than it was for the old whole-path ratio.
    if features.get("ms_straightness", 0) and features["ms_straightness"] < 1.01 and len(moves) >= 3:
        reasons.append("mouse path was unnaturally straight (no human wobble)")

    # 3. Zero jitter combined with nonzero movement = synthetic path
    if len(moves) >= 5 and features.get("ms_jitter", 0) < 0.01:
        reasons.append("mouse path showed no directional micro-adjustments")

    # 4. Keystroke timing with near-zero variance = scripted key events
    #    (a real human never presses keys at machine-perfect intervals)
    if features.get("ks_std_flight", None) is not None and features.get("ks_mean_flight", 0) > 0:
        if features["ks_std_flight"] < 0.5:  # ms
            reasons.append("keystroke timing was machine-perfectly uniform")

    # 5. Implausibly fast, uniform dwell time (sub-human key-hold speed)
    if features.get("ks_mean_dwell", 0) and 0 < features["ks_mean_dwell"] < 8:
        reasons.append("key-hold durations were faster than human muscle response")

    # 6. Mouse velocity that's constant to an unrealistic degree
    if features.get("ms_avg_velocity", 0) > 0 and features.get("ms_std_velocity", 1) / max(features["ms_avg_velocity"], EPS) < 0.03 and len(moves) >= 5:
        reasons.append("mouse speed was suspiciously constant across the whole path")

    return (len(reasons) > 0), reasons


# --------------------------------------------------------------- explain --

def explain(confidence, breakdown, is_bot, bot_reasons, accepted, threshold,
            phrase_ok=True):
    lines = []
    if not phrase_ok:
        lines.append("The typed text did not match the required phrase.")
    if is_bot:
        lines.append("Flagged as non-human / automated input:")
        lines.extend(f"  • {r}" for r in bot_reasons)
    if breakdown:
        top = [b for b in breakdown if b["z_score"] >= 1.5][:3]
        if top:
            lines.append("Behavior that differed most from the enrolled baseline:")
            for b in top:
                lines.append(
                    f"  • {b['description'].capitalize()} was {b['z_score']:.1f}× "
                    f"the normal variation for this user"
                )
        elif accepted:
            lines.append("All behavioral signals were consistent with the enrolled baseline.")
    lines.append(
        f"Decision: {'ACCEPTED' if accepted else 'BLOCKED'} "
        f"(confidence {confidence:.1f}, threshold {threshold})"
    )
    return lines


# --------------------------------------------------------------- helpers --

def _mean(vals):
    return float(statistics.fmean(vals)) if vals else 0.0


def _std(vals):
    if len(vals) < 2:
        return 0.0
    return float(statistics.pstdev(vals))


def _sample_std(vals):
    """Sample standard deviation (n-1); 0.0 when there is only one value."""
    if len(vals) < 2:
        return 0.0
    return float(statistics.stdev(vals))


def _angle_diff(a, b):
    d = a - b
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    return d


def _zeroed(keys):
    return {k: 0.0 for k in keys}
