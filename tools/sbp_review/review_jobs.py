"""The single process-wide "what AI job is running right now" record.

Extracted out of server.py: nearly every domain (journey, persona, as_is,
to_be, jtbd_coding, ...) reads or writes this one shared job state, so it
had to move to a neutral module before server.py could be split into
domain modules -- otherwise every domain module would end up importing
from server.py itself.

This module deliberately does not import anything from server.py. Two
pieces of behavior it needs (resolving the active data root, and turning a
failed job's log into a researcher-facing cause) still live in server.py as
domain/business logic, not infrastructure -- so server.py hands them in via
`configure()` once at startup instead of this module reaching back to ask
for them. Until `configure()` runs, a finished-job diagnostic is simply
skipped rather than raising (diagnostics must never change a job's outcome,
the same rule the original `_JobState._finished_since` already followed for
its own internal `except Exception`).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from sbp_gen import stages as sbp_stages
from sbp_review import diagnostics as sbp_diagnostics

AS_IS_GENERATION_OPERATION_KIND = "generation"
FRESH_CODING_OPERATION_KIND = "fresh_coding"
PERSONA_CONTENT_GENERATION_OPERATION_KIND = "persona_content_generation"
HMW_FORMATION_OPERATION_KIND = "hmw_formation"
JOURNEY_GENERATION_OPERATION_KIND = "journey_generation"
JOURNEY_SOLUTIONS_OPERATION_KIND = "journey_solutions"
AS_IS_BLUEPRINT_OPERATION_KIND = "as_is_blueprint_generation"
CHART_GUIDE_OPERATION_KIND = "chart_guide_drafting"
SELECTIVE_THEMES_OPERATION_KIND = "selective_themes_generation"
JTBD_CANDIDATES_OPERATION_KIND = "jtbd_candidates_generation"
IMPLEMENTATION_RESEARCH_OPERATION_KIND = "implementation_research"

_ACTIVE_JOB_STATES = frozenset({"running", "waiting_for_model"})
_FINISHED_JOB_STATES = frozenset({"done", "failed", "cancelled"})
_RETRY_MARKER = sbp_diagnostics.retry_marker(sbp_stages.RETRY_NOTICE)

# Resolvers server.py hands in via configure() -- see module docstring.
_data_root_resolver: Callable[[], Any] | None = None
_failure_cause_resolver: Callable[[list[str] | None], dict] | None = None


def configure(*, data_root_resolver: Callable[[], Any],
             failure_cause_resolver: Callable[[list[str] | None], dict]) -> None:
    """Called once by server.py at startup, after both resolvers exist as
    real functions there. Not required for _job/_job_lock themselves to
    work -- only for the finished-job diagnostic line to be written."""
    global _data_root_resolver, _failure_cause_resolver
    _data_root_resolver = data_root_resolver
    _failure_cause_resolver = failure_cause_resolver


class _JobState(dict):
    """The process-wide job record. A move from a running state to done, failed or
    cancelled leaves one allowlisted line in the data root's diagnostics folder
    (desktop-runtime-foundation). Callers keep using it as a plain dict."""

    def __setitem__(self, key, value):
        before = self.get("state") if key == "state" else None
        super().__setitem__(key, value)
        if key == "state":
            self._finished_since(before)

    def update(self, *args, **kwargs):
        before = self.get("state")
        super().update(*args, **kwargs)
        self._finished_since(before)

    def _finished_since(self, before) -> None:
        if before not in _ACTIVE_JOB_STATES or self.get("state") not in _FINISHED_JOB_STATES:
            return
        # A job that never went through _replace_job has no start time; leave no record.
        if "started_monotonic" not in self:
            return
        if _data_root_resolver is None or _failure_cause_resolver is None:
            return  # configure() has not run yet -- nothing to record against.
        try:
            event = sbp_diagnostics.build_job_event(
                dict(self), now_monotonic=time.monotonic(), retry=_RETRY_MARKER,
                cause_of=_failure_cause_resolver,
            )
            sbp_diagnostics.record(event, _data_root_resolver())
        except Exception:  # noqa: BLE001 - diagnostics must never change a job's outcome
            pass


_job: dict = _JobState({
    "kind": None,
    "state": "idle",
    "phase": None,
    "log": [],
    "out": None,
    "output_identity": None,
    "result_endpoint": None,
})
_job_lock = threading.Lock()

# merge-persona-matrix-selection-into-one-screen: a third job kind joins the
# existing two -- the "which job is currently running" phrase used in a 409
# refusal needs to name any of the three, not just pick between two with a
# ternary (the previous shape, now widened to a mapping).
JOB_KIND_ACTIVE_PHRASES = {
    FRESH_CODING_OPERATION_KIND: "編碼與顧客任務候選產生",
    "journey_recheck": "歷程資料檢查",
    AS_IS_GENERATION_OPERATION_KIND: "服務藍圖產生",
    PERSONA_CONTENT_GENERATION_OPERATION_KIND: "persona 產生",
    HMW_FORMATION_OPERATION_KIND: "HMW 形成",
    JOURNEY_GENERATION_OPERATION_KIND: "顧客旅程建立",
    JOURNEY_SOLUTIONS_OPERATION_KIND: "解方發展",
    AS_IS_BLUEPRINT_OPERATION_KIND: "服務藍圖產生",
    CHART_GUIDE_OPERATION_KIND: "導讀起草",
    SELECTIVE_THEMES_OPERATION_KIND: "選擇編碼形成",
    JTBD_CANDIDATES_OPERATION_KIND: "顧客任務候選形成",
    "to_be_generation": "改善方案形成",
    "as_is_research": "外部調研",
    IMPLEMENTATION_RESEARCH_OPERATION_KIND: "落地研究",
}


def _job_kind_active_phrase(kind: str | None) -> str:
    return JOB_KIND_ACTIVE_PHRASES.get(kind, JOB_KIND_ACTIVE_PHRASES[AS_IS_GENERATION_OPERATION_KIND])
