# 시트 이동기 13단계 — 통합 UI / Windows 배포

## 핵심 원칙

배포본에는 개발자의 Google 인증 정보나 Ollama 모델을 포함하지 않습니다.

### Google Cloud

현재 번역 파이프라인은 Cloud Translation Advanced(v3) + Glossary를 사용합니다.
v3는 단순 API Key 인증을 지원하지 않으므로 사용자는 자신의 Google Cloud 인증을 설정해야 합니다.

권장 방식:

1. Google Cloud 프로젝트 생성 및 결제 설정
2. Cloud Translation API 활성화
3. Google Cloud CLI 설치
4. `gcloud auth application-default login`
5. 시트 이동기 설정 화면에서 프로젝트 ID 입력

또는 자신의 서비스 계정 JSON을 선택할 수 있습니다.
서비스 계정 파일의 내용은 시트 이동기 설정 파일에 복사하지 않습니다.
경로만 저장합니다.

v13부터 Glossary용 Cloud Storage 업로드는 Python Google Cloud Storage
클라이언트를 사용하므로 서비스 계정 방식에서도 개발자의 gcloud 인증을 빌릴 필요가 없습니다.

### Ollama

Ollama 역시 배포본에 포함하지 않습니다.

1. 설정 화면의 `Ollama 설치 페이지` 버튼으로 설치
2. PowerShell에서 `ollama pull gpt-oss:20b`
3. 설정 화면에서 서버 주소와 모델 확인
4. 메인 화면에서 상태가 `정상`인지 확인

## 한 번에 이동

세 상태가 모두 `정상`이어야 실행 버튼이 활성화됩니다.

- Google Cloud
- Ollama
- Roll20 전용 Chrome

Roll20 전용 디버그 Chrome은 자동으로 열지 않습니다.
메인 화면의 `Roll20 전용 Chrome 열기` 버튼을 눌러 실행합니다.
이미 같은 CDP 포트의 전용 Chrome이 실행 중이면 새 창을 만들지 않고 재사용합니다.
전용 프로필은 `%LOCALAPPDATA%\SheetMover\.roll20_chrome_profile`에 저장되므로
일반 Chrome 프로필과 섞이지 않습니다.

사용자는 처음 한 번 전용 Chrome에서 Roll20에 로그인하고 대상 게임을 열어 두면 됩니다.

`시트 이동 시작`은 다음 순서로 실행합니다.

1. D&D Beyond 수집 / 번역 / 계산
2. Roll20 대상 확인
3. 기본 능력치
4. 인벤토리
5. 주문
6. 특성
7. 무기 공격
8. 주문 공격
9. 숙련
10. 자원

기존 폐기된 Stage 9 generic action writer는 통합 실행에 포함하지 않습니다.

## 사용자 데이터 위치

설정:
`%APPDATA%\SheetMover\settings.json`

결과 / 백업 / 캐시:
`%LOCALAPPDATA%\SheetMover\`

따라서 Program Files 같은 읽기 전용 위치에 배포해도 실행 결과를 설치 폴더에 쓰지 않습니다.

## 개발 환경 실행

```powershell
python main.py
```

## Windows onedir 빌드

```powershell
.\build_windows.ps1
```

완료 후:

```text
dist\
└─ SheetMover\
   ├─ SheetMover.exe
   └─ _internal\
