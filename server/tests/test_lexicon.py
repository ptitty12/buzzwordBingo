"""Tests for the lexical matching engine."""

import pytest

from app.lexicon import (
    exact_key,
    match_keys,
    normalize_text,
    phrase_length,
    phrases_match,
    stem,
    tokenize,
)


class TestNormalize:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize_text("Synergy!!") == "synergy"

    def test_strips_diacritics(self):
        assert normalize_text("Résumé") == "resume"

    def test_expands_ampersand(self):
        assert normalize_text("R&D") == "r and d"

    def test_collapses_whitespace(self):
        assert normalize_text("  low   hanging  ") == "low hanging"

    def test_handles_empty(self):
        assert normalize_text("") == ""


class TestTokenize:
    def test_splits_on_hyphen(self):
        assert tokenize("low-hanging fruit") == ["low", "hanging", "fruit"]

    def test_drops_bare_punctuation(self):
        assert tokenize("well, ... okay?") == ["well", "okay"]

    def test_keeps_digits(self):
        assert tokenize("Q4 2026") == ["q4", "2026"]


class TestStemSymmetry:
    """A word must always match itself — the pipeline is lossy but symmetric."""

    @pytest.mark.parametrize(
        "word",
        [
            "synergy", "leverage", "disrupt", "bandwidth", "alignment", "pivot",
            "scalable", "holistic", "paradigm", "ideate", "roadmap", "blockers",
            "circle back", "low hanging fruit", "move the needle", "deep dive",
        ],
    )
    def test_word_matches_itself(self, word):
        assert phrases_match(word, word)


class TestInflection:
    """The suffixes called out in the product brief: -s, -ed, -ly, plus -ing."""

    @pytest.mark.parametrize(
        ("base", "inflected"),
        [
            ("synergy", "synergies"),
            ("synergy", "synergy"),
            ("leverage", "leveraged"),
            ("leverage", "leveraging"),
            ("leverage", "leverages"),
            ("disrupt", "disrupted"),
            ("disrupt", "disrupting"),
            ("disrupt", "disrupts"),
            ("pivot", "pivoted"),
            ("pivot", "pivoting"),
            ("align", "aligned"),
            ("align", "aligning"),
            ("strategic", "strategically"),
            ("basic", "basically"),
            ("holistic", "holistically"),
            ("quick", "quickly"),
            ("real", "really"),
            ("total", "totally"),
            ("process", "processes"),
            ("ship", "shipped"),
            ("ship", "shipping"),
            ("scale", "scaling"),
            ("scale", "scaled"),
            ("optimize", "optimized"),
            ("optimize", "optimizing"),
            ("iterate", "iterating"),
            ("iterate", "iterated"),
            ("blocker", "blockers"),
            ("deliverable", "deliverables"),
        ],
    )
    def test_inflected_forms_match_base(self, base, inflected):
        assert phrases_match(base, inflected), f"{inflected!r} should match {base!r}"


class TestDerivation:
    @pytest.mark.parametrize(
        ("base", "derived"),
        [
            ("disrupt", "disruption"),
            ("disrupt", "disruptive"),
            ("innovate", "innovation"),
            ("align", "alignment"),
            ("aware", "awareness"),
            ("scalable", "scalability"),
            ("agile", "agility"),
            ("iterate", "iterative"),
            ("operational", "operationalize"),
            ("perform", "performance"),
        ],
    )
    def test_derived_forms_match_base(self, base, derived):
        assert phrases_match(base, derived), f"{derived!r} should match {base!r}"


class TestFalsePositives:
    """Over-stemming is the main risk; guard the pairs most likely to collide."""

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("synergy", "energy"),
            ("pivot", "private"),
            ("scale", "scandal"),
            ("bandwidth", "band"),
            ("this", "thing"),
            ("less", "let"),
            ("news", "new"),
            ("roadmap", "road"),
            ("deep dive", "deep"),
            ("circle back", "circle"),
        ],
    )
    def test_unrelated_words_do_not_match(self, left, right):
        assert not phrases_match(left, right), f"{left!r} must not match {right!r}"


class TestPhrases:
    def test_stopwords_are_optional(self):
        assert phrases_match("move the needle", "move needle")
        assert phrases_match("voice of the customer", "voice of customer")

    def test_phrase_inflection(self):
        assert phrases_match("low hanging fruit", "low-hanging fruits")
        assert phrases_match("circle back", "circling back")

    def test_phrase_length_counts_tokens(self):
        assert phrase_length("low-hanging fruit") == 3
        assert phrase_length("synergy") == 1

    def test_partial_phrase_does_not_match(self):
        assert not phrases_match("low hanging fruit", "low hanging")


class TestExactKey:
    def test_normalises_for_strict_matching(self):
        assert exact_key("Low-Hanging  Fruit!") == "low hanging fruit"

    def test_strict_key_ignores_inflection(self):
        assert exact_key("synergies") != exact_key("synergy")


class TestMatchKeys:
    def test_empty_input_yields_no_keys(self):
        assert match_keys("") == set()
        assert match_keys("   ...  ") == set()

    def test_acronyms_survive_intact(self):
        assert "kpis" in match_keys("KPIs")
        assert stem("ROI") == "roi"
