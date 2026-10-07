# v2.4.1 installer repair

v2.4는 실제 프로젝트를 수정하기 전에 생성 테스트 코드를 검사하는 단계에서
따옴표 escaping 오류로 중단되었습니다. 따라서 프로젝트 본체는 수정되지 않았습니다.

이 복구 스크립트는 기존 `apply_runtime_integrity_v24.py`의 잘못된 테스트 assertion만
안전한 형태로 고쳐 `apply_runtime_integrity_v241.py`를 만듭니다.

그 다음:

1. 수정된 설치기 자체를 py_compile 검증
2. 설치기가 생성할 모든 테스트 문자열을 ast.parse 검증
3. 수정된 설치기를 실행
4. 설치기가 프로젝트 복제본 전체 unittest를 실행
5. 전부 통과한 경우에만 실제 프로젝트 적용

실행:

```powershell
python repair_and_apply_runtime_v241.py
```

성공 후:

```powershell
python main.py
```
