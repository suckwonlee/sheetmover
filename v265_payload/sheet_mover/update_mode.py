# -*- coding: utf-8 -*-
"""Safe comparison/seed support for updating a previously moved character."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any

UPDATE_MODE_VERSION = "2026-10-08-update-mode-v2.6.5"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _stable(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""


def _filename_stamp(path: Path) -> str:
    match = re.search(r"-(\d{8})-(\d{6})(?:\(\d+\))?\.json$", path.name, re.I)
    return "" if not match else match.group(1) + match.group(2)


def _candidate_key(path: Path):
    stamp = _filename_stamp(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (bool(stamp), stamp, mtime, path.name)


def find_previous_result(*, data_root: str | Path, source_id: str, character_name: str):
    """Return newest prior successful sheet-result for this exact character.

    Name equality is a required human-facing guard, while DDB source id is the
    authoritative identity guard. Same-name/different-id characters are never
    treated as update candidates.
    """
    root = Path(data_root)
    source_id = _text(source_id)
    character_name = _text(character_name)
    if not source_id or not character_name:
        return None, None

    candidates = []
    for folder in (root / "results" / "cache", root / "results" / "current"):
        if folder.is_dir():
            candidates.extend(folder.glob(f"sheet-result-{source_id}-*.json"))

    for path in sorted(set(candidates), key=_candidate_key, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        original = _dict(payload.get("original"))
        if _text(original.get("source_id")) != source_id:
            continue
        if _text(original.get("name")) != character_name:
            continue
        raw = payload.get("raw_source")
        if not isinstance(raw, dict):
            continue
        summary = _dict(payload.get("translation_summary"))
        if not summary:
            summary = _dict(_dict(payload.get("translated")).get("translation_summary"))
        if _text(summary.get("status")) not in {"complete", "partial"}:
            continue
        return payload, path
    return None, None


def _class_rows(raw):
    rows = {}
    for entry in _list(_dict(raw).get("classes")):
        entry = _dict(entry)
        definition = _dict(entry.get("definition"))
        subclass = _dict(entry.get("subclassDefinition"))
        class_id = _text(definition.get("id"))
        class_name = _text(definition.get("name")) or class_id or "unknown"
        subclass_id = _text(subclass.get("id"))
        subclass_name = _text(subclass.get("name"))
        key = (class_id or class_name.casefold(), subclass_id or subclass_name.casefold())
        rows[key] = {
            "class": class_name,
            "subclass": subclass_name,
            "level": int(entry.get("level") or 0),
        }
    return rows


def _total_level(raw):
    return sum(row["level"] for row in _class_rows(raw).values())


def _spell_rows(raw):
    out = {}

    def add(entries):
        for entry in _list(entries):
            entry = _dict(entry)
            definition = _dict(entry.get("definition"))
            name = _text(definition.get("name"))
            definition_id = _text(definition.get("id"))
            legacy = definition.get("isLegacy")
            level = definition.get("level")
            if not name:
                continue
            key = (
                definition_id or name.casefold(),
                legacy if isinstance(legacy, bool) else None,
            )
            out[key] = {"name": name, "level": level, "is_legacy": legacy}

    grouped = _dict(_dict(raw).get("spells"))
    for entries in grouped.values():
        add(entries)
    for group in _list(_dict(raw).get("classSpells")):
        add(_dict(group).get("spells"))
    return out


def _inventory_rows(raw):
    out = {}
    for entry in _list(_dict(raw).get("inventory")):
        entry = _dict(entry)
        definition = _dict(entry.get("definition"))
        name = _text(definition.get("name"))
        key = _text(entry.get("id")) or _text(definition.get("id")) or name.casefold()
        if not key:
            continue
        out[key] = {
            "name": name,
            "quantity": entry.get("quantity"),
            "equipped": entry.get("equipped"),
            "attuned": entry.get("isAttuned"),
        }
    return out


def _name_list(rows, keys):
    return sorted(_text(rows[key].get("name")) for key in keys if _text(rows[key].get("name")))


def compare_raw_sources(previous_raw: dict[str, Any], current_raw: dict[str, Any]) -> dict[str, Any]:
    previous_raw = _dict(previous_raw)
    current_raw = _dict(current_raw)

    previous_id = _text(previous_raw.get("id"))
    current_id = _text(current_raw.get("id"))
    previous_name = _text(previous_raw.get("name"))
    current_name = _text(current_raw.get("name"))

    if not previous_id or previous_id != current_id:
        raise RuntimeError("업데이트 비교 대상의 D&D Beyond 캐릭터 ID가 일치하지 않습니다.")
    if not previous_name or previous_name != current_name:
        raise RuntimeError("업데이트 비교 대상의 캐릭터 이름이 일치하지 않습니다.")

    old_classes = _class_rows(previous_raw)
    new_classes = _class_rows(current_raw)
    class_changes = []
    for key in sorted(set(old_classes) | set(new_classes), key=str):
        old = old_classes.get(key)
        new = new_classes.get(key)
        if old == new:
            continue
        class_changes.append({"before": old, "after": new})

    old_spells = _spell_rows(previous_raw)
    new_spells = _spell_rows(current_raw)
    added_spell_keys = sorted(set(new_spells) - set(old_spells), key=str)
    removed_spell_keys = sorted(set(old_spells) - set(new_spells), key=str)

    old_inventory = _inventory_rows(previous_raw)
    new_inventory = _inventory_rows(current_raw)
    added_item_keys = sorted(set(new_inventory) - set(old_inventory), key=str)
    removed_item_keys = sorted(set(old_inventory) - set(new_inventory), key=str)
    changed_item_keys = sorted(
        key for key in set(old_inventory) & set(new_inventory)
        if old_inventory[key] != new_inventory[key]
    )

    tracked_sections = {
        "race": (previous_raw.get("race"), current_raw.get("race")),
        "background": (previous_raw.get("background"), current_raw.get("background")),
        "classes": (previous_raw.get("classes"), current_raw.get("classes")),
        "inventory": (previous_raw.get("inventory"), current_raw.get("inventory")),
        "spells": (previous_raw.get("spells"), current_raw.get("spells")),
        "classSpells": (previous_raw.get("classSpells"), current_raw.get("classSpells")),
        "feats": (previous_raw.get("feats"), current_raw.get("feats")),
        "modifiers": (previous_raw.get("modifiers"), current_raw.get("modifiers")),
        "choices": (previous_raw.get("choices"), current_raw.get("choices")),
        "options": (previous_raw.get("options"), current_raw.get("options")),
        "actions": (previous_raw.get("actions"), current_raw.get("actions")),
        "stats": (
            {
                "stats": previous_raw.get("stats"),
                "bonusStats": previous_raw.get("bonusStats"),
                "overrideStats": previous_raw.get("overrideStats"),
            },
            {
                "stats": current_raw.get("stats"),
                "bonusStats": current_raw.get("bonusStats"),
                "overrideStats": current_raw.get("overrideStats"),
            },
        ),
        "hit_points": (
            [previous_raw.get("baseHitPoints"), previous_raw.get("bonusHitPoints"), previous_raw.get("removedHitPoints")],
            [current_raw.get("baseHitPoints"), current_raw.get("bonusHitPoints"), current_raw.get("removedHitPoints")],
        ),
        "currencies": (previous_raw.get("currencies"), current_raw.get("currencies")),
    }
    changed_sections = [name for name, (before, after) in tracked_sections.items() if _stable(before) != _stable(after)]

    old_level = _total_level(previous_raw)
    new_level = _total_level(current_raw)
    level_delta = new_level - old_level
    if not changed_sections:
        classification = "no_change"
    elif level_delta > 0:
        classification = "probable_level_up"
    elif level_delta < 0:
        classification = "level_decrease_or_rebuild"
    else:
        classification = "same_level_changes"

    return {
        "version": UPDATE_MODE_VERSION,
        "character_id": current_id,
        "character_name": current_name,
        "classification": classification,
        "probable_level_up": level_delta > 0,
        "previous_total_level": old_level,
        "current_total_level": new_level,
        "level_delta": level_delta,
        "class_changes": class_changes,
        "changed_sections": changed_sections,
        "added_spells": _name_list(new_spells, added_spell_keys),
        "removed_spells": _name_list(old_spells, removed_spell_keys),
        "added_items": _name_list(new_inventory, added_item_keys),
        "removed_items": _name_list(old_inventory, removed_item_keys),
        "changed_items": _name_list(new_inventory, changed_item_keys),
    }


def prepare_update_context(current_raw: dict[str, Any], *, data_root: str | Path) -> dict[str, Any]:
    current_raw = _dict(current_raw)
    source_id = _text(current_raw.get("id"))
    character_name = _text(current_raw.get("name"))
    previous, path = find_previous_result(
        data_root=data_root,
        source_id=source_id,
        character_name=character_name,
    )
    if previous is None or path is None:
        raise RuntimeError(
            "업데이트 모드를 선택했지만 같은 D&D Beyond 캐릭터의 이전 시트 이동 성공 결과를 찾지 못했습니다. "
            "이름만 같은 다른 캐릭터는 안전을 위해 자동 연결하지 않습니다."
        )

    comparison = compare_raw_sources(_dict(previous.get("raw_source")), current_raw)
    comparison["previous_result_path"] = str(path.resolve())
    comparison["translation_seed_enabled"] = True
    return comparison


def activate_previous_translation_seed(path: str | Path | None):
    """Make an explicitly verified previous result available to the translator.

    The worker is normally a fresh process, but update loaded modules too so
    tests/debug calls in one process cannot keep a stale module-level value.
    """
    value = str(Path(path).resolve()) if path else ""
    if value:
        os.environ["SHEETMOVER_SEMANTIC_SEED_RESULT"] = value
    else:
        os.environ.pop("SHEETMOVER_SEMANTIC_SEED_RESULT", None)

    for module_name in (
        "sheet_mover.translation_config",
        "sheet_mover.hybrid_translator",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "SEMANTIC_SEED_RESULT", value)
    return value
