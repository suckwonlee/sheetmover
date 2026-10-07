# Sheet Mover runtime-integrity v2.5

검토 기준 GitHub 커밋:
`c0d3905842da144121a3922ef68fdccf6f30b0c6`

이번 패치는 요청한 범위만 수정합니다.

- Bardic Inspiration 자원 누락
- run.log 단일 로그 품질
- 이전 events.jsonl/settings.json 정리

Bardic Inspiration은 D&D Beyond에서 `maxUses=0`,
`statModifierUsesId=6`으로 내려옵니다. 기존 Stage 12는 이 형식을
지원하지 않아 자원에서 제외했습니다.

v2.5는 해당 능력 수정치를 사용합니다.
견본2는 CHA 14이므로 2/2이며, DDB resetType 1을 따라 short로 기록합니다.
서로 다른 action ID로 동일한 Bardic Inspiration이 두 번 내려오는 경우에도
정확히 동일한 자원은 하나로 합칩니다.

run.log에는 다음을 남깁니다.

- 실행 시작/종료
- 주요 진행 메시지
- 각 단계 시작/완료/실패
- 오류 메시지
- 전체 traceback
- 오류 보고서 위치
- 저장 오류
- Stage 12 자원 요약

다음 반복 로그는 제거합니다.

- 번역 1/155...
- 주문 1/21...
- 특성 1/21...
- 주문 공격 1/6...

UI 진행률 자체는 계속 갱신됩니다.

요청대로 이번 패치에서 건드리지 않는 것:

- 주문 슬롯
- Passive Perception
- Hit Dice
- 번역 품질
- Stage 8 특성 매핑

실행:

```powershell
python apply_runtime_integrity_v25.py
```

설치기는 실제 프로젝트를 수정하기 전에 임시 복제본 전체 unittest를 실행합니다.
하나라도 실패하면 실제 프로젝트 파일은 수정하지 않습니다.

통과 후:

```powershell
python main.py
```
