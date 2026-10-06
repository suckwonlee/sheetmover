"""Runtime/user settings for the distributable Sheet Mover UI.

No Google credential content is copied into the application.
The settings file stores only:
- Google Cloud project ID
- authentication mode
- optional path to the user's own service-account JSON
- Ollama host/model
- Roll20 CDP URL
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


APP_NAME = "SheetMover"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gpt-oss:20b"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"


def _roaming_root() -> Path:
    value = os.getenv("APPDATA")
    return Path(value) if value else Path.home() / ".config"


def _local_root() -> Path:
    value = os.getenv("LOCALAPPDATA")
    return Path(value) if value else Path.home() / ".local" / "share"


def config_dir() -> Path:
    return _roaming_root() / APP_NAME


def data_dir() -> Path:
    return _local_root() / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class AppSettings:
    google_project_id: str = ""
    google_auth_mode: str = "adc"
    google_credentials_file: str = ""
    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    roll20_cdp_url: str = DEFAULT_CDP_URL

    def normalized(self) -> "AppSettings":
        return AppSettings(
            google_project_id=str(self.google_project_id or "").strip(),
            google_auth_mode=(
                "service_account"
                if str(self.google_auth_mode or "").strip() == "service_account"
                else "adc"
            ),
            google_credentials_file=str(
                self.google_credentials_file or ""
            ).strip(),
            ollama_host=(
                str(self.ollama_host or DEFAULT_OLLAMA_HOST)
                .strip()
                .rstrip("/")
            ),
            ollama_model=str(
                self.ollama_model or DEFAULT_OLLAMA_MODEL
            ).strip(),
            roll20_cdp_url=(
                str(self.roll20_cdp_url or DEFAULT_CDP_URL)
                .strip()
                .rstrip("/")
            ),
        )


def load_settings(path: str | Path | None = None) -> AppSettings:
    path = Path(path) if path else settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return AppSettings()

    if not isinstance(payload, dict):
        return AppSettings()

    allowed = set(AppSettings.__dataclass_fields__)
    values = {key: payload.get(key) for key in allowed if key in payload}
    return AppSettings(**values).normalized()


def save_settings(
    settings: AppSettings,
    path: str | Path | None = None,
) -> Path:
    path = Path(path) if path else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = settings.normalized()
    path.write_text(
        json.dumps(asdict(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def apply_runtime_environment(settings: AppSettings) -> AppSettings:
    settings = settings.normalized()
    runtime_root = data_dir()
    cache_root = runtime_root / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    os.environ["SHEETMOVER_DATA_DIR"] = str(runtime_root)
    os.environ["SHEETMOVER_TRANSLATION_CACHE"] = str(
        cache_root / "google-translation-cache.json"
    )
    os.environ["SHEETMOVER_REVIEW_CACHE"] = str(
        cache_root / "ollama-review-cache.json"
    )

    os.environ["SHEETMOVER_GOOGLE_PROJECT"] = settings.google_project_id
    os.environ["SHEETMOVER_GOOGLE_AUTH_MODE"] = settings.google_auth_mode

    if (
        settings.google_auth_mode == "service_account"
        and settings.google_credentials_file
    ):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
            settings.google_credentials_file
        )
    else:
        # In this desktop UI, adc means the user's gcloud login.
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

    os.environ["SHEETMOVER_OLLAMA_HOST"] = settings.ollama_host
    os.environ["SHEETMOVER_REVIEW_MODEL"] = settings.ollama_model
    os.environ["ROLL20_CDP_URL"] = settings.roll20_cdp_url
    return settings


def validate_settings(settings: AppSettings) -> list[str]:
    settings = settings.normalized()
    problems = []
    if not settings.google_project_id:
        problems.append("Google Cloud 프로젝트 ID가 비어 있습니다.")
    if settings.google_auth_mode == "service_account":
        path = Path(settings.google_credentials_file)
        if not settings.google_credentials_file:
            problems.append("서비스 계정 JSON 파일을 선택하지 않았습니다.")
        elif not path.is_file():
            problems.append(f"서비스 계정 JSON 파일을 찾을 수 없습니다: {path}")
    if not settings.ollama_host:
        problems.append("Ollama 주소가 비어 있습니다.")
    if not settings.ollama_model:
        problems.append("Ollama 모델명이 비어 있습니다.")
    if not settings.roll20_cdp_url:
        problems.append("Roll20 CDP 주소가 비어 있습니다.")
    return problems


def google_credentials(settings: AppSettings):
    """Resolve the selected account without mutating process-wide settings."""
    import google.auth

    settings = settings.normalized()
    if settings.google_auth_mode == "service_account":
        if not settings.google_credentials_file:
            raise ValueError("서비스 계정 JSON 파일을 선택하지 않았습니다.")
        path = Path(settings.google_credentials_file)
    else:
        config = os.getenv("CLOUDSDK_CONFIG")
        root = Path(config) if config else (
            _roaming_root() / "gcloud" if os.name == "nt"
            else Path.home() / ".config" / "gcloud"
        )
        path = root / "application_default_credentials.json"
    credentials, _ = google.auth.load_credentials_from_file(
        str(path), scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return credentials


def runtime_google_credentials():
    """CLI without UI settings retains normal Google ADC discovery."""
    mode = os.getenv("SHEETMOVER_GOOGLE_AUTH_MODE")
    if not mode:
        return None
    return google_credentials(AppSettings(
        google_auth_mode=mode,
        google_credentials_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
    ))


def check_google(settings: AppSettings) -> dict:
    """Verify the user's own Google Cloud credentials and v3 API access."""
    settings = settings.normalized()
    if not settings.google_project_id:
        return {
            "ok": False,
            "message": "Google Cloud 프로젝트 ID를 입력하세요.",
        }

    try:
        import google.auth
        from google.auth.transport.requests import Request
        from google.cloud import translate_v3
    except ImportError as exc:
        return {
            "ok": False,
            "message": (
                "Google Cloud 라이브러리가 없습니다. 배포본이 아니라 "
                "소스 실행 중이라면 requirements.txt를 설치하세요."
            ),
            "detail": str(exc),
        }

    try:
        credentials = google_credentials(settings)

        if not getattr(credentials, "valid", False):
            request = Request()
            def refresh_request(*args, **kwargs):
                kwargs["timeout"] = 10
                return request(*args, **kwargs)
            credentials.refresh(refresh_request)

        client = translate_v3.TranslationServiceClient(
            credentials=credentials
        )
        parent = (
            f"projects/{settings.google_project_id}"
            "/locations/us-central1"
        )
        client.get_supported_languages(
            request={
                "parent": parent,
                "display_language_code": "ko",
            },
            timeout=10,
            retry=None,
        )
        return {
            "ok": True,
            "message": "Google Cloud Translation v3 인증 정상",
            "project_id": settings.google_project_id,
            "auth_mode": settings.google_auth_mode,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": "Google Cloud Translation v3 인증/권한 확인 실패",
            "detail": str(exc),
        }


