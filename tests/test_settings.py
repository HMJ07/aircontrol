import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aircontrol import config


class SettingsTests(unittest.TestCase):
    def load(self, text):
        path = Path(tempfile.mkdtemp()) / "settings.json"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        with mock.patch.object(config, "SETTINGS_PATH", path):
            return config.Settings.load()

    def test_defaults_when_missing_or_broken(self):
        self.assertEqual(self.load(None), config.Settings())
        self.assertEqual(self.load("{no es json"), config.Settings())

    def test_overrides_only_valid_keys_and_types(self):
        s = self.load(json.dumps({"camera_index": 2, "region_x0": 0.1, "pinch_on": "mucho", "inventada": 1}))
        self.assertEqual((s.camera_index, s.region_x0), (2, 0.1))
        self.assertEqual(s.pinch_on, config.Settings().pinch_on)


if __name__ == "__main__":
    unittest.main()


class SettingsApplyTests(unittest.TestCase):
    def test_apply_validates_ranges_types_and_choices(self):
        s = config.Settings()
        errors = s.apply({"pinch_on": 0.25, "pinch_off": 99, "gaze_click": "blink", "voice_mode": "gritar",
                          "scroll_natural": False, "camera_index": "dos", "inventada": 1})
        self.assertEqual((s.pinch_on, s.gaze_click, s.scroll_natural), (0.25, "blink", False))
        self.assertEqual(s.pinch_off, config.Settings().pinch_off)           # fuera de rango: se rechaza
        self.assertEqual(s.voice_mode, "off")
        self.assertEqual(len(errors), 4)

    def test_int_accepts_float_json_and_bool_is_not_a_number(self):
        s = config.Settings()
        self.assertEqual(s.apply({"camera_index": 1.0}), [])
        self.assertEqual(s.camera_index, 1)
        self.assertEqual(len(s.apply({"camera_index": True})), 1)

    def test_numpy_numbers_are_accepted(self):
        import numpy as np
        s = config.Settings()
        self.assertEqual(s.apply({"region_x0": np.float32(0.25), "camera_index": np.int64(1)}), [])
        self.assertIsInstance(s.region_x0, float)
        self.assertIsInstance(s.camera_index, int)

    def test_save_only_writes_non_defaults_and_roundtrips(self):
        path = Path(tempfile.mkdtemp()) / "settings.json"
        s = config.Settings()
        s.apply({"pinch_on": 0.22, "gaze_click": "blink"})
        with mock.patch.object(config, "SETTINGS_PATH", path):
            s.save()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"pinch_on": 0.22, "gaze_click": "blink"})
            self.assertEqual(config.Settings.load().pinch_on, 0.22)

    def test_describe_lists_every_field_with_label(self):
        fields = {d["name"]: d for d in config.Settings.describe()}
        self.assertEqual(set(fields), set(config.Settings().__dict__))
        self.assertTrue(all(d["label"] for d in fields.values()))
        self.assertEqual(fields["gaze_click"]["choices"], ["dwell", "blink", "pinch", "off"])


class AtomicWriteTests(unittest.TestCase):
    def test_retries_when_windows_reports_file_in_use(self):
        import os
        from aircontrol.fsutil import write_text_atomic
        path = Path(tempfile.mkdtemp()) / "f.json"
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 4:
                raise PermissionError("en uso por otro proceso")       # lo que lanza Windows
            return real(src, dst)

        with mock.patch("aircontrol.fsutil.os.replace", flaky):
            write_text_atomic(path, "hola")
        self.assertEqual(path.read_text(encoding="utf-8"), "hola")
        self.assertEqual(len(calls), 4)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["f.json"])       # sin temporales huérfanos

    def test_gives_up_cleanly(self):
        from aircontrol.fsutil import write_text_atomic
        path = Path(tempfile.mkdtemp()) / "f.json"
        with mock.patch("aircontrol.fsutil.os.replace", side_effect=PermissionError("x")), \
                mock.patch("aircontrol.fsutil.time.sleep"):
            with self.assertRaises(PermissionError):
                write_text_atomic(path, "x", retries=3)
        self.assertEqual(list(path.parent.iterdir()), [])                          # limpia el temporal

    def test_no_reader_ever_sees_a_half_written_file(self):
        import threading
        from aircontrol.fsutil import write_text_atomic
        path = Path(tempfile.mkdtemp()) / "f.json"
        write_text_atomic(path, json.dumps({"n": 0}))
        stop, bad = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    bad.append(1)

        t = threading.Thread(target=reader)
        t.start()
        for i in range(300):
            write_text_atomic(path, json.dumps({"n": i, "pad": "x" * 5000}))
        stop.set()
        t.join()
        self.assertEqual(bad, [])


class FocusAppTests(unittest.TestCase):
    def test_macos_uses_open_dash_a_and_ignores_empty_names(self):
        from aircontrol import system
        with mock.patch.object(system.sys, "platform", "darwin"), mock.patch("subprocess.Popen") as popen:
            self.assertTrue(system.focus_app("Safari"))
            self.assertEqual(popen.call_args[0][0], ["open", "-a", "Safari"])
            popen.reset_mock()
            self.assertFalse(system.focus_app(""))
            popen.assert_not_called()
