"""Render four visually-distinct, authentic scanned remittance templates + ground
truth. Pillow-only (PNG + PyMuPDF PDF + thumbnail). Run from generator/:
    python generate.py
"""
import io
import json
import random
import sys
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from cases import CARC_TEXT, RARC_TEXT, build_cases  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.reconcile import reconcile  # noqa: E402
from backend.schema import LineItem  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data"
W, H, ML, MR = 1700, 2200, 70, 1630
INK, GRAY, RED, MUTE = (22, 22, 22), (95, 95, 95), (150, 35, 35), (150, 150, 150)
UHC_BLUE, OPTUM, CIGNA, AETNA, BCBS = (0, 90, 160), (235, 120, 30), (0, 81, 143), (122, 46, 140), (0, 98, 155)

_PATHS = {
    "mono": ["/System/Library/Fonts/Supplemental/Courier New.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"],
    "mono_b": ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"],
    "sans": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    "sans_b": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
}


@lru_cache(maxsize=None)
def F(kind, size):
    for p in _PATHS[kind]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def L(d, x, y, s, font, fill=INK):
    d.text((x, y), str(s), font=font, fill=fill)


def Rt(d, x, y, s, font, fill=INK):
    d.text((x, y), str(s), font=font, fill=fill, anchor="ra")


def rule(d, y, x0=ML, x1=MR, fill=(120, 120, 120), w=2):
    d.line((x0, y, x1, y), fill=fill, width=w)


def m(x):
    return f"{abs(x):,.2f}"


def used_codes(case):
    codes = []
    for c in case["claims"]:
        codes += [g for g, _ in c["carcs"]] + c["rarcs"]
    if case["recoup"]:
        codes += ["N469"]
    seen, out = set(), []
    for code in codes:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def glossary(d, y, case, font, fill=GRAY, friendly=False):
    L(d, ML, y, "GLOSSARY OF CODES" if not friendly else "Notes & codes", font, fill)
    y += 26
    for code in used_codes(case):
        txt = CARC_TEXT.get(code) or RARC_TEXT.get(code) or ""
        L(d, ML + 10, y, f"{code:8} {txt}", font, fill)
        y += 22
    return y


