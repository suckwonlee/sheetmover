"""Launch a dedicated Chrome profile with CDP enabled for Roll20."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

DEFAULT_URL = "https://app.roll20.net/editor/?viewas="
DEFAULT_PORT = 9222
PROFILE_DIR = Path(".roll20_chrome_profile")


def _chrome_candidates():
    env = os.environ
    values = [
        Path(env.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(env.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    which = shutil.which("chrome") or shutil.which("chrome.exe")
    if which:
        values.insert(0, Path(which))
    return [path for path in values if str(path) and path.is_file()]


def find_chrome():
    candidates = _chrome_candidates()
    if not candidates:
        raise RuntimeError("Google Chrome 실행 파일을 찾지 못했습니다.")
    return candidates[0]


def cdp_is_ready(port=DEFAULT_PORT, timeout=0.6):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def launch(url=DEFAULT_URL, port=DEFAULT_PORT, profile_dir=PROFILE_DIR):
    if cdp_is_ready(port):
        return {"already_running": True, "port": int(port), "profile_dir": str(Path(profile_dir).resolve())}
    chrome = find_chrome()
    profile = Path(profile_dir).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome),
        f"--remote-debugging-port={int(port)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"already_running": False, "port": int(port), "profile_dir": str(profile), "chrome": str(chrome)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    info = launch(args.url, args.port)
    if info["already_running"]:
        print(f"[시트 이동기] 원격 디버깅 Chrome이 이미 실행 중입니다 (포트 {args.port}).")
    else:
        print(f"[시트 이동기] Roll20 전용 Chrome을 열었습니다 (포트 {args.port}).")
        print("[시트 이동기] 이 Chrome 창에서 Roll20에 로그인하고 테스트 게임을 연 상태로 두세요.")
    print(f"[시트 이동기] 로그인 프로필: {info['profile_dir']}")


if __name__ == "__main__":
    main()
