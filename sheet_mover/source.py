"""Conservative D&D Beyond response normalization for review, not direct writes."""
import re
from urllib.parse import urlparse
from .models import CharacterSheet

ABILITIES = {1: "strength", 2: "dexterity", 3: "constitution", 4: "intelligence", 5: "wisdom", 6: "charisma"}


def source_character_id(url):
    parsed = urlparse(url)
    match = re.fullmatch(r"/characters/(\d+)/?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname not in {"www.dndbeyond.com", "dndbeyond.com"} or not match:
        raise ValueError("https://www.dndbeyond.com/characters/숫자 형식의 원본 URL을 입력하세요.")
    return match.group(1)


def _number(value):
    return value if type(value) in (int, float) else None


def _definition(entry, kind):
    definition = entry.get("definition") or entry
    if not isinstance(definition, dict) or not definition.get("name"):
        return None
    return {"source_id": str(entry.get("id") or definition.get("id") or ""), "kind": kind,
            "name": definition["name"], "original_name": definition["name"],
            "description": definition.get("description") or definition.get("snippet") or ""}


def normalize_character(data):
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("원본 응답에 캐릭터 이름이 없습니다.")
    sheet = CharacterSheet(name=data["name"])
    modifiers = [m for group in (data.get("modifiers") or {}).values() if isinstance(group, list) for m in group]
    base = {s["id"]: _number(s.get("value")) for s in data.get("stats") or []}
    bonus = {s["id"]: _number(s.get("value")) for s in data.get("bonusStats") or []}
    override = {s["id"]: _number(s.get("value")) for s in data.get("overrideStats") or []}
    # Do not label base scores as totals without a complete modifier evaluator.
    has_modifiers = bool(modifiers or data.get("characterValues"))
    for key, name in ABILITIES.items():
        if override.get(key) is not None:
            sheet.ability_scores[name] = override[key]
        elif base.get(key) is not None and not has_modifiers:
            sheet.ability_scores[name] = base[key] + (bonus.get(key) or 0)
        else:
            sheet.ability_scores[name] = None
    if any(v is None for v in sheet.ability_scores.values()):
        sheet.warnings.append("일부 최종 능력치는 보정 계산이 필요하여 미확인(null)으로 표시했습니다. 원본 시트에서 확인하세요.")
    sheet.max_hp = _number(data.get("overrideHitPoints"))
    if sheet.max_hp is not None:
        removed = _number(data.get("removedHitPoints"))
        if removed is not None:
            sheet.hp = max(0, sheet.max_hp - removed)
    if sheet.hp is None or sheet.max_hp is None:
        sheet.warnings.append("최종 HP 계산은 미지원입니다. 명시적인 최대 HP 재정의 값이 없는 경우 미확인(null)으로 표시합니다.")
    sheet.proficiencies = sorted({
        (m.get("friendlySubtypeName") or m.get("subType") or "") + (" (expertise)" if m.get("type") == "expertise" else "")
        for m in modifiers if m.get("type") in {"proficiency", "expertise"}
    } - {""})
    sheet.warnings.append("숙련은 응답의 숙련·전문화 목록입니다. 조건부 적용 여부와 미선택 옵션을 대조해야 합니다.")
    for entry in data.get("inventory") or []:
        item = _definition(entry, "equipment")
        if item:
            item.update(quantity=entry.get("quantity"), equipped=entry.get("equipped"),
                        weight=(entry.get("definition") or {}).get("weight"))
            sheet.equipment.append(item)
    spell_groups = list((data.get("spells") or {}).values())
    spell_groups += [c.get("spells") or [] for c in data.get("classSpells") or []]
    for group in spell_groups:
        for entry in group or []:
            item = _definition(entry, "spell")
            if item:
                definition = entry.get("definition") or {}
                item.update(level=definition.get("level"), prepared=entry.get("prepared"),
                            casting_time=definition.get("activation"), range=definition.get("range"),
                            duration=definition.get("duration"), components=definition.get("components"),
                            components_description=definition.get("componentsDescription") or "")
                sheet.spells.append(item)
    feature_groups = [((data.get("race") or {}).get("racialTraits") or [], "racial_trait"),
                      (data.get("feats") or [], "feat")]
    for character_class in data.get("classes") or []:
        feature_groups.append((character_class.get("classFeatures") or [], "class_feature"))
    background = (data.get("background") or {}).get("definition") or {}
    if background.get("featureName"):
        sheet.features.append({"source_id": str(background.get("id") or ""), "kind": "background",
                               "name": background["featureName"], "original_name": background["featureName"],
                               "description": background.get("featureDescription") or ""})
    for entries, kind in feature_groups:
        for entry in entries:
            item = _definition(entry, kind)
            if item:
                sheet.features.append(item)
    sheet.warnings.append("커스텀 항목·선택 옵션·부여 원인별 중복 판정은 아직 미구현입니다. 원본 응답도 함께 보관됩니다.")
    return sheet
