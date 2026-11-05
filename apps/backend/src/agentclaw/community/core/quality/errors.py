"""Quality task domain errors."""

class EvaluationInputIncompleteError(ValueError):
    """An evaluation cannot advance until its required inputs are supplied."""
