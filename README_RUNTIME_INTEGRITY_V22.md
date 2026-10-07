# Sheet Mover runtime-integrity v2.2

검토 기준 GitHub 커밋:

`29c50d9c5850495667a02666e08a1acb25ab1591`

이번 패치는 특정 캐릭터 ID나 Moonbeam 이름을 하드코딩하지 않고, 실제 D&D Beyond/Roll20 데이터 구조를 기준으로 수정합니다.

## 변경점

- **Moonbeam 피해 누락**: `raw_source.spells["class"]` 같은 grouped top-level spell source도 읽습니다. Moonbeam은 `2d10 Radiant`, Constitution save, 성공 시 절반 피해, 상위 슬롯당 `+1d10`이 들어갑니다.
- **Stage 11 timeout**: 숙련 필드를 16개 단위로 저장하고, callback timeout 뒤에는 persisted server state를 확인한 뒤 실제 mismatch만 `wait:false`로 복구합니다.
- **멀티클래스 내성 숙련**: 비시작 클래스의 `Core X Traits`에 포함된 시작 내성 숙련은 제외합니다. Sorcerer 시작/Bard 멀티클래스면 CON/CHA가 시작 내성 숙련입니다.
- **Roll20 선확인**: D&D Beyond에서 ID/이름만 읽은 뒤 Roll20 게임 탭 → 동명 캐릭터 → Legacy OGL5e 시트를 확인합니다. 그 다음 Google/Ollama 번역을 시작합니다.
- **실행 로그 하나**: `workers/<run_id>/`에는 종료 후 `run.log`만 남깁니다. `events.jsonl`과 실행용 settings snapshot은 삭제합니다. `results/current`의 stage JSON/backup JSON은 검증/복구 자료라 유지합니다.

## 적용

```powershell
python apply_runtime_integrity_v22.py --check
python apply_runtime_integrity_v22.py
python -m unittest discover -s tests -v
```

전체 테스트가 통과하면:

```powershell
python main.py
```
