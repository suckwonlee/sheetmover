"""Google Cloud Translation v3 glossary provisioning.

Unlike the old development helper, this version does not shell out to gcloud
for Cloud Storage operations. It uses the user's ADC/service-account
credentials through google-cloud-storage so the packaged application can
provision its glossary without borrowing developer credentials.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import re

from .translator import (
    GOOGLE_GLOSSARY_LOCATION,
    TranslationError,
    cloud_glossary_entries,
    glossary_id_for,
)


def _default_glossary_path() -> Path:
    return Path(__file__).resolve().parent.parent / "glossary.json"


def _load_glossary(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TranslationError(f"glossary.json을 읽지 못했습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise TranslationError("glossary.json 최상위가 객체가 아닙니다.")
    return {
        str(k): str(v)
        for k, v in payload.items()
        if isinstance(k, str) and isinstance(v, str)
    }


def _project_id(explicit=None) -> str:
    detected = (
        explicit
        or os.environ.get("SHEETMOVER_GOOGLE_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    if detected:
        return str(detected).strip()

    try:
        import google.auth
        _credentials, detected = google.auth.default()
    except Exception as exc:
        raise TranslationError(
            "Google Cloud 인증을 찾지 못했습니다. UI의 Google Cloud 설정에서 "
            "본인 계정 ADC 또는 서비스 계정 JSON을 설정하세요."
        ) from exc

    if not detected:
        raise TranslationError(
            "Google Cloud 프로젝트 ID를 찾지 못했습니다."
        )
    return str(detected).strip()


def _bucket_name(project_id: str) -> str:
    explicit = os.environ.get("SHEETMOVER_GOOGLE_GLOSSARY_BUCKET")
    if explicit:
        return explicit.removeprefix("gs://").strip("/")
    value = re.sub(
        r"[^a-z0-9._-]+",
        "-",
        project_id.casefold(),
    ).strip("-._")
    return f"{value}-sheetmover-glossary"


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
        if (
            "\n" in source
            or "\r" in source
            or "\n" in target
            or "\r" in target
        ):
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
    project_id = _project_id(project_id)
    glossary_path = (
        Path(glossary_path)
        if glossary_path
        else _default_glossary_path()
    )
    glossary = _load_glossary(glossary_path)
    glossary_id = os.environ.get(
        "SHEETMOVER_GOOGLE_GLOSSARY",
        glossary_id_for(glossary),
    )
    location = GOOGLE_GLOSSARY_LOCATION
    bucket_name = _bucket_name(project_id)
    object_name = f"glossaries/{glossary_id}.tsv"
    gcs_uri = f"gs://{bucket_name}/{object_name}"

    try:
        from google.cloud import storage
        from google.cloud import translate_v3
        from google.api_core.exceptions import NotFound
    except ImportError as exc:
        raise TranslationError(
            "Google Cloud Translation/Storage 라이브러리가 없습니다."
        ) from exc

    try:
        from .app_config import runtime_google_credentials
        storage_client = storage.Client(
            project=project_id, credentials=runtime_google_credentials()
        )
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            bucket = storage_client.create_bucket(
                bucket_name,
                project=project_id,
                location=location,
            )
        blob = bucket.blob(object_name)
        blob.upload_from_string(
            _tsv_bytes(glossary),
            content_type="text/tab-separated-values; charset=utf-8",
        )
    except Exception as exc:
        raise TranslationError(
            "Google 용어집 원본을 Cloud Storage에 준비하지 못했습니다. "
            "현재 사용자/서비스 계정에 해당 프로젝트의 Storage 권한이 "
            f"있는지 확인하세요: {exc}"
        ) from exc

    from .app_config import runtime_google_credentials
    client = translate_v3.TranslationServiceClient(
        credentials=runtime_google_credentials()
    )
    name = client.glossary_path(
        project_id,
        location,
        glossary_id,
    )
    parent = f"projects/{project_id}/locations/{location}"

    try:
        try:
            existing = client.get_glossary(
                request={"name": name}
            )
        except TypeError:
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
    except Exception as exc:
        class_name = exc.__class__.__name__.casefold()
        if (
            not isinstance(exc, NotFound)
            and "notfound" not in class_name
            and "not found" not in str(exc).casefold()
        ):
            raise TranslationError(
                f"Google 용어집 조회에 실패했습니다: {exc}"
            ) from exc

    language_pair = translate_v3.types.Glossary.LanguageCodePair(
        source_language_code="en",
        target_language_code="ko",
    )
    gcs_source = translate_v3.types.GcsSource(
        input_uri=gcs_uri
    )
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
    except TypeError:
        operation = client.create_glossary(
            parent=parent,
            glossary=glossary_message,
        )
    except Exception as exc:
        raise TranslationError(
            f"Google 용어집 생성을 시작하지 못했습니다: {exc}"
        ) from exc

    try:
        created = operation.result(timeout=timeout)
    except Exception as exc:
        raise TranslationError(
            f"Google 용어집 생성 완료를 기다리던 중 실패했습니다: {exc}"
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
