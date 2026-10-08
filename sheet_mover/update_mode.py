# -*- coding: utf-8 -*-
"""Safe support for updating a character previously moved by Sheet Mover.

The update mode has two independent jobs:
1) compare the current D&D Beyond raw payload with the newest prior payload for
   the *same DDB character id and same character name*;
2) prime the current translator with exact, previously verified source->target
   pairs so unchanged text is reused while new/changed text follows the normal
   Google/Ollama path.

This module deliberately does not patch translator internals or loosen any
existing cache-only behavior.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

UPDATE_MODE_VERSION = "2026-10-08-update-mode-v2.6.5.1"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _stable(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return ""


def _stable_sha256(value):
    stable = _stable(value)
    return sha256(stable.encode("utf-8")).hexdigest() if stable else ""


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


def _translation_status(payload):
    summary = _dict(_dict(payload).get("translation_summary"))
    if not summary:
        summary = _dict(_dict(_dict(payload).get("translated")).get("translation_summary"))
    return _text(summary.get("status"))


def _valid_previous_result(payload, source_id, character_name):
    if not isinstance(payload, dict):
        return False
    original = _dict(payload.get("original"))
    if _text(original.get("source_id")) != _text(source_id):
        return False
    if _text(original.get("name")) != _text(character_name):
        return False
    if not isinstance(payload.get("raw_source"), dict):
        return False
    if not isinstance(payload.get("translated"), dict):
        return False
    if _translation_status(payload) not in {"complete", "partial"}:
        return False
    return True


def find_previous_result(*, data_root: str | Path, source_id: str, character_name: str):
    """Return the newest usable prior sheet-result for this exact character.

    DDB character id is authoritative identity. Name equality is an additional
    guard against accidentally using a renamed/mismatched local artifact.
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
        if _valid_previous_result(payload, source_id, character_name):
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
        key = (
            class_id or class_name.casefold(),
            subclass_id or subclass_name.casefold(),
        )
        rows[key] = {
            "class": class_name,
            "subclass": subclass_name,
            "level": int(entry.get("level") or 0),
        }
    return rows


def _total_level(raw):
    return sum(row["level"] for row in _class_rows(raw).values())


def _spell_rows(raw):
    """Identity-preserving spell index for change reporting only."""
    out = {}

    def add(entries, source_group):
        for entry in _list(entries):
            entry = _dict(entry)
            definition = _dict(entry.get("definition"))
            name = _text(definition.get("name"))
            definition_id = _text(definition.get("id"))
            legacy = definition.get("isLegacy")
            source_id = _text(entry.get("id"))
            if not name:
                continue
            # Definition/version are primary. Character-spell id/source group
            # only disambiguate repeated instances of the same definition.
            key = (
                definition_id or name.casefold(),
                legacy if isinstance(legacy, bool) else None,
                source_id or source_group,
            )
            out[key] = {
                "name": name,
                "level": definition.get("level"),
                "is_legacy": legacy,
                "definition_id": definition_id,
                "source_id": source_id,
                "source_group": source_group,
            }

    grouped = _dict(_dict(raw).get("spells"))
    for group_name, entries in grouped.items():
        add(entries, f"spells.{group_name}")
    for index, group in enumerate(_list(_dict(raw).get("classSpells"))):
        group = _dict(group)
        class_id = _text(group.get("characterClassId")) or str(index)
        add(group.get("spells"), f"classSpells.{class_id}")
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
    return sorted(
        _text(rows[key].get("name"))
        for key in keys
        if _text(rows[key].get("name"))
    )


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
        if old != new:
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
            [
                previous_raw.get("baseHitPoints"),
                previous_raw.get("bonusHitPoints"),
                previous_raw.get("removedHitPoints"),
            ],
            [
                current_raw.get("baseHitPoints"),
                current_raw.get("bonusHitPoints"),
                current_raw.get("removedHitPoints"),
            ],
        ),
        "currencies": (previous_raw.get("currencies"), current_raw.get("currencies")),
    }
    changed_sections = [
        name
        for name, (before, after) in tracked_sections.items()
        if _stable(before) != _stable(after)
    ]

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
        "previous_raw_sha256": _stable_sha256(previous_raw),
        "current_raw_sha256": _stable_sha256(current_raw),
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
            "업데이트 모드를 선택했지만 같은 D&D Beyond 캐릭터의 이전 시트 이동 결과를 찾지 못했습니다. "
            "이름만 같은 다른 캐릭터는 자동 연결하지 않습니다."
        )

    comparison = compare_raw_sources(_dict(previous.get("raw_source")), current_raw)
    comparison["enabled"] = True
    comparison["previous_result_path"] = str(path.resolve())
    return comparison


