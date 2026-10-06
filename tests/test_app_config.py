import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sheet_mover.app_config import (
    AppSettings,
    apply_runtime_environment,
    load_settings,
    save_settings,
    validate_settings,
    google_credentials,
    runtime_google_credentials,
    check_google,
)


class AppConfigTests(unittest.TestCase):
    def test_service_account_to_adc_clears_stale_credentials(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            apply_runtime_environment(AppSettings(google_auth_mode="service_account",
                                                 google_credentials_file="previous.json"))
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], "previous.json")
            apply_runtime_environment(AppSettings(google_auth_mode="adc"))
            self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", os.environ)
            self.assertEqual(os.environ["SHEETMOVER_GOOGLE_PROJECT"], "")

    def test_empty_service_account_does_not_keep_previous_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "LOCALAPPDATA": temp, "GOOGLE_APPLICATION_CREDENTIALS": "old.json",
        }):
            apply_runtime_environment(AppSettings(google_auth_mode="service_account"))
            self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", os.environ)
            with self.assertRaises(ValueError):
                runtime_google_credentials()

    def test_adc_ignores_inherited_service_account_without_mutating_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "CLOUDSDK_CONFIG": temp, "GOOGLE_APPLICATION_CREDENTIALS": "old.json",
        }), patch("google.auth.load_credentials_from_file", return_value=(object(), None)) as load:
            google_credentials(AppSettings(google_auth_mode="adc"))
            self.assertEqual(Path(load.call_args.args[0]),
                             Path(temp) / "application_default_credentials.json")
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], "old.json")

    def test_preflight_and_translator_use_same_selected_credentials(self):
        from sheet_mover.translator import Translator
        from unittest.mock import Mock
        credentials = Mock(valid=True)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}), \
                patch("google.auth.load_credentials_from_file", return_value=(credentials, None)), \
                patch("google.cloud.translate_v3.TranslationServiceClient") as client:
            settings = AppSettings(google_project_id="fixture", google_auth_mode="service_account",
                                   google_credentials_file="chosen.json")
            self.assertTrue(check_google(settings)["ok"])
            apply_runtime_environment(settings)
            translator = Translator(project_id="fixture")
            translator._client()
            self.assertEqual(client.call_count, 2)
            for call in client.call_args_list:
                self.assertIs(call.kwargs["credentials"], credentials)

    def test_plain_cli_keeps_default_google_discovery(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(runtime_google_credentials())

    def test_glossary_storage_and_translation_use_selected_credentials(self):
        from sheet_mover.google_glossary import setup_google_glossary
        credentials = object()
        with patch("sheet_mover.app_config.runtime_google_credentials", return_value=credentials), \
                patch("google.cloud.storage.Client") as storage, \
                patch("google.cloud.translate_v3.TranslationServiceClient") as translation:
            setup_google_glossary(project_id="fixture")
            self.assertIs(storage.call_args.kwargs["credentials"], credentials)
            self.assertIs(translation.call_args.kwargs["credentials"], credentials)

    def test_google_refresh_and_api_probe_have_bounded_timeouts(self):
        from unittest.mock import Mock
        credentials = Mock(valid=False)
        credentials.refresh.side_effect = lambda request: request(url="fixture-url", timeout=120)
        with patch("sheet_mover.app_config.google_credentials", return_value=credentials), \
                patch("google.auth.transport.requests.Request") as request, \
                patch("google.cloud.translate_v3.TranslationServiceClient") as client:
            self.assertTrue(check_google(AppSettings(google_project_id="fixture"))["ok"])
            self.assertEqual(request.return_value.call_args.kwargs["timeout"], 10)
            self.assertEqual(client.return_value.get_supported_languages.call_args.kwargs["timeout"], 10)

    def test_google_and_ollama_settings_are_independent(self):
        settings = AppSettings(
            google_project_id="my-project",
            google_auth_mode="adc",
            ollama_host="http://localhost:11434",
            ollama_model="gpt-oss:20b",
        )
        self.assertEqual(settings.google_project_id, "my-project")
        self.assertEqual(settings.ollama_model, "gpt-oss:20b")

    def test_settings_roundtrip_does_not_store_secret_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            settings = AppSettings(
                google_project_id="project-x",
                google_auth_mode="service_account",
                google_credentials_file=r"C:\secret\service-account.json",
                ollama_model="gpt-oss:20b",
            )
            save_settings(settings, path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("service-account.json", text)
            self.assertNotIn("private_key", text)
            loaded = load_settings(path)
            self.assertEqual(loaded.google_project_id, "project-x")

    def test_service_account_file_is_validated(self):
        settings = AppSettings(
            google_project_id="project-x",
            google_auth_mode="service_account",
            google_credentials_file=r"Z:\missing.json",
        )
        problems = validate_settings(settings)
        self.assertTrue(any("JSON" in row for row in problems))

    def test_runtime_environment_separates_google_and_ollama(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "LOCALAPPDATA": temp,
                "APPDATA": temp,
            }
            with patch.dict(os.environ, env, clear=False):
                settings = AppSettings(
                    google_project_id="p1",
                    ollama_host="http://127.0.0.1:11434",
                    ollama_model="gpt-oss:20b",
                )
                apply_runtime_environment(settings)
                self.assertEqual(
                    os.environ["SHEETMOVER_GOOGLE_PROJECT"],
                    "p1",
                )
                self.assertEqual(
                    os.environ["SHEETMOVER_REVIEW_MODEL"],
                    "gpt-oss:20b",
                )
                self.assertIn(
                    "google-translation-cache.json",
                    os.environ["SHEETMOVER_TRANSLATION_CACHE"],
                )
                self.assertIn(
                    "ollama-review-cache.json",
                    os.environ["SHEETMOVER_REVIEW_CACHE"],
                )


if __name__ == "__main__":
    unittest.main()
