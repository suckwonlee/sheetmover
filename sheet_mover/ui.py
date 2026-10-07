"""Stage 13 distributable desktop UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser
from urllib.parse import urlparse
from uuid import uuid4

from .app_config import (
    AppSettings,
    apply_runtime_environment,
    check_google,
    check_ollama,
    check_roll20,
    data_dir,
    load_settings,
    save_settings,
    settings_path,
)


GOOGLE_AUTH_DOC = "https://cloud.google.com/translate/docs/authentication"
GOOGLE_SETUP_DOC = "https://cloud.google.com/translate/docs/setup"
GOOGLE_CONSOLE_URL = "https://console.cloud.google.com/"
GOOGLE_CLI_INSTALL_URL = "https://cloud.google.com/sdk/docs/install"
GOOGLE_TRANSLATE_API_URL = "https://console.cloud.google.com/apis/library/translate.googleapis.com"
GOOGLE_STORAGE_API_URL = "https://console.cloud.google.com/apis/library/storage.googleapis.com"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"

DEFAULT_SOURCE_URL = "https://www.dndbeyond.com/characters/170892133"


class SheetMoverUI(tk.Tk):
    def __init__(self, source_url=DEFAULT_SOURCE_URL):
        super().__init__()
        self.title("시트 이동기")
        self.geometry("1040x760")
        self.minsize(940, 680)

        self.settings = load_settings()
        self.events = queue.Queue()
        self.worker = None
        self.running = False
        self.check_generation = 0
        self.ready = {
            "google": False,
            "ollama": False,
            "roll20": False,
        }

        self.source_var = tk.StringVar(value=source_url)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value="준비 상태를 확인하세요.")

        self.google_status = tk.StringVar(value="확인 필요")
        self.ollama_status = tk.StringVar(value="확인 필요")
        self.roll20_status = tk.StringVar(value="확인 필요")

        self._style()
        self._build()
        self.after(100, self._drain)
        self.after(300, self.check_all)

    def _style(self):
        self.configure(bg="#0e131a")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Root.TFrame", background="#0e131a")
        style.configure("Card.TFrame", background="#171e28")
        style.configure(
            "Title.TLabel",
            background="#0e131a",
            foreground="#f5f7fa",
            font=("Segoe UI", 23, "bold"),
        )
        style.configure(
            "Sub.TLabel",
            background="#0e131a",
            foreground="#96a3b3",
            font=("Segoe UI", 10),
        )
        style.configure(
            "CardTitle.TLabel",
            background="#171e28",
            foreground="#f2f5f8",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Text.TLabel",
            background="#171e28",
            foreground="#d8e0e8",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Muted.TLabel",
            background="#171e28",
            foreground="#8e9baa",
            font=("Segoe UI", 9),
        )
        style.configure(
            "TEntry",
            fieldbackground="#0c1219",
            foreground="#eef3f8",
            insertcolor="#eef3f8",
            bordercolor="#2b3948",
            padding=8,
        )
        style.configure(
            "TButton",
            background="#25303e",
            foreground="#e7edf5",
            bordercolor="#354456",
            padding=(11, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Primary.TButton",
            background="#347fd1",
            foreground="#ffffff",
            bordercolor="#4c98e9",
            padding=(15, 11),
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Treeview",
            background="#0b1117",
            fieldbackground="#0b1117",
            foreground="#d6dee7",
            rowheight=27,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#202a36",
            foreground="#dfe6ee",
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#0b1117",
            background="#3e92e6",
            thickness=9,
        )

    def _card(self, parent):
        outer = tk.Frame(
            parent,
            bg="#171e28",
            highlightthickness=1,
            highlightbackground="#263240",
        )
        inner = ttk.Frame(outer, style="Card.TFrame", padding=15)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _build(self):
        root = ttk.Frame(self, style="Root.TFrame", padding=20)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(
            header,
            text="시트 이동기",
            style="Title.TLabel",
        ).pack(side="left")
        ttk.Button(
            header,
            text="설정",
            command=self.open_settings,
        ).pack(side="right")
        ttk.Label(
            root,
            text=(
                "해당 프로그램은 완벽하지 않습니다. 번역 오류가 발생한 피처나 주문이 있을 수 있습니다."
            ),
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        top = ttk.Frame(root, style="Root.TFrame")
        top.pack(fill="x")

        left_outer, left = self._card(top)
        left_outer.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right_outer, right = self._card(top)
        right_outer.pack(side="left", fill="both", expand=True, padx=(7, 0))

        ttk.Label(left, text="D&D Beyond", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(left, text="캐릭터 URL", style="Muted.TLabel").pack(
            anchor="w", pady=(10, 4)
        )
        ttk.Entry(left, textvariable=self.source_var).pack(fill="x")
        ttk.Label(
            left,
            text="Roll20에는 동일한 이름의 대상 캐릭터가 미리 있어야 합니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(right, text="실행 환경", style="CardTitle.TLabel").pack(anchor="w")
        self._status_row(right, "Google Cloud", self.google_status)
        self._status_row(right, "Ollama", self.ollama_status)
        self._status_row(right, "Roll20", self.roll20_status)

        buttons = ttk.Frame(right, style="Card.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(
            buttons,
            text="모두 다시 확인",
            command=self.check_all,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Roll20 전용 Chrome 열기",
            command=self.launch_roll20,
        ).pack(side="left", padx=(7, 0))

        run_outer, run_card = self._card(root)
        run_outer.pack(fill="x", pady=(14, 0))
        self.start_button = ttk.Button(
            run_card,
            text="시트 이동 시작",
            style="Primary.TButton",
            command=self.start_full_run,
            state="disabled",
        )
        self.start_button.pack(fill="x")
        ttk.Progressbar(
            run_card,
            variable=self.progress_var,
            maximum=100,
        ).pack(fill="x", pady=(12, 0))
        ttk.Label(
            run_card,
            textvariable=self.progress_text,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(7, 0))

        body = ttk.Frame(root, style="Root.TFrame")
        body.pack(fill="both", expand=True, pady=(14, 0))

        stages_outer, stages = self._card(body)
        stages_outer.pack(side="left", fill="both", expand=True, padx=(0, 7))
        log_outer, log = self._card(body)
        log_outer.pack(side="left", fill="both", expand=True, padx=(7, 0))

        ttk.Label(stages, text="진행 단계", style="CardTitle.TLabel").pack(anchor="w")
        self.stage_tree = ttk.Treeview(
            stages,
            columns=("status",),
            show="tree headings",
            height=12,
        )
        self.stage_tree.heading("#0", text="단계")
        self.stage_tree.heading("status", text="상태")
        self.stage_tree.column("#0", width=250)
        self.stage_tree.column("status", width=90, anchor="center")
        self.stage_tree.pack(fill="both", expand=True, pady=(10, 0))

        names = (
            "D&D Beyond 수집 · 번역 · 계산",
            "Roll20 대상 확인",
            "기본 능력치",
            "인벤토리",
            "주문",
            "특성",
            "무기 공격",
            "주문 공격",
            "숙련",
            "자원",
        )
        self.stage_items = {}
        for index, name in enumerate(names, start=1):
            item = self.stage_tree.insert(
                "",
                "end",
                text=name,
                values=("대기",),
            )
            self.stage_items[index] = item

        log_header = ttk.Frame(log, style="Card.TFrame")
        log_header.pack(fill="x")
        ttk.Label(
            log_header,
            text="로그",
            style="CardTitle.TLabel",
        ).pack(side="left")
        ttk.Button(
            log_header,
            text="결과 폴더 열기",
            command=self.open_results,
        ).pack(side="right")

        self.log_box = tk.Text(
            log,
            bg="#0b1117",
            fg="#c8d1dc",
            relief="flat",
            wrap="word",
            font=("Cascadia Mono", 9),
            padx=10,
            pady=10,
        )
        self.log_box.pack(fill="both", expand=True, pady=(10, 0))
        self.log_box.configure(state="disabled")
        self.log("프로그램을 시작했습니다.")

    def _status_row(self, parent, label, variable):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=label, style="Text.TLabel").pack(side="left")
        ttk.Label(row, textvariable=variable, style="Muted.TLabel").pack(side="right")

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", str(text).rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _post(self, func, *args):
        self.events.put((func, args))

    def _drain(self):
        try:
            while True:
                func, args = self.events.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _refresh_start(self):
        allowed = all(self.ready.values()) and not self.running
        self.start_button.configure(state="normal" if allowed else "disabled")

    def check_all(self):
        if self.running:
            return
        self.google_status.set("확인 중")
        self.ollama_status.set("확인 중")
        self.roll20_status.set("확인 중")
        for key in self.ready:
            self.ready[key] = False
        self._refresh_start()

        settings = self.settings
        self.check_generation += 1
        generation = self.check_generation

        def worker():
            results = {
                "google": check_google(settings),
                "ollama": check_ollama(settings),
                "roll20": check_roll20(settings),
            }
            self._post(self._apply_checks, results, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_checks(self, results, generation=None):
        if generation is not None and generation != self.check_generation:
            return
        for key, variable in (
            ("google", self.google_status),
            ("ollama", self.ollama_status),
            ("roll20", self.roll20_status),
        ):
            result = results[key]
            self.ready[key] = bool(result.get("ok"))
            variable.set("정상" if result.get("ok") else "설정 필요")
            self.log(f"[{key}] {result.get('message')}")
            if result.get("detail"):
                self.log(f"  {result.get('detail')}")
        self._refresh_start()

    def _roll20_debug_port(self):
        parsed = urlparse(self.settings.roll20_cdp_url)
        if parsed.port:
            return int(parsed.port)
        return 9222

    def _launch_roll20_impl(self, *, popup_on_error=False):
        try:
            apply_runtime_environment(self.settings)
            from .roll20_browser import launch

            runtime_profile = data_dir() / ".roll20_chrome_profile"
            port = self._roll20_debug_port()
            info = launch(
                port=port,
                profile_dir=runtime_profile,
            )
            self.log(
                (
                    f"Roll20 전용 Chrome이 이미 실행 중입니다. "
                    f"(디버그 포트 {port})"
                )
                if info.get("already_running")
                else (
                    f"Roll20 전용 Chrome을 자동으로 열었습니다. "
                    f"(디버그 포트 {port}) "
                    "로그인 후 대상 게임을 열어 두세요."
                )
            )
            return True
        except Exception as exc:
            self.log("Roll20 전용 Chrome 자동 실행 실패: " + str(exc))
            if popup_on_error:
                messagebox.showerror("Roll20 Chrome", str(exc))
            return False

    def launch_roll20(self):
        if self._launch_roll20_impl(popup_on_error=True):
            self.roll20_status.set("게임 탭 대기")
            self.after(1800, self.check_all)

    def open_results(self):
        folder = data_dir() / "results" / "current"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def open_settings(self):
        if self.running:
            self.log("작업이 끝난 뒤 설정을 변경하세요.")
            return
        SettingsDialog(self, self.settings, self._settings_saved)

    def _settings_saved(self, settings):
        self.settings = settings
        self.log("설정을 저장했습니다.")
        self.check_all()

    def _worker_command(self, settings_file=None):
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--worker-full-run",
                "--source",
                self.source_var.get().strip(),
                "--settings",
                str(settings_file or settings_path()),
            ]
        return [
            sys.executable,
            "-m",
            "sheet_mover.full_run",
            "--source",
            self.source_var.get().strip(),
            "--settings",
            str(settings_file or settings_path()),
        ]

    def start_full_run(self):
        if self.running:
            return
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("시트 이동기", "D&D Beyond URL을 입력하세요.")
            return
        if not all(self.ready.values()):
            messagebox.showwarning(
                "시트 이동기",
                "Google Cloud, Ollama, Roll20 상태를 먼저 정상으로 만드세요.",
            )
            return

        run_id = uuid4().hex
        run_folder = data_dir() / "workers" / run_id
        try:
            snapshot_path = save_settings(self.settings, run_folder / "settings.json")
        except OSError as exc:
            messagebox.showerror("시트 이동기", f"작업 설정을 저장하지 못했습니다: {exc}")
            return
        self.running = True
        self.progress_var.set(0)
        self.progress_text.set("통합 실행을 시작합니다.")
        for item in self.stage_items.values():
            self.stage_tree.set(item, "status", "대기")
        self._refresh_start()

        event_path = run_folder / "events.jsonl"
        log_path = run_folder / "run.log"
        command = self._worker_command(snapshot_path) + [
            "--events", str(event_path), "--log", str(log_path), "--run-id", run_id,
        ]
        self.log("통합 실행 시작")
        self.log(f"작업 기록: {run_folder}")
        self.log(f"실행 로그: {log_path}")

        def worker():
            env = os.environ.copy()
            try:
                from .worker_protocol import monitor_worker
                # Even native/library output is kept when a frozen worker cannot start.
                with log_path.open("ab") as startup_log:
                    proc = subprocess.Popen(
                        command, stdout=startup_log, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, env=env,
                        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       if os.name == "nt" else 0),
                    )
                    self.worker = proc
                    code, error = monitor_worker(
                        proc, event_path, run_id,
                        lambda event: self._post(self._worker_event, event),
                    )
                self._post(self._worker_done, code, error)
            except Exception as exc:
                self._post(self._worker_failure, str(exc))
            finally:
                # Keep only run.log for one GUI execution.
                try:
                    for transient in list(run_folder.iterdir()):
                        try:
                            if transient.resolve() == log_path.resolve():
                                continue
                            if transient.is_dir():
                                shutil.rmtree(transient, ignore_errors=True)
                            else:
                                transient.unlink(missing_ok=True)
                        except OSError:
                            pass
                except OSError:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _worker_event(self, event):
        kind = event.get("type")
        if kind == "progress":
            self.progress_var.set(min(99, max(0, float(event.get("percent") or 0))))
            self.progress_text.set(str(event.get("message") or ""))
            self.log(event.get("message") or "")
        elif kind == "stage":
            index = int(event.get("index") or 0)
            item = self.stage_items.get(index)
            if item:
                status = {
                    "running": "진행 중",
                    "pass": "완료",
                    "error": "실패",
                }.get(event.get("status"), event.get("status") or "")
                self.stage_tree.set(item, "status", status)
            if event.get("message"):
                self.log(f"[{event.get('name')}] {event.get('message')}")
        elif kind == "complete":
            self.progress_text.set("완료 보고서 확인 중")
            self.log("완료 보고서 검증 통과")
        elif kind == "error":
            self.progress_text.set("실패")
            self.log("오류: " + str(event.get("message") or ""))
            trace = event.get("traceback")
            if trace:
                self.log(trace)
            for error in event.get("save_errors") or []:
                self.log(error)

    def _worker_done(self, code, error):
        self.running = False
        self.worker = None
        self._refresh_start()
        if code == 0 and not error:
            self.progress_var.set(100)
            self.progress_text.set("완료")
            messagebox.showinfo("시트 이동기", "Roll20 시트 이동이 완료되었습니다.")
        else:
            self.progress_text.set("중단 · 완료 확인 실패")
            for item in self.stage_items.values():
                if self.stage_tree.set(item, "status") == "진행 중":
                    self.stage_tree.set(item, "status", "중단 · 확인 필요")
            if error:
                self.log(error)
            messagebox.showerror(
                "시트 이동기",
                (error or "시트 이동이 중단되었습니다.")
                + "\n완료된 단계는 유지됩니다. 다시 실행하면 같은 값은 건너뜁니다. 세부 원인은 run.log를 확인하세요.",
            )

    def _worker_failure(self, message):
        self.running = False
        self.worker = None
        self._refresh_start()
        self.progress_text.set("작업 프로세스 실행 실패")
        self.log("작업 프로세스 실행 실패: " + message)
        messagebox.showerror("시트 이동기", message)


class ScrollableSettingsTab(ttk.Frame):
    """Keep long setup pages reachable without capturing other windows' wheels."""

    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        background = ttk.Style(self).lookup("TFrame", "background") or "#d9d9d9"
        self.canvas = tk.Canvas(
            self, highlightthickness=0, borderwidth=0,
            background=background,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.content = ttk.Frame(self.canvas, padding=18)
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._update_scrollregion)
        self.canvas.bind("<Configure>", self._resize_content)
        self._wheel_remainder = 0.0
        self._wheel_tag = f"SheetMoverSettingsWheel:{self}"
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_class(self._wheel_tag, sequence, self._on_mousewheel)
        self.bind("<Destroy>", self._cleanup_wheel_bindings, add=True)

    def _update_scrollregion(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event):
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def bind_mousewheel(self):
        # A tag before the widget's class also prevents a wheel over an input
        # from accidentally changing its value instead of scrolling the page.
        def bind_children(widget):
            tags = widget.bindtags()
            if self._wheel_tag not in tags:
                widget.bindtags((self._wheel_tag,) + tags)
            for child in widget.winfo_children():
                bind_children(child)
        bind_children(self)

    def _on_mousewheel(self, event):
        region = self.canvas.bbox("all")
        if not region or region[3] - region[1] <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)
            return "break"
        if getattr(event, "num", None) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            delta = event.delta
            scale = 1 if self.tk.call("tk", "windowingsystem") == "aqua" else 120
            self._wheel_remainder -= delta / scale
            steps = int(self._wheel_remainder)
            self._wheel_remainder -= steps
        if steps:
            first = self.canvas.yview()[0]
            self.canvas.yview_moveto(first + steps * 72 / (region[3] - region[1]))
        return "break"

    def _cleanup_wheel_bindings(self, event):
        if event.widget is self:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.unbind_class(self._wheel_tag, sequence)