def check_ollama(settings: AppSettings) -> dict:
    settings = settings.normalized()
    url = settings.ollama_host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "message": "Ollama 서버에 연결할 수 없습니다.",
            "detail": str(exc),
        }

    models = payload.get("models") if isinstance(payload, dict) else []
    names = {
        str((row or {}).get("model") or (row or {}).get("name") or "").strip()
        for row in (models or [])
        if isinstance(row, dict)
    }
    if settings.ollama_model not in names:
        return {
            "ok": False,
            "message": f"Ollama 모델 {settings.ollama_model}이 없습니다.",
            "installed_models": sorted(name for name in names if name),
        }

    return {
        "ok": True,
        "message": f"Ollama {settings.ollama_model} 준비 완료",
    }


def check_roll20(settings: AppSettings) -> dict:
    base = settings.normalized().roll20_cdp_url.rstrip("/")
    try:
        with urllib.request.urlopen(
            base + "/json/list",
            timeout=3,
        ) as response:
            pages = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "message": "Roll20 전용 Chrome 디버깅 포트에 연결할 수 없습니다.",
            "detail": str(exc),
        }

    matches = [
        row for row in (pages or [])
        if isinstance(row, dict)
        and row.get("type") == "page"
        and str(row.get("url") or "").startswith(
            "https://app.roll20.net/editor"
        )
    ]
    if not matches:
        return {
            "ok": False,
            "message": "전용 Chrome은 연결됐지만 열린 Roll20 게임 탭이 없습니다.",
        }
    return {
        "ok": True,
        "message": f"Roll20 게임 탭 {len(matches)}개 확인",
        "count": len(matches),
    }
