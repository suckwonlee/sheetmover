# Sheet Mover runtime-integrity v2.3

기준 커밋: `29c50d9c5850495667a02666e08a1acb25ab1591`

이번 패치는 v2.2 적용 과정에서 생긴 `roll20_proficiencies.py`의 문자 그대로의 `\\nSTAGE11...` SyntaxError를 복구한 뒤, 실제 프로젝트를 수정하기 전에 임시 복제본에서 전체 unittest를 먼저 실행합니다.

추가 동작:

- Moonbeam 같은 `raw_source.spells["class"]` grouped 주문의 피해 modifier를 읽습니다.
- Moonbeam은 `2d10 Radiant`, Constitution save, 성공 시 절반 피해, 상위 슬롯당 `+1d10`으로 매핑됩니다.
- Stage 11은 16개 단위 저장 후 persisted server state를 확인하고, 남은 mismatch만 `wait:false`로 복구합니다.
- Roll20 게임 탭 / 대상 캐릭터 / Legacy OGL5e 확인을 번역보다 먼저 수행합니다.
- 현재 D&D Beyond raw payload가 직전 저장 결과와 정확히 같으면 기존 번역 결과를 재사용하여 Google/Ollama 번역 단계 자체를 건너뜁니다.
- `workers/<run_id>/`에는 종료 후 `run.log` 하나만 남기고 `events.jsonl`, 실행용 `settings.json`은 제거합니다.

사용:

```powershell
python apply_runtime_integrity_v23.py
```

이 명령 하나가 복제본 전체 테스트까지 통과한 뒤에만 실제 소스를 교체합니다. 성공 메시지가 뜨면:

```powershell
python main.py
```
