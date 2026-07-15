import unittest

from modules.execute import execute


class TestExecuteTool(unittest.TestCase):
    def test_echo(self):
        result = execute.invoke({"command": "echo hello"})
        self.assertIn("hello", result)

    def test_outside_project_is_refused(self):
        result = execute.invoke({"command": "cat /etc/passwd"})
        self.assertIn("outside the project folder", result)

    def test_env_file_is_refused(self):
        result = execute.invoke({"command": "cat .env"})
        self.assertIn("protected env file", result)


if __name__ == "__main__":
    unittest.main()