```

`dist\SheetMover` 폴더 전체를 배포합니다.


# 비개발자용 처음 설정 안내

## 1. Google 번역

시트 이동기는 제작자의 Google 계정을 공유하지 않습니다. 사용자가 자신의 Google 계정으로 Google Cloud를 준비해야 합니다.

처음 한 번만 다음 순서로 진행합니다.

1. 설정 화면에서 `Google Cloud 콘솔 열기`
2. Google 계정 로그인
3. 새 프로젝트 만들기 또는 기존 프로젝트 선택
4. 프로젝트의 `프로젝트 ID`를 복사해서 시트 이동기 입력란에 붙여넣기
5. `Cloud Translation API 열기` → `사용/ENABLE`
6. `Cloud Storage API 열기` → `사용/ENABLE`
7. `Google Cloud CLI 설치 페이지`에서 Windows용 CLI 설치
8. 시트 이동기를 다시 실행
9. `Google 계정 로그인 시작`
10. 열린 브라우저에서 같은 Google 계정 로그인
11. `Google 연결 확인`
12. `Google 번역 사용 준비 완료`가 나오면 끝

Google Cloud 사용량에 따라 사용자 본인의 Google 계정에 비용이 발생할 수 있습니다.

### 왜 API 키 입력란이 없나?

현재 시트 이동기는 D&D 용어집 기능을 위해 Google Cloud Translation Advanced(v3)를 사용합니다. 이 구조는 일반적인 API Key 하나만 넣는 방식이 아니라 Google 계정 인증이 필요합니다.

## 2. Ollama

1. 설정 화면에서 `Ollama 설치 페이지 열기`
2. Windows용 Ollama 설치
3. 시트 이동기 다시 실행
4. `gpt-oss:20b 모델 설치 시작`
5. 새 창에서 다운로드가 100% 끝날 때까지 기다리기
6. `Ollama 연결 확인`
7. `Ollama 사용 준비 완료`가 나오면 끝

Ollama와 모델은 사용자의 PC에 설치됩니다. 시트 이동기 제작자의 Ollama 서버를 사용하지 않습니다.

## 3. Roll20

1. 메인 화면에서 `Roll20 전용 Chrome 열기` 버튼 누르기
2. 처음 한 번 Roll20 로그인
3. 캐릭터가 들어 있는 게임을 열기
4. D&D Beyond 캐릭터와 Roll20 캐릭터 이름을 같게 맞추기
5. 전용 Chrome을 닫지 않고 시트 이동기로 돌아오기
6. 메인 화면의 Roll20 상태가 `정상`인지 확인

일반 Chrome은 닫을 필요가 없습니다. 전용 Chrome은 별도의 프로필을 사용합니다.

## 4. 최종 확인

메인 화면에서 다음 세 줄이 모두 `정상`이면 준비 완료입니다.

```text
Google Cloud   정상
Ollama         정상
Roll20         정상
```

그 뒤 D&D Beyond 캐릭터 URL을 붙여넣고 `시트 이동 시작`을 누르면 됩니다.

## 중단되었을 때

서버 저장 확인이 실패하면 조회만 최대 3회 재시도합니다. 확인이 끝나지 않으면 완료로 표시하지 않습니다. 이미 입력된 내용은 남아 있을 수 있으며, 자동으로 되돌리지는 않습니다.

번역이 미완성이거나 중간에 실패하면 `results/current`에 `sheet-partial-*.json`을 저장합니다. 이 파일은 정상 입력용 결과로 자동 선택되지 않습니다. `full-run-*.json`에는 완료한 단계, 실패한 단계, 저장된 결과 경로가 남습니다. 프로세스가 갑자기 종료된 경우에는 마지막 기록이 `running`으로 남을 수 있으며, 이는 완료를 뜻하지 않습니다.

실행별 설정 사본, 진행 메시지, 작업 로그는 `%LOCALAPPDATA%/SheetMover/workers/<실행 ID>`에 보관합니다. 프로그램은 완료 메시지와 모든 단계가 통과한 보고서를 확인한 뒤 완료를 표시합니다. 저장 자체가 실패하면 실패 이유를 로그에 표시합니다.

고급 설정의 `adc`는 `gcloud auth application-default login`으로 로그인한 계정을 사용합니다. 서비스 계정에서 `adc`로 바꾸면 이전 서비스 계정 경로를 사용하지 않습니다. 설정을 바꾼 뒤에는 연결을 다시 확인하세요.