def _collect_string_pairs(left, right, output):
    """Collect paired strings from one *previous* original/translated result.

    Pairing happens inside the same saved result, so list positions are stable.
    Current character structure is never zip-compared with the older structure.
    """
    if isinstance(left, str) and isinstance(right, str):
        output.append((left, right))
        return
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left.keys() & right.keys():
            _collect_string_pairs(left[key], right[key], output)
        return
    if isinstance(left, list) and isinstance(right, list):
        for old_value, translated_value in zip(left, right):
            _collect_string_pairs(old_value, translated_value, output)


def _collect_strings(value, output):
    if isinstance(value, str):
        if value.strip():
            output.add(value)
        return
    if isinstance(value, dict):
        for child in value.values():
            _collect_strings(child, output)
        return
    if isinstance(value, list):
        for child in value:
            _collect_strings(child, output)


def build_verified_translation_seed(previous_payload: dict, current_original: dict):
    """Return exact unchanged source translations safe to reuse.

    A repeated English source that had conflicting previous Korean outputs is
    excluded completely. New/changed English source values are absent from the
    seed and therefore go through the normal translation pipeline.
    """
    previous_payload = _dict(previous_payload)
    previous_original = _dict(previous_payload.get("original"))
    previous_translated = _dict(previous_payload.get("translated"))
    current_original = _dict(current_original)

    expected_id = _text(current_original.get("source_id"))
    expected_name = _text(current_original.get("name"))
    if _text(previous_original.get("source_id")) != expected_id:
        raise RuntimeError("이전 번역 결과의 D&D Beyond 캐릭터 ID가 현재 대상과 다릅니다.")
    if _text(previous_original.get("name")) != expected_name:
        raise RuntimeError("이전 번역 결과의 캐릭터 이름이 현재 대상과 다릅니다.")

    pairs = []
    _collect_string_pairs(previous_original, previous_translated, pairs)
    current_strings = set()
    _collect_strings(current_original, current_strings)

    candidates = defaultdict(set)
    for source, translated in pairs:
        source = str(source)
        translated = str(translated)
        if source in current_strings:
            candidates[source].add(translated)

    # Import validators lazily to avoid making update-mode discovery influence
    # normal translation module initialization.
    from .translator import (
        contains_unexpected_script,
        mechanics_compatible,
        structure_tokens,
    )
    from .semantic_validator import critical_codes

    seed = {}
    conflicts = []
    rejected = []
    for source, targets in candidates.items():
        if len(targets) != 1:
            conflicts.append(source)
            continue
        translated = next(iter(targets))
        if not source.strip() or not translated.strip() or source == translated:
            continue
        # Reuse only an actual Korean translation. English fallback rows such
        # as newly recovered subclass spells intentionally remain unseeded.
        if not re.search(r"[가-힣]", translated):
            rejected.append(source)
            continue
        if contains_unexpected_script(translated):
            rejected.append(source)
            continue
        if structure_tokens(source) != structure_tokens(translated):
            rejected.append(source)
            continue
        if not mechanics_compatible(source, translated):
            rejected.append(source)
            continue
        if critical_codes(source, translated):
            rejected.append(source)
            continue
        seed[source] = translated

    return seed, {
        "candidate_count": len(candidates),
        "seeded_count": len(seed),
        "conflict_count": len(conflicts),
        "rejected_count": len(rejected),
        "conflict_sources": sorted(conflicts)[:20],
        "rejected_sources": sorted(rejected)[:20],
    }


def prime_translator_from_previous_result(
    translator,
    previous_result_path: str | Path,
    current_original: dict,
):
    """Prime hybrid Translator's already-existing resolved-value map.

    This uses the translator's normal ``_equivalent_final`` fast path instead
    of changing cache-only behavior or writing old final translations into the
    persistent Google first-pass cache.
    """
    path = Path(previous_result_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"이전 번역 결과를 읽지 못했습니다: {path}") from exc

    seed, report = build_verified_translation_seed(payload, current_original)
    resolved = getattr(translator, "_equivalent_final", None)
    if not isinstance(resolved, dict):
        raise RuntimeError("현재 번역기가 기존 번역 재사용 인터페이스를 제공하지 않습니다.")

    inserted = 0
    for source, translated in seed.items():
        if source in resolved:
            continue
        resolved[source] = translated
        inserted += 1

    result = dict(report)
    result.update(
        {
            "version": UPDATE_MODE_VERSION,
            "previous_result_path": str(path.resolve()),
            "inserted_count": inserted,
        }
    )
    return result
