"""Render realistic scanned remittance-advice (EOB) images + ground truth.
Pillow-only (no Chrome/poppler). Run from generator/:  python generate.py
"""
import io
import json
import random
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from cases import CARCS, RARCS, RECOVERY_RARC, build_cases  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data"
W, H = 1700, 2200
ML, MR = 70, 1630

_COURIER = ["/System/Library/Fonts/Supplemental/Courier New.ttf", "/Library/Fonts/Courier New.ttf"]
_COURIER_B = ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf", "/Library/Fonts/Courier New Bold.ttf"]
_SANS_B = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]


def _font(paths, size):
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


BODY = _font(_COURIER, 23)
BODY_B = _font(_COURIER_B, 23)
SMALL = _font(_COURIER, 19)
GLOS = _font(_COURIER, 18)
PAYER = _font(_SANS_B, 44)
TITLE = _font(_SANS_B, 24)

INK = (24, 24, 24)
GRAY = (95, 95, 95)
RED = (150, 40, 40)

# service-line columns
X_DOS, X_CPT, X_MOD = ML, 320, 430
R_UN, R_BILL, R_ALLOW, R_DED, R_COIN, R_PAID = 500, 660, 800, 915, 1035, 1185
X_GRP, X_RARC = 1215, 1430


def _fmt(x, blank_zero=False):
    if blank_zero and abs(x) < 0.005:
        return ""
    return f"{x:,.2f}"


def draw_eob(case):
    img = Image.new("RGB", (W, H), (251, 250, 246))
    d = ImageDraw.Draw(img)
    payer, prov = case["payer"], case["provider"]
    y = 64

    d.text((ML, y), payer["name"], font=PAYER, fill=INK)
    d.text((MR, y + 8), "PROVIDER REMITTANCE ADVICE", font=TITLE, fill=INK, anchor="ra")
    y += 54
    d.text((ML, y), payer["addr"], font=SMALL, fill=GRAY)
    d.text((MR, y), f"Payer ID {payer['id']}     Page 1 of 1", font=SMALL, fill=GRAY, anchor="ra")
    y += 42
    d.line((ML, y, MR, y), fill=(120, 120, 120), width=2)
    y += 16

    d.text((ML, y), f"PROVIDER   {prov['name']}", font=BODY, fill=INK)
    d.text((1010, y), f"CHECK/EFT #  {case['check_number']}", font=BODY, fill=INK)
    y += 28
    d.text((ML, y), f"           {prov['addr']}", font=SMALL, fill=GRAY)
    d.text((1010, y), f"CHECK DATE   {case['check_date']}", font=BODY, fill=INK)
    y += 28
    d.text((ML, y), f"PAYEE NPI {prov['npi']}    TAX ID {prov['tax']}", font=SMALL, fill=GRAY)
    d.text((1010, y), "PAYMENT      EFT", font=BODY, fill=INK)
    y += 34
    d.line((ML, y, MR, y), fill=(120, 120, 120), width=2)
    y += 12

    # column header
    d.text((X_DOS, y), "DATE OF SVC", font=SMALL, fill=GRAY)
    d.text((X_CPT, y), "CPT", font=SMALL, fill=GRAY)
    d.text((X_MOD, y), "MD", font=SMALL, fill=GRAY)
    for rx, lbl in [(R_UN, "UN"), (R_BILL, "BILLED"), (R_ALLOW, "ALLOWED"), (R_DED, "DEDUCT"),
                    (R_COIN, "COINS"), (R_PAID, "PAID")]:
        d.text((rx, y), lbl, font=SMALL, fill=GRAY, anchor="ra")
    d.text((X_GRP, y), "GRP/CARC", font=SMALL, fill=GRAY)
    d.text((X_RARC, y), "RARC", font=SMALL, fill=GRAY)
    y += 26
    d.line((ML, y, MR, y), fill=(190, 190, 190), width=1)
    y += 10

    recoup_box = None
    used = set()
    tot_b = tot_a = tot_p = 0.0
    for c in case["claims"]:
        used.add(c["carc"])
        used.update(c["rarc"])
        d.text((ML, y), f"CLAIM {c['icn']}", font=BODY_B, fill=INK)
        d.text((500, y), f"PATIENT {c['patient_ref']}", font=BODY, fill=INK)
        d.text((1050, y), f"MEMBER {c['member']}", font=SMALL, fill=GRAY)
        if c.get("acct"):
            d.text((1360, y), f"ACCT {c['acct']}", font=SMALL, fill=GRAY)
        y += 30
        if c["is_recoup"]:
            d.text((ML + 22, y), f"OVERPAYMENT RECOVERY  {c['fcn']}  recovers prior ICN {c['prior_icn']}",
                   font=SMALL, fill=RED)
            y += 28

        row_y = y
        rec = c["is_recoup"]
        d.text((X_DOS, y), c["dos"], font=BODY, fill=INK)
        d.text((X_CPT, y), c["cpt"], font=BODY, fill=INK)
        d.text((X_MOD, y), c["mod"], font=BODY, fill=INK)
        d.text((R_UN, y), str(c["units"]), font=BODY, fill=INK, anchor="ra")
        d.text((R_BILL, y), _fmt(c["billed"], rec), font=BODY, fill=INK, anchor="ra")
        d.text((R_ALLOW, y), _fmt(c["allowed"], rec), font=BODY, fill=INK, anchor="ra")
        d.text((R_DED, y), _fmt(c["deduct"], rec), font=BODY, fill=INK, anchor="ra")
        d.text((R_COIN, y), _fmt(c["coins"], rec), font=BODY, fill=INK, anchor="ra")
        d.text((R_PAID, y), _fmt(c["paid"]), font=BODY, fill=(RED if c["paid"] < 0 else INK), anchor="ra")
        d.text((X_GRP, y), c["carc"], font=BODY, fill=INK)
        d.text((X_RARC, y), c["rarc"][0], font=SMALL, fill=GRAY)
        if rec:
            recoup_box = {"x": (ML - 8) / W, "y": (row_y - 6) / H, "w": (MR - ML + 16) / W, "h": 40 / H}
        y += 30
        if rec:
            d.text((ML + 22, y), "** RECOUPMENT / OFFSET — overpayment recovered from this check **",
                   font=SMALL, fill=RED)
            y += 26
        d.text((X_CPT, y), "CLAIM TOTAL", font=SMALL, fill=GRAY)
        d.text((R_BILL, y), _fmt(c["billed"], rec), font=SMALL, fill=GRAY, anchor="ra")
        d.text((R_ALLOW, y), _fmt(c["allowed"], rec), font=SMALL, fill=GRAY, anchor="ra")
        d.text((R_PAID, y), _fmt(c["paid"]), font=SMALL, fill=GRAY, anchor="ra")
        y += 32
        d.line((ML, y, MR, y), fill=(205, 205, 205), width=1)
        y += 14
        tot_b += c["billed"]
        tot_a += c["allowed"]
        tot_p += c["paid"]

    d.line((ML, y, MR, y), fill=(120, 120, 120), width=2)
    y += 14
    d.text((ML, y), "REMITTANCE TOTALS", font=BODY_B, fill=INK)
    d.text((R_BILL, y), _fmt(tot_b), font=BODY_B, fill=INK, anchor="ra")
    d.text((R_ALLOW, y), _fmt(tot_a), font=BODY_B, fill=INK, anchor="ra")
    d.text((R_PAID, y), _fmt(tot_p), font=BODY_B, fill=INK, anchor="ra")
    y += 32
    d.text((ML, y), f"NET CHECK / EFT AMOUNT   ${tot_p:,.2f}", font=BODY_B, fill=INK)
    y += 46

    d.text((ML, y), "GLOSSARY OF ADJUSTMENT & REMARK CODES", font=SMALL, fill=GRAY)
    y += 26
    desc = {f"{g}-{n}": dsc for g, n, dsc in CARCS}
    desc.update({code: dsc for code, dsc in RARCS})
    desc[RECOVERY_RARC[0]] = RECOVERY_RARC[1]
    desc["OA-23"] = "Prior payer/overpayment adjustment"
    for code in sorted(used):
        d.text((ML + 10, y), f"{code:7} {desc.get(code, '')}", font=GLOS, fill=GRAY)
        y += 22

    return img, recoup_box


