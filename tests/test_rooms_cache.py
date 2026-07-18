"""Unit tests for service/rooms.py's change-fraction helpers
(_index_diff/_change_fraction) — the boundary logic that decides whether
a cached ProjectSynthesis is still trusted silently or a resync should
be suggested (Room._collect_and_start(), RESYNC_CHANGE_THRESHOLD).
"""

import unittest

from models.file_metadata import FileMetadata
from models.project_index import ProjectIndex
from service.rooms import (
    _aggregate_change_fraction,
    _aggregate_index_diff,
    _change_fraction,
    _index_diff,
)


def _index(files: dict[str, str]) -> ProjectIndex:
    """A minimal ProjectIndex where each value is that file's sha256
    stand-in — only content-equality matters for these helpers."""
    return ProjectIndex(
        project_name="p",
        project_root="/tmp/p",
        last_sync="t",
        files={
            path: FileMetadata(
                path=path,
                size=1,
                mtime=1.0,
                sha256=sha,
                language=None,
                binary=False,
            )
            for path, sha in files.items()
        },
    )


class TestIndexDiff(unittest.TestCase):
    def test_identical_indexes_have_zero_changed(self):
        old = _index({"a.py": "1", "b.py": "2"})
        new = _index({"a.py": "1", "b.py": "2"})
        self.assertEqual(_index_diff(old, new), (0, 2))

    def test_content_changed_file_counts_as_changed(self):
        old = _index({"a.py": "1"})
        new = _index({"a.py": "2"})
        self.assertEqual(_index_diff(old, new), (1, 1))

    def test_new_file_counts_as_changed(self):
        old = _index({"a.py": "1"})
        new = _index({"a.py": "1", "b.py": "2"})
        self.assertEqual(_index_diff(old, new), (1, 2))

    def test_removed_file_counts_as_changed(self):
        old = _index({"a.py": "1", "b.py": "2"})
        new = _index({"a.py": "1"})
        self.assertEqual(_index_diff(old, new), (1, 2))

    def test_no_baseline_treats_every_new_file_as_changed(self):
        new = _index({"a.py": "1", "b.py": "2"})
        self.assertEqual(_index_diff(None, new), (2, 2))

    def test_empty_indexes_report_zero_over_a_floor_of_one(self):
        old = _index({})
        new = _index({})
        self.assertEqual(_index_diff(old, new), (0, 1))


class TestChangeFraction(unittest.TestCase):
    def test_no_baseline_is_fully_changed(self):
        new = _index({"a.py": "1"})
        self.assertEqual(_change_fraction(None, new), 1.0)

    def test_baseline_with_no_files_is_fully_changed(self):
        old = _index({})
        new = _index({"a.py": "1"})
        self.assertEqual(_change_fraction(old, new), 1.0)

    def test_nothing_changed_is_zero(self):
        old = _index({"a.py": "1", "b.py": "2"})
        new = _index({"a.py": "1", "b.py": "2"})
        self.assertEqual(_change_fraction(old, new), 0.0)

    def test_exactly_one_of_five_is_below_the_default_threshold(self):
        # RESYNC_CHANGE_THRESHOLD is 0.2; 1/5 == 0.2 is NOT below it —
        # the boundary is exclusive on the "still trust the cache" side
        # (Room._collect_and_start() uses `fraction < RESYNC_CHANGE_THRESHOLD`).
        old = _index({f"f{i}.py": str(i) for i in range(5)})
        new = _index({**{f"f{i}.py": str(i) for i in range(4)}, "f4.py": "changed"})
        self.assertAlmostEqual(_change_fraction(old, new), 0.2)

    def test_well_below_threshold(self):
        old = _index({f"f{i}.py": str(i) for i in range(10)})
        new = _index({**{f"f{i}.py": str(i) for i in range(9)}, "f9.py": "changed"})
        self.assertAlmostEqual(_change_fraction(old, new), 0.1)

    def test_well_above_threshold(self):
        old = _index({f"f{i}.py": str(i) for i in range(5)})
        new = _index({f"f{i}.py": f"changed{i}" for i in range(5)})
        self.assertAlmostEqual(_change_fraction(old, new), 1.0)


class TestAggregateIndexDiff(unittest.TestCase):
    def test_sums_across_every_attached_project(self):
        results = {
            "project": (_index({"a.py": "1"}), _index({"a.py": "2"})),
            "backend": (
                _index({"b.py": "1", "c.py": "2"}),
                _index({"b.py": "1", "c.py": "2"}),
            ),
        }
        # project: 1 changed of 1; backend: 0 changed of 2.
        self.assertEqual(_aggregate_index_diff(results), (1, 3))

    def test_single_project_matches_index_diff_exactly(self):
        old = _index({"a.py": "1", "b.py": "2"})
        new = _index({"a.py": "1", "b.py": "changed"})
        results = {"project": (old, new)}
        self.assertEqual(_aggregate_index_diff(results), _index_diff(old, new))


class TestAggregateChangeFraction(unittest.TestCase):
    def test_any_project_with_no_baseline_forces_fully_changed(self):
        results = {
            "project": (_index({"a.py": "1"}), _index({"a.py": "1"})),
            "backend": (None, _index({"b.py": "1"})),
        }
        self.assertEqual(_aggregate_change_fraction(results), 1.0)

    def test_nothing_changed_across_any_project_is_zero(self):
        results = {
            "project": (_index({"a.py": "1"}), _index({"a.py": "1"})),
            "backend": (_index({"b.py": "1"}), _index({"b.py": "1"})),
        }
        self.assertEqual(_aggregate_change_fraction(results), 0.0)

    def test_partial_change_averages_across_every_project(self):
        results = {
            "project": (
                _index({f"f{i}.py": str(i) for i in range(5)}),
                _index(
                    {
                        **{f"f{i}.py": str(i) for i in range(4)},
                        "f4.py": "changed",
                    }
                ),
            ),
            "backend": (
                _index({f"g{i}.py": str(i) for i in range(5)}),
                _index({f"g{i}.py": str(i) for i in range(5)}),
            ),
        }
        # 1 changed of 10 total across both projects.
        self.assertAlmostEqual(_aggregate_change_fraction(results), 0.1)

    def test_single_project_matches_change_fraction_exactly(self):
        old = _index({f"f{i}.py": str(i) for i in range(5)})
        new = _index({f"f{i}.py": f"changed{i}" for i in range(5)})
        results = {"project": (old, new)}
        self.assertEqual(
            _aggregate_change_fraction(results), _change_fraction(old, new)
        )


if __name__ == "__main__":
    unittest.main()
