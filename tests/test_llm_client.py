"""Tests for llm/client.py's provider dispatch and llm/providers/*.py's
individual builders.
"""

import os
import unittest
from unittest import mock

from llm import client


class ProviderEnvTestCase(unittest.TestCase):
    """Saves/restores every provider-related env var, the same seam
    convention tests/test_core_settings.py uses for core.settings."""

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
        self._original = {key: os.environ.pop(key, None) for key in self._KEYS}

    def tearDown(self):
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestGetLlmDispatch(ProviderEnvTestCase):
    def test_defaults_to_gapgpt(self):
        sentinel = object()
        fake_build = mock.Mock(return_value=sentinel)
        with mock.patch.dict(client._PROVIDERS, {"gapgpt": fake_build}):
            result = client.get_llm(temperature=0.5, log_raw_io=False)
        fake_build.assert_called_once_with(0.5, False)
        self.assertIs(result, sentinel)

    def test_dispatches_by_llm_provider(self):
        os.environ["LLM_PROVIDER"] = "anthropic"
        sentinel = object()
        fake_build = mock.Mock(return_value=sentinel)
        with mock.patch.dict(client._PROVIDERS, {"anthropic": fake_build}):
            result = client.get_llm()
        fake_build.assert_called_once_with(0, True)
        self.assertIs(result, sentinel)

    def test_unknown_provider_raises_value_error(self):
        os.environ["LLM_PROVIDER"] = "bogus"
        with self.assertRaises(ValueError) as ctx:
            client.get_llm()
        message = str(ctx.exception)
        self.assertIn("bogus", message)
        self.assertIn("anthropic", message)
        self.assertIn("gapgpt", message)
        self.assertIn("ollama", message)


class TestProviderBuilders(ProviderEnvTestCase):
    def test_gapgpt_build_uses_env(self):
        from llm.providers import gapgpt

        os.environ["GAPGPT_API_KEY"] = "sk-test"
        os.environ["GAPGPT_MODEL"] = "custom-model"
        llm = gapgpt.build(temperature=0.3, log_raw_io=False)
        self.assertEqual(llm.model_name, "custom-model")
        self.assertEqual(llm.temperature, 0.3)

    def test_gapgpt_build_requires_api_key(self):
        from llm.providers import gapgpt

        with self.assertRaises(KeyError):
            gapgpt.build(temperature=0, log_raw_io=False)

    def test_anthropic_build_uses_env(self):
        from llm.providers import anthropic

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-5"
        llm = anthropic.build(temperature=0.2, log_raw_io=False)
        self.assertEqual(llm.model, "claude-sonnet-5")
        self.assertEqual(llm.temperature, 0.2)

    def test_anthropic_build_requires_api_key(self):
        from llm.providers import anthropic

        with self.assertRaises(KeyError):
            anthropic.build(temperature=0, log_raw_io=False)

    def test_ollama_build_uses_env(self):
        from llm.providers import ollama

        os.environ["OLLAMA_MODEL"] = "llama3.1"
        llm = ollama.build(temperature=0.1, log_raw_io=False)
        self.assertEqual(llm.model, "llama3.1")

    def test_ollama_build_requires_model(self):
        from llm.providers import ollama

        with self.assertRaises(KeyError):
            ollama.build(temperature=0, log_raw_io=False)


if __name__ == "__main__":
    unittest.main()
