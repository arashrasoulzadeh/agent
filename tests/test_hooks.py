"""Tests for hooks/: registration (hooks/registry.py), dispatch
(hooks/dispatch.py), and extra/ discovery (hooks/loader.py) — against a
temporary extra/ directory, so this never touches the real extra/.
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from hooks import dispatch, loader
from hooks.registry import HOOKS, hook


class TestHookRegistrationAndDispatch(unittest.TestCase):
    def setUp(self):
        self._original_hooks = copy.deepcopy(HOOKS)
        HOOKS.clear()

    def tearDown(self):
        HOOKS.clear()
        HOOKS.update(self._original_hooks)

    def test_hook_decorator_registers_under_the_given_name(self):
        @hook("before_prompt")
        def rewrite(text):
            return text.upper()

        self.assertIn(rewrite, HOOKS["before_prompt"])

    def test_filter_applies_a_single_hook(self):
        @hook("before_prompt")
        def rewrite(text):
            return text.upper()

        self.assertEqual(dispatch.filter("before_prompt", "hi"), "HI")

    def test_filter_threads_value_through_multiple_hooks_in_registration_order(self):
        @hook("before_prompt")
        def first(text):
            return text + "-a"

        @hook("before_prompt")
        def second(text):
            return text + "-b"

        self.assertEqual(dispatch.filter("before_prompt", "x"), "x-a-b")

    def test_filter_none_return_means_no_change(self):
        @hook("before_prompt")
        def noop(text):
            return None

        self.assertEqual(dispatch.filter("before_prompt", "unchanged"), "unchanged")

    def test_filter_with_no_registered_hooks_returns_value_unchanged(self):
        self.assertEqual(dispatch.filter("nobody_home", "value"), "value")
        # Reading a never-registered hook name must not create an entry —
        # HOOKS.get(name, ()), never HOOKS[name].
        self.assertNotIn("nobody_home", HOOKS)

    def test_a_raising_hook_is_isolated_and_does_not_stop_the_others(self):
        @hook("before_prompt")
        def first(text):
            return text + "-a"

        @hook("before_prompt")
        def broken(text):
            raise ValueError("boom")

        @hook("before_prompt")
        def third(text):
            return text + "-c"

        # broken()'s exception is swallowed; its "no change" is carried
        # forward to third(), which still runs.
        self.assertEqual(dispatch.filter("before_prompt", "x"), "x-a-c")

    def test_notify_calls_every_hook_and_discards_return_values(self):
        calls = []

        @hook("on_tool_call")
        def first(name, args):
            calls.append(("first", name, args))
            return "ignored"

        @hook("on_tool_call")
        def second(name, args):
            calls.append(("second", name, args))

        result = dispatch.notify("on_tool_call", "cat", "path='x'")
        self.assertIsNone(result)
        self.assertEqual(
            calls, [("first", "cat", "path='x'"), ("second", "cat", "path='x'")]
        )

    def test_notify_isolates_a_raising_hook(self):
        calls = []

        @hook("on_tool_call")
        def broken(name, args):
            raise ValueError("boom")

        @hook("on_tool_call")
        def second(name, args):
            calls.append("ran")

        dispatch.notify("on_tool_call", "cat", "path='x'")
        self.assertEqual(calls, ["ran"])


class TestExtraDiscovery(unittest.TestCase):
    def setUp(self):
        self._original_hooks = copy.deepcopy(HOOKS)
        HOOKS.clear()

        self.tmp_dir = Path(tempfile.mkdtemp())
        self.package_name = f"_test_extra_{id(self)}"
        package_dir = self.tmp_dir / self.package_name
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("")

        self._original_dir = loader.EXTRA_DIR
        self._original_package = loader.EXTRA_PACKAGE
        loader.EXTRA_DIR = package_dir
        loader.EXTRA_PACKAGE = self.package_name
        sys.path.insert(0, str(self.tmp_dir))
        self.package_dir = package_dir

    def tearDown(self):
        loader.EXTRA_DIR = self._original_dir
        loader.EXTRA_PACKAGE = self._original_package
        sys.path.remove(str(self.tmp_dir))
        for name in list(sys.modules):
            if name == self.package_name or name.startswith(f"{self.package_name}."):
                del sys.modules[name]
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        HOOKS.clear()
        HOOKS.update(self._original_hooks)

    def _write(self, filename: str, content: str) -> None:
        (self.package_dir / filename).write_text(content)

    def test_discover_imports_a_hook_file_and_registers_its_hook(self):
        self._write(
            "my_hook.py",
            """
from hooks import hook

@hook("before_prompt")
def shout(text):
    return text.upper()
""",
        )
        loader.discover()
        self.assertEqual(dispatch.filter("before_prompt", "hi"), "HI")

    def test_underscore_prefixed_files_are_skipped(self):
        self._write(
            "_ignored.py",
            """
from hooks import hook

@hook("before_prompt")
def should_not_register(text):
    return "SHOULD NOT APPEAR"
""",
        )
        loader.discover()
        self.assertEqual(dispatch.filter("before_prompt", "hi"), "hi")


if __name__ == "__main__":
    unittest.main()
