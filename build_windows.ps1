$ErrorActionPreference = "Stop"

Write-Host "[시트 이동기] Windows 배포 빌드를 시작합니다."

python -m pip install -U -r requirements-build.txt
python -m unittest discover -s tests -v

if (Test-Path ".\build") {
    Remove-Item ".\build" -Recurse -Force
}
if (Test-Path ".\dist\SheetMover") {
    Remove-Item ".\dist\SheetMover" -Recurse -Force
}

python -m PyInstaller --noconfirm .\SheetMover.spec

Write-Host ""
Write-Host "[시트 이동기] 빌드 완료"
Write-Host ("배포 폴더: " + (Resolve-Path ".\dist\SheetMover"))
Write-Host "사용자에게는 dist\SheetMover 폴더 전체를 배포하세요."
