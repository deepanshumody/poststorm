"""Synthetic but realistic remittance-advice (EOB) case specs.

Each "case" is one scanned remittance for one check/EFT. It groups claims, each with
a service line (CPT, modifiers, billed/allowed/deductible/coinsurance/paid, group +
CARC/RARC codes). `recoup_cases` of them carry an OVERPAYMENT RECOVERY claim whose
amount + check match a payment to a DIFFERENT patient on the same check — the
cross-patient dump-account offset the reconcile engine catches.

`claims` carries the full detail used for rendering; `lines` is the canonical
extraction target (the LineItem-shaped subset) used by reconcile + ground truth.
"""
import random

PAYERS = [
    {"name": "Aetna", "addr": "PO Box 14079, Lexington, KY 40512-4079", "id": "60054"},
    {"name": "UnitedHealthcare", "addr": "PO Box 30555, Salt Lake City, UT 84130", "id": "87726"},
    {"name": "Cigna Healthcare", "addr": "PO Box 188061, Chattanooga, TN 37422", "id": "62308"},
    {"name": "Blue Cross Blue Shield", "addr": "PO Box 660044, Dallas, TX 75266", "id": "00510"},
    {"name": "Medicare Part B / Novitas", "addr": "PO Box 3093, Mechanicsburg, PA 17055", "id": "MCRPB"},
]
PROVIDERS = [
    {"name": "Riverside Family Medicine", "addr": "1820 W Main St, Springfield, IL 62704",
     "npi": "1043217865", "tax": "37-1882044"},
    {"name": "Lakeshore Internal Medicine", "addr": "455 Lake Ave, Madison, WI 53703",
     "npi": "1295736410", "tax": "39-2014778"},
    {"name": "Summit Orthopedic Associates", "addr": "30 Parkway Dr, Columbus, OH 43215",
     "npi": "1639920457", "tax": "31-0998123"},
]
CPTS = [("99213", "Office/outpatient visit est"), ("99214", "Office/outpatient visit est"),
        ("99215", "Office/outpatient visit est"), ("80053", "Comprehensive metabolic panel"),
        ("85025", "Complete blood count w/ diff"), ("93000", "Electrocardiogram, complete"),
        ("71046", "Chest X-ray, 2 views"), ("20610", "Arthrocentesis, major joint")]
MODS = ["", "", "", "25", "59", "RT"]
CARCS = [("CO", "45", "Charge exceeds fee schedule/maximum allowable"),
         ("PR", "1", "Deductible amount"), ("PR", "2", "Coinsurance amount"),
         ("CO", "97", "Payment included in another service/procedure"),
         ("CO", "16", "Claim lacks information needed for adjudication"),
         ("PR", "3", "Co-payment amount")]
RARCS = [("N130", "Consult plan benefit documents for information"),
         ("M15", "Separately billed services bundled; not separately payable"),
         ("N370", "Billing exceeds the rental months covered"),
         ("MA01", "Alert: appeal rights and timeframe"), ("N130", "Consult plan benefit documents")]
RECOVERY_RARC = ("N469", "Alert: overpayment recovered; offset applied to this remittance")
FIRST = ["James", "Maria", "Robert", "Linda", "David", "Susan", "John", "Karen", "Michael",
         "Nancy", "Daniel", "Patricia", "Angela", "Thomas", "Sandra", "Kevin"]
LAST = ["Lee", "Patel", "Garcia", "Smith", "Nguyen", "Brown", "Jones", "Davis", "Wilson",
        "Khan", "Martinez", "Clark", "Robinson", "Walker", "Young", "Adams"]


def _name(rng):
    return f"{rng.choice(LAST).upper()}, {rng.choice(FIRST).upper()}"


def _icn(rng, dos_compact):
    return f"{dos_compact}{rng.randint(100000, 999999)}"


