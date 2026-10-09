# -*- coding: utf-8 -*-

import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from core.exclusion_rules import (
    ExclusionValidationError, analyze_exclusion_list,
    format_exclusion_diagnostics, prepare_exclusions,
)


class ExclusionRuleTests(unittest.TestCase):
    def assert_match(self, pattern, path, expected=True, kind="file"):
        result = analyze_exclusion_list(pattern, kind)
        self.assertFalse(result.has_errors, result.diagnostics)
        self.assertEqual(result.matches(tuple(path.split("\\"))), expected)

    def test_only_lf_and_crlf_split_rules(self):
        text = "my notes.txt\nreport,final.txt\n;draft.txt\n[a].txt\n{x}.txt\n`a`.txt"
        first = analyze_exclusion_list(text, "file")
        second = analyze_exclusion_list(text.replace("\n", "\r\n"), "file")
        self.assertEqual(first.normalized_text, text)
        self.assertEqual(first.normalized_text, second.normalized_text)
        self.assertEqual(len(first.rules), 6)
        for name in text.split("\n"):
            self.assertTrue(first.matches((name,)))
        self.assertFalse(first.matches(("a.txt",)))
        for control in ("\r", "\t", "\v", "\f", "\x00", "\x1f"):
            with self.subTest(control=repr(control)):
                self.assertTrue(
                    analyze_exclusion_list("a" + control + "b", "file").has_errors,
                )
        self.assertFalse(
            analyze_exclusion_list("a\u2028b", "file").has_errors,
        )

    def test_quotes_empty_lines_and_mapping(self):
        result = analyze_exclusion_list('\n""\n"cache"\n"   "\n<none>\n', "file")
        self.assertEqual(result.normalized_text, "cache\n   \n<none>")
        self.assertEqual(result.line_map, (3, 4, 5))
        self.assertEqual(
            [(item.input_line, item.display_line) for item in result.diagnostics],
            [(4, 2), (5, 3)],
        )
        direct = format_exclusion_diagnostics((("文件名单", result),), True)
        displayed = format_exclusion_diagnostics((("文件名单", result),))
        self.assertIn("第 4 行", direct)
        self.assertIn("第 2 行", displayed)

    def test_quotes_are_deleted_before_single_classification(self):
        examples = {
            '"src\\cache"': "src\\cache",
            'src\\"cache': "src\\cache",
            '"""src\\cache"': "src\\cache",
            '":.git*"': ":.git*",
            ':".git*"': ":.git*",
            '"\\src/cache"': "\\src\\cache",
            '":"': ":",
            ':""': ":",
            '"|:.git*"': "|:.git*",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                result = analyze_exclusion_list(raw, "file")
                self.assertFalse(result.has_errors)
                self.assertEqual(result.normalized_text, expected)
        for name in ("'a'.txt", "`a`.txt", "“a”.txt"):
            self.assert_match(name, name)
        self.assertTrue(
            analyze_exclusion_list('"C:\\Project\\src"', "directory").has_errors,
        )

    def test_disabled_body_is_not_interpreted(self):
        text = '|src/cache\n"|<none>:bad//??"\n|   \n|'
        result = analyze_exclusion_list(text, "file")
        self.assertEqual(
            result.normalized_text, "|src/cache\n|<none>:bad//??\n|   \n|",
        )
        self.assertFalse(result.has_errors)
        self.assertFalse(result.has_warnings)
        self.assertEqual(result.rules, ())
        self.assertTrue(all(item.state == "disabled" for item in result.diagnostics))
        restored = analyze_exclusion_list("src/cache", "file")
        self.assertEqual(restored.normalized_text, "src\\cache")
        for invalid in (":|README", " |cache", "::README", "README:part"):
            self.assertTrue(analyze_exclusion_list(invalid, "file").has_errors)
        self.assert_match(";draft.txt", ";draft.txt")
        self.assert_match(";draft.txt", "draft.txt", False)

    def test_whitespace_is_preserved_and_risk_has_error_priority(self):
        for text in ("   ", '"   "', ":   ", "\\   "):
            with self.subTest(text=text):
                self.assertTrue(analyze_exclusion_list(text, "file").has_errors)
        for text in (" README*", ": README*", "\\ README*", "README ", "* .txt", "   .txt"):
            with self.subTest(text=text):
                result = analyze_exclusion_list(text, "file")
                self.assertFalse(result.has_errors)
                self.assertEqual(result.normalized_text, text)
                self.assertEqual(result.has_warnings, text != "* .txt")
        result = analyze_exclusion_list("report .txt", "file")
        self.assertFalse(result.has_warnings)
        self.assert_match(" my notes.txt ", " my notes.txt ")
        self.assert_match(" my notes.txt ", "my notes.txt", False)
        result = analyze_exclusion_list(" bad|name ", "file")
        self.assertTrue(result.has_errors)
        self.assertFalse(result.has_warnings)
        self.assertTrue(
            analyze_exclusion_list("folder \\file.txt", "file").has_errors,
        )

    def test_no_extension_definition(self):
        expected = {
            "README": True, "LICENSE": True, ".gitignore": True, ".env": True,
            "main.py": False, "archive.tar.gz": False, ".config.json": False,
        }
        for name, no_extension in expected.items():
            with self.subTest(name=name):
                self.assertEqual(PureWindowsPath(name).suffix == "", no_extension)
                self.assertEqual(Path(name).suffix == "", no_extension)
                self.assert_match(":", name, no_extension)
                self.assert_match(":*", name, no_extension)

    def test_no_extension_and_pattern_are_conjoined(self):
        for name in (".gitignore", ".gitattributes", ".gitconfig", ".git"):
            self.assert_match(":.git*", name)
        for name in (".git.txt", ".gitconfig.bak", "README"):
            self.assert_match(":.git*", name, False)
        self.assert_match(":\\README*", "README")
        self.assert_match(":\\README*", "src\\README", False)
        self.assert_match(":README*", "src\\README")
        self.assertTrue(analyze_exclusion_list(":", "directory").has_errors)
        for invalid in (":\\", ":/", "<none>", "<none>:README"):
            self.assertTrue(analyze_exclusion_list(invalid, "file").has_errors)

    def test_directory_names_and_contiguous_paths(self):
        for path in ("cache", "a\\cache", "a\\b\\cache"):
            self.assert_match("cache", path, kind="directory")
            self.assert_match("**\\cache", path, kind="directory")
        for path in ("mycache", "cache_old"):
            self.assert_match("cache", path, False, "directory")
        for path in ("src\\cache", "a\\src\\cache", "a\\b\\src\\cache"):
            self.assert_match("src\\cache", path, kind="directory")
        self.assert_match("src\\cache", "src\\other\\cache", False, "directory")
        self.assert_match("\\src\\cache", "src\\cache", kind="directory")
        self.assert_match("\\src\\cache", "a\\src\\cache", False, "directory")
        self.assert_match("\\cache", "cache", kind="directory")
        self.assert_match("\\cache", "a\\cache", False, "directory")

    def test_file_paths_and_root_constraint(self):
        self.assert_match("src\\README", "src\\README")
        self.assert_match("src\\README", "a\\src\\README")
        self.assert_match("src\\README", "src\\README.txt", False)
        self.assert_match("src\\README", "src\\sub\\README", False)
        self.assert_match("src\\**\\README", "src\\sub\\README")
        self.assert_match("\\src\\README", "src\\README")
        self.assert_match("\\src\\README", "a\\src\\README", False)

    def test_stars_questions_and_literal_characters(self):
        self.assert_match("test_*.py", "test_.py")
        self.assert_match("test_*.py", "test_a.py")
        self.assert_match("\\test_*.py", "test_sub\\a.py", False)
        self.assert_match("part?.txt", "part1.txt")
        self.assert_match("part?.txt", "part.txt", False)
        self.assert_match("part?.txt", "part10.txt", False)
        self.assert_match("[ab].txt", "[ab].txt")
        self.assert_match("[ab].txt", "a.txt", False)
        for name in ("{a,b}.txt", "!file.txt", "#file.txt", "`file`.txt"):
            self.assert_match(name, name)
        self.assert_match("*", ".env")
        self.assert_match("*", "README")

    def test_double_star_zero_multiple_and_trailing_segments(self):
        for path in ("src\\a.py", "src\\lib\\a.py", "src\\lib\\deep\\a.py"):
            self.assert_match("\\src\\**\\*.py", path)
        self.assert_match("\\src\\**\\*.py", "a\\src\\lib\\a.py", False)
        self.assert_match("src\\**\\*.py", "a\\src\\lib\\a.py")
        for path in ("a.txt", "a\\b.txt", "a\\b\\c.txt"):
            self.assert_match("**", path)
        for path in ("tools\\a.txt", "tools\\sub\\a.txt", "a\\tools\\sub\\a.txt"):
            self.assert_match("tools\\**", path)
        self.assert_match("tools\\**", "tools", False)
        self.assert_match("\\tools\\**", "tools\\sub\\a.txt")
        self.assert_match("\\tools\\**", "a\\tools\\a.txt", False)
        for path in ("tools", "tools\\sub", "a\\tools"):
            self.assert_match("tools\\**", path, kind="directory")
        self.assert_match("**", "a", kind="directory")
        result = analyze_exclusion_list("**", "directory")
        self.assertFalse(result.matches(()))
        for invalid in ("a**b", "**.py", "***", "src\\**name\\a.py"):
            with self.subTest(invalid=invalid):
                self.assertTrue(analyze_exclusion_list(invalid, "file").has_errors)

    def test_separators_trailing_cleanup_and_case(self):
        examples = {
            "src/cache/": "src\\cache",
            "src\\cache\\": "src\\cache",
            "src\\cache\\\\\\": "src\\cache",
            "\\src\\cache\\": "\\src\\cache",
            "/src/cache/": "\\src\\cache",
            "src/cache\\sub/": "src\\cache\\sub",
            ":/README*/": ":\\README*",
        }
        for raw, normalized in examples.items():
            with self.subTest(raw=raw):
                result = analyze_exclusion_list(raw, "file")
                self.assertFalse(result.has_errors)
                self.assertEqual(result.normalized_text, normalized)
                repeated = analyze_exclusion_list(result.normalized_text, "file")
                self.assertEqual(repeated.normalized_text, normalized)
        self.assert_match("SRC\\README", "a\\src\\readme")
        self.assertEqual(
            analyze_exclusion_list("SRC/README", "file").normalized_text,
            "SRC\\README",
        )

    def test_illegal_path_structure_is_not_cleaned_to_valid_rule(self):
        invalid = (
            "C:\\src\\cache",
            "\\\\server\\share",
            "\\\\src\\cache",
            "//src/cache",
            "/\\src/cache",
            "\\/",
            "src\\\\cache",
            "src//cache",
            "src/\\cache",
            "src\\/cache",
            "src\\..\\cache",
            "src\\.\\cache",
            ".",
            "..",
            "\\",
            "/",
            ":\\",
            ":/",
        )
        for text in invalid:
            with self.subTest(text=text):
                result = analyze_exclusion_list(text, "file")
                self.assertTrue(result.has_errors)
                self.assertEqual(result.normalized_text, text)
                self.assertEqual(result.rules, ())

    def test_invalid_characters_and_name_boundaries(self):
        invalid = (
            "<none>", "a<b", "a>b", "a:b", "a|b",
            "file.", "file. ", "folder.\\file.txt",
            "folder \\file.txt",
        )
        for text in invalid:
            with self.subTest(text=text):
                result = analyze_exclusion_list(text, "file")
                self.assertTrue(result.has_errors)
                self.assertEqual(result.normalized_text, text)
        for codepoint in range(32):
            if codepoint == 10:
                continue
            text = "a" + chr(codepoint) + "b"
            with self.subTest(codepoint=codepoint):
                self.assertTrue(analyze_exclusion_list(text, "file").has_errors)
        self.assertFalse(analyze_exclusion_list("missing_file.txt", "file").has_errors)

    def test_reserved_device_names_and_wildcard_boundary(self):
        devices = (
            "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
            "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
        )
        for device in devices:
            for component in (device, device.lower() + ".txt", device + " .txt"):
                with self.subTest(component=component):
                    self.assertTrue(
                        analyze_exclusion_list(component, "file").has_errors,
                    )
                    self.assertTrue(
                        analyze_exclusion_list(
                            component + "\\file.txt", "file",
                        ).has_errors,
                    )
        for allowed in ("COM0", "COM10", "LPT0", "LPT10", "CONSOLE", "CON*", "COM?.txt"):
            with self.subTest(allowed=allowed):
                self.assertFalse(analyze_exclusion_list(allowed, "file").has_errors)

    def test_duplicate_order_and_disabled_text_are_preserved(self):
        text = "cache\ncache\n|src/cache\nREADME"
        result = analyze_exclusion_list(text, "file")
        self.assertEqual(result.normalized_text, text)
        self.assertEqual(len(result.rules), 3)
        self.assertEqual(result.line_map, (1, 2, 3, 4))

    def test_compilation_occurs_during_preparation_not_matching(self):
        import core.exclusion_rules as module
        original = module._compile_segment
        with patch.object(module, "_compile_segment", wraps=original) as compile_segment:
            prepared = prepare_exclusions("src\\cache", "*.log\n:README*")
            count = compile_segment.call_count
            self.assertEqual(count, 4)
            for _ in range(20):
                prepared.directories.matches(("a", "src", "cache"))
                prepared.files.matches(("a", "test.log"))
                prepared.files.matches(("README",))
            self.assertEqual(compile_segment.call_count, count)

    def test_invalid_configuration_cannot_be_prepared(self):
        with self.assertRaises(ExclusionValidationError) as caught:
            prepare_exclusions("\n\nbad|directory", "*.log")
        self.assertIn("目录名单，第 3 行", str(caught.exception))


if __name__ == "__main__":
    unittest.main()