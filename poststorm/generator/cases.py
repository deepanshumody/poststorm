"""Synthetic remittance case specs across four authentic payer templates.

Every document shares the 835 skeleton but diverges in masthead/font/wording.
Recoupments are emitted as a provider-level adjustment (PLB) — NOT an inline
negative claim — while still producing one reconcilable recoup LineItem:
a payment to Patient A of $X and an overpayment recovery of $X for a DIFFERENT
Patient B on the SAME check (the cross-patient dump-account signal).

`claims`/`recoup`/header fields drive rendering; `lines` is the canonical
LineItem extraction target consumed by reconcile + ground truth.
"""
import random

TEMPLATES = ["medicare", "uhc", "cigna", "aetna_bcbs"]

MACS = ["NORIDIAN HEALTHCARE SOLUTIONS", "NOVITAS SOLUTIONS", "PALMETTO GBA",
        "WPS GOVERNMENT HEALTH ADMINISTRATORS", "NATIONAL GOVERNMENT SERVICES",
        "CGS ADMINISTRATORS", "FIRST COAST SERVICE OPTIONS"]
MAC_ADDR = "MEDICARE PART B    PO BOX 6704, FARGO, ND 58108-6704"

PAYERS = {
    "uhc": {"name": "UnitedHealthcare", "addr": "PO Box 30555, Salt Lake City, UT 84130", "pid": "87726"},
    "cigna": {"name": "Cigna Healthcare", "addr": "PO Box 188061, Chattanooga, TN 37422", "pid": "62308"},
    "aetna": {"name": "Aetna", "addr": "PO Box 14079, Lexington, KY 40512", "pid": "60054"},
    "bcbs": {"name": "Blue Cross Blue Shield", "addr": "PO Box 660044, Dallas, TX 75266", "pid": "00510"},
}
PROVIDERS = [
    {"name": "Riverside Family Medicine", "addr": "1820 W Main St, Springfield, IL 62704",
     "npi": "1043217865", "ptan": "IL0043217", "tin": "37-1882044"},
    {"name": "Lakeshore Internal Medicine", "addr": "455 Lake Ave, Madison, WI 53703",
     "npi": "1295736410", "ptan": "WI0129573", "tin": "39-2014778"},
    {"name": "Summit Orthopedic Associates", "addr": "30 Parkway Dr, Columbus, OH 43215",
     "npi": "1639920457", "ptan": "OH0163992", "tin": "31-0998123"},
]
CPTS = [("99213", "Office/outpatient visit, est, low"), ("99214", "Office/outpatient visit, est, mod"),
        ("99215", "Office/outpatient visit, est, high"), ("80053", "Comprehensive metabolic panel"),
        ("85025", "Complete blood count w/ auto diff"), ("93000", "Electrocardiogram, complete"),
        ("71046", "Radiologic exam, chest, 2 views"), ("20610", "Arthrocentesis, major joint")]
MODS = ["", "", "", "25", "59", "RT", "LT"]
FIRST = ["JAMES", "MARIA", "ROBERT", "LINDA", "DAVID", "SUSAN", "JOHN", "KAREN", "MICHAEL",
         "NANCY", "DANIEL", "PATRICIA", "ANGELA", "THOMAS", "SANDRA", "KEVIN", "BARBARA",
         "RICHARD", "JESSICA", "CHARLES", "ASHLEY", "JOSEPH", "EMILY", "PAUL"]
LAST = ["LEE", "PATEL", "GARCIA", "SMITH", "NGUYEN", "BROWN", "JONES", "DAVIS", "WILSON",
        "KHAN", "MARTINEZ", "CLARK", "ROBINSON", "WALKER", "YOUNG", "ADAMS", "HALL",
        "ALLEN", "KING", "WRIGHT", "TORRES", "HILL", "GREEN", "BAKER"]

# CARC/RARC verbatim text (only codes actually used get glossed)
CARC_TEXT = {
    "CO-45": "Charge exceeds fee schedule/maximum allowable or contracted fee arrangement",
    "CO-253": "Sequestration - reduction in federal payment",
    "PR-1": "Deductible amount",
    "PR-2": "Coinsurance amount",
    "CO-97": "Benefit included in payment/allowance for another service already adjudicated",
    "OA-23": "Impact of prior payer(s) adjudication including payments and/or adjustments",
}
RARC_TEXT = {
    "N130": "Consult plan benefit documents/guidelines for information about restrictions",
    "M15": "Separately billed services/tests bundled; not separately payable",
    "MA18": "Claim information forwarded to the patient's supplemental/secondary insurer",
    "N469": "Alert: overpayment recovered; offset applied to this remittance",
    "MA01": "Alert: appeal rights and timeframe if you do not agree",
}
_MBI_A = "ACDEFGHJKMNPQRTUVWXY"  # MBI letters exclude S,L,O,I,B,Z


