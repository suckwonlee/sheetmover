"""D&D Beyond preparation pipeline.

Stages 1-3 intentionally do not require Roll20 or a browser.
"""
import asyncio
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .source import fetch_character, normalize_character, source_character_id
from .translator import TranslationError, Translator

SOURCE_URL = "https://www.dndbeyond.com/characters/170892133"


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
        return asdict(self)


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
        try:
            translated = await asyncio.to_thread(
                Translator().translate_character,
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
            exc.partial_payload = {
                "source_url": self.source_url,
                "original": original.to_dict(),
                "translated": partial_translated,
                "warnings": list(original.warnings),
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
        warnings.append(
            "현재 1단계 미리보기입니다. Roll20 캐릭터 탐색과 입력은 아직 수행하지 않습니다."
        )
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
