import unittest
from pathlib import Path

from constructive_media.settings import Database, Settings, SettingsError


class SettingsTest(unittest.TestCase):
    def test_defaults_hang_off_home(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.home, Path("~/.constructive-media").expanduser())
        self.assertEqual(settings.work_dir, settings.home / "work")
        self.assertEqual(settings.models_dir, settings.home / "models")
        self.assertEqual(settings.database, Database())
        self.assertEqual(settings.device, "auto")
        self.assertEqual(dict(settings.adapters), {})
        self.assertEqual(dict(settings.models), {})

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

    def test_database_url_is_taken_apart(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": "postgres://editor:s%40fe@db.example.test:6543/library",
        })
        self.assertEqual(
            settings.database,
            Database(name="library", host="db.example.test", port=6543, user="editor", password="s@fe"),
        )

    def test_database_falls_back_to_the_standard_variables(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": "",
            "PGHOST": "db.example.test", "PGPORT": "6543", "PGUSER": "editor",
            "PGPASSWORD": "made-up", "PGDATABASE": "library",
        })
        self.assertEqual(
            settings.database,
            Database(name="library", host="db.example.test", port=6543, user="editor", password="made-up"),
        )
        self.assertEqual(Settings.from_env({}).database.name, "media")

    def test_the_url_wins_over_the_standard_variables(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": "postgresql://db.example.test/library",
            "PGHOST": "elsewhere.example.test", "PGDATABASE": "other",
        })
        self.assertEqual((settings.database.host, settings.database.name), ("db.example.test", "library"))
        self.assertEqual(settings.database.port, 5432)

    def test_a_bad_database_setting_is_an_error(self):
        for env in (
            {"CONSTRUCTIVE_MEDIA_DATABASE_URL": "mysql://db.example.test/library"},
            {"CONSTRUCTIVE_MEDIA_DATABASE_URL": "postgres://db.example.test:port/library"},
            {"PGPORT": "five"},
        ):
            with self.subTest(env=env), self.assertRaises(SettingsError):
                Settings.from_env(env)

    def test_the_password_stays_out_of_what_is_printed(self):
        settings = Settings.from_env({"PGPASSWORD": "made-up"})
        self.assertNotIn("made-up", repr(settings))

    def test_device_and_model_choice(self):
        settings = Settings.from_env({
            "CONSTRUCTIVE_MEDIA_DEVICE": "CPU",
            "CONSTRUCTIVE_MEDIA_MODEL_DETECTION": "fasterrcnn_mobilenet_v3_large_fpn",
        })
        self.assertEqual(settings.device, "cpu")
        self.assertEqual(settings.model("detection"), "fasterrcnn_mobilenet_v3_large_fpn")
        self.assertIsNone(settings.model("speech"))

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

    def test_where_libraries_are_found_by_name(self):
        self.assertEqual(Settings.from_env({}).indexes, ("constructive-io/media-libraries",))
        chosen = Settings.from_env({"CONSTRUCTIVE_MEDIA_LIBRARY_INDEX": " ~/indexes/ours , owner/theirs,"})
        self.assertEqual(chosen.indexes, ("~/indexes/ours", "owner/theirs"))
        for nowhere in ("none", "OFF"):
            self.assertEqual(Settings.from_env({"CONSTRUCTIVE_MEDIA_LIBRARY_INDEX": nowhere}).indexes, ())

    def test_settings_cannot_be_changed_after_reading(self):
        settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS": "studio"})
        with self.assertRaises(AttributeError):
            settings.home = Path("/elsewhere")
        with self.assertRaises(TypeError):
            settings.adapters["effects"] = "other"


if __name__ == "__main__":
    unittest.main()
