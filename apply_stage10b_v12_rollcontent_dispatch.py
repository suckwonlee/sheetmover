# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys

ROOT = Path.cwd()
MOD = ROOT / "sheet_mover" / "roll20_spell_attacks.py"
TEST = ROOT / "tests" / "test_stage10b_rollcontent_dispatch.py"


def replace_one_of(text, olds, new, label):
    found = [old for old in olds if old in text]
    if len(found) != 1:
        raise RuntimeError(
            f"{label}: 예상 후보 1개가 필요한데 {len(found)}개를 찾았습니다."
        )
    return text.replace(found[0], new, 1)


def patch_module(text):
    if "stage10b-roll20-spell-attacks-v1.2-rollcontent-dispatch" in text:
        return text

    text = replace_one_of(
        text,
        [
            'STAGE10B_VERSION = "2026-10-06-stage10b-roll20-spell-attacks-v1"',
            'STAGE10B_VERSION = "2026-10-07-stage10b-roll20-spell-attacks-v1.1-sequential-link"',
        ],
        'STAGE10B_VERSION = "2026-10-07-stage10b-roll20-spell-attacks-v1.2-rollcontent-dispatch"',
        "Stage 10B 버전",
    )

    if "\nimport time\n" not in text:
        text = text.replace("import json\n", "import json\nimport time\n", 1)

    marker = "\n\ndef spell_attack_row_id(source_key: str) -> str:\n"
    if marker not in text:
        raise RuntimeError("spell_attack_row_id 위치를 찾지 못했습니다.")

    helper = r'''
ROLLCONTENT_DISPATCH_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const attrName = String(arguments[2] || '').trim();
const spec = arguments[3] || {};
const done = arguments[arguments.length - 1];

function val(obj,key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v=obj.get(key); if (v != null) return v;
    }
    if (obj[key] != null) return obj[key];
  } catch (_) {}
  return null;
}
function modelsOf(c) {
  try {
    if (!c) return [];
    if (Array.isArray(c.models)) return c.models;
    if (typeof c.toArray === 'function') return c.toArray();
    if (Array.isArray(c)) return c;
  } catch (_) {}
  return [];
}
function idOf(m) {
  return String(
    val(m,'id') || val(m,'_id') || val(m,'characterid') || (m && m.id) || ''
  ).trim();
}
function findCharacter() {
  const campaigns=[];
  try { if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign); } catch (_) {}
  try { if (window.Campaign) campaigns.push(window.Campaign); } catch (_) {}
  for (const campaign of campaigns) {
    const collections=[
      campaign.characters,
      campaign.attributes && campaign.attributes.characters,
    ];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id=idOf(model);
        const name=String(val(model,'name') || '').trim();
        if ((wantedId && id===wantedId) ||
            (!wantedId && wantedName && name===wantedName)) return model;
      }
    }
  }
  return null;
}
function modelsNamed(collection,name) {
  return modelsOf(collection).filter(
    m=>String(val(m,'name') || '').trim()===name
  );
}
function finish(payload) {
  try { done(payload); } catch (_) {}
}

const character=findCharacter();
if (!character) {
  finish({ok:false,reason:'character_not_found'});
  return;
}
const collection=character.attribs;
if (!collection) {
  finish({ok:false,reason:'attribute_collection_unavailable'});
  return;
}

const current=String(spec.current == null ? '' : spec.current);
const max=String(spec.max == null ? '' : spec.max);

try {
  const existing=modelsNamed(collection,attrName);
  if (existing.length > 1) {
    finish({ok:false,reason:'duplicate_attribute',count:existing.length});
    return;
  }

  if (existing.length === 1) {
    const model=existing[0];
    const before=String(val(model,'current') == null ? '' : val(model,'current'));
    const beforeMax=String(val(model,'max') == null ? '' : val(model,'max'));
    if (before===current && beforeMax===max) {
      finish({ok:true,action:'skip',id:idOf(model)});
      return;
    }

    model.save(
      {current:current,max:max},
      {wait:false}
    );
    finish({
      ok:true,
      action:'update_dispatched',
      id:idOf(model),
      before:before,
      current:current,
    });
    return;
  }

  const model=collection.create(
    {
      name:attrName,
      current:current,
      max:max,
      characterid:wantedId,
    },
    {wait:false}
  );
  finish({
    ok:true,
    action:'create_dispatched',
    id:idOf(model),
    current:current,
  });
} catch(e) {
  finish({
    ok:false,
    reason:'dispatch_exception',
    error:String(e && e.stack ? e.stack : e),
  });
}
"""


def _split_rollcontent_attrs(attrs):
    normal = {}
    rollcontent = {}
    for name, spec in attrs.items():
        if name.endswith("_rollcontent"):
            rollcontent[name] = spec
        else:
            normal[name] = spec
    return normal, rollcontent


def _poll_persisted(driver, target, attrs, label, attempts=8, delay=0.75):
    last_mismatches = []
    for attempt in range(1, attempts + 1):
        after = _snapshot(driver, target, attrs.keys())
        actual, mismatches = _verify(after, attrs)
        last_mismatches = mismatches
        if not mismatches:
            return actual, attempt
        if attempt < attempts:
            time.sleep(delay)

    raise RuntimeError(
        f"{label} 서버 재검증 실패: "
        + json.dumps(last_mismatches, ensure_ascii=False)
    )


def _write_rollcontent_and_verify(driver, target, attrs, label):
    if not attrs:
        return {"ok": True, "action": "none"}, {}

    if len(attrs) != 1:
        raise RuntimeError(
            f"{label}: rollcontent 속성은 한 번에 1개여야 합니다. "
            f"현재 {len(attrs)}개"
        )

    name, spec = next(iter(attrs.items()))
    driver.set_script_timeout(30)
    outcome = driver.execute_async_script(
        ROLLCONTENT_DISPATCH_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        name,
        spec,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            f"{label} dispatch 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )

    actual, attempts = _poll_persisted(
        driver,
        target,
        attrs,
        label,
    )
    outcome = dict(outcome)
    outcome["persisted_verify_attempts"] = attempts
    return outcome, actual
'''
    text = text.replace(marker, "\n\n" + helper + marker, 1)

    if '"rollcontent_no_wait_dispatch": True' not in text:
        policy_old = '            "as_part_of_weapon_attack_stays_spellcard": True,\n'
        if policy_old not in text:
            raise RuntimeError("Stage 10B policy 위치를 찾지 못했습니다.")
        text = text.replace(
            policy_old,
            policy_old
            + '            "rollcontent_no_wait_dispatch": True,\n'
            + '            "rollcontent_persisted_poll_verify": True,\n',
            1,
        )

    pattern = re.compile(
        r'''            _upsert_and_verify\(
                driver,
                target,
                _spell_combat_attributes\(
                    spell_row,
                    character_id,
                    attack_id,
                \),
                f"주문 출력 '\{spell_row\['name'\]\}'",
(?:                sequential=True,\n)?            \)
''',
        re.M,
    )
    match = pattern.search(text)
    if not match:
        raise RuntimeError("주문 출력 저장 호출 블록을 찾지 못했습니다.")

    replacement = '''            spell_attrs = _spell_combat_attributes(
                spell_row,
                character_id,
                attack_id,
            )
            normal_attrs, rollcontent_attrs = _split_rollcontent_attrs(
                spell_attrs
            )

            if normal_attrs:
                try:
                    _upsert_and_verify(
                        driver,
                        target,
                        normal_attrs,
                        f"주문 출력 '{spell_row['name']}'",
                        sequential=True,
                    )
                except TypeError:
                    _upsert_and_verify(
                        driver,
                        target,
                        normal_attrs,
                        f"주문 출력 '{spell_row['name']}'",
                    )

            _write_rollcontent_and_verify(
                driver,
                target,
                rollcontent_attrs,
                f"주문 출력 '{spell_row['name']}' rollcontent",
            )
'''
    text = text[:match.start()] + replacement + text[match.end():]
    return text


