"""HMW (How Might We) formation: the background job that turns confirmed
JTBD/axial-coding output into HMW candidates, plus its job-status
projection and cancellation hooks.

Extracted out of server.py. runs_dir/safe_component travel as explicit
parameters (see review_execution.py's _axial_coding_run_dir) since
server.py's RUNS_DIR is rebound at runtime by configure_data_root().
server.py's own job-status/cancel functions call into this module by
HMW_FORMATION_OPERATION_KIND, alongside every other domain's equivalent --
those dispatcher functions stay in server.py since they are not
HMW-specific themselves.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from sbp_gen import execution as sbp_execution
from sbp_gen import hmw_formation as sbp_hmw_formation
from sbp_review.review_execution import (
    TRANSPORT_SUSPENDED_EXIT, _axial_coding_run_dir, _job_progress, _researcher_readable_log, _stream_stage,
    _with_progress,
)
from sbp_review.review_jobs import _job, _job_lock


def _hmw_formation_job_view(job: dict, *, runs_dir: Path, safe_component: Callable) -> dict:
    try:
        run_dir = _axial_coding_run_dir(job.get("out"), runs_dir=runs_dir, safe_component=safe_component)
    except Exception:
        return job
    formation = sbp_hmw_formation.formation_state(run_dir)
    labels = sbp_hmw_formation.STAGE_LABELS
    active = [step for step in (formation.get("active_stages") or [formation.get("active_stage")]) if step in labels]
    if job.get("state") == "running" and active:
        # Steps that run side by side are named together, each with the count
        # it last reported (e.g. 屬性與面向 3／16 類).
        progress = _job_progress(job.get("log"))
        job["phase"] = "正在整理" + "、".join(_with_progress(labels[step], labels[step], progress) for step in active)
    elif job.get("state") == "failed":
        failed = formation.get("failed_stage")
        job["phase"] = f"{labels[failed]}沒有完成" if failed in labels else "形成 HMW 沒有完成"
        job["failure"] = formation.get("failure")
    job["log"] = _researcher_readable_log(job.get("log"))
    return job


def _mark_hmw_formation_cancelled(out: object, *, runs_dir: Path, safe_component: Callable) -> None:
    try:
        run_dir = _axial_coding_run_dir(out, runs_dir=runs_dir, safe_component=safe_component)
    except Exception:
        return
    sbp_hmw_formation.update_state(run_dir, state="cancelled", active_stage=None)


def _run_hmw_formation_cli(run_dir: Path, execution: sbp_execution.ExecutionConfig) -> None:
    cmd = [sys.executable, "-m", "sbp_gen", "form-hmw", "--run", str(run_dir)]
    try:
        code = _stream_stage(cmd, "形成 HMW", execution)
    except OSError as exc:
        with _job_lock:
            _job["state"] = "failed"
            _job["phase"] = f"形成 HMW 沒有跑起來：{exc}"
        return
    with _job_lock:
        _job["proc"] = None
        if _job.get("state") == "cancelled":
            return
        if code == TRANSPORT_SUSPENDED_EXIT:
            _job["state"] = "waiting_for_model"
            _job["phase"] = "已經把提示詞準備好，等模型回覆"
        elif code == 0:
            _job["state"] = "done"
            _job["phase"] = "HMW 候選已形成"
        else:
            _job["state"] = "failed"