def build_cases(n: int, recoup_cases: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    recoup_idxs = set(rng.sample(range(n), min(recoup_cases, n)))
    cases = []
    for i in range(n):
        payer = rng.choice(PAYERS)
        provider = rng.choice(PROVIDERS)
        check = f"{rng.randint(40000000, 99999999)}"
        mm, dd = rng.randint(1, 1), rng.randint(2, 26)
        cdate = f"01/{rng.randint(20, 28):02d}/2026"
        claims = []

        for _ in range(rng.randint(2, 3)):
            patient = _name(rng)
            dos = f"01/{dd:02d}-01/{dd:02d}/2026"
            cpt, cpt_desc = rng.choice(CPTS)
            billed = float(rng.randint(120, 900))
            allowed = round(billed * rng.uniform(0.42, 0.78), 2)
            deduct = round(rng.choice([0, 0, 0, 25, 50]) * 1.0, 2)
            coins = round(max(allowed - deduct, 0) * rng.choice([0, 0, 0.1, 0.2]), 2)
            paid = round(max(allowed - deduct - coins, 0), 2)
            grp, carc, carc_desc = rng.choice(CARCS)
            rarc, rarc_desc = rng.choice(RARCS)
            claims.append({
                "icn": _icn(rng, f"2026{dd:02d}"), "patient_ref": patient,
                "member": f"{rng.choice('WXYZ')}{rng.randint(10000000, 99999999)}",
                "acct": f"{provider['name'][:3].upper()}-{rng.randint(1000, 9999)}",
                "dos": dos, "service_date": f"2026-01-{dd:02d}", "cpt": cpt, "cpt_desc": cpt_desc,
                "mod": rng.choice(MODS), "units": 1,
                "billed": billed, "allowed": allowed, "deduct": deduct, "coins": coins, "paid": paid,
                "group": grp, "carc": f"{grp}-{carc}", "carc_desc": carc_desc,
                "rarc": [rarc], "rarc_desc": rarc_desc,
                "event_type": "payment", "recoup_flag": False, "is_recoup": False,
            })

        if i in recoup_idxs:
            orig = claims[0]
            amt = orig["paid"]
            patient_b = _name(rng)
            prior_dd = rng.randint(1, 20)
            claims.append({
                "icn": _icn(rng, f"2026{dd:02d}"), "patient_ref": patient_b,
                "member": f"{rng.choice('WXYZ')}{rng.randint(10000000, 99999999)}", "acct": "",
                "dos": f"01/{prior_dd:02d}-01/{prior_dd:02d}/2026", "service_date": f"2026-01-{prior_dd:02d}",
                "cpt": orig["cpt"], "cpt_desc": orig["cpt_desc"], "mod": "", "units": 1,
                "billed": 0.0, "allowed": 0.0, "deduct": 0.0, "coins": 0.0, "paid": -amt,
                "group": "OA", "carc": "OA-23", "carc_desc": "Prior payer/overpayment adjustment",
                "rarc": [RECOVERY_RARC[0]], "rarc_desc": RECOVERY_RARC[1],
                "event_type": "recoup", "recoup_flag": True, "is_recoup": True,
                "fcn": f"FCN{rng.randint(70000000, 99999999)}",
                "prior_icn": _icn(rng, f"202511{prior_dd:02d}"),
            })

        lines = [{
            "claim_id": c["icn"], "payer": payer["name"], "patient_ref": c["patient_ref"],
            "service_date": c["service_date"], "carc": c["carc"], "rarc": c["rarc"],
            "charge": c["billed"], "allowed": c["allowed"], "paid": c["paid"],
            "adjustment": round(c["billed"] - c["allowed"], 2),
            "patient_responsibility": round(c["deduct"] + c["coins"], 2),
            "event_type": c["event_type"], "recoup_flag": c["recoup_flag"], "offset_link": None,
            "check_number": check, "confidence": "high",
            "source_span": f"{c['icn']} {c['patient_ref']} {c['paid']:.2f}",
        } for c in claims]

        cases.append({
            "doc_id": f"eob_{i:03d}", "payer": payer, "provider": provider,
            "check_number": check, "check_date": cdate, "has_planted_recoup": i in recoup_idxs,
            "claims": claims, "lines": lines,
        })
    return cases
