# Stage 8 v1.7.3 핫픽스

v1.7.2 설치 스크립트가 주변 코드 블록 전체를 정확히 일치시키려 해서,
실제 `source.py`의 줄바꿈/들여쓰기 차이 때문에 0개를 찾고 실패했습니다.

v1.7.3은 주변 코드에 의존하지 않고 문제 표현식 하나만 직접 바꿉니다.

```python
_text(_dict(category).get("tagName"))
```

→

```python
str(_dict(category).get("tagName") or "").strip()
```

적용:

```powershell
python apply_stage8_integrity_v173.py --check
python apply_stage8_integrity_v173.py
python -m unittest discover -s tests -v
```

365개가 전부 통과하면:

```powershell
python main.py
```
