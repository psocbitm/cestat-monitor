from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

for _mod_name, _attrs in (
    ("requests", {"Session": object, "Response": object, "RequestException": Exception}),
    ("bs4", {"BeautifulSoup": object}),
    ("pypdf", {"PdfReader": object}),
):
    if _mod_name not in sys.modules:
        try:
            __import__(_mod_name)
        except ModuleNotFoundError:
            _stub = types.ModuleType(_mod_name)
            for _k, _v in _attrs.items():
                setattr(_stub, _k, _v)
            sys.modules[_mod_name] = _stub

from cestat_monitor.core import (
    Bench,
    _consolidate_pdf_matches,
    _filter_benches,
    _highlight_snippet,
    generate_summary_markdown,
    load_config,
    match_text,
)
from cestat_monitor.keywords import (
    KeywordConfigError,
    clean_text,
    compile_matcher,
    parse_exclusion_input,
    parse_keyword_input,
)
from cestat_monitor.manage_keywords import apply_keyword_action


class TestKeywordEngine(unittest.TestCase):
    def test_clean_text_dehyphenates_line_wraps(self) -> None:
        self.assertEqual(clean_text("Anti-\ndumping duty"), "Antidumping duty")
        self.assertEqual(clean_text("M/s. Shree-\nCement Ltd."), "M/s. Shree Cement Ltd.")
        self.assertEqual(clean_text("A\u00adC\u2013C"), "AC-C")

    def test_smart_matching_handles_punctuation_and_suffixes(self) -> None:
        text = "1. M/s. ACC Limited vs Commissioner 2. M/s. A.C.C. vs CC 3. Anti dumping & Antidumping 4. Dalmia Ltd."
        matches = match_text(text, ["Acc Ltd", "A C C", "Anti-dumping", "Dalmia Limited"], [], page_number=1)
        kws = [m["keyword"] for m in matches]
        self.assertIn("Acc Ltd", kws)
        self.assertIn("A C C", kws)
        self.assertIn("Anti-dumping", kws)
        self.assertIn("Dalmia Limited", kws)

    def test_context_window_exclusion_blocks_air_cargo_complex_acc(self) -> None:
        text = "12. C/1234/2026 AIR CARGO COMPLEX (ACC), SAHAR vs M/s XYZ"
        rules = parse_keyword_input("ACC -cargo -sahar")
        matches = match_text(text, rules, ["AIR CARGO COMPLEX"], page_number=1)
        self.assertEqual(matches, [])

    def test_proximity_matching_eliminates_unrelated_shree(self) -> None:
        text = "1. Shree Ganesh Traders vs CC. 2. M/s. Shree Cement Limited vs CCE."
        rules = parse_keyword_input("Shree + Cement")
        matches = match_text(text, rules, [], page_number=1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["keyword"], "Shree + Cement")
        self.assertIn("Shree", matches[0]["matched_texts"])
        self.assertIn("Cement", matches[0]["matched_texts"])

    def test_exact_case_sensitive_mode(self) -> None:
        rules = parse_keyword_input("ACC", default_mode="exact", case_sensitive=True)
        self.assertEqual(match_text("acc is lowercase", rules, [], 1), [])
        self.assertEqual(len(match_text("M/s ACC vs CC", rules, [], 1)), 1)

    def test_suppression_telemetry_counts_blocked_hits(self) -> None:
        rules = parse_keyword_input("ACC")
        matcher = compile_matcher(rules, ["AIR CARGO COMPLEX"])
        suppressions: dict[str, int] = {}
        hits = matcher.match_page("AIR CARGO COMPLEX (ACC) MUMBAI", page_number=1, suppressions=suppressions)
        self.assertEqual(hits, [])
        self.assertEqual(suppressions.get("ACC (near AIR CARGO COMPLEX)"), 1)

    def test_parse_keyword_input_rejects_exclusion_only(self) -> None:
        with self.assertRaises(KeywordConfigError):
            parse_keyword_input("-cargo, -import")

    def test_parse_keyword_input_handles_commas_inside_quotes_and_hyphenated_words(self) -> None:
        rules = parse_keyword_input('"A, B & Co", Anti-dumping, Shree + Cement -ganesh')
        self.assertEqual([r.label for r in rules], ["A, B & Co", "Anti-dumping", "Shree + Cement"])
        self.assertEqual(rules[0].mode, "exact")
        self.assertEqual(rules[2].exclude_nearby, ("ganesh",))

    def test_parse_exclusion_input_strips_noise(self) -> None:
        self.assertEqual(parse_exclusion_input(" -Air Cargo, Sahar, air cargo, "), ["Air Cargo", "Sahar"])

    def test_highlight_snippet_marks_proximity_terms(self) -> None:
        highlighted = _highlight_snippet(
            "M/s. Shree Cement Limited vs CCE (account)",
            "Shree + Cement",
            matched_texts=["Shree", "Cement", "CC"],
        )
        self.assertIn('<mark class="kw-hit">Shree</mark>', highlighted)
        self.assertIn('<mark class="kw-hit">Cement</mark>', highlighted)
        self.assertNotIn('<mark class="kw-hit">cc</mark>', highlighted.lower())

    def test_unclosed_quoted_exclusion_and_accounts_scope(self) -> None:
        rules = parse_keyword_input('ACC -"air cargo')
        self.assertEqual(rules[0].exclude_nearby, ("air cargo",))
        cfg_rules, cfg_exclusions = load_config(Path("config/keywords.json"))
        self.assertNotIn("accounts", [e.casefold() for e in cfg_exclusions])
        hits = match_text("M/s. Ambuja Cement Ltd. Pay & Accounts Office", cfg_rules, cfg_exclusions, 1)
        self.assertEqual([h["keyword"] for h in hits], ["Ambuja"])

    def test_consolidate_pdf_matches_preserves_confidence_and_pages(self) -> None:
        raw = [
            {"keyword": "ACC Limited", "page": 1, "snippet": "M/s ACC Ltd", "confidence": "high", "matched_texts": ["ACC Ltd"]},
            {"keyword": "ACC Limited", "page": 3, "snippet": "M/s ACC Ltd", "confidence": "high", "matched_texts": ["ACC Ltd"]},
        ]
        consolidated = _consolidate_pdf_matches(raw)
        self.assertEqual(len(consolidated), 1)
        self.assertEqual(consolidated[0]["pages"], [1, 3])
        self.assertEqual(consolidated[0]["confidence"], "high")


