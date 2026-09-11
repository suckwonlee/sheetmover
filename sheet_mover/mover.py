"""Read-only preparation. Roll20 writes require a separate, verified adapter."""
import asyncio
import os
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

from .source import normalize_character, source_character_id
from .translator import Translator

SOURCE_URL = "https://www.dndbeyond.com/characters/170892133"


def is_roll20_game(url):
    parsed = urlparse(url)
    return parsed.hostname == "app.roll20.net" and parsed.path.startswith("/editor")


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


class BrowserMover:
    def __init__(self, source_url=SOURCE_URL, cdp_url=None, on_progress=None):
        self.character_id = source_character_id(source_url)
        self.source_url = source_url
        self.cdp_url = cdp_url or os.getenv("ROLL20_CDP_URL", "http://127.0.0.1:9222")
        self.report = on_progress or (lambda percent, message: None)

    async def _connect(self):
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            await pw.stop()
            raise RuntimeError(f"브라우저 연결 실패: {self.cdp_url}") from exc
        return pw, browser

    async def read_source(self, page):
        # Capture the response of the user's authenticated browser.
        def matches(response):
            url = urlparse(response.url)
            return (url.hostname == "character-service.dndbeyond.com"
                    and url.path.rstrip("/").endswith(f"/character/{self.character_id}")
                    and response.request.method == "GET")
        try:
            async with page.expect_response(matches, timeout=60000) as pending:
                await page.goto(self.source_url, wait_until="domcontentloaded", timeout=60000)
            response = await pending.value
        except Exception as exc:
            raise RuntimeError(
                "캐릭터 데이터 응답을 읽지 못했습니다. 열린 D&D Beyond 탭에서 로그인·접근 권한을 "
                "확인한 뒤 다시 시도하세요. 응답 구조가 바뀐 경우 수집기 수정이 필요합니다."
            ) from exc
        if not response.ok:
            raise RuntimeError(f"D&D Beyond 응답 오류 ({response.status}). 시트 접근 권한을 확인하세요.")
        payload = await response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or str(data.get("id")) != self.character_id:
            raise RuntimeError("요청한 캐릭터 ID와 응답이 일치하지 않습니다.")
        return data

    async def prepare(self):
        self.report(5, "브라우저에 연결합니다.")
        pw, browser = await self._connect()
        try:
            pages = [p for c in browser.contexts for p in c.pages if is_roll20_game(p.url)]
            if not pages:
                raise RuntimeError("열려 있는 Roll20 캠페인 탭(/editor)을 찾지 못했습니다.")
            tabs = [{"title": await p.title(), "url": p.url} for p in pages]
            self.report(15, "D&D Beyond 원본 데이터를 수집합니다.")
            source_page = await pages[0].context.new_page()
            # Leave failed pages open for login/challenge handling by the user.
            raw = await self.read_source(source_page)
            await source_page.close()
            original = normalize_character(raw)
            self.report(35, f"원본 확인: {original.name}. 텍스트 번역을 시작합니다.")
            translated = await asyncio.to_thread(
                Translator().translate_character, original.to_dict(),
                lambda current, total: self.report(35 + 60 * current / max(total, 1), f"번역 {current}/{total}"),
            )
            warnings = list(original.warnings)
            warnings.append("미리보기입니다. 실제 시트와 수치를 대조해야 하며 Roll20에는 아직 입력하지 않았습니다.")
            if len(tabs) > 1:
                warnings.append("Roll20 캠페인 탭이 여러 개입니다. 입력 단계에서는 대상 캠페인 선택이 필요합니다.")
            self.report(100, "미리보기 준비 완료. Roll20 입력은 수행하지 않았습니다.")
            return PreparationResult(self.source_url, original.to_dict(), translated, warnings, tabs, raw)
        finally:
            await pw.stop()

    async def move(self):
        raise NotImplementedError("Roll20 실제 입력은 아직 구현되지 않았습니다. 먼저 미리보기를 준비하세요.")


def run(source_url=SOURCE_URL, cdp_url=None, on_progress=None):
    return asyncio.run(BrowserMover(source_url, cdp_url, on_progress).prepare())
