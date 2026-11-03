"""remote-db 客戶端 agent（圖形介面版），打包成 exe 給客戶機器使用。

輸入資料庫名稱、帳號（預設 ehrms）、密碼後登入；連上 DB 與 relay 才顯示 6 碼數字配對碼。
DB 主機與中繼伺服器位址記在 exe 同目錄的 agent.ini（不存密碼）。
1 小時沒有任何查詢自動中斷，每收到一筆查詢重新計時。
"""

import asyncio
import configparser
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk

import agent

APP_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
INI = APP_DIR / "agent.ini"
# 打包時以 --add-data 放進 exe，執行時解壓到 sys._MEIPASS
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "icon.ico"
# 中繼伺服器預設為 zrok 固定網址（relay 跑在工程師電腦 8770，經 zrok 公開）
# relay_ips：客戶機器 DNS 解析不到中繼網址時改連的 IP（zrok 公開節點，逗號分隔）；只在 agent.ini，畫面不顯示
DEFAULTS = {"server": ".", "user": "ehrms", "relay": "wss://ehrms-relay.shares.zrok.io",
            "relay_ips": "52.223.10.144,35.71.189.214"}
IDLE_LIMIT = 60 * 60  # 秒，1 小時沒有任何查詢就中斷；每收到一筆查詢重新計時
WARNING = "此工具僅提供查詢語法，不會更動資料庫資料，並會記錄查詢語法以供稽核"


def load_settings():
    cp = configparser.ConfigParser()
    cp.read(INI, encoding="utf-8")
    return {k: cp.get("agent", k, fallback=v) for k, v in DEFAULTS.items()}


