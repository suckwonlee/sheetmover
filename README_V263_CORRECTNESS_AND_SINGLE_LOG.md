# Sheet Mover v2.6.3.1 correctness + single log

이번 패치는 이전 단일 로그 패치를 포함합니다.

정상 기대값 (D&D Beyond character 125047838):
- STR 25 / DEX 8 / CON 18 / INT 8 / WIS 10 / CHA 22
- HP 134 / 134
- AC 21
- Passive Perception 15
- Divine Sense 7
- Breath Weapon (Necrotic) 5
- GP 10
- Hit Dice 13 / 13

추가 수정:
- top sheet Race 표시용 `race_display`
- spell page `spellclass`
- class_display는 `Paladin 13`을 앞에 표시
- `results/current`은 `sheetmover-run-latest.json` 하나로 통합
- 번역 재사용 데이터는 `results/cache`로 보관

Oath Spells:
현재 DDB raw payload에는 Oath Spells 표의 주문 이름만 있고,
각 주문의 전체 정의가 없습니다. 주문 레벨/세부 효과를 추측해
가짜 주문 행을 만들지 않습니다.

적용:
```powershell
python apply_v2631_correctness_and_single_log.py
```


v2.6.3.1 hotfix:
- results/current paths의 target/basic처럼 키 이름이 _path가 아닌 JSON도 통합합니다.
- 최종 통합 JSON은 기존 full-run의 status/error/reports/paths 구조를 최상위에 그대로 유지합니다.
- 기존 worker protocol 테스트도 새 sheetmover-run-latest.json 경로를 검증하도록 갱신합니다.
