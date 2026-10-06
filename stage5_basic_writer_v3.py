"""Stage 5 basic Roll20 writer v3.

Default behavior: APPLY immediately.
Use --dry-run only when a read-only plan is explicitly wanted.

v3 resumes safely after partial failures:
- reads the current persisted server attributes first
- writes only fields that are missing or different
- writes in small batches so one Selenium async-script timeout cannot cover all fields
- verifies every batch from the server before continuing
- saves a backup before the first mutation
- preserves a detailed progress/error JSON even if a later batch fails
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sheet_mover.result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result
from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
    _select_roll20_tab,
)

from stage5_basic_writer_v2 import (
    VERSION as V2_VERSION,
    build_plan,
    _load_target,
    _snapshot,
    _verify,
)

VERSION = "2026-10-06-stage5-basic-fields-v3"
BATCH_SIZE = 4
ROOT = Path.cwd()

UPSERT_BATCH_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const changes = arguments[2] || {};
const done = arguments[arguments.length - 1];

function val(obj, key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v = obj.get(key);
      if (v != null) return v;
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
  const campaigns = [];
  try {
    if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign);
  } catch (_) {}
  try {
    if (window.Campaign) campaigns.push(window.Campaign);
  } catch (_) {}

  for (const campaign of campaigns) {
    const collections = [
      campaign.characters,
      campaign.attributes && campaign.attributes.characters,
    ];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id = idOf(model);
        const name = String(val(model,'name') || '').trim();
        if ((wantedId && id === wantedId) ||
            (!wantedId && wantedName && name === wantedName)) {
          return model;
        }
      }
    }
  }
  return null;
}

function modelsNamed(collection, name) {
  return modelsOf(collection).filter(
    m => String(val(m,'name') || '').trim() === name
  );
}

function withTimeout(executor, ms, label) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(label + '_timeout'));
    }, ms);

    executor(
      value => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      error => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function fetchCollection(collection) {
  return withTimeout((resolve, reject) => {
    try {
      collection.fetch({
        reset:false,
        success:() => resolve(true),
        error:(_c,xhr) => reject(new Error(
          'fetch_failed status=' + (xhr && xhr.status) +
          ' text=' + (xhr && xhr.statusText)
        )),
      });
    } catch (e) {
      reject(e);
    }
  }, 10000, 'fetch');
}

function sameValue(model, spec) {
  const current = String(val(model,'current') == null ? '' : val(model,'current'));
  const wantedCurrent = String(spec.current == null ? '' : spec.current);
  if (current !== wantedCurrent) return false;

  if (spec.max !== null && spec.max !== undefined) {
    const max = String(val(model,'max') == null ? '' : val(model,'max'));
    if (max !== String(spec.max)) return false;
  }
  return true;
}

function saveExisting(model, name, spec) {
  return withTimeout((resolve, reject) => {
    const before = {
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    };
    const update = {
      current:String(spec.current == null ? '' : spec.current),
    };
    if (spec.max !== null && spec.max !== undefined) {
      update.max = String(spec.max);
    }

    try {
      model.save(update, {
        wait:true,
        success:m => resolve({
          name,
          action:'update',
          id:idOf(m || model),
          before,
          after:{
            current:String(val(m || model,'current') == null ? '' : val(m || model,'current')),
            max:String(val(m || model,'max') == null ? '' : val(m || model,'max')),
          },
        }),
        error:(_m,xhr) => reject(new Error(
          'update_failed ' + name +
          ' status=' + (xhr && xhr.status) +
          ' text=' + (xhr && xhr.statusText)
        )),
      });
    } catch (e) {
      reject(e);
    }
  }, 10000, 'save_' + name);
}

function createNew(collection, name, spec) {
  return withTimeout((resolve, reject) => {
    const attrs = {
      name,
      current:String(spec.current == null ? '' : spec.current),
      max:(spec.max !== null && spec.max !== undefined) ? String(spec.max) : '',
      characterid:wantedId,
    };

    try {
      collection.create(attrs, {
        wait:true,
        success:model => resolve({
          name,
          action:'create',
          id:idOf(model),
          before:null,
          after:{
            current:String(val(model,'current') == null ? '' : val(model,'current')),
            max:String(val(model,'max') == null ? '' : val(model,'max')),
          },
        }),
        error:(_m,xhr) => reject(new Error(
          'create_failed ' + name +
          ' status=' + (xhr && xhr.status) +
          ' text=' + (xhr && xhr.statusText)
        )),
      });
    } catch (e) {
      reject(e);
    }
  }, 10000, 'create_' + name);
}

(async function() {
  const character = findCharacter();
  if (!character) {
    done({ok:false, reason:'character_not_found', log:[]});
    return;
  }

  const collection = character.attribs;
  if (!collection || typeof collection.fetch !== 'function') {
    done({ok:false, reason:'attribute_collection_unavailable', log:[]});
    return;
  }

  const log = [];
  try {
    await fetchCollection(collection);

    for (const [name, spec] of Object.entries(changes)) {
      const existing = modelsNamed(collection, name);

      if (existing.length > 1) {
        throw new Error('duplicate_attribute ' + name + ' count=' + existing.length);
      }

      if (existing.length === 1 && sameValue(existing[0], spec)) {
        log.push({
          name,
          action:'skip',
          id:idOf(existing[0]),
          current:String(val(existing[0],'current') == null ? '' : val(existing[0],'current')),
          max:String(val(existing[0],'max') == null ? '' : val(existing[0],'max')),
        });
        continue;
      }

      if (existing.length === 1) {
        log.push(await saveExisting(existing[0], name, spec));
      } else {
        log.push(await createNew(collection, name, spec));
      }
    }

    done({ok:true, log});
  } catch (e) {
    done({
      ok:false,
      reason:'batch_failed',
      error:String(e && e.stack ? e.stack : e),
      log,
    });
  }
})();
"""


