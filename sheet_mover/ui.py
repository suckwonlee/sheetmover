import importlib.util
import json
import os
import queue
import threading
import urllib.request
import webbrowser
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from .mover import SOURCE_URL, is_roll20_game, run
from .source import source_character_id
from .translator import MODEL_NAME


DEFAULT_CDP_URL = os.getenv(
    "ROLL20_CDP_URL",
    "http://127.0.0.1:9222",
)
OLLAMA_VERSION_URL = "http://127.0.0.1:11434/api/version"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"


class SheetMoverUI(tk.Tk):
    def __init__(self, source_url=SOURCE_URL):
        super().__init__()

        self.title("Beyond → Roll20")
        self.geometry("1120x730")
        self.minsize(1000, 650)
        self.configure(bg="#0e131a")

        self.browser_ready = False
        self.ollama_ready = False
        self.running = False
        self.prepared = None
        self.events = queue.Queue()
        self.browser_check_id = 0
        self.ollama_check_id = 0

        self.source_var = tk.StringVar(value=source_url)
        self.cdp_var = tk.StringVar(value=DEFAULT_CDP_URL)
        self.browser_status_var = tk.StringVar(value="확인 중")
        self.ollama_status_var = tk.StringVar(value="확인 중")
        self.progress_var = tk.DoubleVar(value=0)

        self._setup_styles()
        self._build()

        self.cdp_var.trace_add("write", self._connection_changed)
        self.after(100, self._drain_events)

        self.after(250, self.check_browser)
        self.after(450, self.check_ollama)

    def _setup_styles(self):
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
            "Subtitle.TLabel",
            background="#0e131a",
            foreground="#8e9baa",
            font=("Segoe UI", 10),
        )
        style.configure(
            "CardTitle.TLabel",
            background="#171e28",
            foreground="#f0f4f8",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Text.TLabel",
            background="#171e28",
            foreground="#d5dce5",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Muted.TLabel",
            background="#171e28",
            foreground="#8e9baa",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Step.TLabel",
            background="#171e28",
            foreground="#75b8ff",
            font=("Segoe UI", 10, "bold"),
        )

        style.configure(
            "Neutral.Badge.TLabel",
            background="#252e3a",
            foreground="#bcc5d0",
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Good.Badge.TLabel",
            background="#153526",
            foreground="#88e5af",
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Warn.Badge.TLabel",
            background="#3b2d18",
            foreground="#f1c36e",
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Bad.Badge.TLabel",
            background="#3a2027",
            foreground="#ff9da8",
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )

        style.configure(
            "TEntry",
            fieldbackground="#0c1219",
            foreground="#eef3f8",
            insertcolor="#eef3f8",
            bordercolor="#2b3948",
            lightcolor="#2b3948",
            darkcolor="#2b3948",
            padding=9,
            font=("Segoe UI", 10),
        )

        style.configure(
            "TButton",
            background="#25303e",
            foreground="#e7edf5",
            bordercolor="#354456",
            padding=(12, 8),
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
            "Link.TButton",
            background="#172536",
            foreground="#b8dcff",
            bordercolor="#2d4b69",
            padding=(11, 7),
        )

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#0b1117",
            background="#3e92e6",
            thickness=8,
        )

    def _build(self):
        root = ttk.Frame(
            self,
            style="Root.TFrame",
            padding=(22, 18),
        )
        root.pack(fill="both", expand=True)

        header = ttk.Frame(
            root,
            style="Root.TFrame",
        )
        header.pack(fill="x", pady=(0, 16))

        ttk.Label(
            header,
            text="Beyond → Roll20",
            style="Title.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            header,
            text="원본을 수집하고 번역 결과를 확인합니다. Roll20 입력 단계는 준비 중입니다.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        body = ttk.Frame(
            root,
            style="Root.TFrame",
        )
        body.pack(fill="both", expand=True)

        left = ttk.Frame(
            body,
            style="Root.TFrame",
            width=430,
        )
        left.pack(
            side="left",
            fill="y",
            padx=(0, 14),
        )
        left.pack_propagate(False)

        right = ttk.Frame(
            body,
            style="Root.TFrame",
        )
        right.pack(
            side="left",
            fill="both",
            expand=True,
        )

        self._build_browser_card(left)
        self._build_source_card(left)
        self._build_start_card(left)

        self._build_ollama_card(right)
        self._build_log_card(right)

    def _new_card(self, parent):
        outer = tk.Frame(
            parent,
            bg="#171e28",
            highlightthickness=1,
            highlightbackground="#263240",
        )

        inner = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=(17, 15),
        )
        inner.pack(fill="both", expand=True)

        return outer, inner

    def _heading(self, parent, step, title):
        row = ttk.Frame(
            parent,
            style="Card.TFrame",
        )
        row.pack(fill="x", pady=(0, 11))

        ttk.Label(
            row,
            text=step,
            style="Step.TLabel",
        ).pack(side="left")

        ttk.Label(
            row,
            text=title,
            style="CardTitle.TLabel",
        ).pack(side="left", padx=(8, 0))

    def _build_browser_card(self, parent):
        outer, card = self._new_card(parent)
        outer.pack(fill="x", pady=(0, 12))

        self._heading(
            card,
            "1",
            "Roll20 브라우저",
        )

        row = ttk.Frame(
            card,
            style="Card.TFrame",
        )
        row.pack(fill="x", pady=(0, 9))

        ttk.Label(
            row,
            text="디버깅 포트 연결",
            style="Text.TLabel",
        ).pack(side="left")

        self.browser_badge = ttk.Label(
            row,
            textvariable=self.browser_status_var,
            style="Neutral.Badge.TLabel",
        )
        self.browser_badge.pack(side="right")

        ttk.Label(
            card,
            text="CDP 주소",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(1, 4))

        ttk.Entry(
            card,
            textvariable=self.cdp_var,
        ).pack(fill="x")

        buttons = ttk.Frame(
            card,
            style="Card.TFrame",
        )
        buttons.pack(fill="x", pady=(9, 0))

        ttk.Button(
            buttons,
            text="다시 확인",
            command=self.check_browser,
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Edge 실행 방법",
            command=self.show_browser_help,
        ).pack(side="left", padx=(7, 0))

    def _build_source_card(self, parent):
        outer, card = self._new_card(parent)
        outer.pack(fill="x", pady=(0, 12))

        self._heading(
            card,
            "2",
            "D&D Beyond 캐릭터",
        )

        ttk.Label(
            card,
            text="캐릭터 URL",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        ttk.Entry(
            card,
            textvariable=self.source_var,
        ).pack(fill="x")

        ttk.Label(
            card,
            text="옮길 D&D Beyond 캐릭터의 주소를 입력합니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

    def _build_start_card(self, parent):
        outer, card = self._new_card(parent)
        outer.pack(fill="x", pady=(0, 12))

        self._heading(
            card,
            "3",
            "실행",
        )

        self.start_button = ttk.Button(
            card,
            text="원본 수집 · 번역 미리보기",
            style="Primary.TButton",
            command=self.start_move,
            state="disabled",
        )
        self.start_button.pack(fill="x")

        self.preview_button = ttk.Button(card, text="원본 / 번역 결과 보기", command=self.show_preview, state="disabled")
        self.preview_button.pack(fill="x", pady=(6, 0))

        ttk.Progressbar(
            card,
            variable=self.progress_var,
            maximum=100,
        ).pack(fill="x", pady=(12, 0))

        self.start_hint = ttk.Label(
            card,
            text="브라우저와 Ollama 상태를 확인하고 있습니다.",
            style="Muted.TLabel",
            wraplength=370,
            justify="left",
        )
        self.start_hint.pack(
            anchor="w",
            pady=(8, 0),
        )

    def _build_ollama_card(self, parent):
        outer, card = self._new_card(parent)
        outer.pack(fill="x", pady=(0, 12))

        top = ttk.Frame(
            card,
            style="Card.TFrame",
        )
        top.pack(fill="x")

        title_box = ttk.Frame(
            top,
            style="Card.TFrame",
        )
        title_box.pack(
            side="left",
            fill="x",
            expand=True,
        )

        ttk.Label(
            title_box,
            text="번역 엔진",
            style="CardTitle.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            title_box,
            text=f"Ollama · {MODEL_NAME}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.ollama_badge = ttk.Label(
            top,
            textvariable=self.ollama_status_var,
            style="Neutral.Badge.TLabel",
        )
        self.ollama_badge.pack(side="right")

        self.ollama_detail = ttk.Label(
            card,
            text="Ollama 설치 상태를 확인합니다.",
            style="Text.TLabel",
            wraplength=570,
            justify="left",
        )
        self.ollama_detail.pack(
            anchor="w",
            pady=(13, 9),
        )

        buttons = ttk.Frame(
            card,
            style="Card.TFrame",
        )
        buttons.pack(fill="x")

        ttk.Button(
            buttons,
            text="상태 다시 확인",
            command=self.check_ollama,
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="설치 안내 보기",
            style="Link.TButton",
            command=self.show_install_guide,
        ).pack(
            side="left",
            padx=(7, 0),
        )

        self.guide = tk.Frame(
            card,
            bg="#111821",
            highlightthickness=1,
            highlightbackground="#2b3948",
        )

        guide_inner = tk.Frame(
            self.guide,
            bg="#111821",
        )
        guide_inner.pack(
            fill="x",
            padx=13,
            pady=11,
        )

        tk.Label(
            guide_inner,
            text="Ollama 설치 순서",
            bg="#111821",
            fg="#eef2f6",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        for text in (
            "1. Windows용 Ollama를 설치합니다.",
            f"2. PowerShell에서  ollama pull {MODEL_NAME}",
            "3. Python 모듈이 없으면  pip install ollama",
            "4. Ollama를 실행한 뒤 '상태 다시 확인'을 누릅니다.",
        ):
            tk.Label(
                guide_inner,
                text=text,
                bg="#111821",
                fg="#9eabb9",
                font=("Segoe UI", 9),
                justify="left",
            ).pack(
                anchor="w",
                pady=(5, 0),
            )

        guide_buttons = tk.Frame(
            guide_inner,
            bg="#111821",
        )
        guide_buttons.pack(
            fill="x",
            pady=(10, 0),
        )

        ttk.Button(
            guide_buttons,
            text="Ollama 다운로드",
            style="Link.TButton",
            command=lambda: webbrowser.open(
                OLLAMA_DOWNLOAD_URL
            ),
        ).pack(side="left")

        ttk.Button(
            guide_buttons,
            text="설치 명령 복사",
            command=self.copy_install_commands,
        ).pack(
            side="left",
            padx=(7, 0),
        )

    def _build_log_card(self, parent):
        outer, card = self._new_card(parent)
        outer.pack(fill="both", expand=True)

        row = ttk.Frame(
            card,
            style="Card.TFrame",
        )
        row.pack(fill="x", pady=(0, 10))

        ttk.Label(
            row,
            text="작업 로그",
            style="CardTitle.TLabel",
        ).pack(side="left")

        ttk.Button(
            row,
            text="지우기",
            command=self.clear_log,
        ).pack(side="right")

        holder = tk.Frame(
            card,
            bg="#0b1117",
            highlightthickness=1,
            highlightbackground="#263341",
        )
        holder.pack(fill="both", expand=True)

        self.log_box = tk.Text(
            holder,
            bg="#0b1117",
            fg="#c8d1dc",
            insertbackground="#ffffff",
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Cascadia Mono", 9),
            padx=12,
            pady=11,
        )
        self.log_box.pack(
            fill="both",
            expand=True,
        )
        self.log_box.configure(state="disabled")

        self.log("프로그램을 시작했습니다.")

    def check_browser(self):
        if self.running:
            return

        self.browser_ready = False
        self.browser_check_id += 1
        check_id = self.browser_check_id
        self._refresh_start()

        self._badge(
            self.browser_badge,
            self.browser_status_var,
            "확인 중",
            "neutral",
        )

        cdp_url = (
            self.cdp_var.get()
            .strip()
            .rstrip("/")
        )

        def worker():
            try:
                with urllib.request.urlopen(
                    f"{cdp_url}/json/version",
                    timeout=2,
                ) as response:
                    json.loads(
                        response.read().decode("utf-8")
                    )

                with urllib.request.urlopen(
                    f"{cdp_url}/json/list",
                    timeout=2,
                ) as response:
                    pages = json.loads(
                        response.read().decode("utf-8")
                    )

                count = sum(
                    1
                    for page in pages
                    if page.get("type") == "page" and is_roll20_game(page.get("url", ""))
                )

                self._post(
                    self._browser_result,
                    True,
                    count,
                    check_id,
                )

            except Exception:
                self._post(
                    self._browser_result,
                    False,
                    0,
                    check_id,
                )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def _browser_result(
        self,
        connected,
        roll20_count,
        check_id,
    ):
        if check_id != self.browser_check_id:
            return
        self.browser_ready = (
            connected
            and roll20_count > 0
        )

        if not connected:
            self._badge(
                self.browser_badge,
                self.browser_status_var,
                "연결 안 됨",
                "bad",
            )
            self.log(
                "디버깅 포트에 연결하지 못했습니다."
            )

        elif roll20_count == 0:
            self._badge(
                self.browser_badge,
                self.browser_status_var,
                "Roll20 탭 없음",
                "warn",
            )
            self.log(
                "브라우저는 연결됐지만 Roll20 탭이 열려 있지 않습니다."
            )

        else:
            self._badge(
                self.browser_badge,
                self.browser_status_var,
                "연결됨",
                "good",
            )
            self.log(
                f"열린 Roll20 탭 {roll20_count}개를 찾았습니다."
            )

        self._refresh_start()

    def check_ollama(self):
        if self.running:
            return

        self.ollama_ready = False
        self.ollama_check_id += 1
        check_id = self.ollama_check_id
        self._refresh_start()

        self._badge(
            self.ollama_badge,
            self.ollama_status_var,
            "확인 중",
            "neutral",
        )

        def worker():
            module_exists = (
                importlib.util.find_spec("ollama")
                is not None
            )

            server_running = False
            model_exists = False

            try:
                with urllib.request.urlopen(
                    OLLAMA_VERSION_URL,
                    timeout=1.5,
                ) as response:
                    json.loads(
                        response.read().decode("utf-8")
                    )

                server_running = True
                with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
                    models = json.loads(response.read().decode("utf-8")).get("models", [])
                model_exists = any(m.get("name") == MODEL_NAME or m.get("model") == MODEL_NAME for m in models)

            except Exception:
                pass

            self._post(
                self._ollama_result,
                module_exists,
                server_running,
                model_exists,
                check_id,
            )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def _ollama_result(
        self,
        module_exists,
        server_running,
        model_exists,
        check_id,
    ):
        if check_id != self.ollama_check_id:
            return
        self.ollama_ready = (
            module_exists
            and server_running
            and model_exists
        )

        if not module_exists:
            self._badge(
                self.ollama_badge,
                self.ollama_status_var,
                "Python 모듈 필요",
                "warn",
            )
            self.ollama_detail.configure(
                text=(
                    "Ollama는 설치되어 있지만 Python ollama 모듈이 없습니다. "
                    "pip install ollama 를 실행해 주세요."
                )
            )
            self.show_install_guide()
            self.log(
                "Python ollama 모듈이 없습니다."
            )

        elif not server_running:
            self._badge(
                self.ollama_badge,
                self.ollama_status_var,
                "실행 필요",
                "warn",
            )
            self.ollama_detail.configure(
                text=(
                    "Ollama는 설치되어 있지만 로컬 서버가 응답하지 않습니다. "
                    "Ollama를 실행해 주세요."
                )
            )
            self.show_install_guide()
            self.log(
                "Ollama 로컬 서버가 응답하지 않습니다."
            )

        elif not model_exists:
            self._badge(self.ollama_badge, self.ollama_status_var, "모델 확인 필요", "warn")
            self.ollama_detail.configure(text=f"설치된 모델에서 {MODEL_NAME}을 확인하지 못했습니다. ollama pull {MODEL_NAME}")
            self.show_install_guide()
            self.log(f"{MODEL_NAME} 모델이 없거나 모델 목록을 읽지 못했습니다.")

        else:
            self._badge(
                self.ollama_badge,
                self.ollama_status_var,
                "준비됨",
                "good",
            )
            self.ollama_detail.configure(
                text=(
                    f"프로젝트에 지정된 번역 모델 "
                    f"{MODEL_NAME}을 사용합니다."
                )
            )
            self.guide.pack_forget()
            self.log(
                "Ollama 연결을 확인했습니다."
            )

        self._refresh_start()

    def start_move(self):
        if self.running:
            return

        source_url = (
            self.source_var.get().strip()
        )

        cdp_url = (
            self.cdp_var.get().strip()
        )

        try:
            source_character_id(source_url)
        except ValueError as exc:
            messagebox.showwarning(
                "원본 URL 확인",
                str(exc),
            )
            return

        self.running = True
        self.prepared = None
        self.preview_button.configure(state="disabled")
        self.progress_var.set(15)
        self._refresh_start()

        self.log(
            f"작업 시작: {source_url}"
        )

        def worker():
            try:
                result = run(source_url, cdp_url=cdp_url,
                             on_progress=lambda percent, text: self._post(self._progress, percent, text))

            except Exception as exc:
                error_message = str(exc)

                self._post(
                    self._move_failed,
                    error_message,
                )
                return

            self._post(
                self._move_success,
                result,
            )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def _move_success(self, result):
        self.running = False
        self.prepared = result
        self.preview_button.configure(state="normal")
        self.progress_var.set(100)

        name = (
            getattr(result, "name", "")
            or "캐릭터"
        )

        self.log(
            f"미리보기 준비 완료: {name} (Roll20 미입력)"
        )

        self._refresh_start()

        for warning in result.warnings:
            self.log(f"확인 필요: {warning}")
        self.show_preview()

    def _move_failed(self, message):
        self.running = False
        self.progress_var.set(0)

        self.log(
            f"작업 실패: {message}"
        )

        self._refresh_start()

        messagebox.showerror(
            "작업 실패",
            message,
        )

    def _post(self, callback, *args):
        """Worker threads never call Tk (including Tk.after) directly."""
        self.events.put((callback, args))

    def _drain_events(self):
        try:
            while True:
                callback, args = self.events.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_events)

    def _connection_changed(self, *_):
        self.browser_check_id += 1
        self.browser_ready = False
        self._badge(self.browser_badge, self.browser_status_var, "다시 확인 필요", "warn")
        self._refresh_start()

    def _progress(self, percent, text):
        self.progress_var.set(percent)
        self.log(text)

    def show_preview(self):
        if self.prepared is None:
            return
        result = self.prepared
        window = tk.Toplevel(self)
        window.title(f"{result.name} — 원본 / 번역 미리보기")
        window.geometry("1080x720")
        window.minsize(780, 500)
        window.configure(bg="#0e131a")
        header = ttk.Frame(window, style="Root.TFrame", padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="미리보기 · Roll20에는 입력하지 않았습니다.", style="Subtitle.TLabel").pack(side="left")
        ttk.Button(header, text="원본·번역 JSON 저장", command=lambda: self.save_preview(result)).pack(side="right")
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        sections = [("확인 사항", None), ("능력치 / HP", "stats"), ("숙련", "proficiencies"),
                    ("장비", "equipment"), ("주문", "spells"), ("특성", "features")]
        for title, key in sections:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            if key is None:
                text = "\n\n".join(result.warnings)
                text += "\n\n감지한 캠페인:\n" + "\n".join(t["title"] for t in result.roll20_tabs)
                self._preview_text(frame, text)
                continue
            panes = ttk.Panedwindow(frame, orient="horizontal")
            panes.pack(fill="both", expand=True)
            for label, data in (("원본", result.original), ("한국어 번역", result.translated)):
                pane = ttk.Frame(panes, padding=8)
                panes.add(pane, weight=1)
                ttk.Label(pane, text=label).pack(anchor="w", pady=(0, 6))
                value = ({k: data.get(k) for k in ("name", "ability_scores", "hp", "max_hp")}
                         if key == "stats" else data.get(key))
                self._preview_text(pane, json.dumps(value, ensure_ascii=False, indent=2))

    @staticmethod
    def _preview_text(parent, content):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        text = tk.Text(holder, wrap="word", bg="#0b1117", fg="#c8d1dc", font=("Cascadia Mono", 10))
        scroll = ttk.Scrollbar(holder, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        text.insert("1.0", content)
        text.configure(state="disabled")

    def save_preview(self, result):
        filename = filedialog.asksaveasfilename(
            parent=self, title="원본과 번역 결과 저장", defaultextension=".json",
            initialfile=f"sheet-preview-{datetime.now():%Y%m%d-%H%M%S}.json",
            filetypes=[("JSON", "*.json")],
        )
        if not filename:
            return
        try:
            Path(filename).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self)
            return
        self.log(f"원본·번역 결과 저장: {filename}")

    def _badge(
        self,
        label,
        variable,
        text,
        state,
    ):
        variable.set(text)

        styles = {
            "neutral": "Neutral.Badge.TLabel",
            "good": "Good.Badge.TLabel",
            "warn": "Warn.Badge.TLabel",
            "bad": "Bad.Badge.TLabel",
        }

        label.configure(
            style=styles[state]
        )

    def _refresh_start(self):
        enabled = (
            self.browser_ready
            and self.ollama_ready
            and not self.running
        )

        self.start_button.configure(
            state=(
                "normal"
                if enabled
                else "disabled"
            )
        )

        if self.running:
            text = "작업 중입니다."

        elif not self.browser_ready:
            text = (
                "디버깅 포트로 실행한 Edge에서 "
                "Roll20 탭을 열어 주세요."
            )

        elif not self.ollama_ready:
            text = (
                "Ollama 설치/실행 상태를 확인해 주세요."
            )

        else:
            text = (
                "준비 완료. URL을 확인한 뒤 실행할 수 있습니다."
            )

        self.start_hint.configure(
            text=text
        )

    def show_install_guide(self):
        if not self.guide.winfo_ismapped():
            self.guide.pack(
                fill="x",
                pady=(13, 0),
            )

    def copy_install_commands(self):
        text = (
            f"ollama pull {MODEL_NAME}\n"
            "pip install ollama"
        )

        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

        self.log(
            "Ollama 설치 명령을 클립보드에 복사했습니다."
        )

    def show_browser_help(self):
        messagebox.showinfo(
            "Roll20 자동화 Edge",
            "Edge를 아래 옵션으로 실행한 뒤 Roll20 탭을 열어 주세요.\n\n"
            '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
            '--remote-debugging-port=9222 '
            '--user-data-dir="C:\\Roll20EdgeProfile"',
        )

    def clear_log(self):
        self.log_box.configure(
            state="normal"
        )

        self.log_box.delete(
            "1.0",
            "end",
        )

        self.log_box.configure(
            state="disabled"
        )

        self.log(
            "로그를 초기화했습니다."
        )

    def log(self, message):
        stamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.log_box.configure(
            state="normal"
        )

        self.log_box.insert(
            "end",
            f"[{stamp}] {message}\n",
        )

        self.log_box.see("end")

        self.log_box.configure(
            state="disabled"
        )


def main(source_url=SOURCE_URL):
    app = SheetMoverUI(source_url=source_url)
    app.mainloop()


if __name__ == "__main__":
    main()