# ---------------------------------------------------------------- T1 Medicare SPR
def draw_medicare(c, rng):
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    b, bb, sm = F("mono", 23), F("mono_b", 23), F("mono", 20)
    y = 60
    L(d, ML, y, c["mac"], bb)
    Rt(d, MR, y + 4, "MEDICARE REMITTANCE ADVICE", bb)
    y += 32
    from cases import MAC_ADDR
    L(d, ML, y, MAC_ADDR, sm, GRAY)
    y += 42
    rule(d, y); y += 14
    prov = c["provider"]
    L(d, ML, y, f"PROVIDER  {prov['name'].upper()}", b)
    Rt(d, MR, y, f"CHECK/EFT #: {c['check_number']}", bb)
    y += 28
    L(d, ML, y, f"NPI {prov['npi']}   PTAN {prov['ptan']}", sm, GRAY)
    Rt(d, MR, y, f"EFT DATE: {c['check_date']}   REMIT DATE: {c['check_date']}   PAGE 1 OF 1", sm, GRAY)
    y += 34
    rule(d, y); y += 12

    hdr = "PERF PROV   SERV DATE        POS NOS PROC  MODS"
    L(d, ML, y, hdr, sm, GRAY)
    Rt(d, 980, y, "BILLED", sm, GRAY); Rt(d, 1110, y, "ALLOWED", sm, GRAY)
    Rt(d, 1230, y, "DEDUCT", sm, GRAY); Rt(d, 1345, y, "COINS", sm, GRAY)
    L(d, 1360, y, "GRP/RC-AMT", sm, GRAY); Rt(d, MR, y, "PROV-PD", sm, GRAY)
    y += 26; rule(d, y, fill=(190, 190, 190), w=1); y += 8

    for cl in c["claims"]:
        L(d, ML, y, f"NAME {cl['patient']}   MID {cl['mbi']}   ACNT {cl['acct']}   ICN {cl['icn']}   ASG Y   MOA MA01", sm)
        y += 28
        L(d, ML, y, prov["npi"], b)
        L(d, 250, y, f"{cl['dos_dd']:02d}0126 {cl['dos_dd']:02d}0126", b)
        L(d, 510, y, cl["pos"], b); L(d, 575, y, str(cl["units"]), b)
        L(d, 640, y, cl["cpt"], b); L(d, 770, y, cl["mod"], b)
        Rt(d, 980, y, m(cl["billed"]), b); Rt(d, 1110, y, m(cl["allowed"]), b)
        Rt(d, 1230, y, m(cl["deduct"]), b); Rt(d, 1345, y, m(cl["coins"]), b)
        L(d, 1360, y, f"{cl['carcs'][0][0]} {m(cl['carcs'][0][1])}", b)
        Rt(d, MR, y, m(cl["paid"]), b)
        y += 28
        for g, amt in cl["carcs"][1:]:
            L(d, 1360, y, f"{g} {m(amt)}", sm, GRAY); y += 24
        L(d, 1360, y, f"REM: {cl['rarcs'][0]}", sm, GRAY); y += 26
        L(d, ML, y, "CLAIM TOTALS", sm, GRAY)
        Rt(d, 980, y, m(cl["billed"]), sm, GRAY); Rt(d, 1110, y, m(cl["allowed"]), sm, GRAY)
        Rt(d, 1230, y, m(cl["deduct"]), sm, GRAY); Rt(d, 1345, y, m(cl["coins"]), sm, GRAY)
        Rt(d, MR, y, m(cl["paid"]), sm, GRAY)
        y += 32; rule(d, y, fill=(205, 205, 205), w=1); y += 12

    box = None
    rc = c["recoup"]
    if rc:
        rule(d, y); y += 12
        L(d, ML, y, "PROVIDER ADJ DETAILS", bb); y += 30
        L(d, ML, y, "PLB REASON CODE   FCN              HIC/MID        AMOUNT", sm, GRAY); y += 26
        row_y = y
        L(d, ML, y, f"WO   {rc['fcn']}   {rc['mbi_b']}", bb, RED)
        Rt(d, MR, y, f"-{m(rc['amount'])}", bb, RED)
        y += 26
        L(d, ML + 20, y, f"(OVERPAYMENT RECOVERY  PATIENT {rc['patient_b']}  PRIOR ICN {rc['prior_icn']})", sm, RED)
        box = {"x": (ML - 8) / W, "y": (row_y - 8) / H, "w": (MR - ML + 16) / W, "h": 70 / H}
        y += 40

    rule(d, y); y += 14
    L(d, ML, y, f"# OF CLAIMS {len(c['claims'])}", b)
    L(d, 540, y, f"PROV PD AMT {m(sum(x['paid'] for x in c['claims']))}", b)
    L(d, 1000, y, f"PROV ADJ AMT {('-' + m(rc['amount'])) if rc else '0.00'}", b)
    Rt(d, MR, y, f"CHECK AMT ${m(c['check_amt'])}", bb)
    y += 44
    glossary(d, y, c, sm)
    return img, box


