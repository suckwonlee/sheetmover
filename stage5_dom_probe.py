"""Stage 5 DOM preflight: read OGL5e attr_* field names without editing them."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
)

ROOT = Path.cwd()
TARGET_PATH = ROOT / "results" / "current" / "roll20-target-170892133.json"
OUTPUT_PATH = ROOT / "results" / "current" / "roll20-dom-fields-170892133.json"

SCAN_SCRIPT = r"""
function scanDocument(doc, where, out) {
  if (!doc) return;

  let nodes = [];
  try {
    nodes = Array.from(
      doc.querySelectorAll(
        'input[name*="attr_"], select[name*="attr_"], textarea[name*="attr_"], button[name*="attr_"]'
      )
    );
  } catch (_) {}

  for (const el of nodes) {
    const rawName = String(el.getAttribute('name') || '').trim();
    if (!rawName) continue;

    let value = '';
    try {
      if (el.type === 'checkbox' || el.type === 'radio') {
        value = el.checked ? String(el.value || 'on') : '';
      } else {
        value = String(el.value == null ? '' : el.value);
      }
    } catch (_) {}

    let visible = false;
    try {
      const r = el.getBoundingClientRect();
      const style = doc.defaultView.getComputedStyle(el);
      visible =
        r.width > 0 &&
        r.height > 0 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden';
    } catch (_) {}

    out.push({
      name: rawName,
      attr_name: rawName.replace(/^attr_/, ''),
      tag: String(el.tagName || '').toLowerCase(),
      type: String(el.getAttribute('type') || '').toLowerCase(),
      value,
      visible,
      where,
    });
  }

  let frames = [];
  try { frames = Array.from(doc.querySelectorAll('iframe')); } catch (_) {}

  for (let i = 0; i < frames.length; i++) {
    try {
      const child = frames[i].contentDocument;
      if (child) scanDocument(child, `${where}/iframe[${i}]`, out);
    } catch (_) {}
  }
}

const rows = [];
scanDocument(document, 'document', rows);

const deduped = [];
const seen = new Set();
for (const row of rows) {
  const key = `${row.attr_name}|${row.tag}|${row.type}|${row.where}`;
  if (seen.has(key)) continue;
  seen.add(key);
  deduped.push(row);
}

deduped.sort((a, b) =>
  a.attr_name.localeCompare(b.attr_name) ||
  a.where.localeCompare(b.where)
);

let title = '';
try { title = String(document.title || ''); } catch (_) {}

let bodyHasCharacterName = false;
try {
  bodyHasCharacterName = String(document.body && document.body.innerText || '')
    .includes(arguments[0]);
} catch (_) {}

return {
  title,
  url: String(location.href || ''),
  body_has_character_name: bodyHasCharacterName,
  field_count: deduped.length,
  fields: deduped,
};
"""


def main():
    if not TARGET_PATH.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {TARGET_PATH}")

    target = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    character_name = str(target.get("character_name") or "").strip()
    character_id = str(target.get("roll20_character_id") or "").strip()

    print("[시트 이동기] 5단계 DOM 조사: OGL5e attr_* 필드명을 읽습니다.")
    print(f"[시트 이동기] 대상 캐릭터: {character_name}")
    print("[시트 이동기] Roll20 값을 수정하지 않습니다.")

    _ensure_cdp(DEFAULT_CDP_URL)
    driver = _attach_driver(DEFAULT_CDP_URL)

    windows = []
    try:
        for handle in list(driver.window_handles):
            driver.switch_to.window(handle)
            url = str(driver.current_url or "")
            parsed = urlparse(url)
            if parsed.hostname != "app.roll20.net":
                continue
            result = driver.execute_script(SCAN_SCRIPT, character_name)
            if isinstance(result, dict):
                result["window_handle"] = handle
                windows.append(result)
    finally:
        _disconnect_driver(driver)

    best = max(windows, key=lambda x: int(x.get("field_count") or 0), default=None)
    payload = {
        "source_character_id": str(target.get("source_character_id") or ""),
        "roll20_character_id": character_id,
        "character_name": character_name,
        "read_only": True,
        "window_count": len(windows),
        "windows": windows,
        "best_window": best,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    count = int((best or {}).get("field_count") or 0)
    print(f"[시트 이동기] 발견한 DOM attr_* 필드: {count}개")
    print(f"[시트 이동기] 조사 결과를 저장했습니다: {OUTPUT_PATH.resolve()}")
    print("[시트 이동기] Roll20 시트 값 변경: 0건")

    if count == 0:
        print(
            "[시트 이동기] 필드가 0개입니다. Roll20에서 '견본 캐릭터' 시트를 "
            "화면에 열어 둔 뒤 다시 실행하세요."
        )


if __name__ == "__main__":
    main()
