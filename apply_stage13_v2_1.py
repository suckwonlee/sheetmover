# -*- coding: utf-8 -*-

"""Stage 13 v2.1 resilience patch.

Base: commit 520c24783d87a31eda8750426f4a3604dfbf1747

Fixes:
1) Retry transient D&D Beyond 403/429/5xx responses before failing.
2) A successful translation run with exact-original fallbacks is usable by the
   one-click Roll20 pipeline. Fatal TranslationError still stops before Roll20.
3) Keep the translation summary in the full-run report.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path.cwd()
EXPECTED_HEAD = "520c24783d87a31eda8750426f4a3604dfbf1747"

SOURCE = ROOT / "sheet_mover" / "source.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
TEST_PREP = ROOT / "tests" / "test_preparation.py"
TEST_FULL = ROOT / "tests" / "test_full_run.py"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 마커 1개가 필요하지만 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def git_head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except Exception:
        return ""


def patch_source(text):
    if "retry_delays=(1.0, 2.5)" in text:
        return text

    text = replace_once(
        text,
        "from copy import deepcopy\nimport re\n",
        "from copy import deepcopy\nimport re\nimport time\n",
        "source.py time import",
    )

    text = replace_once(
        text,
        "def fetch_character(source_url, timeout=20, opener=None):\n",
        "def fetch_character(\n"
        "    source_url,\n"
        "    timeout=20,\n"
        "    opener=None,\n"
        "    retry_delays=(1.0, 2.5),\n"
        "):\n",
        "fetch_character signature",
    )

    old = """    open_request = opener or urlopen
    try:
        with open_request(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise RuntimeError(
                "D&D Beyond 캐릭터 데이터를 가져올 수 없습니다. "
                "캐릭터가 링크로 조회 가능한 상태인지 확인하고 다시 시도하세요. "
                f"(HTTP {exc.code})"
            ) from exc
        raise RuntimeError(
            f"D&D Beyond 캐릭터 서비스가 HTTP {exc.code} 오류를 반환했습니다."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            "D&D Beyond에 연결하지 못했습니다. 인터넷 연결을 확인하세요."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "D&D Beyond 응답 시간이 초과되었습니다."
        ) from exc
"""

    new = """    open_request = opener or urlopen
    delays = tuple(retry_delays or ())
    retry_index = 0

    while True:
        try:
            with open_request(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                body = response.read()
            break
        except HTTPError as exc:
            # D&D Beyond can occasionally answer a valid public character with
            # a transient 403. Retry transient statuses only; real 401/404
            # still fail immediately.
            retryable = exc.code in {403, 429, 500, 502, 503, 504}
            if retryable and retry_index < len(delays):
                delay = max(0.0, float(delays[retry_index]))
                retry_index += 1
                if delay:
                    time.sleep(delay)
                continue

            if exc.code in {401, 403, 404}:
                raise RuntimeError(
                    "D&D Beyond 캐릭터 데이터를 가져올 수 없습니다. "
                    "캐릭터가 링크로 조회 가능한 상태인지 확인하고 다시 시도하세요. "
                    f"(HTTP {exc.code})"
                ) from exc
            raise RuntimeError(
                f"D&D Beyond 캐릭터 서비스가 HTTP {exc.code} 오류를 반환했습니다."
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                "D&D Beyond에 연결하지 못했습니다. 인터넷 연결을 확인하세요."
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "D&D Beyond 응답 시간이 초과되었습니다."
            ) from exc
"""
    return replace_once(text, old, new, "fetch_character retry block")


def patch_full_run(text):
    if "stage13-full-run-v2.1-soft-fallback" in text:
        return text

    text = replace_once(
        text,
        'FULL_RUN_VERSION = "2026-10-06-stage13-full-run-v2"',
        'FULL_RUN_VERSION = "2026-10-06-stage13-full-run-v2.1-soft-fallback"',
        "full_run version",
    )

    old = """        summary = payload.get("translation_summary") or {}
        if summary.get("status") != "complete":
            raise RuntimeError("번역이 미완성이므로 Roll20 입력을 중단합니다. "
                               f"원문 유지 {summary.get('original_preserved_count', 0)}개")
        result_path = default_result_path(source_id, root=data_dir())
        _write_json(result_path, payload)
        state["paths"]["sheet_result"] = str(result_path.resolve())
        stage(1, "pass", f"결과 저장: {result_path.name}")
        overall(30, "D&D Beyond 준비 완료")
"""

    new = """        summary = payload.get("translation_summary") or {}
        translation_status = str(summary.get("status") or "").strip()
        preserved_count = int(summary.get("original_preserved_count") or 0)
        state["translation_summary"] = summary

        # translate_character() returning normally means unsafe fragments were
        # already replaced by their exact English source text. That is a safe
        # soft fallback, not corrupted data. A real TranslationError still
        # follows the exception path and stops before Roll20 mutation.
        if translation_status not in {"complete", "partial"}:
            raise RuntimeError(
                "번역 결과 상태를 확인할 수 없어 Roll20 입력을 중단합니다. "
                f"상태={translation_status or '없음'}"
            )

        result_path = default_result_path(source_id, root=data_dir())
        _write_json(result_path, payload)
        state["paths"]["sheet_result"] = str(result_path.resolve())

        if translation_status == "partial":
            stage(
                1,
                "pass",
                f"원문 {preserved_count}개를 안전하게 유지하고 계속 진행합니다. "
                f"결과 저장: {result_path.name}",
            )
            overall(
                30,
                f"D&D Beyond 준비 완료 · 원문 유지 {preserved_count}개",
            )
        else:
            stage(1, "pass", f"결과 저장: {result_path.name}")
            overall(30, "D&D Beyond 준비 완료")
"""
    return replace_once(text, old, new, "full_run partial policy block")


def patch_test_preparation(text):
    if "test_fetch_character_retries_transient_403" in text:
        return text

    text = replace_once(
        text,
        "import unittest\n",
        "import unittest\nfrom urllib.error import HTTPError\n",
        "test_preparation HTTPError import",
    )

    marker = """    def test_fetch_character_rejects_wrong_response_id(self):
"""
    test = """    def test_fetch_character_retries_transient_403(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"id": 170892133, "name": "견본"}}
                ).encode("utf-8")

        calls = {"count": 0}

        def opener(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise HTTPError(
                    request.full_url,
                    403,
                    "Forbidden",
                    hdrs=None,
                    fp=None,
                )
            return FakeResponse()

        data = fetch_character(
            "https://www.dndbeyond.com/characters/170892133",
            opener=opener,
            retry_delays=(0,),
        )
        self.assertEqual(data["name"], "견본")
        self.assertEqual(calls["count"], 2)

"""
    return replace_once(
        text,
        marker,
        test + marker,
        "test_preparation retry insertion",
    )


def patch_test_full(text):
    if "test_partial_return_continues_with_exact_original_fallback" in text:
        return text

    old = """    def test_partial_return_is_saved_before_any_roll20_input(self):
        self.payload["translation_summary"] = {"status": "partial", "original_preserved_count": 1}
        with self.assertRaises(RuntimeError):
            self.run_move()
        self.target.assert_not_called()
        self.assertEqual(self.report()["stage_statuses"]["1"], "error")
        partial = Path(self.report()["paths"]["partial_result"])
        self.assertEqual(json.loads(partial.read_text(encoding="utf-8"))["translated"], self.payload["translated"])
        self.assertIsNone(latest_complete_result(root=self.root))
        self.assertFalse(any(e["type"] == "complete" for e in self.events))
"""

    new = """    def test_partial_return_continues_with_exact_original_fallback(self):
        self.payload["translation_summary"] = {
            "status": "partial",
            "original_preserved_count": 1,
            "original_preserved": [
                {
                    "reason": "unsafe translation; exact source preserved",
                    "source_preview": "The original rule text",
                }
            ],
        }
        result = self.run_move()
        report = self.report()

        self.assertEqual(result["status"], "pass")
        self.assertEqual(report["stage_statuses"]["1"], "pass")
        self.assertEqual(
            report["translation_summary"]["original_preserved_count"],
            1,
        )
        self.target.assert_called_once()
        self.assertTrue(all(writer.called for writer in self.writers))
        self.assertTrue(any(e["type"] == "complete" for e in self.events))

        sheet_result = Path(report["paths"]["sheet_result"])
        saved = json.loads(sheet_result.read_text(encoding="utf-8"))
        self.assertEqual(saved["translated"], self.payload["translated"])
        self.assertEqual(saved["translation_summary"]["status"], "partial")

        # A partial translation is still not advertised as a fully translated
        # standalone result. The one-click run can use it because it passes the
        # exact result_path directly to every Roll20 writer.
        self.assertIsNone(latest_complete_result(root=self.root))
"""
    return replace_once(text, old, new, "test_full_run partial policy")


def backup(path):
    backup_path = path.with_suffix(path.suffix + ".stage13-v2.1.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="HEAD 검사만 건너뜁니다. 소스 마커 검사는 계속 수행합니다.",
    )
    args = parser.parse_args()

    required = [SOURCE, FULL_RUN, TEST_PREP, TEST_FULL]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "필수 파일이 없습니다. ZIP을 E:\\sheet_mover 루트에 풀었는지 확인하세요: "
            + ", ".join(missing)
        )

    head = git_head()
    if not args.force and head and head != EXPECTED_HEAD:
        raise RuntimeError(
            f"이 패치는 커밋 {EXPECTED_HEAD[:12]} 기준입니다. "
            f"현재 HEAD={head[:12]}. 새 커밋이 있다면 먼저 알려주세요."
        )

    source_text = SOURCE.read_text(encoding="utf-8")
    full_text = FULL_RUN.read_text(encoding="utf-8")
    prep_text = TEST_PREP.read_text(encoding="utf-8")
    full_test_text = TEST_FULL.read_text(encoding="utf-8")

    new_source = patch_source(source_text)
    new_full = patch_full_run(full_text)
    new_prep = patch_test_preparation(prep_text)
    new_full_test = patch_test_full(full_test_text)

    if args.check:
        print("[시트 이동기] Stage 13 v2.1 패치 사전 점검 통과")
        print(f"[시트 이동기] 기준 커밋: {EXPECTED_HEAD}")
        print("[시트 이동기] 아직 파일은 수정하지 않았습니다.")
        return 0

    for path in required:
        backup(path)

    SOURCE.write_text(new_source, encoding="utf-8")
    FULL_RUN.write_text(new_full, encoding="utf-8")
    TEST_PREP.write_text(new_prep, encoding="utf-8")
    TEST_FULL.write_text(new_full_test, encoding="utf-8")

    print("[시트 이동기] Stage 13 v2.1 패치 적용 완료")
    print("- D&D Beyond 일시적 403/429/5xx 재시도")
    print("- 안전한 원문 fallback은 원클릭 실행을 중단하지 않음")
    print("- 실제 TranslationError는 기존처럼 Roll20 입력 전에 중단")
    print("- full-run 보고서에 translation_summary 유지")
    print()
    print("다음 명령:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 13 v2.1 패치 실패: {exc}", file=sys.stderr)
        raise
