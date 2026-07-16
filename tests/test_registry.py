"""Tests for tool/registry.py's discovery: tools, AGENT_TOOL
opt-out, and the Lifecycle contract (core/module.py) — against a
temporary tool/ directory, so this never touches the real tool/.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tool import registry


class TestModuleDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.package_name = f"_test_modules_{id(self)}"
        package_dir = self.tmp_dir / self.package_name
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("")

        self._original_dir = registry.TOOLS_DIR
        self._original_package = registry.TOOL_PACKAGE
        registry.TOOLS_DIR = package_dir
        registry.TOOL_PACKAGE = self.package_name
        sys.path.insert(0, str(self.tmp_dir))
        self.package_dir = package_dir

    def tearDown(self):
        registry.TOOLS_DIR = self._original_dir
        registry.TOOL_PACKAGE = self._original_package
        sys.path.remove(str(self.tmp_dir))
        for name in list(sys.modules):
            if name == self.package_name or name.startswith(f"{self.package_name}."):
                del sys.modules[name]
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, filename: str, content: str) -> None:
        (self.package_dir / filename).write_text(content)

    def test_discovers_a_plain_tool(self):
        self._write(
            "greet.py",
            '''
from langchain_core.tools import tool

@tool
def greet(name: str) -> str:
    """Say hello."""
    return f"hello {name}"
''',
        )
        result = registry.discover()
        self.assertIn("greet", result.all_tools)
        self.assertIn(result.all_tools["greet"], result.agent_tools)

    def test_agent_tool_false_excludes_from_agent_tools_but_not_all_tools(self):
        self._write(
            "hidden.py",
            '''
from langchain_core.tools import tool

AGENT_TOOL = False

@tool
def hidden_tool(x: str) -> str:
    """Hidden."""
    return x
''',
        )
        result = registry.discover()
        self.assertIn("hidden_tool", result.all_tools)
        self.assertNotIn(result.all_tools["hidden_tool"], result.agent_tools)

    def test_underscore_prefixed_files_are_skipped(self):
        self._write(
            "_internal.py",
            '''
from langchain_core.tools import tool

@tool
def should_not_appear(x: str) -> str:
    """Should not appear."""
    return x
''',
        )
        result = registry.discover()
        self.assertNotIn("should_not_appear", result.all_tools)

    def test_lifecycle_module_with_all_three_hooks_is_discovered(self):
        self._write(
            "with_lifecycle.py",
            '''
class _Mod:
    def init(self, config):
        pass
    def start(self):
        pass
    def stop(self):
        pass

MODULE = _Mod()
''',
        )
        result = registry.discover()
        self.assertEqual(len(result.lifecycle_modules), 1)

    def test_lifecycle_module_with_only_one_hook_is_still_discovered(self):
        # The whole point of hasattr-based discovery: a module doesn't
        # need to implement init/start/stop, just whichever it needs.
        self._write(
            "stop_only.py",
            '''
class _Mod:
    def stop(self):
        pass

MODULE = _Mod()
''',
        )
        result = registry.discover()
        self.assertEqual(len(result.lifecycle_modules), 1)
        self.assertTrue(hasattr(result.lifecycle_modules[0], "stop"))
        self.assertFalse(hasattr(result.lifecycle_modules[0], "init"))

    def test_module_without_a_module_object_contributes_no_lifecycle_hook(self):
        self._write(
            "plain.py",
            '''
from langchain_core.tools import tool

@tool
def plain_tool(x: str) -> str:
    """Plain."""
    return x
''',
        )
        result = registry.discover()
        self.assertEqual(result.lifecycle_modules, [])

    def test_module_import_only_happens_once_for_both_tools_and_lifecycle(self):
        # A single module can offer both a tool and a lifecycle object;
        # discover() must not import it twice or miss either half.
        self._write(
            "both.py",
            '''
from langchain_core.tools import tool

@tool
def combo_tool(x: str) -> str:
    """Combo."""
    return x

class _Mod:
    def start(self):
        pass

MODULE = _Mod()
''',
        )
        result = registry.discover()
        self.assertIn("combo_tool", result.all_tools)
        self.assertEqual(len(result.lifecycle_modules), 1)

    def test_discover_tools_back_compat_wrapper(self):
        self._write(
            "greet2.py",
            '''
from langchain_core.tools import tool

@tool
def greet2(name: str) -> str:
    """Say hello."""
    return f"hello {name}"
''',
        )
        all_tools, agent_tools = registry.discover_tools()
        self.assertIn("greet2", all_tools)
        self.assertIn(all_tools["greet2"], agent_tools)


if __name__ == "__main__":
    unittest.main()
