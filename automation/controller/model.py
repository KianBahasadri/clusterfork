"""Task commands and views. No transports, ambient clock, or credentials."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json


class Rejected(RuntimeError):
    """The command cannot change the current task; callers must refresh its view."""


class Phase(StrEnum):
    QUEUED = "queued"
    REVIEWING = "reviewing"
    READY = "ready"
    EXECUTING = "executing"
    PUBLISHED = "published"
    HELD = "held"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


class Role(StrEnum):
    REVIEW_SELECT = "review-select"
    REVIEW = "review"
    CODING_SELECT = "coding-select"
    CODING = "coding"

    @property
    def phase(self):
        return "review" if self in (Role.REVIEW_SELECT, Role.REVIEW) else "coding"

    @property
    def selector(self):
        return self in (Role.REVIEW_SELECT, Role.CODING_SELECT)


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class IssueObserved:
    issue_id: str
    project_id: str
    repository: str
    generation: str
    identifier: str
    eligible: bool
    dependencies_ready: bool
    priority: int = 0
    sort_order: float = 0
    content_revision: str = ""
    reason: str = ""
    canceled: bool = False

    @property
    def task_id(self):
        return identity(self.generation, self.project_id, self.issue_id)


@dataclass(frozen=True)
class TaskSpec:
    content_revision: str
    context_sha: str
    runtime_sha: str
    policy_revision: str
    images_sha: str

    def __post_init__(self):
        import re
        if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (self.context_sha, self.runtime_sha)):
            raise ValueError("Task context and runtime require full commit identities")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in
               (self.content_revision, self.policy_revision, self.images_sha)):
            raise ValueError("Task content, policy and images require SHA-256 identities")


@dataclass(frozen=True)
class Admit:
    task_id: str
    spec: TaskSpec


@dataclass(frozen=True)
class DispatchObserved:
    task_id: str
    attempt_id: str
    status: str  # accepted/rejected/unknown; never retry an unknown dispatch


@dataclass(frozen=True)
class Start:
    task_id: str
    attempt_id: str
    run_id: str
    run_attempt: int
    role: Role
    review_job_succeeded: bool = False  # Supplied by the trusted GitHub adapter.


@dataclass(frozen=True)
class Assign:
    task_id: str
    attempt_id: str
    role: Role
    account_id: str


@dataclass(frozen=True)
class Review:
    task_id: str
    attempt_id: str
    result: dict
    binding: TaskSpec


@dataclass(frozen=True)
class Progress:
    task_id: str
    attempt_id: str
    stage: str
    context_sha: str = ""
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Finish:
    task_id: str
    attempt_id: str
    phase: str
    outcome: str
    pull_request: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ExternalObserved:
    task_id: str
    attempt_id: str
    run_id: str
    run_attempt: int
    stopped: bool
    conclusion: str
    all_attempts_stopped: bool
    publication_checked: bool
    pull_request: str = ""
    pull_state: str = ""  # open/merged/closed/absent
    reason: str = ""
    repairable: bool = False


@dataclass(frozen=True)
class Retry:
    task_id: str
    spec: TaskSpec
    reason: str
    kind: str  # startup/capacity/context/repair; policy chooses, caller cannot override


@dataclass(frozen=True)
class Hold:
    owner: str
    reason: str
    release: bool = False


@dataclass(frozen=True)
class SessionUpdated:
    task_id: str
    attempt_id: str
    phase: str
    fields: dict


@dataclass(frozen=True)
class EffectUpdated:
    task_id: str
    effect_id: str
    state: str


@dataclass(frozen=True)
class Authorize:
    task_id: str
    attempt_id: str
    role: Role


@dataclass(frozen=True)
class ExhaustAccount:
    task_id: str
    attempt_id: str
    account_id: str
    thread_id: str


@dataclass(frozen=True)
class ContextObserved:
    task_id: str
    attempt_id: str
    binding: TaskSpec


@dataclass(frozen=True)
class InboxQueued:
    event_id: str
    event: dict


@dataclass(frozen=True)
class InboxDone:
    event_id: str


@dataclass(frozen=True)
class Initialize:
    pass


@dataclass(frozen=True)
class LegacyImported:
    generation: str
    records: tuple[dict, ...]
    source_digest: str
    operator_holds: tuple[tuple[str,str], ...] = ()


Command = (IssueObserved | Admit | DispatchObserved | Start | Assign | Review | Progress |
           Finish | ExternalObserved | Retry | Hold | SessionUpdated | EffectUpdated | Authorize |
           ExhaustAccount | ContextObserved | InboxQueued | InboxDone | Initialize | LegacyImported)


@dataclass(frozen=True)
class TaskView:
    task_id: str
    issue_id: str
    project_id: str
    repository: str
    identifier: str
    phase: Phase
    revision: int
    active: bool
    eligible: bool
    dependencies_ready: bool
    reason: str
    spec: TaskSpec | None
    attempt: dict = field(default_factory=dict)
    sessions: tuple[dict, ...] = ()
    canceled: bool = False

    def session(self, phase):
        return next((dict(s) for s in self.sessions if s["phase"] == phase), None)
