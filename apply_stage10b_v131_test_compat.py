# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
TARGET = ROOT / "tests" / "test_stage10b_rollcontent_dispatch.py"

UPDATED_TEST = r"""import unittest
from unittest.mock import patch

from sheet_mover.roll20_spell_attacks import (
    _poll_persisted,
    _split_link_attrs,
)


class Stage10BRollcontentDispatchTests(unittest.TestCase):
    def test_link_fields_are_split_from_normal_spell_fields(self):
        attrs = {
            "repeating_spell-2_-SMabc_spelloutput": {
                "current": "ATTACK",
                "max": "",
            },
            "repeating_spell-2_-SMabc_spellattackid": {
                "current": "-SMattack",
                "max": "",
            },
            "repeating_spell-2_-SMabc_rollcontent": {
                "current": "%{-CHAR|repeating_attack_-SMattack_attack}",
                "max": "",
            },
        }

        normal, links = _split_link_attrs(attrs)

        self.assertEqual(len(normal), 1)
        self.assertEqual(len(links), 2)
        self.assertIn("repeating_spell-2_-SMabc_spelloutput", normal)
        self.assertIn("repeating_spell-2_-SMabc_spellattackid", links)
        self.assertIn("repeating_spell-2_-SMabc_rollcontent", links)

    def test_poll_waits_until_server_value_matches(self):
        name = "repeating_spell-2_-SMabc_rollcontent"
        value = "%{-CHAR|repeating_attack_-SMattack_attack}"
        attrs = {name: {"current": value, "max": ""}}

        missing = {"attributes": {name: []}}
        present = {
            "attributes": {
                name: [{"id": "-ATTR", "current": value, "max": ""}]
            }
        }

        with patch(
            "sheet_mover.roll20_spell_attacks._snapshot",
            side_effect=[missing, missing, present],
        ), patch(
            "sheet_mover.roll20_spell_attacks.time.sleep"
        ):
            actual, attempts = _poll_persisted(
                object(),
                {},
                attrs,
                "테스트",
                attempts=4,
                delay=0.01,
            )

        self.assertEqual(attempts, 3)
        self.assertEqual(actual[name]["current"], value)


if __name__ == "__main__":
    unittest.main()
"""


def backup(path: Path):
    dst = path.with_suffix(path.suffix + ".pre-stage10b-v1.3.1.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not TARGET.is_file():
        raise RuntimeError(
            f"대상 테스트 파일이 없습니다: {TARGET}"
        )

    current = TARGET.read_text(encoding="utf-8")

    if "_split_rollcontent_attrs" not in current:
        if "_split_link_attrs" in current:
            print("[시트 이동기] Stage 10B v1.3.1 테스트 보정은 이미 적용되어 있습니다.")
            return 0
        raise RuntimeError(
            "기존 v1.2 테스트 형식을 찾지 못했습니다. 현재 테스트 파일 상태가 예상과 다릅니다."
        )

    if args.check:
        print("[시트 이동기] Stage 10B v1.3.1 테스트 보정 사전 점검 통과")
        print("- 오래된 _split_rollcontent_attrs import를 제거 가능")
        print("- v1.3의 _split_link_attrs 기준으로 회귀 테스트 갱신 가능")
        print("- 실제 Roll20 writer 코드는 수정하지 않습니다.")
        return 0

    backup(TARGET)
    TARGET.write_text(UPDATED_TEST, encoding="utf-8")

    print("[시트 이동기] Stage 10B v1.3.1 테스트 보정 완료")
    print("- core writer 변경 없음")
    print("- v1.2 잔여 테스트만 v1.3 API에 맞게 갱신")
    print()
    print("다음:")
    print("python -m unittest discover -s tests -v")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 10B v1.3.1 테스트 보정 실패: {exc}", file=sys.stderr)
        raise
