import unittest

from aircontrol.config import Settings
from aircontrol.diagnose import StepStats, measures, suggest


def stats(left=(0.8,), right=(0.8,), ext=(1.6, 0.6, 0.6, 0.6), modes=None, events=None, n=30, hands=None):
    st = StepStats()
    for i in range(n):
        has_hand = i < (n if hands is None else hands)
        st.add(object() if has_hand else None, "idle", {"left": left[i % len(left)], "right": right[i % len(right)], "ext": list(ext)} if has_hand else None)
    if modes:
        st.modes.clear()
        st.modes.update(modes)
    if events:
        st.events.update(events)
    return st


class SuggestTests(unittest.TestCase):
    cfg = Settings()

    def test_no_hand_visible(self):
        self.assertIn("Casi no se vio la mano", suggest("click", stats(hands=5), self.cfg)[0])

    def test_pointing_fails_because_ring_finger_is_extended(self):
        out = suggest("point", stats(ext=(1.6, 0.6, 1.4, 0.7), modes={"idle": 30}), self.cfg)
        self.assertTrue(any("Anular/meñique" in l and "1.40" in l for l in out))

    def test_pointing_fails_because_middle_reads_as_scroll(self):
        out = suggest("point", stats(ext=(1.6, 1.5, 0.6, 0.6), modes={"idle": 30}), self.cfg)
        self.assertTrue(any("corazón" in l and "✌️" in l for l in out))

    def test_click_threshold_too_strict(self):
        out = suggest("click", stats(left=(0.45, 0.6)), self.cfg)
        self.assertTrue(any("pinch_on" in l and "0.45" in l for l in out))

    def test_click_became_drag(self):
        out = suggest("click", stats(left=(0.1,), events={"press": 1}), self.cfg)
        self.assertTrue(any("arrastre" in l for l in out))

    def test_click_never_started(self):
        out = suggest("click", stats(left=(0.1,)), self.cfg)
        self.assertTrue(any("apunta" in l for l in out))

    def test_scroll_fingers_not_extended_enough(self):
        out = suggest("scroll", stats(ext=(1.2, 1.15, 0.6, 0.6)), self.cfg)
        self.assertTrue(any("scroll_extension" in l for l in out))

    def test_drag_diagnosis_uses_pinch_run_and_tracking_loss(self):
        st = StepStats()
        for i in range(30):                                             # racha de pellizco de ~0,3 s
            st.add(object(), "pinch" if 5 <= i < 15 else "point", {"left": 0.1, "right": 0.8, "ext": [1.3, 0.6, 0.6, 0.6]}, now=i / 30)
        self.assertAlmostEqual(st.longest_pinch(), 9 / 30, places=2)
        self.assertTrue(any("0.30 s" in l or "0.3" in l for l in suggest("drag", st, self.cfg)))
        lost = StepStats()
        for i in range(30):
            mode = "pinch" if 5 <= i < 20 else "idle"
            has = i not in (12, 13)
            lost.add(object() if has else None, mode, {"left": 0.1, "right": 0.8, "ext": [1.3, 0.6, 0.6, 0.6]} if has else None, now=i / 30)
        self.assertGreaterEqual(lost.lost_during_pinch, 1)
        self.assertTrue(any("perdió la mano" in l for l in suggest("drag", lost, Settings(drag_hold_s=0.2))))

    def test_measures_line_is_compact_and_informative(self):
        line = measures(stats(ext=(1.3, 1.2, 0.7, 0.6)))
        self.assertIn("extensión p90", line)
        self.assertIn("1.30/1.20/0.70/0.60", line)
        self.assertEqual(measures(StepStats()), "sin mano")

    def test_nothing_to_say_when_fine(self):
        self.assertEqual(suggest("point", stats(modes={"point": 30}), self.cfg), [])


if __name__ == "__main__":
    unittest.main()
