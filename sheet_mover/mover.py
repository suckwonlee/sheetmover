"""D&D Beyond preparation pipeline.

Stages 1-3 intentionally do not require Roll20 or a browser.
"""
import asyncio
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .source import fetch_character, normalize_character, source_character_id
from .translator import TranslationError, Translator

SOURCE_URL = "https://www.dndbeyond.com/characters/170892133"
TRANSLATION_FALLBACK_WARNING_PREFIX = (
    "번역 서비스가 해당 설명 조각을 안정적으로 반환하지 못해 원문으로 유지했습니다"
)


def translation_summary(translated):
    """Return a stable complete/partial translation summary for CLI/reporting."""
    if not isinstance(translated, dict):
        return {
            "status": "complete",
            "original_preserved_count": 0,
            "original_preserved": [],
        }

    embedded = translated.get("translation_summary")
    if isinstance(embedded, dict):
        preserved = embedded.get("original_preserved")
        if isinstance(preserved, list):
            preserved = [
                item for item in preserved if isinstance(item, dict)
            ]
            count = len(preserved)
            return {
                "status": "partial" if count else "complete",
                "original_preserved_count": count,
                "original_preserved": preserved,
            }

    # Backward compatibility for results produced before machine-readable
    # translation_summary existed.  These warnings already carry the reason
    # and a source preview, so derive a concise summary without losing data.
    preserved = []
    warnings = translated.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if not isinstance(warning, str):
                continue
            if not warning.startswith(TRANSLATION_FALLBACK_WARNING_PREFIX):
                continue
            reason = ""
            preview = ""
            if " (" in warning and "): " in warning:
                _, tail = warning.split(" (", 1)
                reason, preview = tail.split("): ", 1)
            else:
                preview = warning
            preserved.append(
                {
                    "reason": reason,
                    "source_preview": preview,
                }
            )

    return {
        "status": "partial" if preserved else "complete",
        "original_preserved_count": len(preserved),
        "original_preserved": preserved,
    }


def is_roll20_game(url):
    parsed = urlparse(url)
    return (
        parsed.hostname == "app.roll20.net"
        and parsed.path.startswith("/editor")
    )


@dataclass
class PreparationResult:
    source_url: str
    original: dict
    translated: dict
    warnings: list[str]
    roll20_tabs: list[dict]
    raw_source: dict
    applied: bool = False

    @property
    def name(self):
        return self.original["name"]

    def to_dict(self):
        payload = asdict(self)
        payload["translation_summary"] = translation_summary(self.translated)
        return payload


class SheetMover:
    def __init__(
        self,
        source_url=SOURCE_URL,
        cdp_url=None,
        on_progress=None,
    ):
        self.character_id = source_character_id(source_url)
        self.source_url = source_url
        self.cdp_url = cdp_url  # stage 4부터 사용
        self.report = on_progress or (lambda percent, message: None)

    async def prepare(self):
        self.report(5, "D&D Beyond 링크를 확인합니다.")
        self.report(15, "D&D Beyond 원본 데이터를 수집합니다.")

        raw = await asyncio.to_thread(
            fetch_character,
            self.source_url,
        )
        original = normalize_character(raw)

        self.report(
            35,
            f"원본 확인: {original.name}. 텍스트 번역을 시작합니다.",
        )
        translator = Translator()
        try:
            translated = await asyncio.to_thread(
                translator.translate_character,
                original.to_dict(),
                lambda current, total: self.report(
                    35 + 60 * current / max(total, 1),
                    f"번역 {current}/{total} (배치 처리)",
                ),
            )
        except TranslationError as exc:
            partial_translated = getattr(
                exc,
                "partial_translated",
                original.to_dict(),
            )
            partial_warnings = list(original.warnings)
            for warning in translator.warnings:
                if warning not in partial_warnings:
                    partial_warnings.append(warning)
            if isinstance(partial_translated, dict):
                partial_translated.setdefault("warnings", [])
                for warning in translator.warnings:
                    if warning not in partial_translated["warnings"]:
                        partial_translated["warnings"].append(warning)
                partial_translated["translation_summary"] = (
                    translator.translation_summary()
                )

            exc.partial_payload = {
                "source_url": self.source_url,
                "translation_fingerprint": getattr(
                    exc, "translation_fingerprint", None
                ),
                "original": original.to_dict(),
                "translated": partial_translated,
                "translation_summary": translator.translation_summary(),
                "warnings": partial_warnings,
                "roll20_tabs": [],
                "raw_source": raw,
                "applied": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "failed_text": getattr(exc, "failed_text", ""),
                    "progress_index": getattr(exc, "progress_index", None),
                    "progress_total": getattr(exc, "progress_total", None),
                },
            }
            raise

        warnings = list(original.warnings)
        translated_warnings = translated.get("warnings")
        if isinstance(translated_warnings, list):
            for warning in translated_warnings:
                if isinstance(warning, str) and warning not in warnings:
                    warnings.append(warning)

        warnings.append(
            "현재 1단계 미리보기입니다. Roll20 캐릭터 탐색과 입력은 아직 수행하지 않습니다."
        )

        summary = translation_summary(translated)
        if summary["status"] == "partial":
            self.report(
                100,
                "D&D Beyond 수집·번역 부분 완료: "
                f"{original.name}. 원문 유지 {summary['original_preserved_count']}개. "
                "Roll20에는 입력하지 않았습니다.",
            )
        else:
            self.report(
                100,
                f"D&D Beyond 수집·번역 완료: {original.name}. Roll20에는 입력하지 않았습니다.",
            )

        return PreparationResult(
            self.source_url,
            original.to_dict(),
            translated,
            warnings,
            [],
            raw,
        )

    async def move(self):
        raise NotImplementedError(
            "Roll20 실제 입력은 아직 구현되지 않았습니다."
        )


BrowserMover = SheetMover


def run(source_url=SOURCE_URL, cdp_url=None, on_progress=None):
    return asyncio.run(
        SheetMover(
            source_url,
            cdp_url,
            on_progress,
        ).prepare()
    )