# ---------------------------------------------------------------- T2 UHC / Optum PRA
def draw_uhc(c, rng):
    img = Image.new("RGB", (W, H), (253, 253, 253))
    d = ImageDraw.Draw(img)
    b, bb, sm, h1 = F("sans", 21), F("sans_b", 21), F("sans", 18), F("sans_b", 30)
    y = 58
    L(d, ML, y, "UnitedHealthcare", h1, UHC_BLUE)
    w = d.textlength("UnitedHealthcare", font=h1)
    d.ellipse((ML + w + 24, y + 4, ML + w + 52, y + 32), fill=OPTUM)
    L(d, ML + w + 60, y + 4, "Optum Pay", F("sans_b", 22), (70, 70, 70))
    Rt(d, MR, y, "Provider Remittance Advice", bb)
    Rt(d, MR, y + 30, "Page 1 of 1", sm, GRAY)
    y += 64
    L(d, ML, y, f"Provider: {c['provider']['name']}   NPI: {c['provider']['npi']}   Tax ID: {c['provider']['tin']}", sm, GRAY)
    y += 30
    d.rounded_rectangle((ML, y, MR, y + 34), radius=6, fill=(238, 243, 250), outline=(205, 218, 235))
    L(d, ML + 14, y + 6, f"Check/EFT Number: {c['check_number']}    Payment Date: {c['check_date']}"
                         f"    Payer ID: {c['payer_meta']['pid']}    Net Payment: ${m(c['check_amt'])}", b, (30, 50, 80))
    y += 50

    cols = [("Svc Date", ML), ("CPT", 300), ("Mod", 380), ("Units", 440), ("Charged", 640),
            ("Allowed", 770), ("Ded", 870), ("Coins", 970), ("Pt Resp", 1090), ("Adjustment", 1210), ("Paid", MR)]
    for cl in c["claims"]:
        d.rectangle((ML, y, MR, y + 30), fill=(234, 241, 250))
        L(d, ML + 8, y + 5, f"Patient: {cl['patient'].title()}    Member ID: {cl['member']}"
                            f"    Acct: {cl['acct']}    Claim ID: {cl['icn']}", bb, (30, 50, 80))
        y += 38
        for name, x in cols:
            (Rt if name in ("Charged", "Allowed", "Ded", "Coins", "Pt Resp", "Paid") else L)(d, x, y, name, sm, GRAY)
        y += 24
        L(d, ML, y, f"01/{cl['dos_dd']:02d}/26", b); L(d, 300, y, cl["cpt"], b); L(d, 380, y, cl["mod"], b)
        L(d, 440, y, str(cl["units"]), b)
        Rt(d, 640, y, "$" + m(cl["billed"]), b); Rt(d, 770, y, "$" + m(cl["allowed"]), b)
        Rt(d, 870, y, "$" + m(cl["deduct"]), b); Rt(d, 970, y, "$" + m(cl["coins"]), b)
        Rt(d, 1090, y, "$" + m(cl["deduct"] + cl["coins"]), b)
        L(d, 1130, y, f"{cl['carcs'][0][0]} (${m(cl['carcs'][0][1])})", b); Rt(d, MR, y, "$" + m(cl["paid"]), bb)
        y += 28
        L(d, 1130, y, "Remark " + cl["rarcs"][0], sm, GRAY); y += 28
        rule(d, y, fill=(225, 225, 225), w=1); y += 14

    box = None
    rc = c["recoup"]
    if rc:
        y += 6
        L(d, ML, y, "Provider Level Adjustment Details", bb, (30, 50, 80)); y += 30
        L(d, ML, y, "PLB Reason Code", sm, GRAY); L(d, 560, y, "Reference ID", sm, GRAY)
        Rt(d, MR, y, "Adjustment Amount", sm, GRAY); y += 26
        row_y = y
        L(d, ML, y, "WO  -  Overpayment Recovery", bb, RED)
        L(d, 560, y, f"{rc['acct_b']} 01/06/2026 ({rc['patient_b'].title()})", b, RED)
        Rt(d, MR, y, f"(${m(rc['amount'])})", bb, RED)
        box = {"x": (ML - 8) / W, "y": (row_y - 8) / H, "w": (MR - ML + 16) / W, "h": 44 / H}
        y += 42

    rule(d, y); y += 14
    L(d, ML, y, f"Claims: {len(c['claims'])}", b)
    Rt(d, 1180, y, f"Provider Level Adjustments: (${m(rc['amount'])})" if rc else "Provider Level Adjustments: $0.00", b)
    Rt(d, MR, y, f"Net Check/EFT: ${m(c['check_amt'])}", bb)
    y += 24
    L(d, ML, y, "Retain for your records.", sm, GRAY); y += 28
    glossary(d, y, c, sm)
    return img, box


