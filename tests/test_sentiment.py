"""Deterministic, hand-verified tests for the sentiment/event module (PLAN §4.6/§4.7)."""

import pytest

from signal_engine.news.models import EventType
from signal_engine.news.sentiment import (
    EventClassifier,
    FinBERTSentiment,
    LexiconSentiment,
    SentimentModel,
    default_sentiment_model,
)

# --------------------------------------------------------------------------- #
# LexiconSentiment
# --------------------------------------------------------------------------- #

def test_all_positive_scores_plus_one():
    # tokens: company, bags(+), large, order(+), profit(+), jumps(+)
    # p=4, n=0 -> (4-0)/4 = 1.0
    model = LexiconSentiment()
    assert model.score("Company bags large order, profit jumps") == 1.0


def test_mixed_negative_exact_value():
    # tokens: stock, plunges(-), as, company, misses(-), profit(+), and, faces, probe(-)
    # p=1, n=3 -> (1-3)/(1+3) = -0.5
    model = LexiconSentiment()
    assert model.score("Stock plunges as company misses profit and faces probe") == -0.5


def test_neutral_no_keywords_is_zero():
    model = LexiconSentiment()
    assert model.score("The company held its annual general meeting today") == 0.0


def test_balanced_equal_keywords_is_zero():
    # tokens: profit(+), and, loss(-) -> p=1, n=1 -> 0/2 = 0.0
    model = LexiconSentiment()
    assert model.score("profit and loss") == 0.0


def test_scores_always_in_range():
    model = LexiconSentiment()
    samples = [
        "Company bags large order, profit jumps",
        "Stock plunges as company misses profit and faces probe",
        "profit and loss",
        "The company held its annual general meeting today",
        "fraud probe penalty loss downgrade plunge",
        "beats surges wins upgrade record approval buyback dividend",
        "",
    ]
    for s in samples:
        score = model.score(s)
        assert -1.0 <= score <= 1.0


def test_word_sets_introspectable_and_lowercase():
    model = LexiconSentiment()
    assert "profit" in model.positive
    assert "fraud" in model.negative
    assert all(w == w.lower() for w in model.positive)
    assert all(w == w.lower() for w in model.negative)
    # No overlap between the two sets.
    assert model.positive.isdisjoint(model.negative)


# --------------------------------------------------------------------------- #
# EventClassifier
# --------------------------------------------------------------------------- #

def test_classify_order_win():
    clf = EventClassifier()
    assert clf.classify("Larsen bags large order from railways") == EventType.ORDER_WIN


def test_classify_earnings():
    clf = EventClassifier()
    assert clf.classify("Q4 results: net profit up 20%") == EventType.EARNINGS


def test_classify_litigation():
    clf = EventClassifier()
    assert clf.classify("SEBI probe into company") == EventType.LITIGATION


def test_classify_downgrade():
    clf = EventClassifier()
    assert clf.classify("Brokerage downgrades stock to sell") == EventType.DOWNGRADE


def test_classify_corp_action():
    clf = EventClassifier()
    assert clf.classify("Board declares dividend of Rs 5 per share") == EventType.CORP_ACTION


def test_classify_management():
    clf = EventClassifier()
    assert clf.classify("CEO resigns with immediate effect") == EventType.MANAGEMENT


def test_classify_generic():
    clf = EventClassifier()
    assert clf.classify("Company opens new office in Pune") == EventType.GENERIC


# --------------------------------------------------------------------------- #
# FinBERTSentiment stub + factory
# --------------------------------------------------------------------------- #

def test_finbert_raises_without_transformers():
    # transformers is intentionally absent in this environment.
    with pytest.raises(RuntimeError):
        FinBERTSentiment()


def test_default_sentiment_model_is_lexicon():
    model = default_sentiment_model()
    assert isinstance(model, LexiconSentiment)
    assert isinstance(model, SentimentModel)