def backup(path):
    dst = path.with_suffix(path.suffix + ".pre-stage10b-v1.2.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not MOD.is_file():
        raise RuntimeError(f"필수 파일이 없습니다: {MOD}")

    new_text = patch_module(MOD.read_text(encoding="utf-8"))

    if args.check:
        print("[시트 이동기] Stage 10B v1.2 사전 점검 통과")
        print("- rollcontent를 일반 wait:true save에서 분리 가능")
        print("- wait:false dispatch 후 persisted server polling 검증")
        print("- v1 또는 v1.1 상태 모두 적용 가능")
        print("- 파일은 아직 수정하지 않았습니다.")
        return

    backup(MOD)
    MOD.write_text(new_text, encoding="utf-8")

    bundled = Path(__file__).resolve().parent / "tests" / "test_stage10b_rollcontent_dispatch.py"
    if bundled.is_file():
        TEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, TEST)

    print("[시트 이동기] Stage 10B v1.2 적용 완료")
    print("- rollcontent는 Backbone callback을 기다리지 않고 저장 요청")
    print("- 실제 서버 반영 여부를 최대 8회 polling 재검증")
    print("- 다른 주문 전투 필드는 기존 writer 유지")
    print("python -m unittest discover -s tests -v")
    print("python main.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[시트 이동기] Stage 10B v1.2 패치 실패: {exc}", file=sys.stderr)
        raise
