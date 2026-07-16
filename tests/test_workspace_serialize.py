"""Tests for workspace/serialize.py: project/subtree/glob filtering,
against hand-built manifest/index data — no real project directory or
watcher needed, just the on-disk manifest.json/index.json shape.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from models.file_metadata import FileMetadata
from models.project_index import ProjectIndex
from models.session_manifest import ProjectAttachment, SessionManifest
from workspace.index_repository import IndexRepository
from workspace.manager import ProjectNotFound, SessionNotFound
from workspace.manifest_repository import ManifestRepository
from workspace.serialize import (
    render_file_signatures,
    to_lightweight_context,
    to_prompt_context,
)


class _ProjectFixtureMixin:
    """Shared session/project/index fixture for both to_prompt_context()
    (tier 2, full signatures) and to_lightweight_context() (tier 1,
    description only) — same on-disk data, different render. Not itself
    a TestCase, so it's never collected/run on its own."""

    def setUp(self):
        self.session_root = Path(tempfile.mkdtemp())
        session_dir = self.session_root / "s1"

        manifest = SessionManifest(
            name="s1",
            created_at="2026-01-01T00:00:00+00:00",
            projects={
                "p1": ProjectAttachment(
                    name="p1",
                    root="/proj/one",
                    attached_at="2026-01-01T00:00:00+00:00",
                ),
                "p2": ProjectAttachment(
                    name="p2",
                    root="/proj/two",
                    attached_at="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        ManifestRepository(session_dir).save(manifest)

        index_p1 = ProjectIndex(
            project_name="p1",
            project_root="/proj/one",
            last_sync="2026-01-01T00:00:00+00:00",
            files={
                "src/foo.py": FileMetadata(
                    path="src/foo.py",
                    size=100,
                    mtime=0.0,
                    sha256="a" * 64,
                    language="python",
                    binary=False,
                    derived={
                        "summary": "does foo things",
                        "signatures": {
                            "functions": [
                                {
                                    "name": "do_foo",
                                    "async": False,
                                    "params": [
                                        {
                                            "name": "x",
                                            "annotation": "int",
                                            "default": None,
                                        }
                                    ],
                                    "returns": "str",
                                    "decorators": [],
                                    "summary": "Does the foo thing.",
                                }
                            ],
                            "classes": [],
                            "variables": [],
                        },
                    },
                ),
                "src/bar.py": FileMetadata(
                    path="src/bar.py",
                    size=200,
                    mtime=0.0,
                    sha256="b" * 64,
                    language="python",
                    binary=False,
                ),
                "README.md": FileMetadata(
                    path="README.md",
                    size=50,
                    mtime=0.0,
                    sha256="c" * 64,
                    language="markdown",
                    binary=False,
                ),
            },
        )
        IndexRepository(session_dir / "p1").save(index_p1)

        index_p2 = ProjectIndex(
            project_name="p2",
            project_root="/proj/two",
            last_sync="2026-01-01T00:00:00+00:00",
            files={
                "main.go": FileMetadata(
                    path="main.go",
                    size=300,
                    mtime=0.0,
                    sha256="d" * 64,
                    language="go",
                    binary=False,
                ),
            },
        )
        IndexRepository(session_dir / "p2").save(index_p2)

    def tearDown(self):
        shutil.rmtree(self.session_root, ignore_errors=True)


class TestToPromptContext(_ProjectFixtureMixin, unittest.TestCase):
    def test_includes_all_projects_by_default(self):
        text = to_prompt_context("s1", session_root=self.session_root)
        self.assertIn("p1", text)
        self.assertIn("p2", text)
        self.assertIn("src/foo.py", text)
        self.assertIn("main.go", text)

    def test_filters_by_project(self):
        text = to_prompt_context("s1", project="p1", session_root=self.session_root)
        self.assertIn("src/foo.py", text)
        self.assertNotIn("main.go", text)

    def test_unknown_project_raises(self):
        with self.assertRaises(ProjectNotFound):
            to_prompt_context("s1", project="nope", session_root=self.session_root)

    def test_unknown_session_raises(self):
        with self.assertRaises(SessionNotFound):
            to_prompt_context("nope", session_root=self.session_root)

    def test_filters_by_subtree(self):
        text = to_prompt_context(
            "s1", project="p1", subtree="src", session_root=self.session_root
        )
        self.assertIn("src/foo.py", text)
        self.assertIn("src/bar.py", text)
        self.assertNotIn("README.md", text)

    def test_filters_by_glob(self):
        text = to_prompt_context(
            "s1", project="p1", glob="*.md", session_root=self.session_root
        )
        self.assertIn("README.md", text)
        self.assertNotIn("src/foo.py", text)

    def test_derived_summary_shown_inline(self):
        text = to_prompt_context("s1", project="p1", session_root=self.session_root)
        self.assertIn("does foo things", text)

    def test_hash_never_shown(self):
        text = to_prompt_context("s1", project="p1", session_root=self.session_root)
        self.assertNotIn("a" * 64, text)

    def test_signatures_rendered_indented_under_their_file(self):
        text = to_prompt_context("s1", project="p1", session_root=self.session_root)
        self.assertIn("def do_foo(x: int) -> str", text)
        self.assertIn("Does the foo thing.", text)
        lines = text.splitlines()
        foo_line = next(i for i, line in enumerate(lines) if "src/foo.py" in line)
        sig_line = next(i for i, line in enumerate(lines) if "def do_foo" in line)
        self.assertGreater(sig_line, foo_line)
        self.assertTrue(lines[sig_line].startswith("    "))


class TestToLightweightContext(_ProjectFixtureMixin, unittest.TestCase):
    """Tier 1: same filtering as to_prompt_context(), but never includes
    a file's full signatures — only its path/language/size and one-line
    description."""

    def test_includes_path_and_description_not_signatures(self):
        text = to_lightweight_context(
            "s1", project="p1", session_root=self.session_root
        )
        self.assertIn("src/foo.py", text)
        self.assertIn("does foo things", text)
        self.assertNotIn("def do_foo", text)
        self.assertNotIn("Does the foo thing.", text)

    def test_filters_by_project_same_as_full_context(self):
        text = to_lightweight_context(
            "s1", project="p1", session_root=self.session_root
        )
        self.assertIn("src/foo.py", text)
        self.assertNotIn("main.go", text)

    def test_unknown_project_raises(self):
        with self.assertRaises(ProjectNotFound):
            to_lightweight_context(
                "s1", project="nope", session_root=self.session_root
            )

    def test_unknown_session_raises(self):
        with self.assertRaises(SessionNotFound):
            to_lightweight_context("nope", session_root=self.session_root)

    def test_hash_never_shown(self):
        text = to_lightweight_context(
            "s1", project="p1", session_root=self.session_root
        )
        self.assertNotIn("a" * 64, text)


class TestRenderFileSignatures(unittest.TestCase):
    def test_full_detail_for_file_with_signatures(self):
        meta = FileMetadata(
            path="src/foo.py",
            size=100,
            mtime=0.0,
            sha256="a" * 64,
            language="python",
            binary=False,
            derived={
                "summary": "does foo things",
                "signatures": {
                    "functions": [
                        {
                            "name": "do_foo",
                            "async": False,
                            "params": [],
                            "returns": "str",
                            "decorators": [],
                            "summary": "Does the foo thing.",
                        }
                    ],
                    "classes": [],
                    "variables": [],
                },
            },
        )
        text = render_file_signatures("src/foo.py", meta)
        self.assertIn("src/foo.py", text)
        self.assertIn("does foo things", text)
        self.assertIn("def do_foo() -> str", text)
        self.assertIn("Does the foo thing.", text)

    def test_fallback_line_for_file_without_signatures(self):
        meta = FileMetadata(
            path="README.md",
            size=50,
            mtime=0.0,
            sha256="c" * 64,
            language="markdown",
            binary=False,
        )
        text = render_file_signatures("README.md", meta)
        self.assertIn("README.md", text)
        self.assertIn("(no extracted functions, classes, or variables)", text)

    def test_fallback_line_for_docstring_only_file_with_empty_signatures(self):
        """A file with derived["signatures"] present but every category
        empty (e.g. an __init__.py with only a module docstring) must
        still show the fallback line, not silently end after the
        summary — the dict key existing isn't the same as there being
        anything to render."""
        meta = FileMetadata(
            path="__init__.py",
            size=40,
            mtime=0.0,
            sha256="e" * 64,
            language="python",
            binary=False,
            derived={
                "summary": "Package entrypoint.",
                "signatures": {"functions": [], "classes": [], "variables": []},
            },
        )
        text = render_file_signatures("__init__.py", meta)
        self.assertIn("Package entrypoint.", text)
        self.assertIn("(no extracted functions, classes, or variables)", text)


if __name__ == "__main__":
    unittest.main()
