"""Project-intake file upload: parsing a browser's multipart request body,
choosing a case-intake folder name, and writing the uploaded files plus a
placeholder case.yaml to disk.

Extracted out of server.py. `uploads_dir` and `data_root` travel as explicit
call-time parameters rather than being imported once at module load --
server.py's UPLOADS_DIR/DATA_ROOT are rebound at runtime by
configure_data_root() (e.g. for a non-default --data-root), and an import
captured at load time would silently go stale after that. The one real
caller (server.py's /api/upload handler) already has the current value on
hand at call time, so handing it in explicitly costs nothing and can never
go stale.
"""
from __future__ import annotations

import email
import email.policy
from pathlib import Path

import yaml

from pathnames import safe_component as _safe_component


def _parse_multipart(content_type: str, body: bytes, original_names: list | None = None,
                     fields: dict | None = None) -> list[tuple[str, bytes]]:
    """Parse browser multipart data without trusting client paths.

    `original_names` receives each file's client-sent base name, used only as a
    display label, never as a path. `fields` receives plain text form fields."""
    message = email.message_from_bytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body,
        policy=email.policy.default,
    )
    files = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            field = part.get_param("name", header="content-disposition")
            if fields is not None and field:
                # 瀏覽器的 FormData 文字欄位是 UTF-8，但不帶 charset。
                fields[str(field)] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            continue
        files.append((_safe_component(filename, "upload.bin"), part.get_payload(decode=True) or b""))
        if original_names is not None:
            original_names.append(Path(filename.replace("\\", "/")).name)
    return files


def _intake_name_from_files(names: list[str]) -> str:
    """rework-shell-and-project-home：研究者 2026-09-27 裁定建案不再填五欄，
    專案名稱取第一個上傳檔名（去副檔名）。"""
    first = str(names[0] if names else "")
    stem = first.rsplit(".", 1)[0] if "." in first.lstrip(".") else first
    return " ".join(stem.split()) or "case-intake"


def _unused_intake_name(name: str, *, uploads_dir: Path) -> str:
    """同名的上傳資料夾已存在時加 -2、-3……，不寫進、也不覆蓋既有專案。"""
    candidate, suffix = name, 1
    while (uploads_dir / _safe_component(candidate, "case-intake")).exists():
        suffix += 1
        candidate = f"{name}-{suffix}"
    return candidate


def _save_upload(intake_name: str, files: list[tuple[str, bytes]], *,
                 uploads_dir: Path, data_root: Path) -> dict:
    intake_id = _safe_component(intake_name, "case-intake")
    raw_dir = uploads_dir / intake_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, (name, payload) in enumerate(files, 1):
        safe = _safe_component(name, f"upload-{index}.bin")
        target = raw_dir / safe
        if target.resolve().parent != raw_dir.resolve():
            raise ValueError("上傳檔名不得離開案例目錄")
        if target.exists():
            target = raw_dir / f"{target.stem}-{index}{target.suffix}"
        target.write_bytes(payload)
        saved.append(str(target.relative_to(data_root)))
    return {"intake_id": intake_id, "raw_dir": str(raw_dir.relative_to(data_root)), "files": saved}


def _write_intake_case(saved: dict, *, label: str, uploads_dir: Path, data_root: Path) -> str:
    # 只寫名稱與歷程佔位欄位：服務情境、顧客、提供者、改善目標都不再由站①提供，
    # 也不用任何通用值代替（決定六增補；first-project-intake 規格）。
    case_path = uploads_dir / saved["intake_id"] / "case.yaml"
    case = {
        "case_id": f"upload-{saved['intake_id']}",
        "label": " ".join(str(label or "").split()),
        "journey_start": "待確認歷程",
        "journey_end": "待確認歷程",
        "external_partners": [],
        "steps": ["待確認歷程"],
    }
    case_path.write_text(yaml.dump(case, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(case_path.relative_to(data_root))