class TestConfigAndWorkflowHelpers(unittest.TestCase):
    def test_load_config_add_vs_replace_mode(self) -> None:
        cfg_path = Path("config/keywords.json")
        added_rules, exclusions = load_config(
            cfg_path,
            override="Ultratech, Dalmia",
            keyword_mode="Add to saved defaults",
            extra_exclusions="Sahar",
        )
        labels = [r.label for r in added_rules]
        self.assertIn("ACC Limited", labels)
        self.assertIn("Ultratech", labels)
        self.assertIn("Dalmia", labels)
        self.assertIn("Sahar", exclusions)

        replaced_rules, _ = load_config(
            cfg_path,
            override="Ultratech",
            keyword_mode="Search ONLY these keywords",
        )
        self.assertEqual([r.label for r in replaced_rules], ["Ultratech"])

    def test_manage_keywords_add_remove_replace_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            default_path = tmp_dir / "keywords.default.json"
            config_path = tmp_dir / "keywords.json"
            baseline = {
                "entities": [{"label": "ACC Limited", "aliases": ["Acc"], "mode": "smart", "confidence": "high"}],
                "excluded_phrases": ["AIR CARGO COMPLEX"],
            }
            default_path.write_text(json.dumps(baseline), encoding="utf-8")
            config_path.write_text(json.dumps(baseline), encoding="utf-8")

            res_add = apply_keyword_action(config_path, default_path, "Add to defaults", "Ultratech", "Sahar")
            self.assertTrue(res_add["changed"])
            self.assertEqual([r.label for r in res_add["after_rules"]], ["ACC Limited", "Ultratech"])
            self.assertIn("Sahar", res_add["after_exclusions"])

            res_rem = apply_keyword_action(config_path, default_path, "Remove from defaults", "Ultratech", "Sahar")
            self.assertEqual([r.label for r in res_rem["after_rules"]], ["ACC Limited"])
            self.assertNotIn("Sahar", res_rem["after_exclusions"])

            res_rep = apply_keyword_action(config_path, default_path, "Replace all defaults", "Dalmia")
            self.assertEqual([r.label for r in res_rep["after_rules"]], ["Dalmia"])

            res_rst = apply_keyword_action(config_path, default_path, "Reset to factory defaults")
            self.assertEqual([r.label for r in res_rst["after_rules"]], ["ACC Limited"])
            self.assertEqual(res_rst["after_exclusions"], ["AIR CARGO COMPLEX"])

    def test_filter_benches(self) -> None:
        benches = [Bench("1", "Delhi"), Bench("2", "Mumbai"), Bench("3", "Chennai")]
        filtered = _filter_benches(benches, "delhi, chennai")
        self.assertEqual([b.name for b in filtered], ["Delhi", "Chennai"])

    def test_summary_markdown_includes_suppressions(self) -> None:
        payload = {
            "start_date": "01-09-2026",
            "end_date": "07-09-2026",
            "generated_at": "2026-09-01T09:00:00+05:30",
            "keywords": ["ACC Limited"],
            "excluded_phrases": ["AIR CARGO COMPLEX"],
            "suppressions": {"ACC Limited (near AIR CARGO COMPLEX)": 4},
            "results": [],
        }
        md = generate_summary_markdown(payload)
        self.assertIn("Suppressed noisy hits:** 4", md)
        self.assertIn("ACC Limited (near AIR CARGO COMPLEX): 4", md)


if __name__ == "__main__":
    unittest.main()
