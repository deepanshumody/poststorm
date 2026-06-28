"""Deterministic synthetic EOB case specs, including planted cross-patient recoups.

A "case" is one scanned document (one check / remittance advice). Most cases are
ordinary payments; `recoup_cases` of them additionally contain a cross-patient
takeback whose (payer, amount, check_number) matches a payment to a DIFFERENT
patient in the same check — the dump-account offset the reconcile engine catches.
"""
import random

PAYERS = ["Aetna", "UnitedHealthcare", "Cigna", "Medicare", "BCBS"]
CARCS = ["CO-45", "PR-1", "CO-97", "CO-16", "PR-2"]
FIRST = ["James", "Maria", "Robert", "Linda", "David", "Susan", "John", "Karen", "Mike", "Nancy"]
LAST = ["Lee", "Patel", "Garcia", "Smith", "Nguyen", "Brown", "Jones", "Davis", "Wilson", "Khan"]


def _line(claim_id, payer, patient_ref, dos, check, charge, allowed, paid,
          event_type="payment", recoup_flag=False, carc=None):
    adjustment = round(charge - allowed, 2)
    pat_resp = round(max(allowed - paid, 0.0), 2) if paid > 0 else 0.0
    return {
        "claim_id": claim_id, "payer": payer, "patient_ref": patient_ref,
        "service_date": dos, "carc": carc, "rarc": [],
        "charge": charge, "allowed": allowed, "paid": paid,
        "adjustment": adjustment, "patient_responsibility": pat_resp,
        "event_type": event_type, "recoup_flag": recoup_flag, "offset_link": None,
        "check_number": check, "confidence": "high",
        "source_span": f"{claim_id} {payer} {paid:.2f}",
    }


def build_cases(n: int, recoup_cases: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    recoup_idxs = set(rng.sample(range(n), min(recoup_cases, n)))
    cases = []
    claim_ctr = 1000

    for i in range(n):
        payer = rng.choice(PAYERS)
        check = f"CHK{rng.randint(10000, 99999)}"
        dos = f"2026-01-{rng.randint(1, 28):02d}"
        lines = []
        for _ in range(rng.randint(1, 3)):
            claim_ctr += 1
            patient = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            charge = float(rng.randint(120, 900))
            allowed = round(charge * rng.uniform(0.45, 0.8), 2)
            paid = round(allowed * rng.uniform(0.7, 1.0), 2)
            lines.append(_line(f"C{claim_ctr}", payer, patient, dos, check,
                               charge, allowed, paid, carc=rng.choice(CARCS)))

        if i in recoup_idxs:
            # Cross-patient takeback: recoup Patient B for the exact amount paid to
            # Patient A (the first line), inside the SAME check. Reconcile should
            # flag this as a dump-account offset.
            orig = lines[0]
            claim_ctr += 1
            patient_b = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            lines.append(_line(f"C{claim_ctr}", payer, patient_b, dos, check,
                               orig["paid"], orig["paid"], -orig["paid"],
                               event_type="recoup", recoup_flag=True, carc="CO-22"))

        cases.append({
            "doc_id": f"eob_{i:03d}",
            "payer": payer,
            "check_number": check,
            "has_planted_recoup": i in recoup_idxs,
            "lines": lines,
        })
    return cases
