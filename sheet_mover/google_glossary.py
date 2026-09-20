"""One-time Google Cloud glossary provisioning for Sheet Mover."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .translator import (
    GOOGLE_GLOSSARY_LOCATION,
    TranslationError,
    cloud_glossary_entries,
    glossary_id_for,
)


def _default_glossary_path() -> Path:
    return Path(__file__).resolve().parent.parent / "glossary.json"


def _load_glossary(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise TranslationError(f"번역 사전 파일이 없습니다: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in payload.items()
    ):
        raise TranslationError(
            "번역 사전은 영문: 한글 문자열 쌍으로 구성해야 합니다."
        )
    return payload


def _project_id(explicit=None) -> str:
    if explicit:
        return explicit
    env_value = (
        os.environ.get("SHEETMOVER_GOOGLE_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    if env_value:
        return env_value
    try:
        import google.auth

        _credentials, detected = google.auth.default()
    except Exception as exc:
        raise TranslationError(
            "Google Cloud 인증을 찾지 못했습니다. "
            "`gcloud auth application-default login`을 실행하세요."
        ) from exc
    if not detected:
        raise TranslationError(
            "Google Cloud 프로젝트 ID를 찾지 못했습니다. "
            "SHEETMOVER_GOOGLE_PROJECT를 지정하세요."
        )
    return detected


def _bucket_name(project_id: str) -> str:
    explicit = os.environ.get("SHEETMOVER_GOOGLE_GLOSSARY_BUCKET")
    if explicit:
        return explicit.removeprefix("gs://").strip("/")
    value = re.sub(r"[^a-z0-9._-]+", "-", project_id.casefold()).strip("-._")
    return f"{value}-sheetmover-glossary"


def _run_gcloud(args, *, allow_failure=False):
    executable = shutil.which("gcloud")
    if not executable:
        raise TranslationError(
            "gcloud CLI를 찾지 못했습니다. Google Cloud CLI 설치 상태를 확인하세요."
        )
    completed = subprocess.run(
        [executable, *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode and not allow_failure:
        detail = (completed.stderr or completed.stdout).strip()
        raise TranslationError(
            "gcloud 명령 실행에 실패했습니다: " + detail
        )
    return completed


def _tsv_bytes(glossary: dict[str, str]) -> bytes:
    filtered = cloud_glossary_entries(glossary)
    if not filtered:
        raise TranslationError("Google Cloud용 안전 용어가 하나도 없습니다.")

    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter="\t",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    for source, target in sorted(
        filtered.items(),
        key=lambda item: (item[0].casefold(), item[0]),
    ):
        if "\n" in source or "\r" in source or "\n" in target or "\r" in target:
            raise TranslationError(
                f"Google 용어집 항목에는 줄바꿈을 넣을 수 없습니다: {source!r}"
            )
        writer.writerow([source, target])
    return output.getvalue().encode("utf-8")


def setup_google_glossary(
    glossary_path=None,
    project_id=None,
    timeout=240,
):
    """Upload the current safe glossary subset and create the v3 glossary."""
    project_id = _project_id(project_id)
    glossary_path = Path(glossary_path) if glossary_path else _default_glossary_path()
    glossary = _load_glossary(glossary_path)
    glossary_id = os.environ.get(
        "SHEETMOVER_GOOGLE_GLOSSARY",
        glossary_id_for(glossary),
    )
    location = GOOGLE_GLOSSARY_LOCATION
    bucket = _bucket_name(project_id)
    object_name = f"glossaries/{glossary_id}.tsv"
    gcs_uri = f"gs://{bucket}/{object_name}"

    # Keep the bucket creation explicit but automated. Project IDs are globally
    # unique, so this default bucket name is normally unique as well.
    described = _run_gcloud(
        ["storage", "buckets", "describe", f"gs://{bucket}", "--project", project_id],
        allow_failure=True,
    )
    if described.returncode != 0:
        _run_gcloud(
            [
                "storage",
                "buckets",
                "create",
                f"gs://{bucket}",
                "--project",
                project_id,
                f"--location={location}",
                "--uniform-bucket-level-access",
            ]
        )

    with tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".tsv",
        delete=False,
    ) as temp:
        temp.write(_tsv_bytes(glossary))
        temp_path = Path(temp.name)

    try:
        _run_gcloud(
            [
                "storage",
                "cp",
                str(temp_path),
                gcs_uri,
                "--project",
                project_id,
            ]
        )
    finally:
        temp_path.unlink(missing_ok=True)

    try:
        from google.api_core.exceptions import NotFound
        from google.cloud import translate_v3
    except ImportError as exc:
        raise TranslationError(
            "Google Cloud Translation 라이브러리가 없습니다. "
            "`python -m pip install -U google-cloud-translate`를 실행하세요."
        ) from exc

    client = translate_v3.TranslationServiceClient()
    name = client.glossary_path(project_id, location, glossary_id)
    parent = f"projects/{project_id}/locations/{location}"

    try:
        existing = client.get_glossary(request={"name": name})
        return {
            "project": project_id,
            "location": location,
            "glossary_id": glossary_id,
            "glossary_name": existing.name,
            "entry_count": existing.entry_count,
            "input_uri": gcs_uri,
            "created": False,
        }
    except NotFound:
        pass
    except Exception as exc:
        # Some old client versions do not expose request= on get_glossary.
        if exc.__class__.__name__.casefold() == "notfound":
            pass
        elif isinstance(exc, TypeError):
            try:
                existing = client.get_glossary(name=name)
                return {
                    "project": project_id,
                    "location": location,
                    "glossary_id": glossary_id,
                    "glossary_name": existing.name,
                    "entry_count": existing.entry_count,
                    "input_uri": gcs_uri,
                    "created": False,
                }
            except Exception as inner:
                if inner.__class__.__name__.casefold() != "notfound":
                    raise TranslationError(
                        f"Google 용어집 조회에 실패했습니다: {inner}"
                    ) from inner
        else:
            raise TranslationError(
                f"Google 용어집 조회에 실패했습니다: {exc}"
            ) from exc

    language_pair = translate_v3.types.Glossary.LanguageCodePair(
        source_language_code="en",
        target_language_code="ko",
    )
    gcs_source = translate_v3.types.GcsSource(input_uri=gcs_uri)
    input_config = translate_v3.types.GlossaryInputConfig(
        gcs_source=gcs_source
    )
    glossary_message = translate_v3.types.Glossary(
        name=name,
        language_pair=language_pair,
        input_config=input_config,
        display_name="Sheet Mover D&D EN-KO",
    )

    try:
        operation = client.create_glossary(
            request={
                "parent": parent,
                "glossary": glossary_message,
            }
        )
        created = operation.result(timeout=timeout)
    except TypeError:
        operation = client.create_glossary(
            parent=parent,
            glossary=glossary_message,
        )
        created = operation.result(timeout=timeout)
    except Exception as exc:
        raise TranslationError(
            f"Google 용어집 생성에 실패했습니다: {exc}"
        ) from exc

    return {
        "project": project_id,
        "location": location,
        "glossary_id": glossary_id,
        "glossary_name": created.name,
        "entry_count": created.entry_count,
        "input_uri": gcs_uri,
        "created": True,
    }
