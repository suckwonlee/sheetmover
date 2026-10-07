# Sheet Mover combat/features v2.6

D&D Beyond 원본 PDF와 Roll20 결과를 다시 비교해서 확인된 차이를 한 번에 보정합니다.

수정 대상:
- Weapon Mastery 실제 선택: Musket (Slow), Pistol (Vex), Shortsword (Vex)
- Eldritch Invocations 선택: Pact of the Chain 표시
- Pact of the Chain: Attack 설명 연결
- +3 등 마법 무기 명중/피해 보너스
- Archery +2 원거리 무기 명중
- Unarmed Strike
- 명시적/custom AC 계산
- 기존 번역 캐시를 재사용해도 AC를 raw_source에서 무료 재계산
- 오래된 Sheet Mover 자원 반복행 중 현재 자원과 같은 중복행 정리
  (이번 결과에서 반복된 Fey Step 정리 대상)

그대로 두는 것:
- D&D Beyond 원본 자체에 Contact Other Plane이 두 번 존재하는 경우
- 주문 슬롯 정책
- Passive Perception
- Hit Dice
- 번역 품질/고정 용어
- Bardic Inspiration v2.5.2 계산

설치:
```powershell
python apply_combat_features_v26.py
```

설치기는 임시 복제본에 먼저 적용한 뒤 전체 unittest를 실행합니다.
하나라도 실패하면 실제 프로젝트는 수정하지 않습니다.

적용 완료 후:
```powershell
python main.py
```

동일한 D&D Beyond 원본이면 기존 번역 결과를 재사용하므로
새 번역 API 비용이 발생하지 않아야 합니다.
