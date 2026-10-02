import unittest

from aircontrol.actions import Action, ActionError, ActionRunner, parse_action, parse_keys
from aircontrol.profiles import DEFAULT_PROFILES, Profiles


class ParseKeysTests(unittest.TestCase):
    def test_mod_is_platform_specific(self):
        self.assertEqual(parse_keys("mod+c", "darwin"), ["cmd", "c"])
        self.assertEqual(parse_keys("mod+c", "win32"), ["ctrl", "c"])

    def test_combos_and_named_keys(self):
        self.assertEqual(parse_keys("Ctrl+Shift+T", "win32"), ["ctrl", "shift", "t"])
        self.assertEqual(parse_keys("space"), ["space"])
        self.assertEqual(parse_keys("PageDown"), ["page_down"])
        self.assertEqual(parse_keys("alt+f4"), ["alt", "f4"])
        self.assertEqual(parse_keys("mod+[", "darwin"), ["cmd", "["])
        self.assertEqual(parse_keys("mod+plus", "darwin"), ["cmd", "+"])

    def test_invalid(self):
        for bad in ("", "mod+", "ctrl+shift", "banana", "ctrl+banana"):
            with self.assertRaises(ActionError, msg=bad):
                parse_keys(bad)


class ParseActionTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(parse_action("click").kind, "click")
        self.assertEqual(parse_action("scroll:down:3").arg, -3)
        self.assertEqual(parse_action("scroll:up").arg, 5)
        self.assertEqual(parse_action("media:mute").arg, "mute")
        self.assertEqual(parse_action("open:Safari").arg, "Safari")
        self.assertEqual(parse_action("text: hola ").arg, " hola ")
        self.assertEqual(parse_action("key:mod+r", "darwin").arg, ["cmd", "r"])

    def test_per_platform_dict(self):
        spec = {"darwin": "key:mod+[", "default": "key:alt+left"}
        self.assertEqual(parse_action(spec, "darwin").arg, ["cmd", "["])
        self.assertEqual(parse_action(spec, "win32").arg, ["alt", "left"])

    def test_mode_keyboard_voice(self):
        self.assertEqual(parse_action("mode:gaze").arg, "gaze")
        self.assertEqual(parse_action("keyboard").kind, "keyboard")
        self.assertEqual(parse_action("voice").kind, "voice")
        with self.assertRaises(ActionError):
            parse_action("mode:telepatia")

    def test_invalid(self):
        for bad in ("", "dance", "media:teleport", "scroll:sideways", "open:", "click:now", 5, None):
            with self.assertRaises(ActionError, msg=str(bad)):
                parse_action(bad)


class RunnerTests(unittest.TestCase):
    class Fake:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            return lambda *a: self.calls.append((name, *a))

    def test_dispatch(self):
        fake, control = self.Fake(), []
        runner = ActionRunner(fake, on_control=lambda kind, arg=None: control.append(kind))
        for spec in ("key:mod+c", "double_click", "scroll:down:2", "media:next", "open:x", "text:hi", "toggle_pause"):
            runner.run(parse_action(spec, "darwin"))
        self.assertEqual(fake.calls, [("key_combo", ["cmd", "c"]), ("click", "left", 2), ("scroll", 0, -2),
                                      ("media", "next"), ("open_target", "x"), ("type_text", "hi")])
        self.assertEqual(control, ["toggle_pause"])


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.profiles = Profiles()

    def test_default_profiles_are_valid(self):
        self.assertEqual(self.profiles.errors, [])

    def test_same_gesture_differs_by_app(self):
        slides = self.profiles.resolve("thumbs_up", "Keynote")
        video = self.profiles.resolve("thumbs_up", "VLC")
        other = self.profiles.resolve("thumbs_up", "Terminal")
        browser_up = self.profiles.resolve("swipe_up", "Google Chrome")
        self.assertEqual(slides.arg, ["f5"])
        self.assertEqual(video.arg, ["space"])
        self.assertEqual(other, parse_action("media:play_pause"))
        self.assertEqual(browser_up.kind, "scroll")                           # en el navegador sube la página
        self.assertEqual(self.profiles.resolve("swipe_up", "Terminal"), parse_action("media:volume_up"))

    def test_falls_back_to_default_when_profile_lacks_gesture(self):
        self.assertEqual(self.profiles.resolve("swipe_up", "VLC"), parse_action("media:volume_up"))

    def test_unknown_gesture_and_profile_name(self):
        self.assertIsNone(self.profiles.resolve("inventado", "Safari"))
        self.assertEqual(self.profiles.profile_name("Safari"), "Navegador")
        self.assertEqual(self.profiles.profile_name("Terminal"), "default")
        self.assertEqual(self.profiles.profile_name(None), "default")

    def test_default_examples_contain_no_destructive_actions(self):
        """Un gesto accidental no debe costar nada: nada de cerrar pestañas/ventanas, salir, borrar o ir a la barra de URL."""
        import json
        dangerous = ("mod+w", "mod+q", "esc", "delete", "backspace", "mod+l", "alt+f4", "mod+shift+w")
        text = json.dumps(DEFAULT_PROFILES).lower()
        for d in dangerous:
            self.assertNotIn(d, text, d)

    def test_untouched_legacy_profiles_file_is_migrated_and_edited_one_is_not(self):
        import json, tempfile
        from pathlib import Path
        from aircontrol.profiles import LEGACY_DEFAULTS
        d = Path(tempfile.mkdtemp())
        untouched = d / "a.json"
        untouched.write_text(json.dumps(LEGACY_DEFAULTS[0]))
        self.assertTrue(Profiles.load(untouched).migrated)
        self.assertEqual(json.loads(untouched.read_text(encoding="utf-8")), DEFAULT_PROFILES)
        self.assertFalse(Profiles.load(untouched).migrated)                     # ya está al día
        edited = json.loads(json.dumps(LEGACY_DEFAULTS[0]))
        edited["profiles"][0]["bindings"]["swipe_up"] = "key:mod+t"          # el usuario cambió algo a propósito
        mine = d / "b.json"
        mine.write_text(json.dumps(edited))
        self.assertFalse(Profiles.load(mine).migrated)
        self.assertEqual(json.loads(mine.read_text(encoding="utf-8")), edited)                  # no se pisa nada del usuario

    def test_pinky_up_opens_keyboard_even_with_an_old_profile_file(self):
        old = {"default": {"thumbs_up": "click"}, "profiles": []}              # perfil de una versión anterior
        self.assertEqual(Profiles(old).resolve("pinky_up", "Safari").kind, "keyboard")
        self.assertEqual(self.profiles.resolve("pinky_up", "Terminal").kind, "keyboard")

    def test_user_can_override_the_fallback_and_unknown_gestures_stay_unbound(self):
        mine = {"default": {"pinky_up": "voice"}, "profiles": []}
        self.assertEqual(Profiles(mine).resolve("pinky_up", "x").kind, "voice")
        app_level = {"default": {}, "profiles": [{"name": "X", "match": ["x"], "bindings": {"pinky_up": "mode:gaze"}}]}
        self.assertEqual(Profiles(app_level).resolve("pinky_up", "x").kind, "mode")
        self.assertIsNone(self.profiles.resolve("inventado", "x"))

    def test_bad_bindings_are_reported_not_fatal(self):
        data = {"default": {"a": "baila"}, "profiles": [{"name": "X", "match": ["x"], "bindings": {"b": "key:ctrl+"}}]}
        p = Profiles(data)
        self.assertEqual(len(p.errors), 2)
        self.assertIsNone(p.resolve("a", "x"))


if __name__ == "__main__":
    unittest.main()
