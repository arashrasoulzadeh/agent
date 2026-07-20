"""Tests for llm/providers_cli.py (`agent providers`)."""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import llm.providers_cli as providers_cli
from core import settings


class ProvidersCliTestCase(unittest.TestCase):
    _KEYS = [
        "LLM_PROVIDER",
        "GAPGPT_API_KEY",
        "GAPGPT_BASE_URL",
        "GAPGPT_MODEL",
        "GAPGPT_TIMEOUT",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_TIMEOUT",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    ]

    def setUp(self):
        self._original_env = {key: os.environ.pop(key, None) for key in self._KEYS}

        self.tmp_dir = Path(tempfile.mkdtemp())
        self._original_settings_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = self.tmp_dir / "settings.json"

        # Never touch the real .env — same isolation goal as pointing
        # SETTINGS_FILE at a temp path above.
        self._load_dotenv_patch = mock.patch.object(
            providers_cli, "load_dotenv", lambda *a, **k: None
        )
        self._load_dotenv_patch.start()

    def tearDown(self):
        self._load_dotenv_patch.stop()
        settings.SETTINGS_FILE = self._original_settings_file
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, argv=()):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = providers_cli.run(list(argv))
        return code, buf.getvalue()


class TestRun(ProvidersCliTestCase):
    def test_marks_active_provider(self):
        os.environ["LLM_PROVIDER"] = "anthropic"
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("* anthropic", output)
        self.assertIn("  gapgpt", output)
        self.assertIn("  ollama", output)

    def test_defaults_to_gapgpt_active(self):
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("* gapgpt", output)

    def test_masks_api_key(self):
        os.environ["GAPGPT_API_KEY"] = "sk-real-secret-value"
        _, output = self._run()
        self.assertNotIn("real-secret", output)
        self.assertIn("GAPGPT_API_KEY", output)

    def test_shows_non_secret_value_in_full(self):
        os.environ["GAPGPT_MODEL"] = "gpt-5"
        _, output = self._run()
        self.assertIn("gpt-5", output)

    def test_reports_unset_vars(self):
        _, output = self._run()
        self.assertIn("(not set)", output)

    def test_unknown_provider_returns_error(self):
        os.environ["LLM_PROVIDER"] = "bogus"
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("bogus", output)


if __name__ == "__main__":
    unittest.main()
