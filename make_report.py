"""
make_report.py — generates report.pdf: the 1-2 page write-up of
BioPrint's working and its authentication algorithm, required as a
submission deliverable. Run once: `python make_report.py`.
"""

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT

styles = getSampleStyleSheet()
ACCENT = colors.HexColor("#12817A")
DIM = colors.HexColor("#555555")

title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontSize=19,
                              spaceAfter=2, textColor=colors.HexColor("#12181C"))
sub_style = ParagraphStyle("SubX", parent=styles["Normal"], fontSize=10,
                            textColor=DIM, spaceAfter=14)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12.5,
                     spaceBefore=12, spaceAfter=4, textColor=ACCENT)
body = ParagraphStyle("BodyX", parent=styles["Normal"], fontSize=9.7,
                       leading=13.5, alignment=TA_LEFT, spaceAfter=4)
small = ParagraphStyle("SmallX", parent=body, fontSize=8.8, textColor=DIM)

story = []
story.append(Paragraph("BioPrint — Behavior-Based Login Security", title_style))
story.append(Paragraph(
    "Technical report — working of the system and the core logic behind the authentication algorithm",
    sub_style))

story.append(Paragraph("1. Problem &amp; Approach", h2))
story.append(Paragraph(
    "Passwords authenticate <i>knowledge</i>, not identity — a correct password proves someone typed "
    "the right string, not that they are the account owner. BioPrint adds a second, independent signal: "
    "the physical <i>manner</i> in which someone types and moves a mouse, which is far harder to steal or "
    "replay than a string. Even when an attacker has the correct password, BioPrint can still block the "
    "login purely on behavior, with no OTP or secondary-verification step anywhere in the flow.", body))

story.append(Paragraph("2. Enrollment: building a baseline", h2))
story.append(Paragraph(
    "During registration, a user completes 5 short rounds of a fixed task: typing a set phrase and "
    "clicking 4 on-screen targets in sequence. Each round is measured, not judged — the goal is to sample "
    "the user's <i>natural range</i> of behavior across repetitions, not a single frozen snapshot. From "
    "these 5 samples the system computes a per-feature mean and standard deviation, which together form "
    "that user's behavioral profile. Enrollment is one-time only: once finalized, an account cannot be "
    "re-enrolled, so a baseline can't be silently overwritten later by an impostor.", body))

story.append(Paragraph("3. Feature extraction", h2))
story.append(Paragraph(
    "Every raw keystroke and mouse event carries a high-resolution timestamp. From these, 12 numeric, "
    "password-independent features are derived:", body))

data = [
    ["Signal", "Features extracted"],
    ["Keystroke dynamics", "mean/std dwell time (key hold), mean/std flight time (inter-key gap),\ntyping speed, backspace rate"],
    ["Mouse dynamics", "mean/std velocity, mean acceleration, path straightness (path length ÷\nstraight-line distance), click-hold duration, directional jitter"],
]
tbl = Table(data, colWidths=[1.5*inch, 5*inch])
tbl.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12181C")),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 8.8),
    ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#CCCCCC")),
    ("VALIGN", (0,0), (-1,-1), "TOP"),
    ("TOPPADDING", (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
]))
story.append(tbl)
story.append(Spacer(1, 6))

story.append(Paragraph("4. Scoring algorithm", h2))
story.append(Paragraph(
    "At login, the same 12 features are extracted from the single live attempt. For each feature "
    "<i>k</i>, a z-score is computed: z<sub>k</sub> = |value<sub>k</sub> − mean<sub>k</sub>| / std<sub>k</sub>. "
    "These are combined into a weighted RMS distance across all features (keystroke-rhythm and "
    "mouse-straightness features are weighted slightly higher, as they proved most discriminating), which "
    "is a diagonal-covariance approximation of the Mahalanobis distance. The distance is mapped to a "
    "0–100 confidence score with a Gaussian decay: confidence = 100 · exp(−0.5 · (z<sub>rms</sub> / 1.6)²), "
    "so a perfect match scores 100 and confidence falls off smoothly as behavior drifts from baseline. "
    "A login is <b>accepted</b> only if the password is correct <i>and</i> confidence clears a threshold "
    "(default 55) <i>and</i> the attempt is not flagged as automated — three independent gates, all "
    "required.", body))

story.append(Paragraph("5. Bot / automation detection", h2))
story.append(Paragraph(
    "A separate, password-agnostic check flags non-human input regardless of profile match: near-zero "
    "mouse movement before a click, perfectly straight mouse paths (straightness ≈ 1.0), zero directional "
    "jitter, or keystroke timing with sub-millisecond, machine-perfect uniformity. Any one of these marks "
    "the attempt as automated and blocks it outright, since scripted or replayed input can otherwise "
    "produce statistically \"average\" feature values that would slip past the z-score check alone.", body))

story.append(Paragraph("6. Adaptive profiles &amp; explainability", h2))
story.append(Paragraph(
    "Every <i>accepted</i> login nudges the stored profile toward the new sample via an exponential "
    "moving average (α = 0.12), so gradual, genuine drift — a new mouse, a tired evening — doesn't "
    "eventually lock the real user out. Every decision, accepted or blocked, is logged with its full "
    "feature vector and a human-readable explanation naming the specific behaviors that deviated most, "
    "so a blocked user (or a judge) can see exactly why.", body))

story.append(Paragraph("7. Stack", h2))
story.append(Paragraph(
    "Flask (routes/API) + SQLite (users, enrollment samples, profiles, full attempt audit log) + a "
    "dependency-light Python statistics engine (no black-box ML library — every step above is inspectable "
    "and explainable). Frontend is vanilla JS/HTML/CSS; behavior capture and scoring are cleanly separated "
    "so the browser never makes the accept/block decision itself.", body))

doc = SimpleDocTemplate("report.pdf", pagesize=LETTER,
                         topMargin=0.6*inch, bottomMargin=0.6*inch,
                         leftMargin=0.7*inch, rightMargin=0.7*inch)
doc.build(story)
print("wrote report.pdf")
