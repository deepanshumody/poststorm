"""Render synthetic case specs into scanned-looking EOB images (+ a PDF each) and
write data/ground_truth.json. Pillow-only — no Chrome/poppler needed.

Run from the generator/ dir:  python generate.py
"""
import io
import json
import random
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from cases import build_cases  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data"
W, H = 1700, 2200  # ~150dpi letter, portrait

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Courier New.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _font(size):
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


COLS = [("CLAIM", 60), ("PATIENT", 230), ("DOS", 520), ("CHARGE", 700),
        ("ALLOWED", 880), ("PAID", 1060), ("ADJ", 1240), ("PT RESP", 1400), ("CARC", 1580)]


def draw_eob(case) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    big, mid, small = _font(46), _font(30), _font(26)

    d.text((60, 50), case["payer"], font=big, fill="black")
    d.text((60, 120), "REMITTANCE ADVICE / EXPLANATION OF BENEFITS", font=mid, fill="black")
    d.text((60, 165), f"Check #: {case['check_number']}", font=mid, fill="black")
    d.line((60, 215, W - 60, 215), fill="black", width=2)

    for name, x in COLS:
        d.text((x, 235), name, font=small, fill="black")
    d.line((60, 275, W - 60, 275), fill="black", width=1)

    y = 300
    total = 0.0
    for ln in case["lines"]:
        is_recoup = ln["event_type"] in ("recoup", "reversal") or ln["paid"] < 0
        fill = "black"
        cells = [
            ln["claim_id"], ln["patient_ref"], ln["service_date"],
            f"{ln['charge']:.2f}", f"{ln['allowed']:.2f}", f"{ln['paid']:.2f}",
            f"{ln['adjustment']:.2f}", f"{ln['patient_responsibility']:.2f}", ln["carc"] or "",
        ]
        for (_, x), val in zip(COLS, cells):
            d.text((x, y), str(val), font=small, fill=fill)
        if is_recoup:
            d.text((60, y + 32), "** RECOUPMENT / OFFSET — prior overpayment recovered **",
                   font=small, fill="black")
            y += 36
        total += ln["paid"]
        y += 64

    d.line((60, y + 10, W - 60, y + 10), fill="black", width=1)
    d.text((1060, y + 24), f"CHECK TOTAL: {total:.2f}", font=mid, fill="black")
    return img


def scannify(img: Image.Image, seed: int) -> Image.Image:
    # Photocopier feel, but NO rotation so detection bands map to exact rows.
    g = img.convert("L").filter(ImageFilter.GaussianBlur(0.5))
    noise = Image.effect_noise(g.size, 14)
    g = Image.blend(g, noise, 0.05)
    return g.convert("RGB")


def recoup_box(case) -> dict | None:
    """Fractional bbox of the planted recoup row (recoup line is appended last)."""
    if not case["has_planted_recoup"]:
        return None
    row_top = 300 + (len(case["lines"]) - 1) * 64
    return {"x": 56 / W, "y": (row_top - 6) / H, "w": (W - 112) / W, "h": 72 / H}


def save_pdf(img: Image.Image, path: Path):
    """Write a single-page image PDF via PyMuPDF (PIL's PDF writer needs JPEG)."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=img.width, height=img.height)
    page.insert_image(fitz.Rect(0, 0, img.width, img.height), stream=buf.getvalue())
    doc.save(str(path))
    doc.close()


def main(n=24, recoup_cases=3, seed=7):
    (OUT / "eobs").mkdir(parents=True, exist_ok=True)
    cases = build_cases(n, recoup_cases, seed)
    truth = []
    for i, c in enumerate(cases):
        img = scannify(draw_eob(c), seed + i)
        img.save(OUT / "eobs" / f"{c['doc_id']}.png")
        save_pdf(img, OUT / "eobs" / f"{c['doc_id']}.pdf")
        rline = c["lines"][-1] if c["has_planted_recoup"] else None
        truth.append({
            "doc_id": c["doc_id"], "has_planted_recoup": c["has_planted_recoup"],
            "payer": c["payer"], "check_number": c["check_number"],
            "recoup_box": recoup_box(c),
            "recoup_text": (f"{rline['patient_ref']}  {rline['paid']:.2f}" if rline else None),
            "lines": c["lines"],
        })
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    planted = sum(1 for t in truth if t["has_planted_recoup"])
    print(f"Generated {len(cases)} EOBs ({planted} with planted recoups) -> {OUT/'eobs'}")
    print(f"Ground truth -> {OUT/'ground_truth.json'}")


if __name__ == "__main__":
    main()
