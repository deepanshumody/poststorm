"""Money is integer cents everywhere in the ledger; format to dollars only at the edge."""


def to_cents(dollars: float) -> int:
    return int(round(dollars * 100))


def to_dollars(cents: int) -> float:
    return round(cents / 100, 2)
