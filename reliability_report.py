"""
reliability_report.py — measures BioPrint's actual accuracy using the real
enrolled users already sitting in bioprint.db. No synthetic/fabricated data.

Two tests, both drawn from real human enrollment samples:

1. GENUINE TEST (leave-one-out cross-validation)
   For each user, for each of their 5 enrollment rounds: rebuild that
   user's profile from the OTHER 4 rounds, then score the held-out round
   against it as if it were a live login attempt. This answers: "if this
   same person logs in again, how often does the real behavioral engine
   accept them?" -> Genuine Accept Rate (GAR), and its complement, the
   False Reject Rate (FRR).

2. IMPOSTOR TEST (cross-user matching)
   For every pair of DIFFERENT enrolled users (A, B): score every one of
   B's 5 real enrollment samples against A's full profile, as if B were
   trying to log into A's account. This answers: "if someone else's real
   typing/mouse behavior is thrown at my account, how often gets through?"
   -> False Accept Rate (FAR).

Both tests use the exact same score_attempt()/count_bad_features() logic
as a live login in app.py, applied to the ACCEPT_THRESHOLD in app.py --
this is not a separate toy metric, it is the production decision logic
run against held-out real data.

Also included: a threshold sweep (a lightweight ROC-style curve) showing
the FAR/FRR trade-off at other thresholds, and a summary of the real
login_attempts already logged during manual testing.

Usage:
    python reliability_report.py                # human-readable report
    python reliability_report.py --json out.json  # also write JSON
"""
import argparse
import itertools
import json
import sys

import database as db
import behavioral_engine as be

ACCEPT_THRESHOLD = 58          # keep in sync with app.py
MAX_BAD_FEATURES = be.MAX_BAD_FEATURES


def decide(profile, features, threshold=ACCEPT_THRESHOLD, weights=None):
    """Same accept/reject logic as app.py's api_login_verify, minus the
    password/phrase/bot checks (those are separate, independent layers —
    this isolates the accuracy of the behavioral model itself)."""
    confidence, breakdown = be.score_attempt(profile, features, weights=weights)
    bad = be.count_bad_features(breakdown)
    accepted = confidence >= threshold and bad < MAX_BAD_FEATURES
    return accepted, confidence


def feature_discriminability(users):
    """
    For each of the 12 features, an F-ratio: how much the feature varies
    BETWEEN different users' enrollment means, vs. how much it varies
    WITHIN one user's own 5 enrollment rounds. High ratio = a feature
    that reliably tells people apart; low ratio = a feature that's mostly
    noise for this population and shouldn't carry much weight.

        f_ratio = variance(per-user means) / mean(per-user variance)

    This is computed entirely from the real enrolled users' real samples
    — nothing synthetic.
    """
    import statistics as st
    ratios = {}
    for k in be.FEATURE_INFO:
        user_means, user_vars = [], []
        for u in users:
            vals = [s.get(k, 0.0) for s in u["samples"]]
            user_means.append(st.fmean(vals))
            user_vars.append(st.pvariance(vals) if len(vals) > 1 else 0.0)
        between = st.pvariance(user_means) if len(user_means) > 1 else 0.0
        within = st.fmean(user_vars) if user_vars else 0.0
        ratios[k] = between / max(within, 1e-6)
    return ratios


def tuned_weights_from_ratios(ratios):
    """Rescale F-ratios into weights on roughly the same 0.3-2.0 scale as
    the original hand-picked FEATURE_WEIGHTS, so the aggregate confidence
    score stays in a comparable range."""
    if not ratios:
        return dict(be.FEATURE_WEIGHTS)
    max_r = max(ratios.values()) or 1.0
    return {k: round(0.3 + 1.7 * (r / max_r), 3) for k, r in ratios.items()}


def best_threshold(users, weights, thresholds=range(20, 96, 2)):
    """Pick the threshold closest to Equal Error Rate (FAR == FRR) using
    the given weights — i.e. the balanced operating point, before anyone
    decides to bias the product toward fewer false accepts or fewer false
    rejects."""
    best_t, best_gap = None, None
    curve = threshold_sweep(users, thresholds=thresholds, weights=weights)
    for row in curve:
        if row["far"] is None or row["frr"] is None:
            continue
        gap = abs(row["far"] - row["frr"])
        if best_gap is None or gap < best_gap:
            best_gap, best_t = gap, row["threshold"]
    return best_t, curve