# ---------------------------------------------------------------- T3 Cigna EOP
def draw_cigna(c, rng):
    img = Image.new("RGB", (W, H), (253, 253, 253))
    d = ImageDraw.Draw(img)
    b, bb, sm, h1 = F("sans", 21), F("sans_b", 21), F("sans", 18), F("sans_b", 32)
    y = 58
    L(d, ML, y, "Cigna Healthcare", h1, CIGNA)
    L(d, ML, y + 38, "Explanation of Payment (EOP)", bb, CIGNA)
    Rt(d, MR, y, f"Check/EFT #: {c['check_number']}", bb)
    Rt(d, MR, y + 26, f"Payment Date: {c['check_date']}   Payer ID: {c['payer_meta']['pid']}", sm, GRAY)
    Rt(d, MR, y + 50, f"Check Amount: ${m(c['check_amt'])}   Page 1 of 1", sm, GRAY)
    y += 84
    L(d, ML, y, f"{c['provider']['name']}   NPI {c['provider']['npi']}   TIN {c['provider']['tin']}", sm, GRAY)
    y += 26
    L(d, ML, y, "This is not a bill for the participant to pay.", sm, GRAY)
    y += 36
    L(d, ML, y, "Explanation of Initial Claims Payment", bb, CIGNA); y += 34

    cols = [("Svc Dates", ML), ("POS", 300), ("Procedure", 380), ("Mod", 510), ("Units", 580),
            ("Billed", 730), ("Allowed", 870), ("Ded", 960), ("Coins", 1060), ("Pt Resp", 1180), ("Remarks", 1290), ("Paid", MR)]
    for cl in c["claims"]:
        L(d, ML, y, f"Patient: {cl['patient'].title()}    Member ID: {cl['member']}"
                    f"    Account: {cl['acct']}    Claim Number: {cl['icn']}", bb, (20, 40, 70))
        y += 30
        for name, x in cols:
            (Rt if name in ("Billed", "Allowed", "Ded", "Coins", "Pt Resp", "Paid") else L)(d, x, y, name, sm, GRAY)
        y += 24
        L(d, ML, y, f"01/{cl['dos_dd']:02d}-01/{cl['dos_dd']:02d}", b); L(d, 300, y, cl["pos"], b)
        L(d, 380, y, cl["cpt"], b); L(d, 510, y, cl["mod"], b); L(d, 580, y, str(cl["units"]), b)
        Rt(d, 730, y, "$" + m(cl["billed"]), b); Rt(d, 870, y, "$" + m(cl["allowed"]), b)
        Rt(d, 960, y, "$" + m(cl["deduct"]), b); Rt(d, 1060, y, "$" + m(cl["coins"]), b)
        Rt(d, 1180, y, "$" + m(cl["deduct"] + cl["coins"]), b)
        L(d, 1230, y, cl["carcs"][0][0], b); Rt(d, MR, y, "$" + m(cl["paid"]), bb)
        y += 30; rule(d, y, fill=(225, 225, 225), w=1); y += 14

    box = None
    rc = c["recoup"]
    if rc:
        y += 6
        d.rounded_rectangle((ML, y, MR, y + 196), radius=8, outline=CIGNA, width=2, fill=(244, 248, 252))
        L(d, ML + 16, y + 12, "Specification of Recoupment", bb, CIGNA)
        rows = [("Negative Balance ID:", rc["nb_id"]), ("Negative Balance Original Total:", "$" + m(rc["amount"])),
                ("Patient (Account):", f"{rc['patient_b'].title()} / {rc['acct_b']}"),
                ("Original Claim Number:", rc["prior_icn"]),
                ("Recoupment Amount:", "-$" + m(rc["amount"])), ("Remaining Balance:", "$0.00")]
        yy = y + 46
        for lbl, val in rows:
            isamt = lbl.startswith("Recoupment Amount")
            L(d, ML + 24, yy, lbl, b if not isamt else bb, INK if not isamt else RED)
            L(d, ML + 430, yy, val, b if not isamt else bb, INK if not isamt else RED)
            if isamt:
                box = {"x": (ML + 8) / W, "y": (yy - 6) / H, "w": (MR - ML - 16) / W, "h": 36 / H}
            yy += 24
        y += 212

    rule(d, y); y += 14
    Rt(d, 1180, y, f"Less Recoupment: -${m(rc['amount'])}" if rc else "Less Recoupment: $0.00", b)
    Rt(d, MR, y, f"Net Check Amount: ${m(c['check_amt'])}", bb)
    y += 30
    glossary(d, y, c, sm)
    return img, box


