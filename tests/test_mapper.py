"""Tests for the news symbol mapper (PLAN §4.6).

Ordering rule under test: results are sorted by first-match position in the
headline, ties broken alphabetically by symbol.
"""

from signal_engine.news.mapper import SymbolMapper, default_symbol_aliases


def _mapper() -> SymbolMapper:
    return SymbolMapper(default_symbol_aliases())


def test_alias_phrase_match():
    assert _mapper().map("Reliance Industries posts record profit") == ["RELIANCE"]


def test_multiple_symbols_ordered_by_position():
    # "Infosys" appears before "TCS" -> INFY first.
    assert _mapper().map("Infosys wins large deal; TCS also bags order") == ["INFY", "TCS"]


def test_multiword_alias():
    assert _mapper().map("HDFC Bank gains on results") == ["HDFCBANK"]


def test_ticker_whole_word_no_superstring_match():
    m = _mapper()
    assert m.map("RELIANCE Q4 results") == ["RELIANCE"]
    # Made-up superstrings must NOT match the ticker (and no alias matches either).
    assert m.map("RELIANCES surged today") == []
    assert m.map("XRELIANCE is not a stock") == []


def test_ticker_not_inside_word():
    m = _mapper()
    assert m.map("TCS bags order") == ["TCS"]
    assert m.map("TCSX is unrelated") == []
    assert m.map("matcstcs noise") == []


def test_case_insensitive():
    assert _mapper().map("reliance industries posts record profit") == ["RELIANCE"]


def test_no_mapping():
    assert _mapper().map("Market opens flat amid global cues") == []


def test_empty_headline():
    assert _mapper().map("") == []


def test_regex_safety_special_char_alias():
    # Alias with regex-special char must be matched literally, not as a pattern.
    mapper = SymbolMapper({"MM": ["M&M", "Mahindra"]})
    assert mapper.map("M&M reports strong SUV sales") == ["MM"]
    # The "." in this custom alias is a literal dot, not "any char" (word-char edges).
    weird = SymbolMapper({"FOO": ["A.B"]})
    assert weird.map("A.B announces results") == ["FOO"]
    assert weird.map("AXB announces results") == []
    # Must not raise on construction or matching.
    assert weird.map("nothing here") == []


def test_dedup_repeated_mentions():
    # Multiple aliases of the same symbol in one headline -> single entry.
    assert _mapper().map("Reliance and Reliance Industries (RIL) news") == ["RELIANCE"]


def test_map_many_aligned():
    m = _mapper()
    headlines = [
        "Infosys wins large deal",
        "Market opens flat",
        "HDFC Bank gains",
    ]
    result = m.map_many(headlines)
    assert result == [["INFY"], [], ["HDFCBANK"]]
    assert len(result) == len(headlines)
