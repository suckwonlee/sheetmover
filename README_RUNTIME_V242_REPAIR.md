# runtime-integrity v2.4.2 repair

복제본 전체 테스트 383개 중 382개가 통과했고, 남은 1개 실패 원인은
`full-run-<run_id>.json` 위치를 옮긴 설치기 변경이 기존 worker protocol 계약과 충돌한 것입니다.

이 상태 파일은 실행 로그가 아니므로 위치를 옮길 이유가 없습니다.

v2.4.2는 다음 두 문자열만 원래 계약으로 복구한 뒤 v2.4.1 설치기를 다시 검증/실행합니다.

- `results/current/full-run-<run_id>.json` 유지
- `tests/test_full_run.py`도 같은 기존 경로 유지

나머지는 유지합니다.

- `workers/<run_id>/` 종료 후 `run.log` 하나만 유지
- UI에서 단계별 결과 JSON 경로 나열 제거
- Roll20 모든 쓰기 완료 후 화면 reload
- editor 준비 확인 후 완료 처리
- 실제 프로젝트 수정 전 복제본 전체 unittest 실행

실행:

```powershell
python repair_and_apply_runtime_v242.py
```

성공 후:

```powershell
python main.py
```
