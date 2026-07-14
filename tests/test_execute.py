import unittest
from tools.execute import execute

class TestExecuteTool(unittest.TestCase):
    def test_echo(self):
        result = execute({"command": "echo hello"})
        self.assertIn("hello", result)

if __name__ == '__main__':
    unittest.main()
