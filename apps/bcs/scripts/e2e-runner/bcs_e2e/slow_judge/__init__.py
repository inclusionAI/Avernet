"""Dual-environment migration slice for story_provider_callback_survives_slow_judge.

The owned-local leg keeps delegating to the frozen flat black-box executor
(``local_slow_judge_run`` over the reused owned loopback Provider/Judge
receiver); the preprod leg is admitted by profile and fail-closes before any
credential consumption or network access while the bearer journey seam and
the credential window remain pending with the mainline.
"""