def _chunks(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _changed_names(before_snapshot, plan_attrs):
    current = before_snapshot.get("attributes") or {}
    changed = []

    for name, spec in plan_attrs.items():
        rows = current.get(name) or []
        if len(rows) != 1:
            changed.append(name)
            continue

        row = rows[0]
        if str(row.get("current") or "") != str(spec.get("current") or ""):
            changed.append(name)
            continue

        if spec.get("max") is not None:
            if str(row.get("max") or "") != str(spec.get("max") or ""):
                changed.append(name)

    return changed


def run(source_id="170892133", cdp_url=DEFAULT_CDP_URL, dry_run=False):
    result_path = latest_complete_result(source_id=source_id)
    if result_path is None:
        raise RuntimeError(f"source_id={source_id} 정상 결과를 찾지 못했습니다.")

    payload = load_result(result_path)
    plan = build_plan(payload)
    target, target_path = _load_target(source_id)

    if str(plan.get("character_name") or "").strip() != str(target.get("character_name") or "").strip():
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 이름이 다릅니다.")

    attrs = plan["attributes"]
    output_path = Path(CURRENT_RESULT_DIR) / f"roll20-basic-write-v3-{source_id}.json"

    report = {
        "version": VERSION,
        "based_on": V2_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(Path(result_path).resolve()),
        "target_path": str(Path(target_path).resolve()),
        "attribute_count": len(attrs),
        "deferred": plan["deferred"],
        "mutated": False,
        "backup_path": None,
        "batches": [],
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)

        before = _snapshot(driver, target, attrs.keys())
        report["before"] = before["attributes"]

        pending = _changed_names(before, attrs)
        report["initial_pending_count"] = len(pending)
        report["initial_pending_fields"] = pending

        if dry_run:
            report["status"] = "pass"
            report["verification"] = {
                "status": "not_run",
                "reason": "dry_run",
            }
            _save_json(output_path, report)
            return report, output_path

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = Path(CURRENT_RESULT_DIR) / (
            f"roll20-stage5-backup-v3-{source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": VERSION,
                "source_character_id": source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "attributes": before["attributes"],
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        for batch_number, names in enumerate(_chunks(pending, BATCH_SIZE), start=1):
            batch_attrs = {name: attrs[name] for name in names}
            driver.set_script_timeout(50)

            outcome = driver.execute_async_script(
                UPSERT_BATCH_SCRIPT,
                str(target.get("roll20_character_id") or ""),
                str(target.get("character_name") or ""),
                batch_attrs,
            )

            batch_report = {
                "batch": batch_number,
                "fields": names,
                "result": outcome,
                "verification": {},
            }
            report["batches"].append(batch_report)

            if not isinstance(outcome, dict) or not outcome.get("ok"):
                report["status"] = "error"
                report["error"] = (
                    "Roll20 batch 입력 실패: "
                    + json.dumps(outcome, ensure_ascii=False)
                )
                _save_json(output_path, report)
                raise RuntimeError(report["error"])

            report["mutated"] = True

            # Re-fetch persisted state immediately after this small batch.
            batch_snapshot = _snapshot(driver, target, names)
            _, mismatches = _verify(batch_snapshot, batch_attrs)
            batch_report["verification"] = {
                "status": "pass" if not mismatches else "fail",
                "mismatches": mismatches,
            }
            _save_json(output_path, report)

            if mismatches:
                report["status"] = "error"
                report["error"] = (
                    f"{batch_number}번 묶음 서버 재검증 실패: "
                    + json.dumps(mismatches, ensure_ascii=False)
                )
                _save_json(output_path, report)
                raise RuntimeError(report["error"])

        final_snapshot = _snapshot(driver, target, attrs.keys())
        actual, mismatches = _verify(final_snapshot, attrs)

        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "mismatches": mismatches,
            "server_fetch_status": final_snapshot.get("fetch_status"),
        }
        report["status"] = "pass" if not mismatches else "error"

        if mismatches:
            report["error"] = (
                "최종 서버 재검증 실패: "
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
    parser.add_argument("--source-id", default="170892133")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="읽기 전용 계획만 수행. 기본값은 실제 입력입니다.",
    )
    args = parser.parse_args()

    print("[시트 이동기] 5단계 v3: 서버 attribute 재개형 입력")
    print(
        "[시트 이동기] 모드: "
        + ("읽기 전용" if args.dry_run else "실제 입력")
    )
    print(f"[시트 이동기] 묶음 크기: {BATCH_SIZE}")

    report, output = run(
        source_id=str(args.source_id),
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(
        f"[시트 이동기] 최초 미완료 attribute: "
        f"{report.get('initial_pending_count', 0)}개"
    )

    if not args.dry_run:
        print(
            f"[시트 이동기] 처리한 묶음: "
            f"{len(report.get('batches') or [])}개"
        )
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )

    print(
        "[시트 이동기] 장비/주문목록/특성/행동/공격/"
        "기술·내성/자원은 수정하지 않았습니다."
    )
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
