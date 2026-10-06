"""Stage 4 read-only Roll20 target discovery through an attached Chrome session."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import urllib.error
import urllib.request

from .result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result

STAGE4_VERSION = "2026-10-06-stage4-roll20-target-v1"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"

DISCOVERY_SCRIPT = r"""
const targetName = arguments[0];
const out = {source: null, campaign_id: null, campaign_name: null, matches: [], dom_matches: []};
const seen = new Set();
function val(obj, key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v = obj.get(key); if (v != null) return v;
    }
    if (obj[key] != null) return obj[key];
  } catch (_) {}
  return null;
}
function addModel(model, source) {
  const name = String(val(model, 'name') || '').trim();
  if (name !== targetName) return;
  const attrs = model && model.attributes ? model.attributes : {};
  const id = String(
    val(model, 'id') || val(model, '_id') || val(model, 'characterid') ||
    (model && model.id) || attrs._id || ''
  ).trim();
  const key = `${name}|${id}|${source}`;
  if (seen.has(key)) return;
  seen.add(key);
  out.matches.push({
    name,
    id,
    source,
    sheet_type: String(
      attrs.charactersheetname || attrs.sheet_type || attrs.sheettype || attrs.character_sheet || ''
    ).trim()
  });
}
function addCollection(collection, source) {
  if (!collection) return;
  let models = [];
  try {
    if (Array.isArray(collection.models)) models = collection.models;
    else if (typeof collection.toArray === 'function') models = collection.toArray();
    else if (Array.isArray(collection)) models = collection;
  } catch (_) {}
  for (const model of models) addModel(model, source);
}
const campaignCandidates = [];
try { if (window.d20 && window.d20.Campaign) campaignCandidates.push(['d20.Campaign', window.d20.Campaign]); } catch (_) {}
try { if (window.Campaign) campaignCandidates.push(['Campaign', window.Campaign]); } catch (_) {}
for (const [label, campaign] of campaignCandidates) {
  if (!out.campaign_id) out.campaign_id = String(val(campaign, 'id') || val(campaign, '_id') || campaign.id || '').trim() || null;
  if (!out.campaign_name) out.campaign_name = String(val(campaign, 'name') || '').trim() || null;
  const collection = campaign.characters || (campaign.attributes && campaign.attributes.characters);
  addCollection(collection, `${label}.characters`);
  if (out.matches.length && !out.source) out.source = `${label}.characters`;
}
// DOM fallback: read-only, no clicks. It is useful when Roll20 stops exposing the Backbone collection globally.
const attrs = ['data-itemid', 'data-characterid', 'data-character-id', 'data-id'];
const leaves = Array.from(document.querySelectorAll('a,button,span,div'))
  .filter(el => el.children.length === 0 && String(el.textContent || '').trim() === targetName);
