# Sheet Mover ability-choice v2.6.1

이번 실패는 거인 힘의 벨트 자체를 못 읽은 것이 아닙니다.

업로드된 `바리언트 드라칸` 원본에는:
- 기본 STR 14
- Belt of Fire Giant Strength: equipped=true, attuned=true
- `set / strength-score / 25`
가 정상적으로 들어 있습니다.

실제 실패 원인은 Paladin 4레벨 Ability Score Improvement에서
능력치 +1/+1 대신 Resilient 특기를 골랐는데, D&D Beyond raw modifiers에는
그 ASI용 generic `choose-an-ability-score +1` 두 개가 여전히 남아 있기 때문입니다.
기존 보수적 계산기는 어느 능력치인지 확정할 수 없으면 모든 능력치를 null로
만들어 Roll20에 추측값을 쓰지 않도록 되어 있었습니다.

v2.6.1:
- 선택된 feat child choice가 확인된 ASI component의 generic +1/+1만 제거
- 실제 선택된 ASI(예: CHA 선택)와 feat 보너스는 유지
- Belt of Fire Giant Strength의 STR 25 set 효과 유지
- 실패 직전에 만든 번역 결과를 그대로 재사용
- 캐시에서 원문 raw_source만 다시 정규화하여 계산값/roll20_payload 갱신
- Google/Ollama 번역을 다시 하지 않음

이 캐릭터에서 기대하는 능력치는:
- STR 25
- DEX 8
- CON 16
- INT 8
- WIS 10
- CHA 21

설치:
```powershell
python apply_ability_choice_v261.py
```

전체 복제본 unittest가 통과해야 실제 파일을 수정합니다.

적용 후:
```powershell
python main.py
```
