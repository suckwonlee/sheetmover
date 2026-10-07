# Stage 5 멀티클래스 지원 v1

`견본2`의 `Sorcerer 1 / Bard 6`에서 단일 클래스 제한으로 중단되는 문제를 수정합니다.

적용:

```powershell
python apply_stage5_multiclass_v1.py --check
python apply_stage5_multiclass_v1.py
python -m unittest discover -s tests -v
python main.py
```

기대 핵심 값:

```text
class = Sorcerer
base_level = 1
multiclass1_flag = 1
multiclass1 = Bard
multiclass1_lvl = 6
level = 7
pb = 3
caster_level = 7
spellcasting_ability = @{charisma_mod}+
```