def _mbi(rng):
    p = [str(rng.randint(1, 9)), rng.choice(_MBI_A), rng.choice(_MBI_A + "0123456789"),
         str(rng.randint(0, 9)), rng.choice(_MBI_A), rng.choice(_MBI_A + "0123456789"),
         str(rng.randint(0, 9)), rng.choice(_MBI_A), rng.choice(_MBI_A),
         str(rng.randint(0, 9)), str(rng.randint(0, 9))]
    return "".join(p)


def _member(rng, bcbs=False):
    pre = rng.choice(["XJK", "ZUH", "QPR", "YMA"]) if bcbs else ""
    return pre + "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(9, 10)))


def _icn(rng):
    return f"{rng.randint(10, 39)}26{rng.randint(100000000, 999999999)}"[:13]


def _claim(rng, payer, check, dos_dd, medicare):
    cpt, cpt_desc = rng.choice(CPTS)
    billed = float(rng.randint(140, 900))
    allowed = round(billed * rng.uniform(0.42, 0.78), 2)
    deduct = float(rng.choice([0, 0, 0, 25, 50]))
    coins = round(max(allowed - deduct, 0) * rng.choice([0, 0, 0.1, 0.2]), 2)
    pre = max(allowed - deduct - coins, 0)
    seq = round(pre * 0.02, 2) if medicare else 0.0
    paid = round(pre - seq, 2)
    carcs = [("CO-45", round(billed - allowed, 2))]
    if medicare and seq:
        carcs.append(("CO-253", seq))
    if deduct:
        carcs.append(("PR-1", deduct))
    if coins:
        carcs.append(("PR-2", coins))
    rarcs = [rng.choice(["N130", "M15"])]
    return {
        "patient": f"{rng.choice(LAST)}, {rng.choice(FIRST)}", "mbi": _mbi(rng),
        "member": _member(rng, payer == "bcbs"), "acct": f"{rng.choice(['RIV','LAK','SUM'])}-{rng.randint(1000,9999)}",
        "icn": _icn(rng), "dos_dd": dos_dd, "dos_iso": f"2026-01-{dos_dd:02d}",
        "cpt": cpt, "cpt_desc": cpt_desc, "mod": rng.choice(MODS), "pos": rng.choice(["11", "11", "22", "19"]),
        "units": 1, "billed": billed, "allowed": allowed, "deduct": deduct, "coins": coins,
        "seq": seq, "paid": paid, "carcs": carcs, "rarcs": rarcs,
    }


def _line(c, payer_str, check):
    return {
        "claim_id": c["icn"], "payer": payer_str, "patient_ref": c["patient"],
        "service_date": c["dos_iso"], "carc": c["carcs"][0][0], "rarc": c["rarcs"],
        "charge": c["billed"], "allowed": c["allowed"], "paid": c["paid"],
        "adjustment": round(c["billed"] - c["allowed"], 2),
        "patient_responsibility": round(c["deduct"] + c["coins"], 2),
        "event_type": "payment", "recoup_flag": False, "offset_link": None,
        "check_number": check, "confidence": "high",
        "source_span": f"{c['icn']} {c['patient']} {c['paid']:.2f}",
    }


