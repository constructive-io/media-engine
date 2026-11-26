import unittest
from pathlib import Path

from constructive_media.settings import Settings


class SettingsTest(unittest.TestCase):
    def test_defaults_hang_off_home(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.home, Path("~/.constructive-media").expanduser())
        self.assertEqual(settings.work_dir, settings.home / "work")
        self.assertEqual(settings.models_dir, settings.home / "models")
        self.assertIsNone(settings.database_url)
        self.assertEqual(dict(settings.adapters), {})

    def test_home_moves_everything_under_it(self):
        settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_HOME": "/srv/media"})
        self.assertEqual(settings.work_dir, Path("/srv/media/work"))
        self.assertEqual(settings.models_dir, Path("/srv/media/models"))

    def test_each_directory_can_be_set_alone(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_HOME": "/srv/media",
            "CONSTRUCTIVE_MEDIA_MODELS_DIR": "/mnt/models",
        })
        self.assertEqual(settings.models_dir, Path("/mnt/models"))
        self.assertEqual(settings.work_dir, Path("/srv/media/work"))

    def test_database_url_is_read_and_blank_means_unset(self):
        url = "postgres://example.test/media"
        self.assertEqual(
            Settings.from_env({"CONSTRUCTIVE_MEDIA_DATABASE_URL": url}).database_url, url
        )
        self.assertIsNone(Settings.from_env({"CONSTRUCTIVE_MEDIA_DATABASE_URL": ""}).database_url)

    def test_adapter_choice_per_slot(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS": "studio",
            "CONSTRUCTIVE_MEDIA_ADAPTER_VOICE_CONVERSION": "example",
            "CONSTRUCTIVE_MEDIA_ADAPTER_SPEECH": "",
            "UNRELATED": "ignored",
        })
        self.assertEqual(settings.adapter("effects"), "studio")
        self.assertEqual(settings.adapter("voice_conversion"), "example")
        self.assertIsNone(settings.adapter("speech"))
        self.assertIsNone(settings.adapter("image"))

    def test_settings_cannot_be_changed_after_reading(self):
        settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS": "studio"})
        with self.assertRaises(AttributeError):
            settings.home = Path("/elsewhere")
        with self.assertRaises(TypeError):
            settings.adapters["effects"] = "other"


if __name__ == "__main__":
    unittest.main()
