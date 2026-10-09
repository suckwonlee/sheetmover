"""Read-only retries which never accept browser cache as persisted state."""
from __future__ import annotations

import time


class PersistedReadError(RuntimeError):
    code = "verification_unconfirmed"


def read_persisted(driver, script, *args, timeout=25, attempts=3):
    last_status = "unknown"
    for attempt in range(attempts):
        driver.set_script_timeout(timeout)
        try:
            result = driver.execute_async_script(script, *args)
        except Exception as exc:
            last_status = type(exc).__name__
        else:
            if not isinstance(result, dict):
                last_status = "invalid_response"
            else:
                last_status = str(result.get("fetch_status") or "missing_fetch_status")
                if result.get("ok") and last_status in ("success", "success_promise"):
                    return result
                if result.get("reason") and not result.get("fetch_status"):
                    raise PersistedReadError(f"Roll20 대상 조회 실패: {result['reason']}")
        if attempt + 1 < attempts:
            time.sleep(0.25 * (attempt + 1))
    raise PersistedReadError(
        f"Roll20 서버 조회를 {attempts}회 시도했지만 저장 여부를 확인하지 "
        f"못했습니다 ({last_status}). 이미 입력된 내용이 있을 수 있습니다. "
        "연결을 확인한 뒤 시트를 다시 조회하세요."
    )
