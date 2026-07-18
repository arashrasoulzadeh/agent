"""Tests for core/settings.py: the process-wide, runtime-editable
settings store behind the TUI's /settings screen and the
/settings/list, /settings/update wire routes.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core import settings


class SettingsTestCase(unittest.TestCase):
    """Monkeypatches SETTINGS_FILE to a temp path and saves/restores
    every known setting's env var, the same seam convention
    tests/test_guard.py uses for core.guard's contextvars."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self._original_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = self.tmp_dir / "settings.json"

        self._original_env = {}
        for spec in settings.SETTINGS:
            self._original_env[spec.key] = os.environ.pop(spec.key, None)

    def tearDown(self):
        settings.SETTINGS_FILE = self._original_file
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestListSettings(SettingsTestCase):
    def test_returns_every_known_setting(self):
        result = settings.list_settings()
        keys = {s["key"] for s in result}
        self.assertEqual(keys, {spec.key for spec in settings.SETTINGS})

    def test_defaults_when_nothing_set(self):
        result = {s["key"]: s for s in settings.list_settings()}
        self.assertEqual(result["GAPGPT_MODEL"]["value"], "gpt-4o-mini")
        self.assertEqual(
            result["GAPGPT_BASE_URL"]["value"], "https://api.gapgpt.app/v1"
        )
        self.assertFalse(result["GAPGPT_MODEL"]["set"])

    def test_reflects_env_var_for_non_secret(self):
        os.environ["GAPGPT_MODEL"] = "gpt-5"
        result = {s["key"]: s for s in settings.list_settings()}
        self.assertEqual(result["GAPGPT_MODEL"]["value"], "gpt-5")
        self.assertTrue(result["GAPGPT_MODEL"]["set"])

    def test_masks_secret_value(self):
        os.environ["GAPGPT_API_KEY"] = "sk-real-secret-value"
        result = {s["key"]: s for s in settings.list_settings()}
        value = result["GAPGPT_API_KEY"]["value"]
        self.assertNotIn("real-secret", value)
        self.assertNotEqual(value, "sk-real-secret-value")
        self.assertTrue(value)  # not empty — something is shown

    def test_empty_secret_stays_empty_not_masked(self):
        result = {s["key"]: s for s in settings.list_settings()}
        self.assertEqual(result["NOTION_API_KEY"]["value"], "")

    def test_non_secret_specs_are_not_masked(self):
        os.environ["GAPGPT_BASE_URL"] = "https://example.com/v1"
        result = {s["key"]: s for s in settings.list_settings()}
        self.assertEqual(result["GAPGPT_BASE_URL"]["value"], "https://example.com/v1")


class TestUpdateSetting(SettingsTestCase):
    def test_persists_to_settings_file(self):
        settings.update_setting("GAPGPT_MODEL", "gpt-5")
        payload = json.loads(settings.SETTINGS_FILE.read_text())
        self.assertEqual(payload["GAPGPT_MODEL"], "gpt-5")

    def test_applies_to_environ_immediately(self):
        settings.update_setting("NOTION_API_KEY", "secret-token")
        self.assertEqual(os.environ["NOTION_API_KEY"], "secret-token")

    def test_list_settings_reflects_update_right_away(self):
        settings.update_setting("GAPGPT_TIMEOUT", "120")
        result = {s["key"]: s for s in settings.list_settings()}
        self.assertEqual(result["GAPGPT_TIMEOUT"]["value"], "120")
        self.assertTrue(result["GAPGPT_TIMEOUT"]["set"])

    def test_unknown_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            settings.update_setting("NOT_A_REAL_SETTING", "x")

    def test_second_update_merges_not_overwrites_other_keys(self):
        settings.update_setting("GAPGPT_MODEL", "gpt-5")
        settings.update_setting("GAPGPT_TIMEOUT", "90")
        payload = json.loads(settings.SETTINGS_FILE.read_text())
        self.assertEqual(payload["GAPGPT_MODEL"], "gpt-5")
        self.assertEqual(payload["GAPGPT_TIMEOUT"], "90")


class TestApplyPersisted(SettingsTestCase):
    def test_pushes_persisted_values_into_environ(self):
        settings.SETTINGS_FILE.write_text(
            json.dumps({"GAPGPT_MODEL": "gpt-5", "AGENT_VERBOSE": "1"})
        )
        settings.apply_persisted()
        self.assertEqual(os.environ["GAPGPT_MODEL"], "gpt-5")
        self.assertEqual(os.environ["AGENT_VERBOSE"], "1")

    def test_leaves_unmentioned_keys_untouched(self):
        settings.SETTINGS_FILE.write_text(json.dumps({"GAPGPT_MODEL": "gpt-5"}))
        settings.apply_persisted()
        self.assertNotIn("NOTION_API_KEY", os.environ)

    def test_no_file_is_a_noop(self):
        settings.apply_persisted()  # must not raise
        self.assertNotIn("GAPGPT_MODEL", os.environ)

    def test_corrupt_file_is_treated_as_empty(self):
        settings.SETTINGS_FILE.write_text("{not valid json")
        settings.apply_persisted()  # must not raise
        self.assertNotIn("GAPGPT_MODEL", os.environ)


if __name__ == "__main__":
    unittest.main()
