# -*- coding: utf-8 -*-

"""有限通配符、严格白名单及持久化契约测试。"""

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import ConfigSaveManager, load_user_config
from core.constants import DEFAULT_CONFIG
from core.text_search import (
    DEFAULT_BOUNDARY_CODEPOINTS, WHITE_SPACE_CODEPOINTS,
    compose_search_replacement, find_text_matches,
    validate_boundary_codepoints,
)


class TextSearchTests(unittest.TestCase):
    def pieces(self, source, query, **options):
        result = find_text_matches(source, query, **options)
        self.assertEqual(result.issue, "")
        return [source[start:end] for start, end in result.ranges]

    def test_unicode_case_and_case_sensitive(self):
        source = "Test test TEST É é"
        self.assertEqual(self.pieces(source, "Test"), ["Test", "test", "TEST"])
        self.assertEqual(
            self.pieces(source, "Test", case_sensitive=True), ["Test"],
        )
        self.assertEqual(self.pieces(source, "é"), ["É", "é"])

    def test_strict_boundaries(self):
        accepted = ("(test)", "test-value", "test.js", "test")
        rejected = ("my_test", "test＿value", "test😀", "test\u200b", "test\u0301")
        for source in accepted:
            with self.subTest(source=source):
                self.assertEqual(self.pieces(source, "test", whole_word=True), ["test"])
        for source in rejected:
            with self.subTest(source=source):
                self.assertEqual(self.pieces(source, "test", whole_word=True), [])

    def test_entire_keyword_boundaries(self):
        for source, query, expected in (
            ("(hello world)", "hello world", ["hello world"]),
            ("(C++)", "C++", ["C++"]),
            ("C++abc", "C++", []),
            ("x(test)y", "(test)", []),
            ("[(test)]", "(test)", ["(test)"]),
            ("这是中文内容", "中文", []),
            ("中文，说明", "中文", ["中文"]),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    self.pieces(source, query, whole_word=True), expected,
                )

    def test_defaults_are_exact_and_immutable(self):
        self.assertIsInstance(DEFAULT_BOUNDARY_CODEPOINTS, tuple)
        self.assertTrue(set(WHITE_SPACE_CODEPOINTS).issubset(DEFAULT_BOUNDARY_CODEPOINTS))
        for character in "_＿😀\u200b\u200d\ufeff\u0301":
            self.assertNotIn(ord(character), DEFAULT_BOUNDARY_CODEPOINTS)

    def test_custom_boundary_and_empty_whitelist(self):
        self.assertEqual(
            self.pieces(
                "x_test", "test", whole_word=True,
                boundary_codepoints=(ord("_"),),
            ),
            ["test"],
        )
        self.assertEqual(
            self.pieces("(test)", "test", whole_word=True, boundary_codepoints=()),
            [],
        )
        self.assertEqual(
            self.pieces("test", "test", whole_word=True, boundary_codepoints=()),
            ["test"],
        )

    def test_wildcards_and_literal_replacement(self):
        source = "file1.py file😀.py"
        self.assertEqual(
            self.pieces(source, "file?.py", wildcard=True),
            ["file1.py", "file😀.py"],
        )
        result = find_text_matches(source, "file?.py", wildcard=True)
        self.assertEqual(
            compose_search_replacement(source, result.ranges, r"new*.py $1 \1"),
            r"new*.py $1 \1 new*.py $1 \1",
        )

    def test_three_escapes_unknown_and_trailing_backslash(self):
        for source, query in (
            ("*", r"\*"),
            ("?", r"\?"),
            ("\\", r"\\"),
            (r"\d", r"\d"),
            ("tail\\", "tail\\"),
            (r"\n", r"\n"),
            (r"\t", r"\t"),
        ):
            with self.subTest(query=query):
                self.assertEqual(self.pieces(source, query, wildcard=True), [source])
        self.assertEqual(self.pieces(r"\*", r"\*"), [r"\*"])
        self.assertEqual(self.pieces("abc", r"\d", wildcard=True), [])

    def test_regex_symbols_are_literal(self):
        source = ".+()[]{}^$|"
        self.assertEqual(self.pieces(source, source, wildcard=True), [source])

    def test_greedy_and_boundary_backtracking(self):
        source = "a1b a2bx"
        self.assertEqual(self.pieces(source, "a*b", wildcard=True), ["a1b a2b"])
        self.assertEqual(
            self.pieces(source, "a*b", wildcard=True, whole_word=True), ["a1b"],
        )
        self.assertEqual(self.pieces("abc", "a***c", wildcard=True), ["abc"])
        self.assertEqual(
            self.pieces("foo bar!", "foo*", wildcard=True, whole_word=True),
            ["foo bar!"],
        )

    def test_wildcards_cross_non_line_characters(self):
        source = "a \t\u3000\u00a0/\\😀,b"
        self.assertEqual(self.pieces(source, "a*b", wildcard=True), [source])
        for separator in "\r\n\x85\u2028\u2029":
            with self.subTest(separator=separator):
                self.assertEqual(
                    self.pieces("a" + separator + "b", "a*b", wildcard=True), [],
                )
                self.assertEqual(
                    self.pieces("a" + separator + "b", "a?b", wildcard=True), [],
                )
        for separator in "\v\f":
            self.assertEqual(
                self.pieces("a" + separator + "b", "a?b", wildcard=True),
                ["a" + separator + "b"],
            )

    def test_unicode_codepoints_and_nonoverlap(self):
        source = "😀é aaaa"
        self.assertEqual(
            find_text_matches(source, "?", wildcard=True).ranges,
            tuple((index, index + 1) for index in range(len(source))),
        )
        self.assertEqual(find_text_matches("aaaa", "aa").ranges, ((0, 2), (2, 4)))
        self.assertEqual(self.pieces("😀", "??", wildcard=True), [])

    def test_empty_star_queries_and_zero_length_guard(self):
        self.assertEqual(find_text_matches("abc", "").ranges, ())
        for query in ("*", "**", "***"):
            result = find_text_matches("abc", query, wildcard=True)
            self.assertTrue(result.issue)
            self.assertEqual(result.ranges, ())
        self.assertEqual(self.pieces("*", r"\*", wildcard=True), ["*"])
        self.assertEqual(self.pieces("abc", "?", wildcard=True), ["a", "b", "c"])
        with self.assertRaises(ValueError):
            compose_search_replacement("abc", ((1, 1),), "x")

    def test_invalid_codepoints(self):
        for values in ([True], [-1], [0x110000], [0xD800], [32, 32], [33, 32]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_boundary_codepoints(values)

    def test_custom_boundary_is_exact_even_when_query_ignores_case(self):
        boundaries = (ord("X"),)
        for source, expected in (
            ("XtestX", ["test"]),
            ("XTESTX", ["TEST"]),
            ("xtestX", []),
            ("Xtestx", []),
            ("xtestx", []),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    self.pieces(
                        source, "test", whole_word=True,
                        boundary_codepoints=boundaries,
                    ),
                    expected,
                )
        self.assertEqual(
            self.pieces(
                "XTESTX", "test", whole_word=True, case_sensitive=True,
                boundary_codepoints=boundaries,
            ),
            [],
        )
        self.assertEqual(
            self.pieces(
                "Xa1bX", "a*b", whole_word=True, wildcard=True,
                boundary_codepoints=boundaries,
            ),
            ["a1b"],
        )
        self.assertEqual(
            self.pieces(
                "Xa1bx", "a*b", whole_word=True, wildcard=True,
                boundary_codepoints=boundaries,
            ),
            [],
        )

    def test_save_reload_and_default_recovery(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = deepcopy(DEFAULT_CONFIG)
            manager = ConfigSaveManager(config, path=path, needs_write=True)
            selected = [ord("_")]
            manager.accept(config["search"], {"boundary_codepoints": selected})
            self.assertTrue(manager.save().persisted)
            loaded, changed = load_user_config(path)
            self.assertFalse(changed)
            self.assertEqual(loaded["search"]["boundary_codepoints"], selected)
            manager.accept(
                config["search"],
                {"boundary_codepoints": list(DEFAULT_BOUNDARY_CODEPOINTS)},
            )
            self.assertTrue(manager.save().persisted)
            loaded, _ = load_user_config(path)
            self.assertEqual(
                loaded["search"]["boundary_codepoints"],
                list(DEFAULT_BOUNDARY_CODEPOINTS),
            )


if __name__ == "__main__":
    unittest.main()