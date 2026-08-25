"""Tests for Unicode normalizer v2 (spec §8.2)."""

from __future__ import annotations

import pytest

from app.document_ir.normalize import (
    NORMALIZER_VERSION,
    NormalizationError,
    formula_search_aliases,
    normalize_for_retrieval,
)


class TestVersion:
    def test_normalizer_version_pinned(self) -> None:
        assert NORMALIZER_VERSION == "unicode-v2"


class TestNewlinesAndNul:
    def test_crlf_to_lf(self) -> None:
        assert normalize_for_retrieval("a\r\nb").text == "a b"

    def test_cr_to_lf(self) -> None:
        assert normalize_for_retrieval("a\rb").text == "a b"

    def test_nul_deleted(self) -> None:
        result = normalize_for_retrieval("a\x00b")
        assert "\x00" not in result.text


class TestNfkc:
    def test_fullwidth_folds(self) -> None:
        assert normalize_for_retrieval("ＡＢＣ１２３").text == "ABC123"

    def test_greek_preserved(self) -> None:
        text = "ε θ Σ μ"
        assert normalize_for_retrieval(text).text == "ε θ Σ μ"

    def test_plus_minus_percent_preserved(self) -> None:
        assert normalize_for_retrieval("9.46±0.11 (85%)").text == "9.46±0.11 (85%)"

    def test_superscript_folds_via_nfkc(self) -> None:
        assert normalize_for_retrieval("x²").text == "x2"


class TestSoftHyphenAndLigatures:
    def test_soft_hyphen_removed(self) -> None:
        assert normalize_for_retrieval("deci\u00adsion").text == "decision"

    def test_ligatures_folded(self) -> None:
        result = normalize_for_retrieval("\ufb01le \ufb02ow \ufb00ow \ufb03x \ufb04x")
        assert result.text == "file flow ffow ffix fflx"


class TestDehyphenation:
    def test_letter_hyphen_newline_joins(self) -> None:
        result = normalize_for_retrieval("archi-\ntecture next")
        assert result.text == "architecture next"

    def test_digit_range_keeps_hyphen(self) -> None:
        result = normalize_for_retrieval("13.61-\n13.09 minutes")
        assert result.text == "13.61-13.09 minutes"

    def test_negative_number_not_merged_with_next_line(self) -> None:
        # A lone leading minus is not preceded by a digit, so it stays.
        result = normalize_for_retrieval("value\n-5 and 3")
        assert result.text == "value -5 and 3"


class TestDashUnification:
    def test_unicode_minus_becomes_ascii(self) -> None:
        assert normalize_for_retrieval("A − B").text == "A - B"

    def test_en_and_em_dash_become_ascii(self) -> None:
        assert normalize_for_retrieval("3–4 and 5—6").text == "3-4 and 5-6"

    def test_ascii_hyphen_untouched(self) -> None:
        assert normalize_for_retrieval("well-known").text == "well-known"


class TestWhitespaceCollapse:
    def test_mixed_whitespace_collapses(self) -> None:
        assert normalize_for_retrieval("a\n\nb\t c  d").text == "a b c d"

    def test_leading_trailing_stripped(self) -> None:
        assert normalize_for_retrieval("  hello  ").text == "hello"


class TestReplacementCharacter:
    def test_counted_not_deleted(self) -> None:
        result = normalize_for_retrieval("bad\ufffdrest \ufffd")
        assert "\ufffd" in result.text
        assert result.replacement_char_count == 2

    def test_zero_when_clean(self) -> None:
        assert normalize_for_retrieval("clean").replacement_char_count == 0


class TestEmptyOutput:
    def test_empty_raises_by_default(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_for_retrieval("   ")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_for_retrieval("\n\t")

    def test_allow_empty_for_figures(self) -> None:
        result = normalize_for_retrieval("", allow_empty=True)
        assert result.text == ""

    def test_nonempty_ok(self) -> None:
        assert normalize_for_retrieval("x").text == "x"


class TestFormulaAliases:
    def test_aliases_sorted(self) -> None:
        assert formula_search_aliases("θ then Σ then ε") == ["epsilon", "sigma", "theta"]

    def test_no_symbols_gives_empty(self) -> None:
        assert formula_search_aliases("plain text") == []

    def test_mu_alias(self) -> None:
        assert formula_search_aliases("μ value") == ["mu"]

    def test_all_four_symbols(self) -> None:
        assert formula_search_aliases("εθΣμ") == ["epsilon", "mu", "sigma", "theta"]
