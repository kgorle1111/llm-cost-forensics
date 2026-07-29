"""Judge parsing + verdict logic (the API-calling path is exercised manually
via scripts/judge_run.py with real keys — never in CI)."""
from app.judge import parse_judge_scores, verdict


def test_parse_clean_json():
    assert parse_judge_scores('{"score_a": 8, "score_b": 7.5}') == (8.0, 7.5)


def test_parse_json_wrapped_in_prose():
    text = 'Sure — here is my assessment:\n{"score_a": 6, "score_b": 9}\nHope that helps!'
    assert parse_judge_scores(text) == (6.0, 9.0)


def test_parse_garbage_returns_none():
    assert parse_judge_scores("I cannot compare these responses.") is None


def test_verdict_bands():
    assert verdict(win_rate_b=0.9, mean_delta=0.1) == "safe"
    assert verdict(win_rate_b=0.7, mean_delta=-1.0) == "risky"
    assert verdict(win_rate_b=0.3, mean_delta=-3.0) == "unsafe"