for (const leaf of leaves) {
  let node = leaf;
  for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
    let id = '';
    for (const attr of attrs) {
      const v = node.getAttribute && node.getAttribute(attr);
      if (v) { id = String(v).trim(); break; }
    }
    if (!id && node.id) {
      const m = String(node.id).match(/(?:journalitem_|character_|journal_)([-A-Za-z0-9_]+)/i);
      if (m) id = m[1];
    }
    if (!id && node.getAttribute) {
      const href = String(node.getAttribute('href') || '');
      const m = href.match(/(?:character|characters)\/([-A-Za-z0-9_]+)/i);
      if (m) id = m[1];
    }
    if (id) {
      out.dom_matches.push({name: targetName, id, source: 'dom'});
      if (!out.matches.some(x => x.name === targetName && x.id === id)) {
        out.matches.push({name: targetName, id, source: 'dom', sheet_type: ''});
      }
      break;
    }
  }
}
return out;
"""


@dataclass(frozen=True)
class Roll20Target:
    version: str
    source_character_id: str
    character_name: str
    roll20_character_id: str
    campaign_id: str
    campaign_name: str
    sheet_type: str
    discovery_source: str
    page_url: str
    checked_at: str


def _progress(percent, message):
    print(f"[{float(percent):5.1f}%] {message}", flush=True)


def _cdp_parts(cdp_url):
    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"잘못된 CDP 주소입니다: {cdp_url}")
    return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def _ensure_cdp(cdp_url, timeout=1.0):
    endpoint = cdp_url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Chrome 디버깅 포트 응답 코드: {response.status}")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(
            "원격 디버깅 Chrome에 연결할 수 없습니다. "
            "먼저 `python -m sheet_mover.roll20_browser`로 전용 Chrome을 여세요."
        ) from exc


def _attach_driver(cdp_url):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError(
            "Selenium이 필요합니다. `.venv`에서 `pip install -r requirements-stage4.txt`를 한 번 실행하세요."
        ) from exc
    host, port = _cdp_parts(cdp_url)
    options = Options()
    options.debugger_address = f"{host}:{port}"
    driver = webdriver.Chrome(options=options)
    return driver


def _select_roll20_tab(driver):
    handles = list(driver.window_handles)
    current_handle = getattr(driver, "current_window_handle", None)
    candidates = []
    for handle in handles:
        driver.switch_to.window(handle)
        url = driver.current_url
        parsed = urlparse(url)
        if parsed.hostname == "app.roll20.net" and parsed.path.startswith("/editor"):
            candidates.append((handle, url))
    if not candidates:
        raise RuntimeError("원격 디버깅 Chrome에서 열린 Roll20 게임 탭을 찾지 못했습니다.")
    if len(candidates) > 1:
        active = [item for item in candidates if item[0] == current_handle]
        if len(active) == 1:
            handle, url = active[0]
        else:
            raise RuntimeError(
                f"Roll20 게임 탭이 {len(candidates)}개 열려 있습니다. 대상 게임 탭 하나만 남기거나 그 탭을 활성화하세요."
            )
    else:
        handle, url = candidates[0]
    driver.switch_to.window(handle)
    return url


def _disconnect_driver(driver):
    # Stopping ChromeDriver's service leaves the externally launched Chrome alive.
    service = getattr(driver, "service", None)
    if service is not None and hasattr(service, "stop"):
        try:
            service.stop()
            return
        except Exception:
            pass
    try:
        driver.quit()
    except Exception:
        pass


def _target_from_result(path):
    payload = load_result(path)
    roll20_payload = payload.get("roll20_payload") if isinstance(payload, dict) else None
    character = roll20_payload.get("character") if isinstance(roll20_payload, dict) else None
    if not isinstance(character, dict):
        character = payload.get("translated") if isinstance(payload, dict) else None
    if not isinstance(character, dict):
        character = payload.get("original") if isinstance(payload, dict) else None
    if not isinstance(character, dict):
        raise RuntimeError("결과 JSON에서 캐릭터 정보를 찾지 못했습니다.")
    name = str(character.get("name") or "").strip()
    source_id = str(character.get("source_id") or (roll20_payload or {}).get("source_character_id") or "").strip()
    if not name:
        raise RuntimeError("결과 JSON의 캐릭터 이름이 비어 있습니다.")
    return source_id, name


def _normalize_matches(raw):
    matches = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(matches, list):
        return []
    unique = []
    seen = set()
    for item in matches:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        ident = str(item.get("id") or "").strip()
        key = (name, ident)
        if key in seen:
            continue
        seen.add(key)
        unique.append({
            "name": name,
            "id": ident,
            "source": str(item.get("source") or "").strip(),
            "sheet_type": str(item.get("sheet_type") or "").strip(),
        })
    return unique


def check_roll20_target(result_path=None, cdp_url=DEFAULT_CDP_URL, save=True, driver_factory=None, on_progress=None):
    report = on_progress or (lambda _p, _m: None)
    path = Path(result_path) if result_path else latest_complete_result()
    if path is None or not path.is_file():
        raise RuntimeError("사용할 정상 결과 JSON이 없습니다. results/current 또는 프로젝트 루트를 확인하세요.")

    report(10, "최신 D&D Beyond 결과를 확인합니다.")
    source_id, name = _target_from_result(path)
    report(30, "원격 디버깅 Chrome 연결을 확인합니다.")
    _ensure_cdp(cdp_url)
    driver = driver_factory(cdp_url) if driver_factory else _attach_driver(cdp_url)
    try:
        report(50, "열려 있는 Roll20 게임을 확인합니다.")
        page_url = _select_roll20_tab(driver)
        report(75, f"Roll20에서 동명 캐릭터 '{name}'을 찾습니다.")
        raw = driver.execute_script(DISCOVERY_SCRIPT, name)
        matches = _normalize_matches(raw or {})
        exact = [item for item in matches if item["name"] == name]
        if not exact:
            raise RuntimeError(f"Roll20에서 동명 캐릭터 '{name}'을 찾지 못했습니다.")
        if len(exact) > 1:
            ids = ", ".join(item["id"] or "ID 미확인" for item in exact)
            raise RuntimeError(f"Roll20에 '{name}' 캐릭터가 {len(exact)}개 있습니다 ({ids}). 자동 선택하지 않습니다.")
        match = exact[0]
        if not match["id"]:
            raise RuntimeError(f"'{name}' 캐릭터는 찾았지만 Roll20 내부 ID를 확인하지 못했습니다.")

        raw = raw if isinstance(raw, dict) else {}
        target = Roll20Target(
            version=STAGE4_VERSION,
            source_character_id=source_id,
            character_name=name,
            roll20_character_id=match["id"],
            campaign_id=str(raw.get("campaign_id") or "").strip(),
            campaign_name=str(raw.get("campaign_name") or "").strip(),
            sheet_type=match["sheet_type"],
            discovery_source=match["source"],
            page_url=page_url,
            checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if save:
            folder = Path(CURRENT_RESULT_DIR)
            folder.mkdir(parents=True, exist_ok=True)
            target_path = folder / f"roll20-target-{source_id or 'unknown'}.json"
            target_path.write_text(json.dumps(asdict(target), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            target_path = None
        report(100, "Roll20 동명 캐릭터 연결 확인을 완료했습니다.")
        return target, target_path
    finally:
        _disconnect_driver(driver)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", help="사용할 sheet-result JSON. 생략하면 최신 정상 결과 자동 선택")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    target, target_path = check_roll20_target(
        result_path=args.result,
        cdp_url=args.cdp_url,
        save=not args.no_save,
        on_progress=_progress,
    )
    print(f"[시트 이동기] 대상 캐릭터: {target.character_name}")
    print(f"[시트 이동기] Roll20 character id: {target.roll20_character_id}")
    if target.campaign_name:
        print(f"[시트 이동기] Roll20 게임: {target.campaign_name}")
    if target.sheet_type:
        print(f"[시트 이동기] 시트 유형: {target.sheet_type}")
    if target_path:
        print(f"[시트 이동기] 연결 정보를 저장했습니다: {target_path.resolve()}")
    print("[시트 이동기] Roll20 시트 내용은 수정하지 않았습니다.")


if __name__ == "__main__":
    main()