def build_cases(n: int, recoup_cases: int, seed: int, ambiguous_cases: int = 1) -> list[dict]:
    rng = random.Random(seed)
    # Spread planted recoups across DISTINCT templates so each shows its own recoup
    # rendering (Medicare PLB WO / UHC Provider-Level Adj / Cigna recoupment box / Aetna band).
    recoup_idxs, used_t = set(), set()
    for i in range(len(TEMPLATES), n):
        if len(recoup_idxs) >= recoup_cases:
            break
        t = TEMPLATES[i % len(TEMPLATES)]
        if t not in used_t:
            recoup_idxs.add(i)
            used_t.add(t)

    # Choose ambiguous indices: distinct from recoup indices, with room for 2 degraded followers.
    # Selection is deterministic (first valid indices) to avoid extra rng calls before the main loop.
    ambig_idxs: set[int] = set()
    for i in range(n - 2):
        if len(ambig_idxs) >= ambiguous_cases:
            break
        if i not in recoup_idxs and (i + 1) not in recoup_idxs and (i + 2) not in recoup_idxs:
            ambig_idxs.add(i)

    # Two indices following each ambiguous index are marked degraded.
    degraded_idxs: set[int] = set()
    for ai in ambig_idxs:
        degraded_idxs.add(ai + 1)
        degraded_idxs.add(ai + 2)

    cases = []
    for i in range(n):
        template = TEMPLATES[i % len(TEMPLATES)]
        medicare = template == "medicare"
        if medicare:
            payer_key = "medicare"
            payer_str = "Medicare Part B"
            mac = MACS[i % len(MACS)]
        elif template == "aetna_bcbs":
            payer_key = rng.choice(["aetna", "bcbs"])
            payer_str = PAYERS[payer_key]["name"]
            mac = None
        else:
            payer_key = template
            payer_str = PAYERS[payer_key]["name"]
            mac = None

        prov = rng.choice(PROVIDERS)
        check = ("EFT" + str(rng.randint(1000000, 9999999))) if template == "uhc" else str(rng.randint(1000000, 99999999))
        cdate = f"01/{rng.randint(20, 28):02d}/2026"

        is_ambiguous = i in ambig_idxs
        is_degraded = i in degraded_idxs

        claims = [_claim(rng, payer_key, check, rng.randint(2, 26), medicare) for _ in range(rng.randint(2, 3))]
        # keep patient names distinct within the doc
        seen = set()
        for c in claims:
            while c["patient"] in seen:
                c["patient"] = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
            seen.add(c["patient"])

        # For ambiguous docs: force claims[0] and claims[1] to have the same paid amount so that
        # reconcile finds two cross-patient candidates and emits needs_review.
        if is_ambiguous:
            claims[1]["paid"] = claims[0]["paid"]

        lines = [_line(c, payer_str, check) for c in claims]
        recoup = None
        planted = i in recoup_idxs
        if planted:
            # choose a paid claim P whose paid is unique on this check
            uniq = [c for c in claims if sum(1 for x in claims if abs(x["paid"] - c["paid"]) < 0.005) == 1]
            P = (uniq or claims)[0]
            X = P["paid"]  # copy exactly
            pb = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
            while pb in seen:
                pb = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
            prior_icn = _icn(rng)
            recoup = {
                "patient_b": pb, "mbi_b": _mbi(rng), "member_b": _member(rng, payer_key == "bcbs"),
                "acct_b": f"{rng.choice(['RIV','LAK','SUM'])}-{rng.randint(1000,9999)}",
                "amount": X, "prior_icn": prior_icn,
                "fcn": f"21{rng.randint(10,39)}R{rng.randint(1000000,9999999)}",
                "nb_id": f"NB-2026-{rng.randint(10000,99999)}", "date": cdate,
            }
            lines.append({
                "claim_id": prior_icn, "payer": payer_str, "patient_ref": pb,
                "service_date": cdate.replace("01/", "2026-01-").replace("/2026", ""),
                "carc": None, "rarc": ["N469"], "charge": X, "allowed": X, "paid": -X,
                "adjustment": X, "patient_responsibility": 0.0, "event_type": "recoup",
                "recoup_flag": True, "offset_link": None, "check_number": check, "confidence": "high",
                "source_span": f"WO overpayment recovery {pb} -{X:.2f}",
            })

        if is_ambiguous:
            # Plant ambiguous recoup: same amount as claims[0]["paid"] but for a third patient,
            # creating two cross-patient candidates so reconcile emits needs_review.
            # ambiguous doc draws extra rng (names/ids) — intentionally advances the sequence for later docs
            X = claims[0]["paid"]
            pb = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
            while pb in seen:
                pb = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
            seen.add(pb)
            prior_icn = _icn(rng)
            recoup = {
                "patient_b": pb, "mbi_b": _mbi(rng), "member_b": _member(rng, payer_key == "bcbs"),
                "acct_b": f"{rng.choice(['RIV','LAK','SUM'])}-{rng.randint(1000,9999)}",
                "amount": X, "prior_icn": prior_icn,
                "fcn": f"21{rng.randint(10,39)}R{rng.randint(1000000,9999999)}",
                "nb_id": f"NB-2026-{rng.randint(10000,99999)}", "date": cdate,
            }
            lines.append({
                "claim_id": prior_icn, "payer": payer_str, "patient_ref": pb,
                "service_date": cdate.replace("01/", "2026-01-").replace("/2026", ""),
                "carc": None, "rarc": ["N469"], "charge": X, "allowed": X, "paid": -X,
                "adjustment": X, "patient_responsibility": 0.0, "event_type": "recoup",
                "recoup_flag": True, "offset_link": None, "check_number": check, "confidence": "high",
                "source_span": f"WO overpayment recovery {pb} -{X:.2f}",
            })

        check_amt = round(sum(c["paid"] for c in claims) - (recoup["amount"] if recoup else 0), 2)
        cases.append({
            "doc_id": f"eob_{i:03d}", "template": template, "payer_key": payer_key,
            "payer_str": payer_str, "mac": mac, "payer_meta": PAYERS.get(payer_key, {}),
            "provider": prov, "check_number": check, "check_date": cdate, "check_amt": check_amt,
            "has_planted_recoup": planted or is_ambiguous, "claims": claims, "recoup": recoup,
            "lines": lines, "ambiguous": is_ambiguous, "degraded": is_degraded,
        })
    return cases
