"""Linear issue metadata shared by creation, readiness and repository setup."""

import math


ISSUE_METADATA_POLICY = """Every Symphony Linear issue, including agent-created and follow-up issues,
must have an explicit priority (1 Urgent, 2 High, 3 Normal, or 4 Low) and an
estimate from its team's configured estimate scale. No priority (0) and a
missing estimate are incomplete. Choose values from the issue's urgency, scope,
complexity and verification work; preserve priorities and estimates already
supplied by the user. An explicit zero estimate is valid only when the team
allows it and the scope justifies it. Fill missing fields autonomously before
implementation; missing metadata alone never requires human review.
"""

REVIEW_METADATA_POLICY = """
Include issue_metadata with a priority, estimate and a short rationale tied to
this issue's urgency, scope and verification. Use the broker's estimate_values
(numeric API values, including for T-shirt sizes); do not invent a scale or
use a blanket default. Retain existing values. Propose values for missing fields
even when other readiness criteria are unresolved. The controller, rather than
this read-only assessment, writes only missing fields before coding can start.
"""


def estimate_values(team):
    # https://linear.app/docs/issue-property#scale-range-options
    scales = {"linear": [1, 2, 3, 4, 5, 6, 7],
              "fibonacci": [1, 2, 3, 5, 8, 13, 21],
              "exponential": [1, 2, 4, 8, 16, 32, 64],
              "tShirt": [1, 2, 3, 5, 8, 13, 21]}
    if (not isinstance(team, dict) or team.get("issueEstimationType") not in scales
            or type(team.get("issueEstimationExtended")) is not bool
            or type(team.get("issueEstimationAllowZero")) is not bool):
        raise ValueError("Cannot verify the team's configured estimate scale; estimates must be enabled")
    values = scales[team["issueEstimationType"]][:7 if team["issueEstimationExtended"] else 5]
    return ([0] if team["issueEstimationAllowZero"] else []) + values


def missing_metadata(issue):
    return [name for name in ("priority", "estimate")
            if issue.get(name) is None or name == "priority" and issue[name] == 0]


def validate_metadata(fields, *, estimates=None):
    if "priority" in fields and (type(fields["priority"]) is not int or fields["priority"] not in range(1, 5)):
        raise ValueError("Symphony issues require an explicit priority from 1 to 4")
    if "estimate" in fields:
        value = fields["estimate"]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value != int(value) or value < 0 or estimates is not None and value not in estimates):
            raise ValueError("Symphony issues require an estimate from the team's configured scale")
