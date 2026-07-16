"""Tests for workspace/ignore.py: gitignore patterns, extra patterns,
binary/oversized/symlink exclusion, and the hardcoded .env exclusion
(which must hold even with no .gitignore entry for it at all).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from workspace.ignore import IgnoreRules


class TestIgnoreRules(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, rel_path: str, content: str = "") -> Path:
        full = self.tmp_dir / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return full

    def test_gitignore_pattern_excludes_matching_file(self):
        self._write(".gitignore", "*.log\n")
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip("debug.log", 1))

    def test_gitignore_does_not_exclude_unmatched_file(self):
        self._write(".gitignore", "*.log\n")
        rules = IgnoreRules(self.tmp_dir)
        self.assertFalse(rules.should_skip("main.py", 1))

    def test_extra_pattern_excludes_matching_file(self):
        rules = IgnoreRules(self.tmp_dir, extra_patterns=["*.pb.go"])
        self.assertTrue(rules.should_skip("generated.pb.go", 1))

    def test_binary_extension_excluded(self):
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip("image.png", 1))

    def test_oversized_file_excluded(self):
        rules = IgnoreRules(self.tmp_dir, max_file_size=100)
        self.assertTrue(rules.should_skip("big.txt", 101))
        self.assertFalse(rules.should_skip("small.txt", 99))

    def test_symlink_file_excluded(self):
        target = self._write("real.txt", "hi")
        link = self.tmp_dir / "link.txt"
        os.symlink(target, link)
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip("link.txt", 2))

    def test_symlink_dir_excluded(self):
        real_dir = self.tmp_dir / "real_dir"
        real_dir.mkdir()
        link_dir = self.tmp_dir / "link_dir"
        os.symlink(real_dir, link_dir, target_is_directory=True)
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip_dir("link_dir"))

    def test_env_file_excluded_with_no_gitignore_entry_for_it(self):
        # No .gitignore at all - the .env exclusion must not depend on it.
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip(".env", 10))

    def test_env_variant_excluded(self):
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip(".env.production", 10))

    def test_env_excluded_even_if_gitignore_would_otherwise_allow_it(self):
        # A gitignore that (mistakenly, or via a broad "!" negation)
        # doesn't exclude .env must not matter - is_secret() is hardcoded,
        # not merged into the gitignore-derived pathspec.
        self._write(".gitignore", "!.env\n")
        rules = IgnoreRules(self.tmp_dir)
        self.assertTrue(rules.should_skip(".env", 10))

    def test_normal_text_file_not_excluded(self):
        rules = IgnoreRules(self.tmp_dir)
        self.assertFalse(rules.should_skip("main.py", 11))

    def test_normal_dir_not_excluded(self):
        rules = IgnoreRules(self.tmp_dir)
        self.assertFalse(rules.should_skip_dir("src"))


if __name__ == "__main__":
    unittest.main()