def _stamp(seed):
    rng = random.Random(seed)
    txt = f"RECEIVED\n{rng.choice(['JAN'])} {rng.randint(23,28)} 2026\nMAILROOM"
    layer = Image.new("RGBA", (360, 200), (0, 0, 0, 0))
    sd = ImageDraw.Draw(layer)
    sd.rounded_rectangle((6, 6, 354, 194), radius=10, outline=(150, 40, 40, 150), width=4)
    sf = _font(_SANS_B, 40)
    sd.multiline_text((30, 28), txt, font=sf, fill=(150, 40, 40, 150), spacing=8)
    return layer.rotate(rng.uniform(-16, -8), expand=True, resample=Image.BICUBIC)


def scannify(img, seed):
    rng = random.Random(seed)
    stamp = _stamp(seed)
    img.paste(stamp, (rng.randint(1180, 1280), rng.randint(70, 110)), stamp)
    # subtle fold shadow
    fold = Image.new("L", img.size, 0)
    fd = ImageDraw.Draw(fold)
    fy = rng.randint(int(H * 0.45), int(H * 0.6))
    fd.rectangle((0, fy - 2, W, fy + 2), fill=rng.randint(18, 34))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, fold.filter(ImageFilter.GaussianBlur(3)))
    # photocopier grain + soft blur
    g = img.convert("L").filter(ImageFilter.GaussianBlur(0.5))
    noise = Image.effect_noise(g.size, 13)
    g = Image.blend(g, noise, rng.uniform(0.045, 0.07))
    return Image.merge("RGB", (g, g, g)).point(lambda v: int(min(255, v * rng.uniform(0.97, 1.03))))


def save_pdf(img, path):
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
        base, box = draw_eob(c)
        img = scannify(base, seed + i)
        img.save(OUT / "eobs" / f"{c['doc_id']}.png")
        save_pdf(img, OUT / "eobs" / f"{c['doc_id']}.pdf")
        thumb = img.copy()
        thumb.thumbnail((260, 340))
        thumb.save(OUT / "eobs" / f"{c['doc_id']}.thumb.png")
        rline = c["lines"][-1] if c["has_planted_recoup"] else None
        truth.append({
            "doc_id": c["doc_id"], "has_planted_recoup": c["has_planted_recoup"],
            "payer": c["payer"]["name"], "check_number": c["check_number"],
            "recoup_box": box,
            "recoup_text": (f"{rline['patient_ref']}  {rline['paid']:.2f}" if rline else None),
            "lines": c["lines"],
        })
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    planted = sum(1 for t in truth if t["has_planted_recoup"])
    print(f"Generated {len(cases)} realistic EOBs ({planted} with recoups) -> {OUT/'eobs'}")


if __name__ == "__main__":
    main()
