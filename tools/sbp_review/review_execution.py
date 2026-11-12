"""Shared CLI-job execution and reporting helpers.

Every domain's "run this AI stage as a background job" code (as_is, to_be,
journey, persona, hmw, jtbd_coding, ...) streams a subprocess through the
same pattern: spawn it under the shared job lock, append its output to the
shared job log, scrub any BYOK key out of that log, and resolve which run
directory a job's "out" field refers to. None of that is domain-specific,
so it had to be extracted before any individual domain's job-running
functions could move out of server.py on their own -- otherwise each
domain module would end up reaching back into server.py for it.

_job/_job_lock are imported directly from review_jobs (not passed as
parameters) because, unlike server.py's DATA_ROOT family, they are never
reassigned at runtime -- always the same dict/lock, only ever mutated in
place. REPO is likewise computed locally rather than imported, since it is
derived once from this file's own location and server.py's is identical
(both live in tools/sbp_review/). RUNS_DIR *is* rebound at runtime by
configure_data_root(), so _axial_coding_run_dir takes it as an explicit
call-time parameter instead -- same reasoning as review_upload.py's
uploads_dir.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from sbp_gen import execution as sbp_execution
from sbp_review import child_processes
from sbp_review.review_jobs import _job, _job_lock

REPO = Path(__file__).resolve().parent.parent.parent

_PROGRESS_LINE_RE = re.compile(r"\[進度\] (\S+) (\d+)/(\d+) (\S+)")

# The manual-transport "suspended, waiting for the operator to relay a
# prompt" exit code -- distinct from a normal 0 (done) or non-zero (failed).
TRANSPORT_SUSPENDED_EXIT = 3


class ReviewRunNotFound(Exception):
    pass


def _job_progress(log: object) -> dict[str, tuple[int, int, str]]:
    """Latest `[進度] <label> <done>/<total> <unit>` per label in a job log."""
    progress: dict[str, tuple[int, int, str]] = {}
    for line in log if isinstance(log, list) else []:
        match = _PROGRESS_LINE_RE.search(str(line))
        if match:
            progress[match.group(1)] = (int(match.group(2)), int(match.group(3)), match.group(4))
    return progress


def _with_progress(text: str, label: str, progress: dict) -> str:
    """「正在整理開放編碼」 + 6/15 份 -> 「正在整理開放編碼（6／15 份）」."""
    if label not in progress:
        return text
    done, total, unit = progress[label]
    return f"{text}（{done}／{total} {unit}）"


def _require_execution(execution: object) -> sbp_execution.ExecutionConfig:
    """Fail closed when a worker is started without a validated adapter.

    Workers never fall back to transport (or any other mode) on their own;
    manual/debug callers must pass ``ExecutionConfig(mode="transport")``.
    """
    if not isinstance(execution, sbp_execution.ExecutionConfig):
        raise sbp_execution.ExecutionConfigError("這個工作沒有指定執行方式。")
    return execution


def _redact_execution_text(value: object, config: sbp_execution.ExecutionConfig | None = None) -> str:
    """Keep a request-scoped BYOK out of in-memory job logs and projections."""
    text = str(value)
    key = config.api_key if config is not None else None
    return text.replace(key, "[redacted]") if key else text


def _researcher_readable_log(lines: list[str]) -> list[str]:
    """把命令列的原始輸出濾成研究者讀得懂的幾行。

    手動傳輸暫停時會印一段給終端機操作者看的指引——打開哪個檔案、存成哪個檔案、
    再跑哪一道指令——那三行對研究者沒有意義，而且帶著絕對路徑與檔名，正是
    CLAUDE.md 明列不得出現在畫面上的東西。這裡整段濾掉，只留真正講「發生了什麼」
    的那幾行，並把殘留的路徑收斂成檔案自己的名字。
    """
    root = str(REPO) + "/"
    out = []
    for line in lines or []:
        stripped = line.strip()
        if stripped.startswith(("$ ", "1. ", "2. ", "3. ", "python3 -m sbp_gen")):
            continue
        if "把它整份交給" in stripped or "SBP_TRANSPORT_MODEL" in stripped:
            continue
        # 「[手動傳輸] <內部階段名> 第 N 輪：提示詞已存好」——這件事狀態列已經
        # 用人話講過了，這行只多帶一個研究者不需要知道的內部階段名。
        if stripped.startswith("[手動傳輸]"):
            continue
        # Progress counts already appear beside the processing message.
        if stripped.startswith("[進度]"):
            continue
        cleaned = stripped.replace(root, "")
        cleaned = re.sub(r"[^\s]*/([\w.-]+\.(?:yaml|md|txt|json))", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned:
            out.append(cleaned)
    return out


def _axial_coding_run_dir(run_name: object, *, runs_dir: Path, safe_component) -> Path:
    safe_name = safe_component(str(run_name or ""), "")
    if not safe_name:
        raise ReviewRunNotFound(f"找不到 run: {run_name}")
    run_dir = runs_dir / safe_name
    if not run_dir.is_dir():
        raise ReviewRunNotFound(f"找不到 run: {run_name}")
    return run_dir


def _stream_stage(cmd: list[str], plain_name: str, execution: sbp_execution.ExecutionConfig | None = None) -> int:
    execution = _require_execution(execution)
    env = execution.child_environment()
    if execution.mode == "transport":
        env["SBP_TRANSPORT_MODEL"] = "claude-opus-5"
    with _job_lock:
        _job["phase"] = f"正在跑{plain_name}"
        _job["log"].append(f"$ {' '.join(cmd[2:])}")
    proc = child_processes.spawn(cmd, cwd=REPO / "tools", env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    with _job_lock:
        _job["proc"] = proc
    for line in proc.stdout:
        with _job_lock:
            _job["log"].append(_redact_execution_text(line.rstrip(), execution))
    return proc.wait()
