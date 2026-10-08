"""Direct Plan input generation from the user-provided ``--goal``."""

from .service import DirectGoalResult, build_direct_goal_plan

__all__ = ["DirectGoalResult", "build_direct_goal_plan"]