# ---------------------------------------------------------------- T4 Aetna / BCBS statement
def draw_aetna(c, rng):
    base = Image.new("RGB", (W, H), (253, 252, 250))
    # THIS IS NOT A BILL watermark (faint, diagonal, behind content)
    wl = Image.new("RGBA", (1400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(wl).text((20, 60), "THIS IS NOT A BILL", font=F("sans_b", 110), fill=(120, 120, 120, 38))
    wl = wl.rotate(22, expand=True, resample=Image.BICUBIC)
    base.paste(wl, (120, 760), wl)
    img = base
    d = ImageDraw.Draw(img)
    is_bcbs = c["payer_key"] == "bcbs"
    accent = BCBS if is_bcbs else AETNA
    b, bb, sm, h1 = F("sans", 22), F("sans_b", 22), F("sans", 19), F("sans_b", 34)
    y = 56
    if is_bcbs:
        d.rectangle((ML, y, ML + 34, y + 34), fill=accent)
        d.ellipse((ML + 22, y + 4, ML + 50, y + 32), fill=(220, 60, 60))
        L(d, ML + 64, y + 2, "Blue Cross Blue Shield", h1, accent)
        L(d, ML, y + 46, "An independent licensee of the Blue Cross and Blue Shield Association", sm, GRAY)
        title = "Remittance Voucher"
    else:
        L(d, ML, y, "aetna", h1, accent)
        title = "Explanation of Benefits"
    Rt(d, MR, y, title, bb)
    y += 84
    L(d, ML, y, f"Provider: {c['provider']['name']}   NPI {c['provider']['npi']}", sm, GRAY)
    Rt(d, MR, y, f"Check/EFT #: {c['check_number']}   Date: {c['check_date']}", sm, GRAY)
    y += 26
    Rt(d, MR, y, f"Payer ID: {c['payer_meta']['pid']}   Amount paid to provider: ${m(c['check_amt'])}", sm, GRAY)
    y += 40

    for cl in c["claims"]:
        ch = 150
        d.rounded_rectangle((ML, y, MR, y + ch), radius=10, outline=(220, 220, 225), width=1, fill=(255, 255, 255))
        d.rounded_rectangle((ML, y, MR, y + 36), radius=10, fill=tuple(min(255, v + 150) for v in accent))
        L(d, ML + 16, y + 7, f"{cl['patient'].title()}   ·   Member {cl['member']}   ·   Claim {cl['icn']}"
                             f"   ·   Date of service 01/{cl['dos_dd']:02d}/2026", bb, accent)
        yy = y + 50
        heads = [("Service", ML + 16), ("Charged", 620), ("Plan discount", 800), ("Plan paid", 1010),
                 ("Deductible", 1170), ("Coins", 1310), ("What you may owe", MR)]
        for name, x in heads:
            (Rt if x > 600 else L)(d, x, yy, name, sm, GRAY)
        yy += 28
        L(d, ML + 16, yy, f"{cl['cpt']}  {cl['cpt_desc']}", b)
        Rt(d, 620, yy, "$" + m(cl["billed"]), b)
        Rt(d, 800, yy, f"{cl['carcs'][0][0]} ${m(cl['carcs'][0][1])}", b)
        Rt(d, 1010, yy, "$" + m(cl["paid"]), b)
        Rt(d, 1170, yy, "$" + m(cl["deduct"]), b); Rt(d, 1310, yy, "$" + m(cl["coins"]), b)
        Rt(d, MR, yy, "$" + m(cl["deduct"] + cl["coins"]), bb)
        yy += 30
        L(d, ML + 16, yy, f"Amount we paid your provider: ${m(cl['paid'])}    See note {cl['rarcs'][0]}", sm, GRAY)
        y += ch + 16

    box = None
    rc = c["recoup"]
    if rc:
        bh = 96
        d.rounded_rectangle((ML, y, MR, y + bh), radius=10, fill=(253, 240, 222), outline=(212, 150, 60), width=2)
        L(d, ML + 16, y + 12, "Prior Account Adjustment / Overpayment Recovery", bb, (150, 90, 20))
        row_y = y + 46
        L(d, ML + 16, row_y, f"We recovered a prior overpayment originally paid for {rc['patient_b'].title()} "
                             f"(Acct {rc['acct_b']}, Claim {rc['prior_icn']}).", b, (90, 60, 20))
        Rt(d, MR - 16, row_y, f"-${m(rc['amount'])}", F("sans_b", 24), RED)
        box = {"x": (ML - 8) / W, "y": (row_y - 8) / H, "w": (MR - ML + 16) / W, "h": 44 / H}
        y += bh + 16

    rule(d, y); y += 14
    Rt(d, 1200, y, f"Prior overpayment recovered: -${m(rc['amount'])}" if rc else "Prior overpayment recovered: $0.00", b)
    Rt(d, MR, y, f"Net paid to provider: ${m(c['check_amt'])}", bb)
    y += 30
    glossary(d, y, c, sm, friendly=True)
    return img, box


DRAW = {"medicare": draw_medicare, "uhc": draw_uhc, "cigna": draw_cigna, "aetna_bcbs": draw_aetna}


def _stamp(seed):
    rng = random.Random(seed)
    layer = Image.new("RGBA", (360, 150), (0, 0, 0, 0))
    sd = ImageDraw.Draw(layer)
    sd.rounded_rectangle((6, 6, 354, 144), radius=10, outline=(150, 40, 40, 140), width=4)
    sd.multiline_text((28, 22), f"RECEIVED\nJAN {rng.randint(23,28)} 2026\nMAILROOM",
                      font=F("sans_b", 36), fill=(150, 40, 40, 140), spacing=6)
    return layer.rotate(rng.uniform(-15, -7), expand=True, resample=Image.BICUBIC)


def scannify(img, template, seed):
    rng = random.Random(seed)
    # Mailroom stamp dropped into clear lower-right whitespace (off the claim data).
    if template in ("medicare", "uhc"):
        st = _stamp(seed)
        img.paste(st, (rng.randint(1175, 1300), rng.randint(1030, 1150)), st)
    fold = Image.new("L", img.size, 0)
    fy = rng.randint(int(H * 0.5), int(H * 0.62))
    ImageDraw.Draw(fold).rectangle((0, fy - 2, W, fy + 2), fill=rng.randint(16, 30))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, fold.filter(ImageFilter.GaussianBlur(3)))
    if template == "medicare":
        # Authentic monochrome line-printer output.
        g = img.convert("L").filter(ImageFilter.GaussianBlur(0.45))
        g = Image.blend(g, Image.effect_noise(g.size, 12), rng.uniform(0.04, 0.06))
        return Image.merge("RGB", (g, g, g)).point(lambda v: int(min(255, v * rng.uniform(0.97, 1.03))))
    # Color scan: keep payer branding, add grain + soft blur + slight gamma drift.
    img = img.filter(ImageFilter.GaussianBlur(0.4))
    img = Image.blend(img, Image.effect_noise(img.size, 10).convert("RGB"), rng.uniform(0.035, 0.05))
    return img.point(lambda v: int(min(255, v * rng.uniform(0.98, 1.02))))


def save_pdf(img, path):
    buf = io.BytesIO(); img.save(buf, format="PNG")
    doc = fitz.open(); page = doc.new_page(width=img.width, height=img.height)
    page.insert_image(fitz.Rect(0, 0, img.width, img.height), stream=buf.getvalue())
    doc.save(str(path)); doc.close()


def _validate(case):
    if not case["has_planted_recoup"]:
        return
    rr = reconcile([LineItem(**ln) for ln in case["lines"]])
    cross = [x for x in rr.recoups if x.status == "matched" and x.cross_patient]
    assert len(cross) == 1, f"{case['doc_id']}: expected 1 cross recoup, got {len(cross)}"
    paid = sum(c["paid"] for c in case["claims"])
    assert abs((paid - case["recoup"]["amount"]) - case["check_amt"]) < 0.01, f"{case['doc_id']}: check doesn't foot"


def main(n=24, recoup_cases=3, seed=7):
    (OUT / "eobs").mkdir(parents=True, exist_ok=True)
    cases = build_cases(n, recoup_cases, seed)
    truth = []
    for i, c in enumerate(cases):
        _validate(c)
        base, box = DRAW[c["template"]](c, random.Random(seed + i))
        img = scannify(base, c["template"], seed + i)
        img.save(OUT / "eobs" / f"{c['doc_id']}.png")
        save_pdf(img, OUT / "eobs" / f"{c['doc_id']}.pdf")
        th = img.copy(); th.thumbnail((260, 340)); th.save(OUT / "eobs" / f"{c['doc_id']}.thumb.png")
        rc = c["recoup"]
        truth.append({
            "doc_id": c["doc_id"], "template": c["template"], "has_planted_recoup": c["has_planted_recoup"],
            "payer": c["payer_str"], "check_number": c["check_number"], "recoup_box": box,
            "recoup_text": (f"{rc['patient_b']}  -{rc['amount']:.2f}" if rc else None),
            "lines": c["lines"],
        })
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    by_t = {}
    for c in cases:
        by_t[c["template"]] = by_t.get(c["template"], 0) + 1
    planted = sum(1 for t in truth if t["has_planted_recoup"])
    print(f"Generated {len(cases)} EOBs across {by_t}; {planted} planted recoups. Validation passed.")


if __name__ == "__main__":
    main()
