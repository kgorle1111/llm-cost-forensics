"""Threshold tuned from a labeled should-hit / should-miss set, not vibes."""
from app.semcache import SemanticCache, similarity

SHOULD_HIT = [
    ("Summarize this ticket: user cannot reset password, email never arrives",
     "summarize this ticket:  User cannot reset password — email never arrives!"),
    ("Rewrite this search query for better recall: red running shoes",
     "rewrite this search query for better recall: red running shoes."),
]

SHOULD_MISS = [
    ("Summarize this ticket: user cannot reset password",
     "Summarize this ticket: app crashes when uploading a photo"),
    ("Translate to French: good morning",
     "Translate to German: good evening friends of the sea"),
    ("What is our refund policy?",
     "What is our shipping policy for international orders?"),
]


def test_labeled_pairs_hit_and_miss():
    for a, b in SHOULD_HIT:
        assert similarity(a, b) >= 0.85, (a, b, similarity(a, b))
    for a, b in SHOULD_MISS:
        assert similarity(a, b) < 0.85, (a, b, similarity(a, b))


def test_cache_lookup_scoping_and_ttl():
    c = SemanticCache(ttl_s=9999, threshold=0.85)
    c.put("team-a", "openai", "gpt-4o", "hello world question", {"ok": 1}, 0.01)
    assert c.lookup("team-a", "openai", "gpt-4o", "hello world question") is not None
    # different team, model, or provider => never a hit
    assert c.lookup("team-b", "openai", "gpt-4o", "hello world question") is None
    assert c.lookup("team-a", "openai", "gpt-4o-mini", "hello world question") is None

    expired = SemanticCache(ttl_s=0.0, threshold=0.85)
    expired.put("t", "openai", "m", "some prompt text here", {"ok": 1}, 0.01)
    import time
    time.sleep(0.01)
    assert expired.lookup("t", "openai", "m", "some prompt text here") is None
