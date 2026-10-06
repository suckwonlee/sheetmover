"""Stage 9 Roll20 Legacy OGL5e action-card writer.

Roll20 Legacy OGL5e has no separate repeating PC "Actions" section.
Non-attack actions are therefore represented in ``repeating_attack`` with
their attack/save/damage mechanics explicitly disabled unless Stage 9 can
safely model a non-attack effect (currently Second Wind healing).

Scope:
- imports Stage 3 ``roll20_payload.actions`` into ``repeating_attack``
- preserves unrelated/manual attack rows
- deterministically reuses Sheet Mover row IDs on rerun
- keeps attack rolls disabled (Stage 10 owns attacks)
- keeps resource/limited-use links disabled (Stage 12 owns resources)
- stores action activation + translated description
- models Second Wind as healing, not as an attack
- backs up all managed attributes before mutation
- verifies every imported row from persisted Roll20 attrs

Default behavior is APPLY. Use ``--dry-run`` for a read-only plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result
from .roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
    _select_roll20_tab,
)
from .roll20_inventory import (
    UPSERT_ROW_SCRIPT,
    _dict,
    _list,
    _plain_text,
    _save_json,
    _snapshot,
    _text,
    _verify,
)


STAGE9_VERSION = "2026-10-06-stage9-roll20-actions-v1"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

DMG_FLAG = "{{damage=1}} {{dmg1flag=1}}"

ACTION_FIELDS = (
    "options-flag",
    "atkname",
    "atkflag",
    "atkattr_base",
    "atkmod",
    "atkprofflag",
    "atkmagic",
    "atkcritrange",
    "atkrange",
    "dmgflag",
    "dmgbase",
    "dmgattr",
    "dmgmod",
    "dmgtype",
    "dmgcustcrit",
    "dmg2flag",
    "dmg2base",
    "dmg2attr",
    "dmg2mod",
    "dmg2type",
    "dmg2custcrit",
    "saveflag",
    "saveattr",
    "savedc",
    "saveflat",
    "saveeffect",
    "ammo",
    "atk_desc",
    "rollbase",
    "rollbase_dmg",
    "rollbase_crit",
    "hldmg",
    "spelllevel",
    "itemid",
    "spellid",
    "spell_innate",
    "atkbonus",
    "atkdmgtype",
)

_ACTION_ROLLBASE = (
    "@{wtype}&{template:dmg} "
    "{{rname=@{atkname}}} "
    "{{range=@{atkrange}}} "
    "{{desc=@{atk_desc}}} "
    "@{charname_output} "
    "{{licensedsheet=@{licensedsheet}}}"
)

_HEALING_ROLLBASE = (
    "@{wtype}&{template:dmg} "
    "{{rname=@{atkname}}} "
    "{{range=@{atkrange}}} "
    "@{dmgflag} "
    "{{dmg1=[[@{dmgbase}]]}} "
    "{{dmg1type=@{dmgtype}}} "
    "{{desc=@{atk_desc}}} "
    "@{charname_output} "
    "{{licensedsheet=@{licensedsheet}}}"
)


def action_row_id(source_key: str) -> str:
    source_key = _text(source_key)
    if not source_key:
        raise ValueError("action source_key가 비어 있습니다.")
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def _scalar(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    return ""


def format_activation(value) -> str:
    data = _dict(value)
    activation_type = data.get("activationType")
    activation_time = data.get("activationTime")

    if activation_type == 1:
        return "행동"
    if activation_type == 2:
        return "행동 없음"
    if activation_type == 3:
        return "추가 행동"
    if activation_type == 4:
        return "반응"
    if activation_type == 7:
        amount = _scalar(activation_time) or "1"
        return f"{amount}시간"
    if activation_type == 8:
        return "특수"

    if activation_type is None:
        return ""
    return f"기타({activation_type})"


def format_range(value) -> str:
    data = _dict(value)
    normal = data.get("range")
    long_range = data.get("longRange")
    minimum = data.get("minimumRange")
    aoe_type = _text(data.get("aoeType"))
    aoe_size = data.get("aoeSize")

    parts = []
    if normal not in (None, "") and long_range not in (None, ""):
        parts.append(f"{_scalar(normal)}/{_scalar(long_range)} ft.")
    elif normal not in (None, ""):
        parts.append(f"{_scalar(normal)} ft.")
    elif minimum not in (None, ""):
        parts.append(f"최소 {_scalar(minimum)} ft.")

    if aoe_type and aoe_size not in (None, ""):
        parts.append(f"{_scalar(aoe_size)} ft. {aoe_type}")

    return " / ".join(part for part in parts if part)


def _description_with_activation(item) -> str:
    description = _plain_text(item.get("description"))
    activation = format_activation(item.get("activation"))
    if activation and description:
        return f"사용: {activation}\n\n{description}"
    if activation:
        return f"사용: {activation}"
    return description


def _dice_string(item) -> str:
    dice = _dict(item.get("dice"))
    return _text(dice.get("diceString"))


def _is_safe_healing_action(item) -> bool:
    # Stage 3 does not yet expose a generic semantic healing flag for actions.
    # Avoid heuristics: only the exact well-known Second Wind action is modeled
    # as healing. Other dice-bearing non-attacks remain description cards.
    return _text(item.get("original_name")).casefold() == "second wind"


def map_action_row(item: dict[str, Any]) -> dict[str, Any]:
    item = _dict(item)
    source_key = _text(item.get("source_key"))
    row_id = action_row_id(source_key)

    name = _plain_text(item.get("name") or item.get("original_name"))
    if not name:
        raise ValueError(f"행동 이름이 비어 있습니다: {source_key}")

    description = _description_with_activation(item)
    action_range = format_range(item.get("range"))

    healing = _is_safe_healing_action(item)
    dice_string = _dice_string(item) if healing else ""
    healing = healing and bool(dice_string)

    fields = {
        "options-flag": "0",
        "atkname": name,

        # Stage 10 owns attack rolls. Explicit 0 values also prevent Roll20's
        # HTML defaults from turning a newly-created row into an attack.
        "atkflag": "0",
        "atkattr_base": "0",
        "atkmod": "",
        "atkprofflag": "0",
        "atkmagic": "",
        "atkcritrange": "20",
        "atkrange": action_range,

        # A non-attack healing roll is safe at Stage 9. Everything else is a
        # description-only action card until its owning later stage.
        "dmgflag": DMG_FLAG if healing else "0",
        "dmgbase": dice_string if healing else "",
        "dmgattr": "0",
        "dmgmod": "",
        "dmgtype": "Healing" if healing else "",
        "dmgcustcrit": "",

        "dmg2flag": "0",
        "dmg2base": "",
        "dmg2attr": "0",
        "dmg2mod": "",
        "dmg2type": "",
        "dmg2custcrit": "",

        # Stage 10 owns save/attack mechanics.
        "saveflag": "0",
        "saveattr": "",
        "savedc": "",
        "saveflat": "",
        "saveeffect": "",

        "ammo": "",
        "atk_desc": description,

        # Direct Backbone writes do not rely on a sheet-worker event firing.
        # Store a valid Roll20 roll template ourselves so the row is clickable
        # immediately after import.
        "rollbase": _HEALING_ROLLBASE if healing else _ACTION_ROLLBASE,
        "rollbase_dmg": "",
        "rollbase_crit": "",
        "hldmg": "",

        # No spell/item linkage at Stage 9.
        "spelllevel": "",
        "itemid": "",
        "spellid": "",
        "spell_innate": "",

        # Display-only computed fields. Keeping these explicit avoids waiting
        # for a sheetworker refresh after direct attribute creation.
        "atkbonus": "-",
        "atkdmgtype": (
            f"{dice_string} Healing"
            if healing
            else ""
        ),
    }

    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "kind": _text(item.get("kind")),
        "row_id": row_id,
        "name": name,
        "activation": format_activation(item.get("activation")),
        "range": action_range,
        "healing_roll": healing,
        "limited_use": item.get("limited_use"),
        "fields": fields,
    }


def action_attribute_name(row_id: str, field: str) -> str:
    if field not in ACTION_FIELDS:
        raise ValueError(f"허용되지 않은 action 필드: {field}")
    if not row_id or "_" in row_id:
        raise ValueError(f"잘못된 반복행 ID: {row_id}")
    return f"repeating_attack_{row_id}_{field}"


def _row_attributes(row):
    row_id = _text(row.get("row_id"))
    fields = _dict(row.get("fields"))
    return {
        action_attribute_name(row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in ACTION_FIELDS
    }


def plan_action_attributes(plan):
    attrs = {}
    for row in _list(plan.get("rows")):
        for name, spec in _row_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"Roll20 action attribute 이름 중복: {name}")
            attrs[name] = spec
    return attrs


def build_action_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    actions = _list(roll20_payload.get("actions"))
    source_character_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))

    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    rows = []
    seen_source_keys = set()
    seen_row_ids = set()

    for item in actions:
        row = map_action_row(item)
        if row["source_key"] in seen_source_keys:
            raise RuntimeError(f"행동 source_key 중복: {row['source_key']}")
        if row["row_id"] in seen_row_ids:
            raise RuntimeError(f"행동 row_id 충돌: {row['row_id']}")
        seen_source_keys.add(row["source_key"])
        seen_row_ids.add(row["row_id"])
        rows.append(row)

    return {
        "version": STAGE9_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "row_count": len(rows),
        "rows": rows,
        "policy": {
            "roll20_section": "repeating_attack",
            "preserve_unmanaged_rows": True,
            "delete_existing_rows": False,
            "stable_row_ids": True,
            "attack_rolls_enabled": False,
            "save_mechanics_enabled": False,
            "non_attack_healing_enabled": True,
            "resource_links_enabled": False,
            "collapse_rows": True,
        },
        "deferred": [
            "attacks",
            "skill_save_proficiency_expertise",
            "class_resources",
        ],
    }


def _load_target(source_id: str):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"9단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


def _is_complete(snapshot, attrs):
    _, mismatches = _verify(snapshot, attrs)
    return not mismatches


def _upsert_and_verify(driver, target, attrs, label):
    driver.set_script_timeout(45)
    outcome = driver.execute_async_script(
        UPSERT_ROW_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        attrs,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            f"{label} 저장 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )

    after = _snapshot(driver, target, attrs.keys())
    actual, mismatches = _verify(after, attrs)
    if mismatches:
        raise RuntimeError(
            f"{label} 서버 재검증 실패: "
            + json.dumps(mismatches, ensure_ascii=False)
        )
    return outcome, actual


def apply_actions(
    *,
    result_path: str | Path | None = None,
    source_id: str | None = None,
    cdp_url: str = DEFAULT_CDP_URL,
    dry_run: bool = False,
):
    source_id = _text(source_id)
    path = (
        Path(result_path)
        if result_path
        else latest_complete_result(source_id=source_id or None)
    )
    if path is None or not path.is_file():
        raise RuntimeError("사용할 정상 sheet-result JSON이 없습니다.")

    payload = load_result(path)
    plan = build_action_plan(payload)
    actual_source_id = plan["source_character_id"]
    target, target_path = _load_target(actual_source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    all_attrs = plan_action_attributes(plan)
    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-actions-{actual_source_id}.json"
    )

    report = {
        "version": STAGE9_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "row_count": plan["row_count"],
        "managed_attribute_count": len(all_attrs),
        "policy": plan["policy"],
        "rows": plan["rows"],
        "backup_path": None,
        "mutated": False,
        "row_results": [],
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)
        before = _snapshot(driver, target, all_attrs.keys())
        report["before"] = before["attributes"]

        pending_rows = []
        for row in plan["rows"]:
            attrs = _row_attributes(row)
            if not _is_complete(before, attrs):
                pending_rows.append(row)

        report["initial_pending_rows"] = [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "kind": row["kind"],
                "name": row["name"],
                "activation": row["activation"],
                "healing_roll": row["healing_roll"],
            }
            for row in pending_rows
        ]

        if dry_run:
            report["status"] = "pass"
            report["verification"] = {
                "status": "not_run",
                "reason": "dry_run",
            }
            _save_json(output_path, report)
            return report, output_path

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = (
            Path(CURRENT_RESULT_DIR)
            / f"roll20-stage9-actions-backup-"
              f"{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE9_VERSION,
                "source_character_id": actual_source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "managed_attributes": before["attributes"],
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        total = len(pending_rows)
        for index, row in enumerate(pending_rows, start=1):
            print(
                f"[시트 이동기] 행동 {index}/{total}: "
                f"{row['name']}"
                + (f" [{row['activation']}]" if row["activation"] else ""),
                flush=True,
            )
            attrs = _row_attributes(row)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                attrs,
                f"행동 '{row['name']}'",
            )
            report["mutated"] = True
            report["row_results"].append({
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "kind": row["kind"],
                "name": row["name"],
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            })
            _save_json(output_path, report)

        final_snapshot = _snapshot(driver, target, all_attrs.keys())
        actual, mismatches = _verify(final_snapshot, all_attrs)
        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "mismatches": mismatches,
            "server_fetch_status": final_snapshot.get("fetch_status"),
        }
        report["status"] = "pass" if not mismatches else "error"

        if mismatches:
            report["error"] = (
                "9단계 행동 최종 서버 재검증 실패: "
                + json.dumps(mismatches, ensure_ascii=False)
            )

        _save_json(output_path, report)

        if mismatches:
            raise RuntimeError(report["error"])

        return report, output_path

    except Exception as exc:
        if "error" not in report:
            report["status"] = "error"
            report["error"] = str(exc)
            _save_json(output_path, report)
        raise
    finally:
        _disconnect_driver(driver)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", help="사용할 sheet-result JSON")
    parser.add_argument("--source-id", default="170892133")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="읽기 전용 계획만 수행. 기본값은 실제 입력입니다.",
    )
    args = parser.parse_args()

    print("[시트 이동기] 9단계: Roll20 행동 카드 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] Roll20 Legacy의 ATTACKS & SPELLCASTING 영역을 사용합니다.")
    print("[시트 이동기] 모든 행동의 명중 굴림은 비활성화합니다.")
    print("[시트 이동기] 재기의 바람만 비공격 회복 굴림을 입력합니다.")
    print("[시트 이동기] 사용 횟수/자원 연결은 12단계까지 건드리지 않습니다.")
    print("[시트 이동기] 기존 수동 공격/행동 행은 삭제하지 않습니다.")

    report, output = apply_actions(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] D&D Beyond 행동: {report['row_count']}개")
    print(
        f"[시트 이동기] 최초 미완료 행동: "
        f"{len(report.get('initial_pending_rows') or [])}개"
    )
    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print("[시트 이동기] 실제 공격/기술·내성/클래스 자원은 수정하지 않았습니다.")
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
