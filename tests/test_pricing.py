"""The non-negotiable: dollar amounts must be exactly right."""
from app.pricing import cost_usd, count_tokens, usage_from_response


def test_cost_hand_computed_openai():
    # gpt-4o: $2.50/1M in, $10.00/1M out
    assert cost_usd("openai", "gpt-4o", 1_000, 500) == 0.0025 + 0.005


def test_cost_hand_computed_anthropic():
    # claude-haiku-4-5: $1/1M in, $5/1M out
    assert cost_usd("anthropic", "claude-haiku-4-5", 2_000_000, 100_000) == 2.0 + 0.5


def test_unknown_model_returns_none_not_zero():
    assert cost_usd("openai", "gpt-99-turbo-max", 1000, 1000) is None


def test_dated_model_snapshot_prefix_match():
    # dated snapshots must price as their base model
    assert cost_usd("openai", "gpt-4o-2024-08-06", 1_000_000, 0) == 2.50
    # ...and must NOT collide with the longer-named sibling
    assert cost_usd("openai", "gpt-4o-mini-2024-07-18", 1_000_000, 0) == 0.15


def test_token_count_nonzero_and_exactness_flag():
    n, exact = count_tokens("hello world, this is a token counting test")
    assert n > 0
    assert isinstance(exact, bool)


def test_usage_extraction_both_providers():
    assert usage_from_response("openai", {"usage": {"prompt_tokens": 7, "completion_tokens": 3}}) == (7, 3)
    assert usage_from_response("anthropic", {"usage": {"input_tokens": 9, "output_tokens": 4}}) == (9, 4)
    assert usage_from_response("openai", {}) is None
