"""File-based worker events, usable by windowed executables without stdio."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time


def validate_event(event):
    kind = event.get("type")
    if kind not in ("progress", "stage", "complete", "error"):
        raise ValueError("알 수 없는 작업 메시지입니다.")
    for key in ("message", "name", "traceback", "report"):
        if key in event and event[key] is not None and not isinstance(event[key], str):
            raise ValueError(f"작업 메시지 {key}가 문자열이 아닙니다.")
    if kind == "progress":
        percent = event.get("percent")
        if (type(percent) not in (int, float) or not math.isfinite(percent)
                or not 0 <= percent <= 100):
            raise ValueError("잘못된 진행률입니다.")
    elif kind == "stage":
        if type(event.get("index")) is not int or not 1 <= event["index"] <= 10:
            raise ValueError("잘못된 작업 단계입니다.")
        if event.get("status") not in ("running", "pass", "error"):
            raise ValueError("잘못된 작업 단계 상태입니다.")
    elif kind == "complete":
        if not event.get("report"):
            raise ValueError("완료 보고서 경로가 없습니다.")
    elif kind == "error":
        errors = event.get("save_errors", [])
        paths = event.get("paths", {})
        if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
            raise ValueError("저장 오류 목록 형식이 잘못되었습니다.")
        if not isinstance(paths, dict) or not all(isinstance(p, str) for p in paths.values()):
            raise ValueError("결과 경로 목록 형식이 잘못되었습니다.")


class EventWriter:
    def __init__(self, path, run_id):
        self.path = Path(path)
        self.run_id = run_id
        self.sequence = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=False)

    def emit(self, event):
        self.sequence += 1
        record = dict(event, run_id=self.run_id, sequence=self.sequence)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()


class EventReader:
    def __init__(self, path, run_id):
        self.path = Path(path)
        self.run_id = run_id
        self.offset = 0
        self.buffer = b""
        self.sequence = 0

    def read(self, *, final=False):
        if self.path.exists():
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                self.buffer += stream.read()
                self.offset = stream.tell()
        events = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            event = json.loads(line.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("작업 메시지가 객체가 아닙니다.")
            if event.get("run_id") != self.run_id:
                raise ValueError("다른 실행의 작업 메시지가 도착했습니다.")
            if type(event.get("sequence")) is not int or event["sequence"] != self.sequence + 1:
                raise ValueError("작업 메시지 순서가 누락되거나 중복되었습니다.")
            validate_event(event)
            self.sequence += 1
            events.append(event)
        if final and self.buffer:
            raise ValueError("작업 프로세스가 메시지를 끝까지 저장하지 못했습니다.")
        return events


def completion_error(code, complete, run_id, errors=()):
    """Exit code alone never means the sheet was moved successfully."""
    if errors:
        return " / ".join(str(error) for error in errors)
    if code != 0:
        return f"작업 프로세스가 중단되었습니다 (종료 코드 {code})."
    if not complete:
        return "완료 메시지가 없습니다. 마지막 진행 상태와 로그를 확인하세요."
    try:
        report = json.loads(Path(complete["report"]).read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("run_id") != run_id:
            return "완료 보고서가 현재 실행과 일치하지 않습니다."
        stages = report.get("stage_statuses") or {}
        expected = {str(i) for i in range(1, 11)}
        if (report.get("status") != "pass" or not isinstance(stages, dict) or set(stages) != expected
                or any(status != "pass" for status in stages.values())):
            return "모든 단계의 완료가 확인되지 않았습니다."
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"완료 보고서를 확인하지 못했습니다: {exc}"
    return None


def monitor_worker(proc, event_path, run_id, on_event):
    reader = EventReader(event_path, run_id)
    complete = None
    errors = []

    def read_events(final=False):
        nonlocal complete
        if errors and any(error.startswith("작업 통신 오류:") for error in errors):
            return
        try:
            events = reader.read(final=final)
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(f"작업 통신 오류: {exc}")
            return
        for event in events:
            if event.get("type") == "complete":
                if complete:
                    errors.append("완료 메시지가 중복되었습니다.")
                complete = event
            elif event.get("type") == "error":
                errors.append(str(event.get("message") or "작업 실패"))
                errors.extend(str(error) for error in event.get("save_errors", []))
            on_event(event)

    while proc.poll() is None:
        read_events()
        time.sleep(.1)
    read_events(final=True)
    code = proc.wait()
    return code, completion_error(code, complete, run_id, errors)