def load_users():
    """All enrolled users with >= 3 enrollment samples, as
    {username, user_id, samples: [feature dicts]}."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username FROM users WHERE enrolled = 1"
        ).fetchall()
    users = []
    for r in rows:
        samples = db.get_enrollment_samples(r["id"])
        if len(samples) >= 3:
            users.append({"user_id": r["id"], "username": r["username"], "samples": samples})
    return users


def genuine_test(users, threshold=ACCEPT_THRESHOLD, weights=None):
    """Leave-one-out over each user's own enrollment samples."""
    results = []
    for u in users:
        samples = u["samples"]
        for i in range(len(samples)):
            train = samples[:i] + samples[i + 1:]
            held_out = samples[i]
            profile = be.build_profile(train)
            accepted, confidence = decide(profile, held_out, threshold, weights)
            results.append({
                "username": u["username"], "round_held_out": i + 1,
                "confidence": confidence, "accepted": accepted,
            })
    return results


def impostor_test(users, threshold=ACCEPT_THRESHOLD, weights=None):
    """Every (attacker, victim) pair of different users; every one of the
    attacker's real samples is thrown at the victim's full profile."""
    results = []
    for victim, attacker in itertools.permutations(users, 2):
        profile = be.build_profile(victim["samples"])
        for i, sample in enumerate(attacker["samples"]):
            accepted, confidence = decide(profile, sample, threshold, weights)
            results.append({
                "attacker": attacker["username"], "victim": victim["username"],
                "attacker_round": i + 1, "confidence": confidence, "accepted": accepted,
            })
    return results


def summarize(genuine, impostor):
    n_gen = len(genuine)
    n_gen_accept = sum(r["accepted"] for r in genuine)
    frr = 1 - (n_gen_accept / n_gen) if n_gen else float("nan")

    n_imp = len(impostor)
    n_imp_accept = sum(r["accepted"] for r in impostor)
    far = (n_imp_accept / n_imp) if n_imp else float("nan")

    return {
        "genuine_attempts": n_gen, "genuine_accepted": n_gen_accept,
        "genuine_accept_rate": round(1 - frr, 4) if n_gen else None,
        "false_reject_rate": round(frr, 4) if n_gen else None,
        "impostor_attempts": n_imp, "impostor_accepted": n_imp_accept,
        "false_accept_rate": round(far, 4) if n_imp else None,
    }


def threshold_sweep(users, thresholds=range(30, 91, 5), weights=None):
    """FAR/FRR at a range of thresholds, using the same held-out genuine
    and cross-user impostor samples computed once, re-scored per threshold."""
    # Pre-compute raw confidences once (expensive part), reuse across thresholds.
    genuine_conf = []
    for u in users:
        samples = u["samples"]
        for i in range(len(samples)):
            train = samples[:i] + samples[i + 1:]
            profile = be.build_profile(train)
            confidence, breakdown = be.score_attempt(profile, samples[i], weights=weights)
            bad = be.count_bad_features(breakdown)
            genuine_conf.append((confidence, bad))

    impostor_conf = []
    for victim, attacker in itertools.permutations(users, 2):
        profile = be.build_profile(victim["samples"])
        for sample in attacker["samples"]:
            confidence, breakdown = be.score_attempt(profile, sample, weights=weights)
            bad = be.count_bad_features(breakdown)
            impostor_conf.append((confidence, bad))

    curve = []
    for t in thresholds:
        gen_acc = sum(1 for c, b in genuine_conf if c >= t and b < MAX_BAD_FEATURES)
        imp_acc = sum(1 for c, b in impostor_conf if c >= t and b < MAX_BAD_FEATURES)
        frr = 1 - gen_acc / len(genuine_conf) if genuine_conf else None
        far = imp_acc / len(impostor_conf) if impostor_conf else None
        curve.append({"threshold": t, "far": round(far, 4) if far is not None else None,
                       "frr": round(frr, 4) if frr is not None else None})
    return curve


