from datetime import datetime, timedelta, timezone

from app import security


def test_passcode_format_and_split():
    code = security.new_passcode("AB2C")
    tag, secret = security.split_passcode(code.lower())  # case-insensitive entry
    assert tag == "AB2C" and len(secret) == 10
    assert security.split_passcode("nope") is None


def test_hash_roundtrip_and_salting():
    h1, h2 = security.hash_passcode("AB2C-XXXXXXXXXX"), security.hash_passcode("AB2C-XXXXXXXXXX")
    assert h1 != h2  # unique salt per hash
    assert security.verify_passcode("AB2C-XXXXXXXXXX", h1)
    assert not security.verify_passcode("AB2C-YYYYYYYYYY", h1)
    assert not security.verify_passcode("x", "garbage")


def test_token_roundtrip_tamper_and_expiry():
    token = security.issue_token("s", 7, 1, 0, 5)
    assert security.decode_token("s", token)["sub"] == "7"
    assert security.decode_token("other", token) is None
    assert security.decode_token("s", token + "x") is None
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    assert security.decode_token("s", security.issue_token("s", 7, 1, 0, 5, now=old)) is None


def test_throttle_locks_then_recovers():
    now = [0.0]
    t = security.LoginThrottle(3, 60, clock=lambda: now[0])
    for _ in range(3):
        assert t.retry_after("k") == 0
        t.failure("k")
    assert t.retry_after("k") > 0
    now[0] = 61
    assert t.retry_after("k") == 0
    t.failure("k")  # counter restarted after lockout expired
    assert t.retry_after("k") == 0
