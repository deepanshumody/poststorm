from backend.ratelimit import RateLimiter


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bucket_allows_capacity_then_blocks():
    clock = _Clock()
    rl = RateLimiter(capacity=2, refill_per_sec=0.0, now=clock)
    assert rl.check("acme")[0] is True
    assert rl.check("acme")[0] is True
    allowed, retry = rl.check("acme")
    assert allowed is False and retry > 0


def test_bucket_refills_over_time():
    clock = _Clock()
    rl = RateLimiter(capacity=1, refill_per_sec=1.0, now=clock)
    assert rl.check("acme")[0] is True
    assert rl.check("acme")[0] is False
    clock.t = 1.0  # one second → one token
    assert rl.check("acme")[0] is True


def test_buckets_are_per_key():
    clock = _Clock()
    rl = RateLimiter(capacity=1, refill_per_sec=0.0, now=clock)
    assert rl.check("a")[0] is True
    assert rl.check("b")[0] is True  # different tenant, own bucket
    assert rl.check("a")[0] is False


def test_http_429_with_retry_after(monkeypatch):
    from backend import ratelimit
    from tests._auth import authed_client
    ratelimit.set_limiter(RateLimiter(capacity=1, refill_per_sec=0.0, now=lambda: 0.0))
    try:
        c = authed_client(role="viewer", tenant="rl_tenant")
        assert c.get("/ledger/balances").status_code == 200   # consumes the 1 token
        r = c.get("/ledger/balances")
        assert r.status_code == 429
        assert "retry-after" in {k.lower() for k in r.headers}
    finally:
        ratelimit.set_limiter(None)  # reset to default for other tests