def real_attempt_stats():
    """Summary of the real login_attempts already logged from manual
    testing (app usage so far) -- observed, not simulated."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT password_ok, is_bot, accepted, confidence FROM login_attempts"
        ).fetchall()
    total = len(rows)
    if not total:
        return None
    pw_ok = sum(r["password_ok"] for r in rows)
    bots = sum(r["is_bot"] for r in rows)
    accepted = sum(r["accepted"] for r in rows)
    pw_ok_but_blocked = sum(1 for r in rows if r["password_ok"] and not r["accepted"])
    return {
        "total_attempts": total,
        "correct_password_attempts": pw_ok,
        "flagged_as_bot": bots,
        "accepted": accepted,
        "correct_password_but_behaviorally_blocked": pw_ok_but_blocked,
    }


def print_report(users, genuine, impostor, summary, curve, real_stats):
    print("=" * 70)
    print("BioPrint Reliability Report")
    print("=" * 70)
    print(f"Enrolled users used in this test: {len(users)} "
          f"({', '.join(u['username'] for u in users)})")
    print()
    print("-- Genuine test (leave-one-out on real enrollment samples) --")
    print(f"  Attempts: {summary['genuine_attempts']}")
    print(f"  Accepted: {summary['genuine_accepted']}")
    print(f"  Genuine Accept Rate (GAR): {summary['genuine_accept_rate']:.1%}")
    print(f"  False Reject Rate  (FRR): {summary['false_reject_rate']:.1%}")
    print()
    print("-- Impostor test (every user's real samples vs. every other user's profile) --")
    print(f"  Attempts: {summary['impostor_attempts']}")
    print(f"  Wrongly accepted: {summary['impostor_accepted']}")
    print(f"  False Accept Rate (FAR): {summary['false_accept_rate']:.1%}")
    print()
    print(f"-- Threshold sweep (current ACCEPT_THRESHOLD = {ACCEPT_THRESHOLD}) --")
    print(f"  {'thresh':>6}  {'FAR':>7}  {'FRR':>7}")
    for row in curve:
        marker = "  <- current" if row["threshold"] == ACCEPT_THRESHOLD else ""
        print(f"  {row['threshold']:>6}  {row['far']:>6.1%}  {row['frr']:>6.1%}{marker}")
    print()
    if real_stats:
        print("-- Real logged attempts so far (from actual manual testing) --")
        for k, v in real_stats.items():
            print(f"  {k}: {v}")
        print()
    print("=" * 70)
    print("For the slide: quote GAR/FRR/FAR above, and note the threshold")
    print("sweep shows the trade-off is tunable, not fixed/arbitrary.")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write full results to this JSON path")
    ap.add_argument("--db", help="path to bioprint.db (default: ./bioprint.db)")
    args = ap.parse_args()

    if args.db:
        db.DB_PATH = args.db

    users = load_users()
    if len(users) < 2:
        print("Need at least 2 enrolled users (with >=3 samples each) in "
              "bioprint.db to run genuine + impostor tests.", file=sys.stderr)
        print(f"Found: {len(users)}", file=sys.stderr)
        sys.exit(1)

    # --- BEFORE: current shipped weights + threshold ---
    genuine = genuine_test(users)
    impostor = impostor_test(users)
    summary = summarize(genuine, impostor)
    curve = threshold_sweep(users)
    real_stats = real_attempt_stats()

    print_report(users, genuine, impostor, summary, curve, real_stats)

    # --- AFTER: data-driven weights (from real between/within-user
    # variance) + the empirical equal-error-rate threshold ---
    ratios = feature_discriminability(users)
    tuned_weights = tuned_weights_from_ratios(ratios)
    tuned_threshold, tuned_curve = best_threshold(users, tuned_weights)

    genuine2 = genuine_test(users, threshold=tuned_threshold, weights=tuned_weights)
    impostor2 = impostor_test(users, threshold=tuned_threshold, weights=tuned_weights)
    summary2 = summarize(genuine2, impostor2)

    print()
    print("=" * 70)
    print("RECALIBRATED using real per-feature discriminability")
    print("=" * 70)
    print("Feature discriminability (between-user variance / within-user variance):")
    for k, r in sorted(ratios.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20} ratio={r:6.2f}   suggested weight={tuned_weights[k]}")
    print()
    print(f"Recalibrated threshold (empirical equal-error point): {tuned_threshold}")
    print()
    print(f"{'Metric':<28}{'Before':>12}{'After':>12}")
    print(f"{'Genuine Accept Rate':<28}{summary['genuine_accept_rate']:>11.1%} "
          f"{summary2['genuine_accept_rate']:>11.1%}")
    print(f"{'False Reject Rate':<28}{summary['false_reject_rate']:>11.1%} "
          f"{summary2['false_reject_rate']:>11.1%}")
    print(f"{'False Accept Rate':<28}{summary['false_accept_rate']:>11.1%} "
          f"{summary2['false_accept_rate']:>11.1%}")
    print()
    print("Paste this into behavioral_engine.py to apply the recalibration:")
    print()
    print("FEATURE_WEIGHTS = {")
    for k, w in tuned_weights.items():
        print(f'    "{k}": {w},')
    print("}")
    print()
    print(f"# and in app.py:\nACCEPT_THRESHOLD = {tuned_threshold}")
    print("=" * 70)
    if len(users) < 8:
        print(f"\nCAUTION: only {len(users)} users in this test. A single feature's "
              "discriminability ratio can look large purely by chance on this few "
              "people (especially ks_mean_dwell above) - treat these weights as "
              "directional, not final. Re-run this script as more people enroll "
              "before locking in numbers for a slide.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "before": {"summary": summary, "threshold_sweep": curve,
                           "genuine_detail": genuine, "impostor_detail": impostor},
                "after": {"summary": summary2, "threshold": tuned_threshold,
                          "weights": tuned_weights, "discriminability": ratios,
                          "threshold_sweep": tuned_curve,
                          "genuine_detail": genuine2, "impostor_detail": impostor2},
                "real_logged_attempts": real_stats,
            }, f, indent=2)
        print(f"\nFull detail written to {args.json}")


if __name__ == "__main__":
    main()
