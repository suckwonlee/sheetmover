# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "sheet_mover" / "source.py"
FEATURES = ROOT / "sheet_mover" / "roll20_features.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다."
        )
    return text.replace(old, new, 1)


def patch_source(text: str) -> str:
    if "Unselected top-level feats are candidates, not owned feats." in text:
        return text

    old = """    if selected_feat_ids:
        active_feats = [
            entry
            for entry in raw_feats
            if str(
                _dict(_dict(entry).get("definition") or entry).get("id")
                or ""
            )
            in selected_feat_ids
        ]
    else:
        # Conservative compatibility fallback for older/alternate payloads that
        # do not expose choice/grant links.
        active_feats = raw_feats
"""

    new = """    if selected_feat_ids:
        active_feats = [
            entry
            for entry in raw_feats
            if str(
                _dict(_dict(entry).get("definition") or entry).get("id")
                or ""
            )
            in selected_feat_ids
        ]
    else:
        # Unselected top-level feats are candidates, not owned feats.
        # D&D Beyond can expose package/adventure helper feats (for example
        # Dark Bargain) in raw `feats` even when the character never selected
        # or received them. Fail closed: without an explicit choice/grant,
        # do not publish any top-level feat as an owned character feature.
        active_feats = []
"""
    return replace_once(text, old, new, "source.py feat fail-closed")


def patch_features(text: str) -> str:
    if "stage8-roll20-features-v1.7-row-integrity" in text:
        return text

    text = replace_once(
        text,
        'STAGE8_VERSION = "2026-10-06-stage8-roll20-features-v1.6-fighting-style-description"',
        'STAGE8_VERSION = "2026-10-06-stage8-roll20-features-v1.7-row-integrity"',
        "Stage 8 version",
    )

    old_policy = """            "stable_row_ids": True,
            "write_limited_use": False,
"""
    new_policy = """            "stable_row_ids": True,
            "sequential_attribute_writes": True,
            "remove_stale_sheetmover_rows": True,
            "write_limited_use": False,
"""
    text = replace_once(text, old_policy, new_policy, "Stage 8 policy")

    marker = "\ndef _trait_state(driver, target):\n"
    if marker not in text:
        raise RuntimeError("roll20_features.py의 _trait_state 위치를 찾지 못했습니다.")

    inserted = r'''
_OWNED_TRAIT_ROW_RE = re.compile(r"^-SM[0-9a-f]{17}$")


def _build_sequential_trait_upsert_script():
    script = UPSERT_ROW_SCRIPT

    jobs_marker = "    const jobs=[];\n\n"
    push_marker = """      jobs.push(
        existing.length===1
          ? saveExisting(existing[0],name,spec)
          : createNew(collection,name,spec)
      );
"""
    finish_marker = """    const completed=await Promise.all(jobs);
    log.push(...completed);
"""

    for label, needle in (
        ("jobs 선언", jobs_marker),
        ("jobs.push 블록", push_marker),
        ("Promise.all 블록", finish_marker),
    ):
        if needle not in script:
            raise RuntimeError(
                f"공용 Roll20 upsert 스크립트 구조가 변경되었습니다: {label}"
            )

    script = script.replace(jobs_marker, "", 1)
    script = script.replace(
        push_marker,
        """      log.push(
        existing.length===1
          ? await saveExisting(existing[0],name,spec)
          : await createNew(collection,name,spec)
      );
""",
        1,
    )
    script = script.replace(
        finish_marker,
        """    await fetchCollection(collection);
""",
        1,
    )
    return script


TRAIT_UPSERT_SEQUENTIAL_SCRIPT = _build_sequential_trait_upsert_script()


def _stale_sheetmover_row_ids(state, plan):
    existing = [
        _text(value)
        for value in _list(_dict(state).get("row_ids"))
        if _text(value)
    ]
    desired_or_known = {
        _text(value)
        for value in _list(_dict(plan).get("all_managed_row_ids"))
        if _text(value)
    }
    return [
        row_id
        for row_id in existing
        if _OWNED_TRAIT_ROW_RE.fullmatch(row_id)
        and row_id not in desired_or_known
    ]


'''
    text = text.replace(
        marker,
        "\n" + inserted + "def _trait_state(driver, target):\n",
        1,
    )

    text = replace_once(
        text,
        """        UPSERT_ROW_SCRIPT,
        _text(target.get("roll20_character_id")),
""",
        """        TRAIT_UPSERT_SEQUENTIAL_SCRIPT,
        _text(target.get("roll20_character_id")),
""",
        "_upsert_and_verify sequential writer",
    )

    start = text.find("def _delete_excluded_rows(driver, target, plan):\n")
    end = text.find(
        "\n\ndef _verify_excluded_absent(driver, target, plan):\n",
        start,
    )
    if start < 0 or end < 0:
        raise RuntimeError("Stage 8 삭제 helper 위치를 찾지 못했습니다.")

    delete_helpers = r"""def _delete_trait_row_ids(driver, target, row_ids, label):
    row_ids = list(dict.fromkeys(
        _text(row_id) for row_id in row_ids if _text(row_id)
    ))
    if not row_ids:
        return {"ok": True, "deleted_count": 0, "deleted": []}

    driver.set_script_timeout(max(45, len(row_ids) * 20))
    result = driver.execute_async_script(
        DELETE_TRAIT_ROWS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        row_ids,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            f"{label} 삭제 실패: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


def _delete_excluded_rows(driver, target, plan):
    return _delete_trait_row_ids(
        driver,
        target,
        [
            _text(row.get("row_id"))
            for row in _list(plan.get("excluded_rows"))
            if _text(row.get("row_id"))
        ],
        "제외 특성행",
    )
"""
    text = text[:start] + delete_helpers + text[end:]

    text = replace_once(
        text,
        """        "excluded_rows": plan["excluded_rows"],
        "desired_managed_order": plan["desired_managed_order"],
""",
        """        "excluded_rows": plan["excluded_rows"],
        "stale_owned_rows_before": [],
        "desired_managed_order": plan["desired_managed_order"],
""",
        "Stage 8 report stale field",
    )

    old = """        excluded_present = [
            row for row in plan["excluded_rows"]
            if row["row_id"] in existing_ids
        ]
        desired_reporder = _build_reporder(state_before, plan)
"""
    new = """        excluded_present = [
            row for row in plan["excluded_rows"]
            if row["row_id"] in existing_ids
        ]
        stale_owned_rows = _stale_sheetmover_row_ids(
            state_before,
            plan,
        )
        desired_reporder = _build_reporder(state_before, plan)
"""
    text = replace_once(text, old, new, "Stage 8 stale detection")

    text = replace_once(
        text,
        """        report["excluded_present_before"] = excluded_present
        report["desired_reporder"] = desired_reporder
""",
        """        report["excluded_present_before"] = excluded_present
        report["stale_owned_rows_before"] = stale_owned_rows
        report["desired_reporder"] = desired_reporder
""",
        "Stage 8 stale report",
    )

    old = """        if excluded_present:
            print(
                "[시트 이동기] 제외 특성행을 정리합니다: "
                + ", ".join(row["name"] for row in excluded_present),
                flush=True,
            )
            report["delete_result"] = _delete_excluded_rows(driver, target, plan)
            report["mutated"] = True
            _save_json(output_path, report)
"""
    new = """        delete_row_ids = [
            row["row_id"] for row in excluded_present
        ] + stale_owned_rows
        if delete_row_ids:
            labels = [row["name"] for row in excluded_present]
            labels.extend(stale_owned_rows)
            print(
                "[시트 이동기] 제외/이전 특성행을 정리합니다: "
                + ", ".join(labels),
                flush=True,
            )
            report["delete_result"] = _delete_trait_row_ids(
                driver,
                target,
                delete_row_ids,
                "제외/이전 특성행",
            )
            report["mutated"] = True
            _save_json(output_path, report)
"""
    text = replace_once(text, old, new, "Stage 8 stale delete block")

    old = """        if excluded_still_present:
            mismatches.append({
                "reason": "excluded_rows_still_present",
                "rows": excluded_still_present,
            })

        expected_order = final_reporder
"""
    new = """        if excluded_still_present:
            mismatches.append({
                "reason": "excluded_rows_still_present",
                "rows": excluded_still_present,
            })

        stale_still_present = _stale_sheetmover_row_ids(
            state_final,
            plan,
        )
        if stale_still_present:
            mismatches.append({
                "reason": "stale_sheetmover_rows_still_present",
                "row_ids": stale_still_present,
            })

        expected_order = final_reporder
"""
    text = replace_once(text, old, new, "Stage 8 stale final verification")

    text = replace_once(
        text,
        """            "excluded_rows_absent": not excluded_still_present,
            "final_reporder": final_order,
""",
        """            "excluded_rows_absent": not excluded_still_present,
            "stale_owned_rows_absent": not stale_still_present,
            "final_reporder": final_order,
""",
        "Stage 8 stale verification report",
    )

    return text


