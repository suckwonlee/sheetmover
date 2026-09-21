# D&D Beyond → Roll20 sheet mover

D&D Beyond 캐릭터 데이터를 수집해 Google Cloud Translation으로 1차 번역하고, 필수 로컬 Ollama `gpt-oss:20b`로 위험 규칙만 2차 검열하는 Windows용 시트 이동기입니다.

**현재 작업 단계는 1단계 미리보기입니다. Roll20 실제 입력은 아직 수행하지 않습니다.**

## 실행

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ollama pull gpt-oss:20b
python main.py
```

CLI는 다음처럼 사용할 수 있습니다.

```powershell
python -m sheet_mover --source "https://www.dndbeyond.com/characters/170892133" --cli
```

실행 전에 전체 회귀 테스트가 반드시 통과해야 합니다.

```powershell
python -m unittest discover -s tests -v
```

## v15 번역 구조

### 1. Google 1차 번역 캐시와 2차 검열 캐시 분리

Google 결과와 Ollama 교정 결과를 같은 캐시에 섞지 않습니다.

- `.sheetmover-google-translation-cache.json`: Google 1차 번역만 저장
- `.sheetmover-ollama-review-cache.json`: 현재 검열기 버전에서 통과한 최종 결과만 저장

검열 규칙이나 Ollama 프롬프트가 바뀌어도 Google 1차 번역을 다시 호출할 필요가 없도록 분리했습니다. 검열 캐시는 validator/model/think/glossary 버전을 fingerprint에 포함합니다.

### 2. 캐릭터 전체에서 동일 규칙을 번역 전에 그룹화

D&D Beyond가 같은 규칙을 feature/action 등 여러 위치에 중복 제공하는 경우가 있습니다. HTML/D&D 태그를 제외한 사용자 표시 원문이 동일하면 먼저 같은 규칙 그룹으로 묶습니다.

대표 원문은 D&D 인라인 태그가 가장 적은 것을 우선합니다. 대표 번역이 안전하게 끝나면 중복 사본은 가능한 경우 결정적으로 재사용합니다. 태그를 다시 삽입할 때 대상 한국어가 정확히 한 곳에서만 발견되는 경우에만 처리하며, 두 곳 이상이면 추측하지 않고 일반 번역 경로로 넘깁니다.

### 3. 전체 description이 아니라 문단/문장 단위 의미 검열

뒤 문단의 우연한 키워드가 앞 문장의 누락을 가리지 못하도록 HTML 블록과 문장 단위로 규칙 의미를 확인합니다.

현재 hard semantic invariant는 다음처럼 플레이 규칙을 바꿀 수 있는 고신뢰 관계만 대상으로 합니다.

- 예외: except / excluding / other than
- unless
- instead of
- at least와 `N 미만` 같은 정상 역표현
- 부정: can't / cannot / isn't / must not 등
- until
- half
- twice 및 `두 배`
- once per turn
- up to N
- additional action
- extra attack

`must`, before/after, 일반 if/when 등은 문맥상 다양한 자연 번역이 가능하므로 완화된 검토 신호로 사용합니다.

War Bond의 `아닌 이상`, Nick의 `...이 아닌 ...의 일부로`, Heavy의 `13 미만`, Tough의 `레벨 두 배`처럼 의미가 맞는 한국어 동치는 hard 실패로 보지 않습니다.

### 4. 위험한 규칙 블록만 `gpt-oss:20b` 검토

긴 주문/클래스 설명 전체를 20B 모델에 한 번에 넘기지 않습니다. 위험 신호가 있는 문단만 보냅니다.

- `think="low"`
- temperature 0
- JSON Schema structured output
- 동일 JSON Schema를 프롬프트에도 제공
- timeout 시 1회 재시도

Ollama 서버, Python `ollama` 모듈 또는 정확한 검토 모델이 없으면 UI/CLI 모두 시작하지 않습니다.

### 5. Ollama 결과도 다시 deterministic validation

Ollama가 수정했다고 바로 믿지 않습니다. 다음을 다시 검사합니다.

- HTML/D&D 태그 순서와 종류
- 알려진 D&D 태그 내부 표준 한글명
- 숫자, 주사위식, DC, 거리, HP, Roll20 수식
- 허용되지 않은 문자 체계
- hard semantic invariant

LLM이 `keep`을 반환해도 hard semantic 누락이 남아 있으면 강제 재검열합니다. 그래도 복구하지 못하면 잘못된 한국어를 `complete`로 내보내지 않고 해당 규칙 조각을 영문 원문으로 보존해 `partial`로 표시합니다.

### 6. D&D 태그는 내부 JSON에 유지

`[action]Magic[/action]` 같은 태그는 번역/검증용 의미 메타데이터이므로 내부 translated JSON에서는 유지합니다.

최종 Roll20/사람 표시 단계에서만 `translation_render.strip_dnd_display_tags()`를 사용해 껍데기를 제거합니다. 예:

```text
[action]마법[/action] 행동
→
마법 행동
```

따라서 태그 제거가 번역 검증을 무력화하지 않습니다.

## 결과 진단

`translation_summary`에는 다음 진단값이 포함됩니다.

- `review_model`
- `validator_version`
- `translation_pipeline`
- `review_stats.google_api_calls`
- `review_stats.google_items_sent`
- `review_stats.google_cache_hits`
- `review_stats.review_cache_hits`
- `review_stats.review_candidates`
- `review_stats.ollama_calls`
- `review_stats.ollama_replaced`
- `review_stats.ollama_kept`
- `review_stats.ollama_rejected`
- `review_stats.ollama_timeouts`
- `review_stats.forced_retries`
- `review_stats.deterministic_repairs`
- `review_stats.critical_fallbacks`

최상단 `translation_summary`에도 같은 진단값을 전달하므로 저장된 JSON만 보고 이번 실행에서 실제 Google Translation API 호출이 있었는지 확인할 수 있습니다.

## 환경 변수

```powershell
$env:SHEETMOVER_REVIEW_MODEL="gpt-oss:20b"
$env:SHEETMOVER_OLLAMA_HOST="http://127.0.0.1:11434"
$env:SHEETMOVER_OLLAMA_TIMEOUT="300"
$env:SHEETMOVER_OLLAMA_REVIEW_RETRIES="1"
$env:SHEETMOVER_OLLAMA_THINK="low"
$env:SHEETMOVER_OLLAMA_CONTEXT="4096"
$env:SHEETMOVER_OLLAMA_NUM_PREDICT="1024"
```

필요하면 캐시 경로도 별도로 지정할 수 있습니다.

```powershell
$env:SHEETMOVER_TRANSLATION_CACHE="E:\\sheet_mover\\.sheetmover-google-translation-cache.json"
$env:SHEETMOVER_REVIEW_CACHE="E:\\sheet_mover\\.sheetmover-ollama-review-cache.json"
```

## 현재 단계에서 남은 것

- 최종 능력치/HP/AC/숙련 보너스 등 2단계 계산
- Roll20 2014 시트 필드 매핑
- 동명 캐릭터 탐색 및 중복 경고
- 반복 항목 유지/추가, 단일 값 교체, 저장 결과 재확인

1단계 번역 품질 보정이 검증되기 전에는 다음 단계로 넘어가지 않습니다.