class SettingsDialog(tk.Toplevel):
    """처음 설치하는 비개발자도 순서대로 따라갈 수 있는 설정창."""

    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("시트 이동기 처음 설정")
        self.geometry("860x760")
        self.minsize(820, 700)
        self.transient(parent)
        self.grab_set()
        self.on_save = on_save

        settings = settings.normalized()
        self.project_var = tk.StringVar(value=settings.google_project_id)
        self.auth_var = tk.StringVar(value=settings.google_auth_mode)
        self.credential_var = tk.StringVar(value=settings.google_credentials_file)
        self.ollama_host_var = tk.StringVar(value=settings.ollama_host)
        self.ollama_model_var = tk.StringVar(value=settings.ollama_model)
        self.cdp_var = tk.StringVar(value=settings.roll20_cdp_url)
        self.google_test_var = tk.StringVar(value="아직 확인하지 않음")
        self.ollama_test_var = tk.StringVar(value="아직 확인하지 않음")

        notebook = ttk.Notebook(self)
        self.setting_tabs = []
        for title in ("처음 설정", "1. Google 번역", "2. Ollama", "3. Roll20", "고급 설정"):
            tab = ScrollableSettingsTab(notebook)
            notebook.add(tab, text=title)
            self.setting_tabs.append(tab)
        first, google, ollama, roll20, advanced = [tab.content for tab in self.setting_tabs]

        self._build_first_tab(first, notebook)
        self._build_google_tab(google)
        self._build_ollama_tab(ollama)
        self._build_roll20_tab(roll20)
        self._build_advanced_tab(advanced)
        for tab in self.setting_tabs:
            tab.bind_mousewheel()

        footer = ttk.Frame(self, padding=(14, 0, 14, 14))
        footer.pack(side="bottom", fill="x")
        ttk.Label(
            footer,
            text="처음이라면 Google → Ollama → Roll20 순서로 끝낸 뒤 '저장하고 확인'을 누르세요.",
        ).pack(side="left")
        ttk.Button(footer, text="취소", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="저장하고 확인", command=self._save).pack(side="right", padx=(0, 6))
        notebook.pack(fill="both", expand=True, padx=14, pady=(14, 8))

    @staticmethod
    def _heading(parent, text):
        ttk.Label(parent, text=text, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 5))

    @staticmethod
    def _body(parent, text, pady=(0, 10)):
        ttk.Label(parent, text=text, wraplength=760, justify="left").pack(anchor="w", pady=pady)

    @staticmethod
    def _step(parent, number, title, text):
        box = ttk.LabelFrame(parent, text=f"{number}. {title}", padding=12)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text=text, wraplength=720, justify="left").pack(anchor="w")
        return box

    def _build_first_tab(self, parent, notebook):
        self._heading(parent, "처음 사용하는 분은 이것만 따라 하세요")
        self._body(
            parent,
            "시트 이동기는 제작자의 Google 계정이나 AI 서버를 빌려 쓰지 않습니다. "
            "Google 번역은 본인의 Google Cloud 계정으로, Ollama는 본인의 PC에서 직접 실행됩니다. "
            "아래 1 → 2 → 3 순서대로 한 번만 설정하면 됩니다.",
        )

        box = self._step(parent, 1, "Google 번역 준비", "Google Cloud에서 본인 프로젝트를 하나 만들고 번역 기능을 켠 뒤, 본인 Google 계정으로 로그인합니다. 처음에는 단계가 몇 개 있지만 한 번만 하면 됩니다.")
        ttk.Button(box, text="Google 번역 설정 자세히 보기", command=lambda: notebook.select(1)).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 2, "Ollama 설치", "Ollama는 번역 결과를 한 번 더 검사하는 로컬 AI 프로그램입니다. Ollama를 설치하고 gpt-oss:20b 모델을 한 번 내려받으면 됩니다.")
        ttk.Button(box, text="Ollama 설정 자세히 보기", command=lambda: notebook.select(2)).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 3, "Roll20 로그인", "시트 이동기가 Roll20 전용 Chrome을 자동으로 엽니다. 그 창에서 Roll20에 로그인하고 캐릭터가 있는 게임을 열어 두면 됩니다.")
        ttk.Button(box, text="Roll20 설정 자세히 보기", command=lambda: notebook.select(3)).pack(anchor="w", pady=(8,0))
        self._body(parent, "설정을 다 끝낸 뒤 '저장하고 확인'을 누르면 메인 화면에서 Google / Ollama / Roll20을 자동 검사합니다. 세 항목이 모두 '정상'이면 '시트 이동 시작' 버튼이 활성화됩니다.", pady=(8,0))

    def _build_google_tab(self, parent):
        self._heading(parent, "Google 번역 설정 — 처음 한 번만 하면 됩니다")
        self._body(parent, "중요: 이 프로그램은 단순 API 키를 입력하는 방식이 아닙니다. D&D 용어집을 적용하기 위해 Google Cloud Translation Advanced를 사용하므로 본인의 Google 계정으로 로그인해야 합니다. 아래 1번부터 차례대로 진행하세요.")
        notice = ttk.LabelFrame(parent, text="먼저 알아둘 점", padding=12); notice.pack(fill="x", pady=(0,10))
        ttk.Label(notice, text="Google Cloud는 사용량에 따라 비용이 발생할 수 있습니다. 결제 수단과 사용량은 사용자 본인의 Google Cloud 계정에서 관리됩니다. 시트 이동기 제작자의 결제 정보나 인증 정보는 사용하지 않습니다.", wraplength=720, justify="left").pack(anchor="w")

        box = self._step(parent, 1, "Google Cloud 프로젝트 만들기", "아래 버튼을 눌러 Google Cloud 콘솔을 엽니다.\n처음이면 Google 계정으로 로그인 → 새 프로젝트 만들기 → 원하는 프로젝트 이름 지정 순서로 진행하세요.\n이미 본인 프로젝트가 있다면 새로 만들지 않고 그 프로젝트를 써도 됩니다.")
        ttk.Button(box, text="Google Cloud 콘솔 열기", command=lambda: webbrowser.open(GOOGLE_CONSOLE_URL)).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 2, "프로젝트 ID 복사해서 붙여넣기", "Google Cloud 화면 위쪽의 프로젝트 선택 메뉴에서 '프로젝트 ID'를 확인할 수 있습니다.\n프로젝트 이름이 아니라 프로젝트 ID를 넣어야 합니다. 예: my-sheet-project-123456")
        ttk.Entry(box, textvariable=self.project_var).pack(fill="x", pady=(8,0))
        box = self._step(parent, 3, "필요한 Google 기능 켜기", "Cloud Translation API와 Cloud Storage API를 각각 활성화하세요.\n아래 버튼을 하나씩 누른 뒤 열린 페이지에서 '사용' 또는 'ENABLE'을 누릅니다.\n프로젝트 선택을 묻는다면 1번에서 만든 같은 프로젝트를 선택하세요.")
        row = ttk.Frame(box); row.pack(fill="x", pady=(8,0))
        ttk.Button(row, text="Cloud Translation API 열기", command=lambda: webbrowser.open(self._project_url(GOOGLE_TRANSLATE_API_URL))).pack(side="left")
        ttk.Button(row, text="Cloud Storage API 열기", command=lambda: webbrowser.open(self._project_url(GOOGLE_STORAGE_API_URL))).pack(side="left", padx=(6,0))
        box = self._step(parent, 4, "Google Cloud CLI 설치", "Google 계정 로그인을 시트 이동기가 확인하려면 Google Cloud CLI가 필요합니다.\n아래 버튼 → Windows 설치 프로그램 다운로드 → 기본 설정대로 설치하세요.\n설치가 끝나면 시트 이동기를 한 번 껐다 켜는 것이 가장 확실합니다.")
        ttk.Button(box, text="Google Cloud CLI 설치 페이지", command=lambda: webbrowser.open(GOOGLE_CLI_INSTALL_URL)).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 5, "Google 계정으로 로그인", "Google Cloud CLI 설치가 끝났다면 아래 버튼을 누르세요.\n브라우저가 열리면 1번에서 사용한 Google 계정으로 로그인하고 권한을 허용합니다.\n검은 창이 잠깐 열릴 수 있으며 정상입니다.")
        ttk.Button(box, text="Google 계정 로그인 시작", command=self._start_google_login).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 6, "연결 확인", "1~5번을 끝냈다면 아래 버튼을 누르세요. 'Google 번역 사용 준비 완료'가 나오면 끝입니다.")
        row = ttk.Frame(box); row.pack(fill="x", pady=(8,0))
        ttk.Button(row, text="Google 연결 확인", command=self._test_google).pack(side="left")
        ttk.Label(row, textvariable=self.google_test_var).pack(side="left", padx=(10,0))

    def _build_ollama_tab(self, parent):
        self._heading(parent, "Ollama 설정 — PC에 AI 모델을 설치합니다")
        self._body(parent, "Ollama는 Google 번역 결과 중 규칙이 이상하게 번역된 부분을 사용자 PC에서 다시 확인합니다. 제작자의 Ollama 서버나 별도 AI API 키를 사용하지 않습니다.")
        box = self._step(parent, 1, "Ollama 설치", "아래 버튼을 눌러 Windows용 Ollama를 다운로드하고 설치하세요. 설치가 끝나면 보통 Ollama가 자동으로 실행됩니다.")
        ttk.Button(box, text="Ollama 설치 페이지 열기", command=lambda: webbrowser.open(OLLAMA_DOWNLOAD_URL)).pack(anchor="w", pady=(8,0))
        box = self._step(parent, 2, "번역 검사용 모델 설치", "시트 이동기는 기본적으로 gpt-oss:20b 모델을 사용합니다.\nOllama 설치 후 아래 버튼을 누르면 모델 다운로드를 시작합니다. 파일이 크기 때문에 인터넷 속도에 따라 시간이 걸릴 수 있습니다.\n새로 열린 창에서 다운로드가 100% 완료될 때까지 기다리세요.")
        row = ttk.Frame(box); row.pack(fill="x", pady=(8,0))
        ttk.Button(row, text="gpt-oss:20b 모델 설치 시작", command=self._install_ollama_model).pack(side="left")
        ttk.Button(row, text="설치 명령만 복사", command=self._copy_ollama_command).pack(side="left", padx=(6,0))
        box = self._step(parent, 3, "연결 확인", "설치가 끝난 뒤 아래 버튼을 누르세요. 'Ollama 사용 준비 완료'가 나오면 끝입니다.")
        row = ttk.Frame(box); row.pack(fill="x", pady=(8,0))
        ttk.Button(row, text="Ollama 연결 확인", command=self._test_ollama).pack(side="left")
        ttk.Label(row, textvariable=self.ollama_test_var).pack(side="left", padx=(10,0))
        info = ttk.LabelFrame(parent, text="일반 사용자는 아래 값 그대로 두세요", padding=12); info.pack(fill="x", pady=(6,0))
        ttk.Label(info, text="서버 주소").grid(row=0,column=0,sticky="w")
        ttk.Entry(info, textvariable=self.ollama_host_var).grid(row=0,column=1,sticky="ew",padx=(10,0),pady=4)
        ttk.Label(info, text="모델").grid(row=1,column=0,sticky="w")
        ttk.Entry(info, textvariable=self.ollama_model_var).grid(row=1,column=1,sticky="ew",padx=(10,0),pady=4)
        info.columnconfigure(1, weight=1)

    def _build_roll20_tab(self, parent):
        self._heading(parent, "Roll20 설정 — 대부분 자동입니다")
        self._body(parent, "Roll20용 Chrome은 시트 이동기를 실행할 때 자동으로 열립니다. 일반 Chrome과 별도의 로그인 공간을 사용하므로 기존 브라우저를 닫을 필요가 없습니다.")
        self._step(parent, 1, "전용 Chrome에서 Roll20 로그인", "처음 한 번만 Roll20 계정으로 로그인하세요. 다음 실행부터는 같은 전용 프로필을 사용하므로 로그인 상태가 유지됩니다.")
        self._step(parent, 2, "캐릭터가 있는 게임 열기", "Roll20에서 실제로 옮길 캐릭터가 들어 있는 게임을 엽니다.\nD&D Beyond 캐릭터 이름과 Roll20 캐릭터 이름이 같아야 자동으로 안전하게 대상을 찾을 수 있습니다.")
        self._step(parent, 3, "전용 Chrome을 그대로 두기", "게임 화면이 열린 전용 Chrome을 닫지 말고 그대로 둔 채 시트 이동기 메인 화면으로 돌아오세요.\n메인 화면의 Roll20 상태가 '정상'이면 준비 완료입니다.")
        note = ttk.LabelFrame(parent, text="문제가 있을 때만", padding=12); note.pack(fill="x", pady=(8,0))
        ttk.Label(note, text="전용 Chrome이 열리지 않으면 메인 화면의 'Roll20 전용 Chrome 열기' 버튼을 다시 누르세요. 그래도 안 되면 고급 설정의 연결 주소를 확인합니다.", wraplength=720, justify="left").pack(anchor="w")

    def _build_advanced_tab(self, parent):
        self._heading(parent, "고급 설정 — 일반 사용자는 건드리지 않아도 됩니다")
        self._body(parent, "회사용 계정이나 특별한 네트워크 설정을 사용하는 경우에만 아래 항목이 필요합니다.")
        google_box = ttk.LabelFrame(parent, text="Google 고급 인증", padding=12); google_box.pack(fill="x", pady=(0,12))
        ttk.Label(google_box, text="기본값 adc는 위의 'Google 계정 로그인' 방식입니다. 서비스 계정 JSON은 Google Cloud 사용 경험이 있는 사람만 사용하세요.", wraplength=720, justify="left").pack(anchor="w")
        ttk.Label(google_box, text="인증 방식").pack(anchor="w", pady=(10,2))
        ttk.Combobox(google_box, textvariable=self.auth_var, values=("adc","service_account"), state="readonly").pack(fill="x")
        ttk.Label(google_box, text="서비스 계정 JSON 파일").pack(anchor="w", pady=(10,2))
        row = ttk.Frame(google_box); row.pack(fill="x")
        ttk.Entry(row, textvariable=self.credential_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="찾기", command=self._browse_credentials).pack(side="left", padx=(6,0))
        roll20_box = ttk.LabelFrame(parent, text="Roll20 연결 주소", padding=12); roll20_box.pack(fill="x")
        ttk.Label(roll20_box, text="기본값을 바꾸지 않는 것을 권장합니다. 9222 포트가 다른 프로그램과 충돌할 때만 변경하세요.", wraplength=720, justify="left").pack(anchor="w")
        ttk.Entry(roll20_box, textvariable=self.cdp_var).pack(fill="x", pady=(8,0))

    def _project_url(self, base):
        project = self.project_var.get().strip()
        if not project:
            return base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}project={project}"

    def _browse_credentials(self):
        path = filedialog.askopenfilename(title="Google 서비스 계정 JSON 선택", filetypes=[("JSON", "*.json"), ("모든 파일", "*.*")])
        if path:
            self.credential_var.set(path)

    def _gcloud_executable(self):
        return shutil.which("gcloud") or shutil.which("gcloud.cmd") or shutil.which("gcloud.exe")

    def _ollama_executable(self):
        return shutil.which("ollama") or shutil.which("ollama.exe")

    def _start_google_login(self):
        executable = self._gcloud_executable()
        if not executable:
            messagebox.showwarning("Google Cloud CLI가 없습니다", "Google Cloud CLI가 아직 설치되지 않았습니다.\n\n먼저 'Google Cloud CLI 설치 페이지'에서 설치한 뒤 시트 이동기를 다시 실행하세요.")
            webbrowser.open(GOOGLE_CLI_INSTALL_URL)
            return
        try:
            subprocess.Popen([executable, "auth", "application-default", "login"], creationflags=(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0))
            messagebox.showinfo("Google 로그인", "Google 로그인 절차를 시작했습니다.\n\n열린 브라우저에서 Google 계정 로그인을 끝낸 뒤 이 창으로 돌아와 'Google 연결 확인'을 누르세요.")
        except Exception as exc:
            messagebox.showerror("Google 로그인 시작 실패", str(exc))

    def _copy_ollama_command(self):
        model = self.ollama_model_var.get().strip() or "gpt-oss:20b"
        command = f"ollama pull {model}"
        self.clipboard_clear(); self.clipboard_append(command)
        messagebox.showinfo("복사 완료", f"아래 명령을 복사했습니다.\n\n{command}\n\nPowerShell에 붙여넣고 Enter를 누르면 됩니다.")

    def _install_ollama_model(self):
        executable = self._ollama_executable()
        if not executable:
            messagebox.showwarning("Ollama가 없습니다", "Ollama 프로그램이 아직 설치되지 않았습니다.\n\n먼저 'Ollama 설치 페이지 열기'에서 설치한 뒤 시트 이동기를 다시 실행하세요.")
            webbrowser.open(OLLAMA_DOWNLOAD_URL)
            return
        model = self.ollama_model_var.get().strip() or "gpt-oss:20b"
        try:
            subprocess.Popen([executable, "pull", model], creationflags=(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0))
            messagebox.showinfo("모델 설치 시작", f"{model} 다운로드를 시작했습니다.\n\n새로 열린 창에서 다운로드가 100% 완료될 때까지 기다린 뒤 'Ollama 연결 확인'을 누르세요.")
        except Exception as exc:
            messagebox.showerror("Ollama 모델 설치 실패", str(exc))

    def _current_settings(self):
        return AppSettings(google_project_id=self.project_var.get(), google_auth_mode=self.auth_var.get(), google_credentials_file=self.credential_var.get(), ollama_host=self.ollama_host_var.get(), ollama_model=self.ollama_model_var.get(), roll20_cdp_url=self.cdp_var.get()).normalized()

    def _test_google(self):
        self.google_test_var.set("확인 중..."); self.update_idletasks()
        result = check_google(self._current_settings())
        if result.get("ok"):
            self.google_test_var.set("Google 번역 사용 준비 완료")
            messagebox.showinfo("Google 연결 정상", "Google Cloud Translation 연결이 정상입니다.\nGoogle 번역 설정은 끝났습니다.")
        else:
            self.google_test_var.set("아직 설정이 필요합니다")
            detail = result.get("detail") or ""
            messagebox.showwarning("Google 연결 실패", f"{result.get('message')}\n\n{detail}\n\n위의 1~5번을 다시 확인하세요.")

    def _test_ollama(self):
        self.ollama_test_var.set("확인 중..."); self.update_idletasks()
        result = check_ollama(self._current_settings())
        if result.get("ok"):
            self.ollama_test_var.set("Ollama 사용 준비 완료")
            messagebox.showinfo("Ollama 연결 정상", "Ollama와 번역 검사용 모델이 정상적으로 준비되었습니다.")
        else:
            self.ollama_test_var.set("아직 설정이 필요합니다")
            detail = result.get("detail") or ""
            installed = result.get("installed_models") or []
            installed_text = "\n현재 설치된 모델: " + ", ".join(installed) if installed else ""
            messagebox.showwarning("Ollama 확인 실패", f"{result.get('message')}\n\n{detail}{installed_text}\n\n위의 1~2번을 다시 확인하세요.")

    def _save(self):
        settings = self._current_settings()
        save_settings(settings)
        self.destroy()
        self.on_save(settings)


def main(source_url=DEFAULT_SOURCE_URL):
    SheetMoverUI(source_url=source_url).mainloop()