def backup(path: Path):
    backup_path = path.with_suffix(path.suffix + ".pre-stage8-v1.7.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    missing = [str(p) for p in (SOURCE, FEATURES) if not p.is_file()]
    if missing:
        raise RuntimeError(
            "필수 파일이 없습니다. ZIP을 E:\\sheet_mover 루트에 풀어주세요: "
            + ", ".join(missing)
        )

    source_old = SOURCE.read_text(encoding="utf-8")
    features_old = FEATURES.read_text(encoding="utf-8")

    source_new = patch_source(source_old)
    features_new = patch_features(features_old)

    if args.check:
        print("[시트 이동기] Stage 8 v1.7 무결성 패치 사전 점검 통과")
        print("- 선택/부여 근거 없는 top-level feat 차단 가능")
        print("- 특성 반복행 순차 저장 전환 가능")
        print("- 오래된 Sheet Mover 특성행 정리 가능")
        print("[시트 이동기] 파일은 아직 수정하지 않았습니다.")
        return 0

    backup(SOURCE)
    backup(FEATURES)
    SOURCE.write_text(source_new, encoding="utf-8")
    FEATURES.write_text(features_new, encoding="utf-8")

    print("[시트 이동기] Stage 8 v1.7 무결성 패치 적용 완료")
    print("- Dark Bargain 같은 미선택 후보 feat 차단")
    print("- repeating_traits 5개 필드를 순차 저장")
    print("- 현재 결과에 없는 예전 Sheet Mover 특성행만 정리")
    print("- 이름/설명 포함 서버 최종 재검증 유지")
    print()
    print("다음 명령:")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] Stage 8 v1.7 패치 실패: {exc}", file=sys.stderr)
        raise
