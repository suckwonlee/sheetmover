"""Stage 10B: Roll20 Legacy spell ATTACK output linkage.

This stage intentionally uses Roll20's own spell/attack field conventions but
creates the linked repeating_attack row directly and deterministically.
Reason: Sheet Mover persists attributes through Roll20's Backbone models; that
does not reliably fire the sheet-worker `change:...:spelloutput` event that
normally calls `create_attack_from_spell()`.

Only spells classified as Roll20 OUTPUT=ATTACK receive repeating_attack rows.
Generic actions remain absent from ATTACKS & SPELLCASTING.
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
    _save_json,
    _snapshot,
    _text,
    _verify,
)
from .roll20_spells import (
    ROLLCONTENT,
    build_spell_plan,
    spell_attribute_name,
)


STAGE10B_VERSION = "2026-10-06-stage10b-roll20-spell-attacks-v1"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

ATTACK_FLAG = "{{attack=1}}"
DMG1_FLAG = "{{damage=1}} {{dmg1flag=1}}"
SAVE_FLAG = (
    "{{save=1}} {{saveattr=@{saveattr}}} "
    "{{savedesc=@{saveeffect}}} "
    "{{savedc=[[[[@{savedc}]][SAVE]]]}}"
)

SPELL_ATTACK_FIELDS = (
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
    "hldmg",
    "spelllevel",
    "itemid",
    "spellid",
    "spell_innate",
    "atkbonus",
    "atkdmgtype",
    "rollbase",
    "rollbase_dmg",
    "rollbase_crit",
)

SPELL_COMBAT_FIELDS = (
    "spelloutput",
    "spellattack",
    "spelldamage",
    "spelldamagetype",
    "spelldamage2",
    "spelldamagetype2",
    "spellhealing",
    "spelldmgmod",
    "spellsave",
    "spellsavesuccess",
    "spellhldie",
    "spellhldietype",
    "spellhlbonus",
    "includedesc",
    "spell_damage_progression",
    "spellattackid",
    "rollcontent",
)


def spell_attack_row_id(source_key: str) -> str:
    source_key = _text(source_key)
    if not source_key:
        raise ValueError("spell source_key가 비어 있습니다.")
    digest = hashlib.sha1(
        f"spell-attack:{source_key}".encode("utf-8")
    ).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 spell attack row ID: {row_id}")
    return row_id


def _linked_rollcontent(character_id: str, attack_row_id: str) -> str:
    return (
        f"%{{{character_id}|"
        f"repeating_attack_{attack_row_id}_attack}}"
    )


def _attack_attr(row_id: str, field: str) -> str:
    if field not in SPELL_ATTACK_FIELDS:
        raise ValueError(f"허용되지 않은 spell attack 필드: {field}")
    return f"repeating_attack_{row_id}_{field}"


def _cantrip_damage_base(fields):
    damage = _text(_dict(fields).get("spelldamage"))
    progression = _text(_dict(fields).get("spell_damage_progression"))
    if progression == "Cantrip Dice" and len(damage) >= 2 and damage[0].isdigit():
        return "[[round((@{level} + 1) / 6 + 0.5)]]" + damage[1:]
    return damage


def _higher_level_damage(fields):
    fields = _dict(fields)
    count = _text(fields.get("spellhldie"))
    die_type = _text(fields.get("spellhldietype"))
    if not count or not die_type:
        return ""

    try:
        base_level = int(_text(fields.get("spelllevel")))
    except Exception:
        return ""

    query = "?{Cast at what level?"
    for i in range(0, 10 - base_level):
        query += f"|Level {base_level + i},{i}"
    query += "}"

    bonus = _text(fields.get("spellhlbonus"))
    bonus_part = f"+({bonus}*{query})" if bonus else ""
    return f"{{{{hldmg=[[({count}*{query}){die_type}{bonus_part}]]}}}}"


def _damage_display(fields):
    damage = _text(_dict(fields).get("spelldamage"))
    damage_type = _text(_dict(fields).get("spelldamagetype"))
    if not damage:
        return ""
    return f"{damage} {damage_type}".strip()


def _rollbase_for_spell(fields):
    fields = _dict(fields)
    has_attack = _text(fields.get("spellattack")) not in {"", "None"}
    has_damage = bool(_text(fields.get("spelldamage")))

    common_tail = (
        "@{saveflag} {{desc=@{atk_desc}}} @{hldmg} "
        "{{spelllevel=@{spelllevel}}} "
        "{{innate=@{spell_innate}}} "
        "@{charname_output} "
        "{{licensedsheet=@{licensedsheet}}}"
    )

    if has_attack:
        # Standard spell attack: spell ability + PB + spell attack modifier.
        return (
            "@{wtype}&{template:atkdmg} "
            "{{mod=@{atkbonus}}} "
            "{{rname=@{atkname}}} "
            "{{r1=[[@{d20}cs>@{atkcritrange}+@{spell_attack_bonus}]]}} "
            "@{rtype}cs>@{atkcritrange}+@{spell_attack_bonus}]]}} "
            "@{atkflag} {{range=@{atkrange}}} "
            "@{dmgflag} {{dmg1=[[@{dmgbase}]]}} "
            "{{dmg1type=@{dmgtype}}} "
            "{{crit1=[[@{dmgcustcrit}]]}} "
            + common_tail
        )

    if has_damage:
        return (
            "@{wtype}&{template:dmg} "
            "{{rname=@{atkname}}} "
            "@{atkflag} {{range=@{atkrange}}} "
            "@{dmgflag} {{dmg1=[[@{dmgbase}]]}} "
            "{{dmg1type=@{dmgtype}}} "
            + common_tail
        )

    return (
        "@{wtype}&{template:dmg} "
        "{{rname=@{atkname}}} "
        "@{atkflag} {{range=@{atkrange}}} "
        + common_tail
    )


def map_spell_attack_row(spell_row: dict[str, Any], attack_row_id: str):
    fields = _dict(spell_row.get("fields"))
    if _text(fields.get("spelloutput")) != "ATTACK":
        raise ValueError("ATTACK 출력 주문만 attack row로 변환할 수 있습니다.")

    spellattack = _text(fields.get("spellattack"))
    damage = _cantrip_damage_base(fields)
    damage_type = _text(fields.get("spelldamagetype"))
    save = _text(fields.get("spellsave"))
    save_effect = _text(fields.get("spellsavesuccess"))

    has_attack = spellattack not in {"", "None"}
    has_damage = bool(damage)

    attack_fields = {
        "options-flag": "0",
        "atkname": _text(fields.get("spellname")),
        "atkflag": ATTACK_FLAG if has_attack else "0",
        "atkattr_base": "spell",
        "atkmod": "",
        "atkprofflag": "(@{pb})",
        "atkmagic": "",
        "atkcritrange": "20",
        "atkrange": _text(fields.get("spellrange")),

        "dmgflag": DMG1_FLAG if has_damage else "0",
        "dmgbase": damage,
        "dmgattr": "spell" if _text(fields.get("spelldmgmod")) == "Yes" else "0",
        "dmgmod": "",
        "dmgtype": damage_type,
        "dmgcustcrit": damage if has_attack and has_damage else "",

        "dmg2flag": "0",
        "dmg2base": "",
        "dmg2attr": "0",
        "dmg2mod": "",
        "dmg2type": "",
        "dmg2custcrit": "",

        "saveflag": SAVE_FLAG if save else "0",
        "saveattr": save,
        "savedc": "(@{spell_save_dc})" if save else "",
        "saveflat": "",
        "saveeffect": save_effect,

        "ammo": "",
        "atk_desc": _text(fields.get("spelldescription")),
        "hldmg": _higher_level_damage(fields),
        "spelllevel": _text(fields.get("spelllevel")),
        "itemid": "",
        "spellid": _text(spell_row.get("row_id")),
        "spell_innate": _text(fields.get("innate")),

        "atkbonus": "@{spell_attack_bonus}" if has_attack else "-",
        "atkdmgtype": _damage_display(fields),
        "rollbase": _rollbase_for_spell(fields),
        "rollbase_dmg": "",
        "rollbase_crit": "",
    }

    return {
        "source_key": spell_row["source_key"],
        "spell_row_id": spell_row["row_id"],
        "section": spell_row["section"],
        "name": spell_row["name"],
        "attack_row_id": attack_row_id,
        "fields": attack_fields,
    }


def _attack_attributes(attack_row):
    row_id = _text(attack_row.get("attack_row_id"))
    fields = _dict(attack_row.get("fields"))
    return {
        _attack_attr(row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in SPELL_ATTACK_FIELDS
    }


def _spell_combat_attributes(spell_row, character_id, attack_row_id=None):
    fields = _dict(spell_row.get("fields"))
    section = spell_row["section"]
    row_id = spell_row["row_id"]
    output = _text(fields.get("spelloutput"))

    desired = {
        field: _text(fields.get(field))
        for field in SPELL_COMBAT_FIELDS
        if field not in {"spellattackid", "rollcontent"}
    }

    if output == "ATTACK":
        if not attack_row_id:
            raise ValueError("ATTACK 주문에는 attack_row_id가 필요합니다.")
        desired["spellattackid"] = attack_row_id
        desired["rollcontent"] = _linked_rollcontent(
            character_id,
            attack_row_id,
        )
    else:
        desired["spellattackid"] = ""
        desired["rollcontent"] = ROLLCONTENT

    return {
        spell_attribute_name(section, row_id, field): {
            "current": value,
            "max": "",
        }
        for field, value in desired.items()
    }


def build_spell_attack_plan(result_payload):
    spell_plan = build_spell_plan(result_payload)
    attack_rows = []

    for spell_row in spell_plan["rows"]:
        if _text(_dict(spell_row.get("fields")).get("spelloutput")) != "ATTACK":
            continue
        attack_row_id = spell_attack_row_id(spell_row["source_key"])
        attack_rows.append(
            map_spell_attack_row(spell_row, attack_row_id)
        )

    return {
        "version": STAGE10B_VERSION,
        "source_character_id": spell_plan["source_character_id"],
        "character_name": spell_plan["character_name"],
        "spell_rows": spell_plan["rows"],
        "attack_rows": attack_rows,
        "attack_count": len(attack_rows),
        "policy": {
            "preserve_manual_attacks": True,
            "generic_actions_in_attack_section": False,
            "use_roll20_attack_output_semantics": True,
            "deterministic_spell_attack_ids": True,
            "as_part_of_weapon_attack_stays_spellcard": True,
        },
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
            f"10B단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


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


def apply_spell_attacks(
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
    plan = build_spell_attack_plan(payload)
    source_id = plan["source_character_id"]
    target, target_path = _load_target(source_id)
    character_id = _text(target.get("roll20_character_id"))

    attack_by_source = {
        row["source_key"]: row
        for row in plan["attack_rows"]
    }

    all_attrs = {}
    for spell_row in plan["spell_rows"]:
        attack_row = attack_by_source.get(spell_row["source_key"])
        attack_id = attack_row["attack_row_id"] if attack_row else None
        all_attrs.update(
            _spell_combat_attributes(
                spell_row,
                character_id,
                attack_id,
            )
        )
        if attack_row:
            all_attrs.update(_attack_attributes(attack_row))

    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-spell-attacks-{source_id}.json"
    )

    report = {
        "version": STAGE10B_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": character_id,
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "attack_count": plan["attack_count"],
        "policy": plan["policy"],
        "spell_rows": plan["spell_rows"],
        "attack_rows": plan["attack_rows"],
        "backup_path": None,
        "mutated": False,
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
            / f"roll20-stage10b-spell-attacks-backup-"
              f"{source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE10B_VERSION,
                "source_character_id": source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": character_id,
                "read_only_snapshot_before_apply": True,
                "managed_attributes": before["attributes"],
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        # Create/update linked attack rows first.
        for index, attack_row in enumerate(plan["attack_rows"], start=1):
            print(
                f"[시트 이동기] 주문 공격 {index}/{plan['attack_count']}: "
                f"{attack_row['name']}",
                flush=True,
            )
            _upsert_and_verify(
                driver,
                target,
                _attack_attributes(attack_row),
                f"주문 공격 '{attack_row['name']}'",
            )
            report["mutated"] = True

        # Then update spell OUTPUT and link it to the deterministic attack row.
        for spell_row in plan["spell_rows"]:
            attack_row = attack_by_source.get(spell_row["source_key"])
            attack_id = attack_row["attack_row_id"] if attack_row else None
            _upsert_and_verify(
                driver,
                target,
                _spell_combat_attributes(
                    spell_row,
                    character_id,
                    attack_id,
                ),
                f"주문 출력 '{spell_row['name']}'",
            )
            report["mutated"] = True

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
                "10B 주문 공격 최종 서버 재검증 실패: "
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

    print("[시트 이동기] 10B단계: 주문 ATTACK 출력 연결")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 일반 행동 카드는 공격 영역에 추가하지 않습니다.")
    print("[시트 이동기] 무기 공격은 10A 결과를 그대로 유지합니다.")
    print("[시트 이동기] 무기 공격을 대신 수행하는 주문은 SPELLCARD로 유지합니다.")

    report, output = apply_spell_attacks(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] ATTACK 출력 주문: {report['attack_count']}개")
    for row in report["attack_rows"]:
        fields = row["fields"]
        parts = []
        if fields["saveattr"]:
            parts.append(f"{fields['saveattr']} 내성")
        if fields["dmgbase"]:
            parts.append(f"{fields['dmgbase']} {fields['dmgtype']}")
        print(f"  - {row['name']}: " + " / ".join(parts))

    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
