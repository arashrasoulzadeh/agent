"""Tests for workspace/signatures.py: function/class/variable signature
extraction from Python source, never full bodies, and graceful handling
of unparseable/empty content.
"""

import unittest

from workspace.signatures import extract_python, extract_signatures


class TestExtractPython(unittest.TestCase):
    def test_function_signature_with_defaults_and_annotations(self):
        source = '''
def get_llm(temperature: float = 0, log_raw_io: bool = True) -> str:
    """Build a client."""
    return "x"
'''
        result = extract_python(source)
        self.assertEqual(len(result["functions"]), 1)
        fn = result["functions"][0]
        self.assertEqual(fn["name"], "get_llm")
        self.assertFalse(fn["async"])
        self.assertEqual(fn["returns"], "str")
        self.assertEqual(fn["summary"], "Build a client.")
        self.assertEqual(
            fn["params"],
            [
                {"name": "temperature", "annotation": "float", "default": "0"},
                {"name": "log_raw_io", "annotation": "bool", "default": "True"},
            ],
        )
        # The function body itself must never appear (only the "returns"
        # annotation key, not the body's `return "x"` statement/value).
        self.assertNotIn('"x"', str(result))
        self.assertNotIn("return \"x\"", str(result))

    def test_async_function_detected(self):
        source = "async def serve() -> None:\n    pass\n"
        result = extract_python(source)
        self.assertTrue(result["functions"][0]["async"])

    def test_function_with_star_args_and_decorators(self):
        source = '''
@tool
@staticmethod
def run(a, *args, b=1, **kwargs):
    pass
'''
        result = extract_python(source)
        fn = result["functions"][0]
        names = [p["name"] for p in fn["params"]]
        self.assertEqual(names, ["a", "*args", "b", "**kwargs"])
        self.assertEqual(fn["decorators"], ["tool", "staticmethod"])

    def test_class_signature_with_bases_and_methods(self):
        source = '''
class ProjectAnalyst(Base):
    """Reason about a project."""

    def __init__(self, llm):
        self.llm = llm

    def ask(self, query: str) -> str:
        """Answer a question."""
        return query
'''
        result = extract_python(source)
        self.assertEqual(len(result["classes"]), 1)
        cls = result["classes"][0]
        self.assertEqual(cls["name"], "ProjectAnalyst")
        self.assertEqual(cls["bases"], ["Base"])
        self.assertEqual(cls["summary"], "Reason about a project.")
        method_names = [m["name"] for m in cls["methods"]]
        self.assertEqual(method_names, ["__init__", "ask"])
        self.assertEqual(cls["methods"][1]["summary"], "Answer a question.")

    def test_module_level_variable_with_annotation(self):
        source = "TIMEOUT: int = 30\n"
        result = extract_python(source)
        self.assertEqual(
            result["variables"],
            [{"name": "TIMEOUT", "annotation": "int", "value": "30"}],
        )

    def test_module_level_variable_plain_assignment(self):
        source = "DEFAULT_MAX_FILE_SIZE = 5 * 1024 * 1024\n"
        result = extract_python(source)
        var = result["variables"][0]
        self.assertEqual(var["name"], "DEFAULT_MAX_FILE_SIZE")
        self.assertIsNone(var["annotation"])
        # Not a simple literal (it's a multiplication expression) - no
        # value captured, only the name.
        self.assertIsNone(var["value"])

    def test_module_level_variable_simple_literal_value_captured(self):
        source = "MODEL_NAME = 'gpt-4o-mini'\n"
        result = extract_python(source)
        self.assertEqual(result["variables"][0]["value"], "'gpt-4o-mini'")

    def test_nested_function_local_variables_not_captured(self):
        source = '''
def outer():
    local_var = 1
    return local_var
'''
        result = extract_python(source)
        self.assertEqual(result["variables"], [])
        self.assertEqual(len(result["functions"]), 1)

    def test_no_signatures_returns_none(self):
        source = "print('just a statement, no defs')\n"
        result = extract_python(source)
        self.assertIsNone(result)

    def test_empty_source_returns_none(self):
        self.assertIsNone(extract_python(""))

    def test_syntax_error_returns_none_not_raises(self):
        result = extract_python("def broken(:\n    pass")
        self.assertIsNone(result)

    def test_full_source_body_never_present_in_output(self):
        source = '''
SECRET_LOOKING_STRING = "not-actually-a-secret-just-body-content"

def process(data):
    transformed = data.upper()
    return transformed
'''
        result = extract_python(source)
        blob = str(result)
        self.assertNotIn("transformed", blob)
        self.assertNotIn("data.upper", blob)


class TestExtractSignaturesDispatch(unittest.TestCase):
    def test_dispatches_to_python_extractor(self):
        result = extract_signatures("python", "def f():\n    pass\n")
        self.assertEqual(result["functions"][0]["name"], "f")

    def test_unregistered_language_returns_none(self):
        self.assertIsNone(extract_signatures("go", "func main() {}"))

    def test_none_language_returns_none(self):
        self.assertIsNone(extract_signatures(None, "def f(): pass"))


if __name__ == "__main__":
    unittest.main()
