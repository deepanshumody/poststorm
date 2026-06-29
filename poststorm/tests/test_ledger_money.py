from backend.ledger import money


def test_to_cents_rounds():
    assert money.to_cents(202.03) == 20203
    assert money.to_cents(-202.03) == -20203
    assert money.to_cents(0.1 + 0.2) == 30  # no float drift


def test_to_dollars():
    assert money.to_dollars(20203) == 202.03