def save_settings(values):
    cp = configparser.ConfigParser()
    cp["agent"] = values
    try:
        with open(INI, "w", encoding="utf-8") as f:
            cp.write(f)
    except OSError:
        pass  # 放在唯讀位置時不影響使用


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()  # 背景執行緒 → 介面
        self.busy = False
        self.loop = None
        self.task = None
        self.deadline = None       # 閒置中斷的時間點（time.monotonic），收到查詢時延後
        self.cancel_reason = None  # 主動中斷時要顯示的原因

        root.title("EHRMS 遠端查詢")
        root.resizable(False, False)
        settings = load_settings()
        self.vars = {k: tk.StringVar(value=settings[k]) for k in DEFAULTS}
        self.vars["database"] = tk.StringVar()
        self.db_names = {}  # 下拉選項文字 -> 資料庫名稱
        self.password = tk.StringVar()
        self.status = tk.StringVar()

        self.build_login()
        self.build_session()
        self.login_frame.pack(fill="both")

        root.bind("<Return>", lambda e: self.login())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        root.after(1000, self.tick)

    def warning(self, parent):
        return tk.Label(parent, text=WARNING, fg="#c62828", font=("Microsoft JhengHei UI", 10, "bold"),
                        wraplength=480, justify="left")

    def build_login(self):
        f = self.login_frame = ttk.Frame(self.root, padding=20)
        self.warning(f).grid(row=99, column=0, columnspan=2, sticky="w", pady=(10, 0))
        fields = [
            ("帳號", self.vars["user"], ""),
            ("密碼", self.password, "●"),
            ("DB 主機", self.vars["server"], ""),
            ("資料庫名稱", None, ""),
            ("中繼伺服器", self.vars["relay"], ""),
        ]
        for i, (label, var, show) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            if var is None:  # 資料庫：可偵測的下拉選單，也可直接輸入
                self.db_box = ttk.Combobox(f, textvariable=self.vars["database"], width=24)
                self.db_box.grid(row=i, column=1, pady=4, sticky="w")
                self.detect_btn = ttk.Button(f, text="偵測", width=6, command=self.detect)
                self.detect_btn.grid(row=i, column=1, pady=4, sticky="e")
                continue
            entry = ttk.Entry(f, textvariable=var, show=show, width=34)
            entry.grid(row=i, column=1, pady=4)
            if var is self.password:
                entry.focus_set()
        self.login_btn = ttk.Button(f, text="登入", command=self.login)
        self.login_btn.grid(row=len(fields), column=0, columnspan=2, pady=(12, 4), sticky="ew")
        ttk.Label(f, text="資料庫名稱留空時，登入會自動偵測（通常為客戶統編）", foreground="#666666").grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="w")
        ttk.Label(f, textvariable=self.status, foreground="#b00020", wraplength=320).grid(
            row=len(fields) + 2, column=0, columnspan=2, sticky="w")

    def build_session(self):
        f = self.session_frame = ttk.Frame(self.root, padding=20)
        self.warning(f).pack(anchor="w", pady=(0, 10))
        ttk.Label(f, text="配對碼（請提供給 104 工程師）").pack(anchor="w")
        self.code_var = tk.StringVar()
        row = ttk.Frame(f)
        row.pack(pady=(4, 8))
        tk.Label(row, textvariable=self.code_var, font=("Consolas", 36, "bold"), fg="#1a4d8f").pack(side="left")
        self.copy_btn = ttk.Button(row, text="複製", width=6, command=self.copy_code)
        self.copy_btn.pack(side="left", padx=(16, 0))
        self.info_var = tk.StringVar()
        ttk.Label(f, textvariable=self.info_var, justify="left").pack(anchor="w")
        self.remain_var = tk.StringVar()
        ttk.Label(f, textvariable=self.remain_var, foreground="#1a4d8f").pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text="查詢紀錄").pack(anchor="w", pady=(10, 2))
        self.log_box = scrolledtext.ScrolledText(f, width=72, height=14, state="disabled", font=("Consolas", 9))
        self.log_box.pack()
        ttk.Button(f, text="中斷連線", command=self.disconnect).pack(pady=(10, 0), fill="x")

    # ---- 登入與連線（DB 與 relay 在背景執行緒，避免視窗卡住）----

    def set_busy(self, busy):
        self.busy = busy
        for btn in (self.login_btn, self.detect_btn):
            btn.state(["disabled"] if busy else ["!disabled"])

    def detect(self, then_login=False):
        if self.busy:
            return
        if not self.vars["user"].get().strip() or not self.password.get():
            self.status.set("請先輸入帳號與密碼")
            return
        self.set_busy(True)
        self.status.set("偵測資料庫中…")
        args = (self.vars["server"].get().strip(), self.vars["user"].get().strip(), self.password.get(), then_login)
        threading.Thread(target=self.run_detect, args=args, daemon=True).start()

    def run_detect(self, server, user, password, then_login):
        try:
            self.events.put(("detected", agent.detect_databases(server, user, password), then_login))
        except Exception as e:
            self.events.put(("failed", f"偵測資料庫失敗：{e}"))

    def selected_database(self):
        text = self.vars["database"].get().strip()
        return self.db_names.get(text, text)  # 下拉選項顯示「統編　公司名」，換回資料庫名稱

    def login(self):
        if self.busy:
            return
        values = {k: v.get().strip() for k, v in self.vars.items()}
        if not values["user"] or not self.password.get():
            self.status.set("請輸入帳號與密碼")
            return
        if not values["database"]:
            self.detect(then_login=True)
            return
        values["database"] = self.selected_database()
        save_settings({k: values[k] for k in DEFAULTS})
        self.set_busy(True)
        self.status.set("連線資料庫中…")
        threading.Thread(target=self.run_session, args=(values, self.password.get()), daemon=True).start()

    def run_session(self, values, password):
        try:
            conn_str = agent.build_conn_str(values["server"], values["database"], values["user"], password)
            info = agent.read_customer_info(conn_str)
        except Exception as e:
            self.events.put(("failed", f"資料庫連線失敗：{e}"))
            return
        code = agent.make_code()
        self.events.put(("status", "連線中繼伺服器中…"))
        self.loop = asyncio.new_event_loop()
        self.task = self.loop.create_task(agent.serve(
            values["relay"], code, conn_str, info,
            log=lambda m: self.events.put(("log", m)),
            on_ready=lambda: self.events.put(("ready", code, info)),
            on_query=lambda: self.events.put(("query",)),
            fallback_ips=[ip.strip() for ip in values["relay_ips"].split(",") if ip.strip()],
        ))
        try:
            self.loop.run_until_complete(self.task)
            self.events.put(("failed", "中繼伺服器已關閉連線"))
        except asyncio.CancelledError:
            self.events.put(("failed", self.cancel_reason or "已中斷連線"))
        except Exception as e:
            self.events.put(("failed", f"無法連線或已斷線（{values['relay']}）：{e}"))
        finally:
            self.loop.close()
            self.loop = self.task = None

    def copy_code(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.code_var.get().replace(" ", ""))
        self.copy_btn.configure(text="已複製")
        self.root.after(1500, lambda: self.copy_btn.configure(text="複製"))

    def disconnect(self, reason=None):
        if self.loop and self.task:
            self.cancel_reason = reason
            self.loop.call_soon_threadsafe(self.task.cancel)

    def tick(self):
        if self.deadline is not None:
            left = int(self.deadline - time.monotonic())
            if left <= 0:
                self.deadline = None
                self.disconnect("已 1 小時沒有任何查詢，自動中斷連線；如需繼續請重新登入")
            else:
                self.remain_var.set(f"閒置倒數 {left // 60:02d}:{left % 60:02d}（1 小時無查詢自動中斷，有查詢重新計時）")
        self.root.after(1000, self.tick)

    def on_close(self):
        self.disconnect()
        self.root.destroy()

    # ---- 介面更新 ----

    def poll(self):
        while not self.events.empty():
            kind, *args = self.events.get()
            getattr(self, "on_" + kind)(*args)
        self.root.after(100, self.poll)

    def on_status(self, text):
        self.status.set(text)

    def on_detected(self, found, then_login):
        self.set_busy(False)
        self.db_names = {}
        for d in found:
            label = d["name"] + ("　" + d["company"] if d["company"] else "") + ("" if d["ehrms"] else "（非 EHRMS）")
            self.db_names[label] = d["name"]
        self.db_box["values"] = list(self.db_names)
        ehrms = [d for d in found if d["ehrms"]]
        if not found:
            self.status.set("沒有偵測到可存取的資料庫，請確認帳號權限或手動輸入")
            return
        self.db_box.current(0)
        if not ehrms:
            self.status.set("沒有偵測到 EHRMS 客戶資料庫，請從清單選擇或手動輸入")
        elif len(ehrms) == 1 and then_login:
            self.login()
        else:
            self.status.set(f"偵測到 {len(ehrms)} 個 EHRMS 資料庫，請確認後按登入")

    def on_ready(self, code, info):
        self.code_var.set(f"{code[:3]} {code[3:]}")
        lines = [f"資料庫：{info['database']}（{info['server']}）",
                 f"表單資料庫：{info.get('wf_database') or '無'}"]
        lines += [f"公司：{c['code']} {c['name']}　統編 {c['sno']}（{c['status']}）" for c in info["companies"]]
        if "companies_error" in info:
            lines.append(info["companies_error"])
        self.info_var.set("\n".join(lines))
        self.deadline = time.monotonic() + IDLE_LIMIT
        self.cancel_reason = None
        self.login_frame.pack_forget()
        self.session_frame.pack(fill="both")

    def on_query(self):
        if self.deadline is not None:
            self.deadline = time.monotonic() + IDLE_LIMIT

    def on_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def on_failed(self, text):
        self.deadline = None
        self.cancel_reason = None
        self.set_busy(False)
        self.password.set("")
        self.session_frame.pack_forget()
        self.login_frame.pack(fill="both")
        self.status.set(text)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高解析度螢幕避免模糊
    except Exception:
        pass
    root = tk.Tk()
    try:
        root.iconbitmap(default=str(ICON))
    except tk.TclError:
        pass  # 找不到圖示時沿用預設
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
