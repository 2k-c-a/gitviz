"""Candidate maintenance-ranking policy; not a calibrated defect model."""
import math

DEFAULT_WINDOW_DAYS = 180
LOW_SUPPORT_THRESHOLD = 5
FORMULA_VERSION = "gitviz-2026.10-priority-v3"


def percentile_rank(values):
    """Midrank scaled to [0,1]; ties share a rank, constant samples give 0.

    Missing observations remain None and do not enter the comparison population.
    """
    observed = sorted(v for v in values.values() if v is not None)
    if not observed:
        return {k: None for k in values}
    if observed[0] == observed[-1]:
        return {k: 0.0 if v is not None else None for k, v in values.items()}
    ranks = {}
    i = 0
    while i < len(observed):
        j = i + 1
        while j < len(observed) and observed[j] == observed[i]:
            j += 1
        ranks[observed[i]] = ((i + j - 1) / 2) / (len(observed) - 1)
        i = j
    return {k: ranks[v] if v is not None else None for k, v in values.items()}


def large_change_threshold(file_count):
    return max(15, math.ceil(0.1 * file_count))
