from __future__ import annotations

import unittest

from app.scene_planner import RuleBasedScenePlanner, estimate_generation_count, validate_plan_inputs


class ScenePlannerTests(unittest.TestCase):
    def test_exact_target_count_and_economical_defaults(self) -> None:
        script = " ".join(
            f"Chapter {index} remembers a neighborhood kitchen and explains one carefully observed detail."
            for index in range(1, 31)
        )
        drafts = RuleBasedScenePlanner().plan(
            script,
            theme_id="us_nostalgia",
            duration_seconds=125.0,
            target_scene_count=10,
        )
        self.assertEqual(len(drafts), 10)
        self.assertEqual(estimate_generation_count(drafts), 10)
        self.assertTrue(all(scene.candidate_count == 1 for scene in drafts))
        self.assertTrue(all(scene.model_role == "photoreal" for scene in drafts))
        self.assertAlmostEqual(drafts[0].start_seconds, 0.0)
        self.assertAlmostEqual(drafts[-1].end_seconds, 125.0)
        self.assertTrue(all(left.end_seconds <= right.start_seconds for left, right in zip(drafts, drafts[1:])))

    def test_emotion_changes_prompt_and_motion(self) -> None:
        script = (
            "The old diner vanished and the final empty booth was never seen again. "
            "However, a hidden recipe held the surprising truth."
        )
        drafts = RuleBasedScenePlanner().plan(
            script,
            theme_id="us_nostalgia",
            duration_seconds=20.0,
            target_scene_count=2,
        )
        self.assertIn(drafts[0].emotion.value, {"loss", "nostalgia"})
        self.assertIn("Palette:", drafts[0].prompt)
        self.assertEqual(drafts[0].timeline_actions[0].type, "motion")

    def test_short_script_and_unrealistic_target_are_explained(self) -> None:
        warnings = validate_plan_inputs(
            "This is only a short hook for testing.",
            duration_seconds=149 * 60,
            target_scene_count=715,
            actual_scene_count=2,
        )
        codes = {warning["code"] for warning in warnings}
        self.assertTrue({"short_script", "duration_too_long", "too_many_images", "target_not_reached"} <= codes)


if __name__ == "__main__":
    unittest.main()
