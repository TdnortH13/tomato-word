"""Word 2019 外观的阅读器外壳。

设计原则：
- 一切都是"文档"：搜索结果是文档目录，章节正文是文档正文，书架是文档列表。
- 视觉上对齐 Word 2019：蓝色标题栏 + Ribbon 选项卡 + 灰色工作区 + 白纸 + 状态栏。
- 老板键 Ctrl+Alt+W 一键在"假周报"和"正在读的书"之间切换。
- 阅读态下敲任意键吐下一段（word-touchfish 的手感），PgUp/PgDn 换章。
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import demo
from .engine import Chapter, EngineError, TomatoEngine, html_to_text

# ---------------------------------------------------------------- 配色
TITLE_BG = "#185ABD"
RIBBON_BG = "#FFFFFF"
TABBAR_BG = "#F3F2F1"
WS_BG = "#F3F2F1"
PAGE_BG = "#FFFFFF"
PAGE_BORDER = "#D6D4D1"
STATUS_BG = "#F3F2F1"
TXT = "#1F1F1F"
MUTED = "#767676"
ACCENT = "#185ABD"
BORDER = "#E1DFDD"
HOVER = "#EAE8E6"
CHROME_FAMILY = "Microsoft YaHei UI"
BODY_FAMILY = "宋体"

LINES_PER_PAGE = 38


def cf(size=9, bold=False):
    return (CHROME_FAMILY, size, "bold") if bold else (CHROME_FAMILY, size)


def split_paragraphs(text: str) -> list[str]:
    """把章节正文切成适合排版的段落。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in text.split("\n") if b.strip()]
    out: list[str] = []
    for b in blocks:
        if len(b) <= 300:
            out.append(b)
            continue
        buf = ""
        for seg in re.split(r"(?<=[。！？!?…”])", b):
            if not seg:
                continue
            if buf and len(buf) + len(seg) > 260:
                out.append(buf)
                buf = seg
            else:
                buf += seg
        if buf:
            out.append(buf)
    return out


# ---------------------------------------------------------------- 全局老板键
class HotkeyListener(threading.Thread):
    """Ctrl+Alt+W 全局热键（窗口不在前台也有效）。"""

    WM_HOTKEY = 0x0312
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000

    def __init__(self, callback, vk: int = 0x57):
        super().__init__(daemon=True)
        self.callback = callback
        self.vk = vk
        self.ok = False
        self._stop = False
        self._tid = 0

    def run(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._tid = kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT, self.vk):
                return
            self.ok = True
            msg = wintypes.MSG()
            while not self._stop:
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    if msg.message == self.WM_HOTKEY:
                        try:
                            self.callback()
                        except Exception:
                            pass
                else:
                    time.sleep(0.03)
            user32.UnregisterHotKey(None, 1)
        except Exception:
            return

    def stop(self) -> None:
        self._stop = True


# ---------------------------------------------------------------- 主窗口
class WordApp:
    def __init__(self, root: tk.Tk, engine: TomatoEngine, store, demo_mode: bool = False):
        self.root = root
        self.engine = engine
        self.store = store
        self.demo_mode = demo_mode
        # 引擎连不上时自动降级到官网直连；这两个字段给界面显示用
        self.web_fallback = False
        self._ime_ctx = None
        self._ime_ok = False
        self._engine_reason = ""

        self.mode = "doc"
        self.font_size = int(store.ui("font_size", 12) or 12)
        # 打字机模式：默认关闭（整章直接铺开），开了才"敲一个字吐一段"
        self.typing_mode = bool(store.ui("typing_mode", False))
        self._nav: list = []
        self._tag_seq = 0

        # 阅读态
        self.read_book: dict | None = None
        self.read_chapters: list[Chapter] = []
        self.read_index = 0
        self._paras: list[str] = []
        self._shown = 0
        self._typing_job = None
        self._saved_view = None

        self._busy = False
        self._status_msg = ""
        self._link_seq = 0

        self._build_window()
        self._build_ribbon()
        self._build_workspace()
        self._build_statusbar()
        self._bind_keys()

        self._hotkey = HotkeyListener(self._on_hotkey)
        self._hotkey.start()

        self.show_document()
        if demo_mode:
            # 演示模式没有引擎，直接落在书城，方便看有哪些书
            self.show_home()
        else:
            self.root.after(150, self._connect_engine)
        # 给窗口挂 IME 上下文。Tk 的窗口在启动过程中可能被重建，
        # 所以隔一段时间重试几次；挂不上也没关系，搜索框支持 Ctrl+V 粘贴。
        for delay in (1200, 3000, 6000):
            self.root.after(delay, self._force_ime)

    # ============================================================ 窗口骨架
    def _build_window(self) -> None:
        r = self.root
        # Tk 在 Windows 上默认把输入法（IME）关着，中文候选框根本不弹。
        # 这一句才是"能不能打中文"的真正开关，窗口边框只是前提条件。
        try:
            r.tk.call("tk", "useinputmethods", True)
        except Exception:
            pass
        # 用系统原生的标题栏。两个原因：
        # 1. Tk 的无边框窗口（overrideredirect）脱离 WM，输入法（IME）拿不到
        #    焦点，中文打不进 Entry；即使用 Win32 剥 GWL_STYLE 也治不好。
        # 2. 真正的 Word 窗口本来就有系统标题栏（浅色、带最小化/最大化/关闭），
        #    自己再画一条深蓝假标题栏反而是两条，不如让系统那条充当。
        r.overrideredirect(False)
        r.title("第三季度项目进展周报（草稿） - Word")
        r.configure(bg=WS_BG)
        w, h = 1200, 780
        self._win_size = (w, h)
        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        r.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//3)}")
        r.minsize(860, 520)
        r.after(600, self._as_app_window)

    def _top_hwnd(self):
        """Tk 的 toplevel 是「子窗口 + 外层 wrapper」，返回最外层那个。"""
        hwnd = self.root.winfo_id()
        if sys.platform != "win32":
            return hwnd
        try:
            import ctypes
            user32 = ctypes.windll.user32
            for _ in range(8):
                parent = user32.GetParent(hwnd)
                if not parent:
                    break
                hwnd = parent
        except Exception:
            pass
        return hwnd

    def _as_app_window(self) -> None:
        """无边框窗口默认带 WS_EX_TOOLWINDOW（所以没有任务栏按钮、不在 Alt+Tab）。

        改成 WS_EX_APPWINDOW 并让样式立即生效。注意不能用 withdraw/deiconify 重新映射，
        那会让 Tk 重建 HWND，样式又丢回去。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20
            user32 = ctypes.windll.user32
            # Tk 的无边框窗口是"子窗口 + 外层 wrapper"两层，任务栏认的是最外层，往上找到顶
            hwnd = self.root.winfo_id()
            for _ in range(8):
                parent = user32.GetParent(hwnd)
                if not parent:
                    break
                hwnd = parent
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
            self._taskbar_hwnd = hwnd
        except Exception:
            pass

    def _build_ribbon(self) -> None:
        ribbon = tk.Frame(self.root, bg=RIBBON_BG)
        ribbon.pack(side="top", fill="x")

        tabs = tk.Frame(ribbon, bg=TABBAR_BG, height=30)
        tabs.pack(fill="x")
        tabs.pack_propagate(False)

        self._tab_labels = {}
        for name in ("文件", "开始", "插入", "设计", "布局", "引用", "邮件", "审阅", "视图", "帮助"):
            holder = tk.Frame(tabs, bg=TABBAR_BG)
            holder.pack(side="left", fill="y")
            accent = tk.Frame(holder, bg=TABBAR_BG, height=2)
            accent.pack(fill="x")
            lb = tk.Label(holder, text=name, bg=TABBAR_BG, fg="#444444", font=cf(9), padx=11, pady=4)
            lb.pack(fill="both", expand=True)
            lb.bind("<Button-1>", lambda e, n=name: self._select_tab(n))
            self._tab_labels[name] = (holder, accent, lb)

        body = tk.Frame(ribbon, bg=RIBBON_BG, height=96)
        body.pack(fill="x")
        body.pack_propagate(False)
        self.ribbon_body = body

        # 剪贴板
        g = self._group(body, "剪贴板")
        self._big_button(g, "📋", "粘贴")

        # 字体
        g = self._group(body, "字体")
        row = tk.Frame(g, bg=RIBBON_BG)
        row.pack(anchor="w")
        self._combo(row, "宋体")
        self._combo(row, str(self.font_size), width=5)
        for txt in ("B", "I", "U"):
            lb = tk.Label(row, text=txt, bg=RIBBON_BG, fg="#333333", font=cf(10, True), width=3, pady=2)
            lb.pack(side="left", padx=1)
            self._hover(lb, RIBBON_BG, HOVER)
        row2 = tk.Frame(g, bg=RIBBON_BG)
        row2.pack(anchor="w", pady=(2, 0))
        for txt in ("A⁺", "A⁻", "ab", "Aa"):
            lb = tk.Label(row2, text=txt, bg=RIBBON_BG, fg="#333333", font=cf(9), padx=5, pady=2)
            lb.pack(side="left")
            self._hover(lb, RIBBON_BG, HOVER)
        row2.winfo_children()[0].bind("<Button-1>", lambda e: self.change_font(+1))
        row2.winfo_children()[1].bind("<Button-1>", lambda e: self.change_font(-1))

        # 段落
        g = self._group(body, "段落")
        row = tk.Frame(g, bg=RIBBON_BG)
        row.pack(anchor="w")
        for txt in ("≡", "☰", "⇤", "⇥"):
            lb = tk.Label(row, text=txt, bg=RIBBON_BG, fg="#333333", font=cf(10), width=3, pady=2)
            lb.pack(side="left", padx=1)
            self._hover(lb, RIBBON_BG, HOVER)
        row2 = tk.Frame(g, bg=RIBBON_BG)
        row2.pack(anchor="w", pady=(2, 0))
        for txt in ("•", "1.", "⋮≡", "¶"):
            lb = tk.Label(row2, text=txt, bg=RIBBON_BG, fg="#333333", font=cf(9), width=3, pady=2)
            lb.pack(side="left", padx=1)
            self._hover(lb, RIBBON_BG, HOVER)

        # 样式
        g = self._group(body, "样式")
        row = tk.Frame(g, bg=RIBBON_BG)
        row.pack(anchor="w")
        for txt, fn in (("正文", None), ("标题 1", None), ("标题 2", None)):
            lb = tk.Label(row, text=txt, bg="#F7F7F7", fg="#333333", font=cf(9), padx=8, pady=6)
            lb.pack(side="left", padx=2)
            self._hover(lb, "#F7F7F7", HOVER)

        # 阅读 / 查找
        g = self._group(body, "查找", last=True)
        row = tk.Frame(g, bg=RIBBON_BG)
        row.pack(anchor="w")
        box = tk.Frame(row, bg="#FFFFFF", highlightbackground="#C8C6C4", highlightthickness=1)
        box.pack(side="left")
        self.search_var = tk.StringVar()
        # Tk 8.6 在 Windows 上不处理 IME/TSF，输入法的中文候选框往往弹不出来。
        # 所以这里做了三手准备，保证一定能输入中文：
        #   1) 显式绑定 Ctrl+V 粘贴（剪贴板走 WM_PASTE，完全不过输入法）
        #   2) 右键菜单：粘贴 / 全选 / 清空
        #   3) 尝试用 imm32 给窗口强行挂一个 IME 上下文（能挂上就直接打）
        self.search_entry = ttk.Entry(
            box, textvariable=self.search_var, width=18, font=cf(9),
        )
        self.search_entry.pack(side="left", padx=6, pady=3)
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        self.search_entry.bind("<Escape>", lambda e: self.root.focus_set())
        self.search_entry.bind("<Control-v>", self._paste_into_search)
        self.search_entry.bind("<Control-V>", self._paste_into_search)
        self.search_entry.bind("<Control-a>", self._select_all_search)
        self.search_entry.bind("<Button-3>", self._search_menu)
        self.search_entry.bind("<FocusIn>", self._on_search_focus)
        tk.Label(row, text="⌕", bg=RIBBON_BG, fg="#666666", font=cf(10), padx=4).pack(side="left")

        row2 = tk.Frame(g, bg=RIBBON_BG)
        row2.pack(anchor="w", pady=(4, 0))
        for txt, cmd in (
            ("书城", self.show_home),
            ("书架", self.show_shelf),
            ("搜索", self.do_search),
            ("粘贴搜", self._search_clipboard),
            ("打字机", self.toggle_typing),
            ("伪装", self.show_document),
        ):
            lb = tk.Label(row2, text=txt, bg="#F7F7F7", fg="#333333", font=cf(9), padx=9, pady=3)
            lb.pack(side="left", padx=2)
            lb.bind("<Button-1>", lambda e, c=cmd: c())
            self._hover(lb, "#F7F7F7", HOVER)

        self._select_tab("开始")

    def _build_workspace(self) -> None:
        ws = tk.Frame(self.root, bg=WS_BG)
        ws.pack(side="top", fill="both", expand=True)
        self.ws = ws

        self.page = tk.Frame(ws, bg=PAGE_BG, highlightbackground=PAGE_BORDER, highlightthickness=1)
        self.text = tk.Text(
            self.page, bg=PAGE_BG, fg=TXT, bd=0, relief="flat", highlightthickness=0,
            wrap="word", padx=58, pady=46, insertwidth=0, cursor="arrow",
            selectbackground="#CDE0F5", selectforeground=TXT,
            font=(BODY_FAMILY, self.font_size),
        )
        self.text.pack(fill="both", expand=True)

        # 纸右侧的细滚动条（Word 风格：无箭头、窄条）
        self.vsb = tk.Scrollbar(
            self.page, orient="vertical", command=self.text.yview,
            width=10, bd=0, relief="flat", highlightthickness=0,
            troughcolor=PAGE_BG, bg="#D2D0CE", activebackground="#A19F9D",
        )
        self.text.configure(yscrollcommand=self.vsb.set)
        self.vsb.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0, width=10)

        self.text.tag_configure("h1", font=(CHROME_FAMILY, 17, "bold"), spacing1=6, spacing3=16, foreground="#1A1A1A")
        self.text.tag_configure("h2", font=(CHROME_FAMILY, 12, "bold"), spacing1=12, spacing3=6, foreground="#1A1A1A")
        self.text.tag_configure("body", font=(BODY_FAMILY, self.font_size), spacing1=7, spacing3=7, lmargin1=0, lmargin2=0)
        self.text.tag_configure("indent", font=(BODY_FAMILY, self.font_size), spacing1=7, spacing3=7, lmargin1=24, lmargin2=24)
        self.text.tag_configure("meta", font=cf(9), foreground=MUTED, spacing3=10)
        self.text.tag_configure("link", font=(BODY_FAMILY, self.font_size), foreground=ACCENT, spacing1=4, spacing3=4)
        self.text.tag_configure("small", font=cf(9), foreground="#333333")
        self.text.configure(state="disabled")

        self.text.bind("<MouseWheel>", self._on_wheel)
        ws.bind("<Configure>", self._layout_page)
        self.root.after(60, self._layout_page)

        # 右下角缩放把手
        self.grip = tk.Label(ws, text="◢", bg=WS_BG, fg="#B9B7B4", font=cf(9), cursor="size_nw_se")
        self.grip.place(relx=1.0, rely=1.0, anchor="se")
        self.grip.bind("<ButtonPress-1>", self._resize_start)
        self.grip.bind("<B1-Motion>", self._resize_move)
        self._resize_from = None

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=STATUS_BG, height=26, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.statusbar = bar

        self.st_page = tk.Label(bar, text="第 1 页，共 1 页", bg=STATUS_BG, fg="#444444", font=cf(9), padx=12)
        self.st_page.pack(side="left", pady=3)
        self.st_words = tk.Label(bar, text="字数：0", bg=STATUS_BG, fg="#444444", font=cf(9), padx=12)
        self.st_words.pack(side="left")
        self.st_hint = tk.Label(bar, text="", bg=STATUS_BG, fg=MUTED, font=cf(9), padx=12)
        self.st_hint.pack(side="left")

        for txt in ("▤", "▥", "▦"):
            tk.Label(bar, text=txt, bg=STATUS_BG, fg="#666666", font=cf(9), padx=5).pack(side="right")
        tk.Label(bar, text="100%", bg=STATUS_BG, fg="#444444", font=cf(9), padx=8).pack(side="right")
        for txt, fn in (("−", lambda: self.change_font(-1)), ("+", lambda: self.change_font(+1))):
            lb = tk.Label(bar, text=txt, bg=STATUS_BG, fg="#444444", font=cf(10), padx=6)
            lb.pack(side="right")
            lb.bind("<Button-1>", lambda e, f=fn: f())

        self.text.bind("<KeyRelease>", lambda e: None)
        self.text.bind("<Configure>", lambda e: self._update_status())

    # ============================================================ 小部件工具
    def _hover(self, widget, normal, hot):
        widget.bind("<Enter>", lambda e: widget.configure(bg=hot))
        widget.bind("<Leave>", lambda e: widget.configure(bg=normal))

    def _group(self, parent, name, last=False):
        holder = tk.Frame(parent, bg=RIBBON_BG)
        holder.pack(side="left", fill="y", padx=(10, 0), pady=4)
        inner = tk.Frame(holder, bg=RIBBON_BG)
        inner.pack(fill="both", expand=True)
        tk.Label(holder, text=name, bg=RIBBON_BG, fg="#8A8886", font=cf(8)).pack(side="bottom", pady=(2, 0))
        if not last:
            sep = tk.Frame(parent, bg=BORDER, width=1)
            sep.pack(side="left", fill="y", pady=8, padx=(10, 0))
        return inner

    def _big_button(self, parent, glyph, label):
        holder = tk.Frame(parent, bg=RIBBON_BG)
        holder.pack(side="left", padx=2)
        top = tk.Label(holder, text=glyph, bg=RIBBON_BG, fg="#333333", font=cf(15), padx=12, pady=2)
        top.pack()
        tk.Label(holder, text=label, bg=RIBBON_BG, fg="#333333", font=cf(8)).pack()
        self._hover(top, RIBBON_BG, HOVER)

    def _combo(self, parent, text, width=10):
        box = tk.Frame(parent, bg="#FFFFFF", highlightbackground="#C8C6C4", highlightthickness=1)
        box.pack(side="left", padx=(0, 3))
        tk.Label(box, text=text, bg="#FFFFFF", fg="#333333", font=cf(9), width=width, anchor="w", padx=4, pady=2).pack(side="left")
        tk.Label(box, text="▾", bg="#FFFFFF", fg="#666666", font=cf(7), padx=3).pack(side="left")

    def _select_tab(self, name: str) -> None:
        for n, (holder, accent, lb) in self._tab_labels.items():
            active = n == name
            bg = RIBBON_BG if active else TABBAR_BG
            holder.configure(bg=bg)
            accent.configure(bg=ACCENT if active else TABBAR_BG)
            lb.configure(bg=bg, fg="#1F1F1F" if active else "#444444")

    # ============================================================ 拖拽/缩放
    def _drag_start(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(), ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        if getattr(self.root, "_maxed", False):
            return
        dx, dy = self._drag
        self.root.geometry(f"+{ev.x_root - dx}+{ev.y_root - dy}")

    def toggle_max(self) -> None:
        if getattr(self.root, "_maxed", False):
            self.root.geometry(self.root._restore_geom)
            self.root._maxed = False
        else:
            self.root._restore_geom = self.root.geometry()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{sw}x{sh-40}+0+0")
            self.root._maxed = True

    def minimize(self) -> None:
        # 窗口现在是普通窗口（只剥了标题栏样式），iconify() 本来就能用；
        # 仍然优先走 Win32 SW_MINIMIZE，避免 Tk 在样式被改过后判断错状态。
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = getattr(self, "_taskbar_hwnd", None) or self._top_hwnd()
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                return
            except Exception:
                pass
        try:
            self.root.iconify()
        except Exception:
            self.root.withdraw()

    def _resize_start(self, ev):
        self._resize_from = (ev.x_root, ev.y_root, self.root.winfo_width(), self.root.winfo_height())

    def _resize_move(self, ev):
        if not self._resize_from:
            return
        x0, y0, w0, h0 = self._resize_from
        w = max(860, w0 + (ev.x_root - x0))
        h = max(520, h0 + (ev.y_root - y0))
        self.root.geometry(f"{w}x{h}")

    def _layout_page(self, ev=None):
        w = self.ws.winfo_width()
        h = self.ws.winfo_height()
        pw = min(1000, max(420, w - 90))
        ph = max(200, h - 34)
        self.page.place(relx=0.5, y=16, anchor="n", width=pw, height=ph)

    def _on_wheel(self, ev):
        self.text.yview_scroll(int(-1 * (ev.delta / 120)) * 3, "units")
        self._update_status()
        return "break"

    # ============================================================ 快捷键
    def _bind_keys(self) -> None:
        r = self.root
        r.bind("<Control-Alt-w>", lambda e: self._on_hotkey())
        r.bind("<Control-Alt-W>", lambda e: self._on_hotkey())
        r.bind("<Control-f>", lambda e: self._focus_search())
        r.bind("<Control-o>", lambda e: self.show_shelf())
        r.bind("<Control-h>", lambda e: self.show_home())
        r.bind("<Control-t>", lambda e: self.toggle_typing())
        r.bind("<Control-plus>", lambda e: self.change_font(+1))
        r.bind("<Control-minus>", lambda e: self.change_font(-1))
        r.bind("<Next>", lambda e: self.page_down())
        r.bind("<Prior>", lambda e: self.page_up())
        r.bind("<BackSpace>", self._on_back)
        r.bind("<Key>", self._on_key)
        r.bind("<Escape>", lambda e: self.root.focus_set())

    def _on_back(self, ev=None):
        focus = self.root.focus_get()
        if focus is not None and focus.winfo_class() in ("Entry", "TEntry"):
            return None  # 输入框里的退格要留给输入框
        self.go_back()
        return "break"

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def _on_key(self, ev):
        if self.mode != "read":
            return
        focus = self.root.focus_get()
        if focus is not None and focus.winfo_class() in ("Entry", "TEntry"):
            return
        if ev.state & 0x4:  # Ctrl
            return
        if ev.keysym in ("Next", "Prior", "Up", "Down", "Left", "Right", "Escape",
                         "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"):
            return
        if ev.char and ev.char.isprintable():
            if self.typing_mode:
                self._reveal_next()
            else:
                self.page_down()

    def _on_hotkey(self):
        def toggle():
            if self.mode == "doc":
                if self._saved_view:
                    self._saved_view()
                else:
                    self.show_shelf()
            else:
                self._saved_view = self._current_render
                self.show_document()

        self.root.after(0, toggle)

    # ============================================================ 视图：伪装文档
    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _ins(self, s: str, tag: str = "body") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", s, tag)
        self.text.configure(state="disabled")

    def _link(self, label: str, cb) -> None:
        """在正文里插一段可点击的蓝色文字（用于"重试 / 演示模式"这类操作）。"""
        tag = f"lnk{self._link_seq}"
        self._link_seq += 1
        self.text.configure(state="normal")
        self.text.insert("end", label, ("link", tag))
        self.text.configure(state="disabled")
        self.text.tag_bind(tag, "<Button-1>", lambda e: cb())
        self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))

    def _set_title(self, s: str) -> None:
        # 标题直接写进系统标题栏 —— 那才是 Word 的样子
        try:
            self.root.title(f"{s} - Word")
        except Exception:  # noqa: BLE001
            pass

    # ============================================================ 导航
    def _enter_view(self, mode: str) -> None:
        """切换视图时把"当前视图"压进返回栈，供 BackSpace / 返回用。"""
        prev = getattr(self, "mode", None)
        if prev and prev != mode:
            cur = getattr(self, "_current_render", None)
            if cur is not None:
                self._nav.append(cur)
                if len(self._nav) > 30:
                    self._nav.pop(0)

    def go_back(self) -> None:
        if not self._nav:
            self.set_status("已经回到最早的一页了")
            return
        fn = self._nav.pop()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"返回失败：{exc}")

    def _new_tag(self, prefix: str = "t") -> str:
        self._tag_seq += 1
        return f"{prefix}{self._tag_seq}"

    def _quick_search(self, kw: str) -> None:
        self.search_var.set(kw)
        self.do_search()

    def toggle_typing(self) -> None:
        self.typing_mode = not self.typing_mode
        self.store.set_ui("typing_mode", self.typing_mode)
        self.set_status("打字机模式：" + ("开（敲一下吐一段）" if self.typing_mode else "关（整章直接显示）"))
        if self.mode == "read":
            self._shown = 8 if self.typing_mode else len(self._paras)
            self._render_read(keep_progress=self.typing_mode)

    # ============================================================ 翻页
    def _scroll_page(self, direction: int) -> bool:
        """在正文里滚一屏；返回是否真的滚动了（False = 已经到顶/到底）。"""
        before = self.text.yview()
        self.text.yview_scroll(direction, "pages")
        self.root.update_idletasks()
        moved = self.text.yview() != before
        if moved:
            self._update_status()
        return moved

    def page_down(self) -> None:
        if self.mode == "read" and self._typing_job is not None:
            return
        if not self._scroll_page(+1) and self.mode == "read":
            self.next_chapter()

    def page_up(self) -> None:
        if not self._scroll_page(-1) and self.mode == "read":
            self.prev_chapter()

    # ============================================================ 视图：书城首页
    HOME_KEYWORDS = ("玄幻", "都市", "悬疑", "科幻", "历史", "言情")

    def _safe_categories(self):
        """官网的 18 个分类；拿不到就返回空（书城照样能用）。"""
        getter = getattr(self.engine, "categories", None)
        if getter is None:
            return []
        try:
            return list(getter())
        except Exception:
            return []

    def _safe_homepage(self):
        """首页推荐拿不到就退榜单，再不行就空列表（不要让书城变成报错页）。"""
        try:
            books = self.engine.homepage()
        except Exception:
            books = []
        if not books:
            try:
                books = self.engine.rank("1")
            except Exception:
                books = []
        return books

    def show_home(self, books=None, cats=None) -> None:
        self._enter_view("home")
        self.mode = "home"
        self._current_render = lambda: self.show_home(books, cats)
        self._saved_view = self._current_render
        self._clear()
        self._set_title("书城")
        if self.demo_mode:
            self._ins("演示数据 · ", "meta")
            self._link("点这里接入番茄真实书城", self._connect_engine)
            self._ins("\n\n", "meta")
        elif self.web_fallback:
            self._ins("官网直连 · 番茄小说全站榜单已收录本地书库\n", "meta")
            self._ins("\n", "meta")
        self._ins("番茄书城\n", "h1")
        self._ins(
            "点分类可按类翻（每类 30–40 本）；搜索框可输书名／作者／分类名，"
            "也可直接粘贴官网书籍链接或数字书号。\n",
            "meta",
        )
        # 分类：能拿到官网那 18 个就用真的，否则退回硬编码
        real_cats = cats or []
        self._ins("分类浏览：", "body")
        if real_cats:
            for code, name in real_cats:
                self._link(
                    f" {name} ",
                    lambda c=code, n=name: self.show_category(c, n),
                )
        else:
            for kw in self.HOME_KEYWORDS:
                self._link(f" {kw} ", lambda k=kw: self._quick_search(k))
        self._ins("\n\n", "body")
        if not self.demo_mode and (books is None or cats is None):
            self._ins("热门推荐\n", "h2")
            self._ins("正在载入…\n", "meta")
            if not self._busy:
                self._run(
                    lambda: (self._safe_homepage(), self._safe_categories()),
                    lambda pair: self.show_home(pair[0], pair[1]),
                    what="载入书城",
                )
            return
        if books is None:
            books = demo.demo_books()
        self._ins(f"热门推荐（{len(books)} 本）\n", "h2")
        if not books:
            self._ins("没拿到推荐列表，可以直接用搜索。\n", "body")
        self._render_book_list(books)
        self.text.yview_moveto(0)
        self.set_status("")
        self._update_status()

    def _render_book_list(self, books) -> None:
        """书单渲染（书城/分类/搜索共用）：序号 + 书名(可点) + 作者 + 摘要。"""
        for i, raw in enumerate(books, 1):
            b = self._as_dict(raw)
            tag = self._new_tag("hb")
            self.text.configure(state="normal")
            self.text.insert("end", f"{i}. {b.get('title','')}", ("link", tag))
            self.text.insert("end", f"　{b.get('author','')}\n", "small")
            if b.get("abstract"):
                self.text.insert("end", f"　　{b['abstract'][:60]}\n", "meta")
            self.text.configure(state="disabled")
            self.text.tag_bind(tag, "<Button-1>", lambda e, bk=b: self.open_book(bk))
            self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))

    def _safe_cat(self, code: str):
        getter = getattr(self.engine, "by_category", None)
        if getter is None:
            return []
        try:
            return list(getter(code, limit=20))
        except Exception:
            return []

    def show_category(self, code: str, name: str, books=None) -> None:
        """分类榜单（官网 /rank/0_1_<code>，每类十本）。"""
        self._enter_view(f"cat:{code}")
        self.mode = "cat"
        self._current_render = lambda: self.show_category(code, name, books)
        self._saved_view = self._current_render
        self._clear()
        self._set_title(f"分类 · {name}")
        self._ins(f"{name}\n", "h1")
        self._ins("分类榜单 · 点书名看目录\n", "meta")
        if books is None:
            if self.demo_mode:
                books = demo.demo_books()
            else:
                self._ins("正在载入…\n", "meta")
                if getattr(self.engine, "by_category", None) is None:
                    self._ins("当前引擎不支持分类浏览，改用搜索。\n", "body")
                    self._link("返回书城", self.show_home)
                    return
                if not self._busy:
                    self._run(
                        lambda: self._safe_cat(code),
                        lambda bs: self.show_category(code, name, bs),
                        what=f"载入分类 {name}",
                    )
                return
        books = books or []
        self._ins(f"共 {len(books)} 本\n\n", "meta")
        if not books:
            self._ins("这个分类没抓到书。\n", "body")
        self._render_book_list(books)
        self._ins("\n", "body")
        self._link("返回书城", self.show_home)
        self._ins("　", "body")
        self._link("我的书架", self.show_shelf)
        self.text.yview_moveto(0)
        self.set_status("")
        self._update_status()

    def show_document(self) -> None:
        self._enter_view("doc")
        self.mode = "doc"
        self._current_render = self.show_document
        self._clear()
        self._set_title("番茄文档1")
        self._ins(demo.DOC_TITLE, "h1")
        self._ins("编制：项目管理办公室        更新日期：本周五        版本：v0.3（草稿）\n", "meta")
        for para in demo.DOC_PARAGRAPHS:
            tag = "body"
            if re.match(r"^[一二三四五六七八九十]+、", para) or re.match(r"^\d+\.", para):
                tag = "body"
            self._ins(para + "\n", tag)
        self.text.yview_moveto(0)
        self.set_status("")
        self._update_status()

    # ============================================================ 视图：搜索结果
    def do_search(self) -> None:
        q = self.search_var.get().strip()
        if not q:
            self._focus_search()
            return
        if self.demo_mode:
            self.show_results(demo.demo_books(), q)
            return
        if self._busy:
            return
        self._run(
            lambda: self.engine.search(q),
            lambda books: self.show_results(books, q),
            what=f"搜索「{q}」",
        )

    # ---------------------------------------------------------- 搜索框的中文输入兜底
    def _paste_into_search(self, event=None):
        """Ctrl+V 粘贴。剪贴板走 WM_PASTE，不经过输入法，所以一定能把中文送进去。"""
        try:
            data = self.root.clipboard_get()
        except Exception:
            return "break"
        data = " ".join(str(data).split())
        if not data:
            return "break"
        cur = self.search_var.get()
        try:
            if self.search_entry.selection_present():
                self.search_entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        self.search_var.set(self.search_var.get() + data)
        self.search_entry.icursor("end")
        self.set_status("已从剪贴板粘贴")
        self.root.after(1500, lambda: self.set_status("") if self.get_status().startswith("已从剪贴板") else None)
        return "break"

    def _select_all_search(self, event=None):
        try:
            self.search_entry.select_range(0, "end")
            self.search_entry.icursor("end")
        except Exception:
            pass
        return "break"

    def _search_menu(self, event=None):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="粘贴（Ctrl+V）", command=self._paste_into_search)
        m.add_command(label="全选（Ctrl+A）", command=self._select_all_search)
        m.add_separator()
        m.add_command(label="按剪贴板内容搜索", command=self._search_clipboard)
        m.add_command(label="清空", command=lambda: self.search_var.set(""))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _search_clipboard(self) -> None:
        """不用键盘也能搜：直接拿剪贴板内容当关键词。"""
        try:
            data = " ".join(str(self.root.clipboard_get()).split())
        except Exception:
            self.set_status("剪贴板里没有文字")
            self.root.after(2000, lambda: self.set_status(""))
            return
        if not data:
            self.set_status("剪贴板里没有文字")
            self.root.after(2000, lambda: self.set_status(""))
            return
        if len(data) > 60:
            data = data[:60]
        self.search_var.set(data)
        self.search_entry.icursor("end")
        self.do_search()

    def _on_search_focus(self, event=None):
        # 焦点进搜索框时，再试一次给窗口挂 IME 上下文（Tk 可能在运行期重建 HWND）
        self.root.after(60, self._force_ime)

    def _force_ime(self, tries: int = 0) -> None:
        """尝试用 imm32 给 Tk 顶层窗口强行关联一个 IME 上下文。

        Tk 8.6 的 Windows 端不处理 IME/TSF，正常路径下现代输入法不会激活；
        这里直接把窗口和 IME 上下文挂上，能挂上就能直接打中文。
        挂不上也不要紧，Ctrl+V 粘贴是保底方案。
        """
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            imm32 = ctypes.WinDLL("imm32", use_last_error=True)
            hwnd = self._top_hwnd()
            if not hwnd:
                if tries < 5:
                    self.root.after(300, lambda: self._force_ime(tries + 1))
                return
            ctx = imm32.ImmGetContext(hwnd)
            if not ctx:
                created = imm32.ImmCreateContext()
                if created:
                    old = imm32.ImmAssociateContext(hwnd, created)
                    ctx = created
                    self._ime_ctx = created
            if ctx:
                # IACE_DEFAULT = 0x0010
                imm32.ImmAssociateContextEx(hwnd, None, 0x0010)
                try:
                    user32.SendMessageW(hwnd, 0x0281, 0, 0)  # WM_IME_NOTIFY / 让系统重新同步
                except Exception:
                    pass
                self._ime_ok = True
        except Exception:
            pass

    def get_status(self) -> str:
        try:
            return self.status_label.cget("text")
        except Exception:
            return ""

    def show_results(self, books, query: str) -> None:
        self._enter_view("results")
        self.mode = "results"
        self._current_render = lambda: self.show_results(books, query)
        self._saved_view = self._current_render
        self._clear()
        self._set_title(f"搜索结果 - {query}")
        self._ins("搜索结果\n", "h1")
        self._ins(f"关键字：{query}    共 {len(books)} 条\n", "meta")
        if not books:
            self._ins("没有找到相关作品。换个关键词试试，比如书名或作者名。\n", "body")
        for i, b in enumerate(books, 1):
            b = self._as_dict(b)
            tag = self._new_tag("bk")
            self.text.configure(state="normal")
            start = self.text.index("end-1c")
            self.text.insert("end", f"{i}. {b.get('title','')}", ("link", tag))
            self.text.insert("end", f"　{b.get('author','')}\n", "small")
            self.text.insert("end", f"　　{b.get('abstract') or '（暂无简介）'}\n\n", "meta")
            self.text.tag_bind(tag, "<Button-1>", lambda e, bk=b: self.open_book(bk))
            self.text.tag_bind(tag, "<Enter>", lambda e, t=tag: self.text.configure(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))
            self.text.configure(state="disabled")
        self._ins("\n", "body")
        self._link("返回书城\n", self.show_home)
        self._link("我的书架\n", self.show_shelf)
        self.text.yview_moveto(0)
        self.set_status(f"找到 {len(books)} 条结果")
        self._update_status()

    # ============================================================ 视图：书架
    def show_shelf(self) -> None:
        self._enter_view("shelf")
        self.mode = "shelf"
        self._current_render = self.show_shelf
        self._saved_view = self._current_render
        shelf = self.store.shelf
        self._clear()
        self._set_title("书架")
        self._ins("我的书架\n", "h1")
        self._ins(f"共 {len(shelf)} 本\n", "meta")
        if not shelf:
            self._ins("书架还是空的。在搜索框里输入书名，点搜索结果即可加入书架。\n", "body")
        for i, b in enumerate(shelf, 1):
            tag = f"sh{i}"
            prog = self.store.get_progress(b.get("book_id", ""))
            self.text.configure(state="normal")
            self.text.insert("end", f"{i}. {b.get('title','')}", ("link", tag))
            self.text.insert("end", f"　{b.get('author','')}\n", "small")
            if prog:
                self.text.insert("end", f"　　读到：{prog.get('title','')}\n", "meta")
            self.text.configure(state="disabled")
            self.text.tag_bind(tag, "<Button-1>", lambda e, bk=b: self.open_book(bk))
        self._ins("\n", "body")
        self._link("去书城逛逛\n", self.show_home)
        self.text.yview_moveto(0)
        self._update_status()

    # ============================================================ 打开书籍
    @staticmethod
    def _as_dict(book) -> dict:
        """接受 Book 数据类或 dict，统一成 dict。"""
        if isinstance(book, dict):
            return book
        return {
            "book_id": getattr(book, "book_id", ""),
            "title": getattr(book, "title", ""),
            "author": getattr(book, "author", ""),
            "abstract": getattr(book, "abstract", ""),
        }

    def open_book(self, book) -> None:
        book = self._as_dict(book)
        if self.demo_mode:
            self._open_ok(book, demo.demo_chapters(book["book_id"]))
            return
        if self._busy:
            return
        self._run(
            lambda: self.engine.directory(book["book_id"]),
            lambda chs: self._open_ok(book, chs),
            what=f"载入《{book.get('title','')}》目录",
        )

    def _open_ok(self, book: dict, chapters: list[Chapter]) -> None:
        self.store.add_book(book["book_id"], book.get("title", ""), book.get("author", ""))
        if not chapters:
            self.set_status("这本没有可读章节")
            return
        self.show_toc(book, chapters)

    def show_toc(self, book: dict, chapters: list[Chapter]) -> None:
        self._enter_view("toc")
        self.mode = "toc"
        self._current_render = lambda: self.show_toc(book, chapters)
        self._saved_view = self._current_render
        self.read_book = book
        self.read_chapters = chapters
        self._clear()
        self._set_title(book.get("title", "目录"))
        self._ins(f"{book.get('title','')}\n", "h1")
        self._ins(f"作者：{book.get('author') or '佚名'}    共 {len(chapters)} 章\n", "meta")
        last_volume = None
        for i, ch in enumerate(chapters):
            if ch.volume and ch.volume != last_volume:
                last_volume = ch.volume
                self._ins(f"{ch.volume}\n", "h2")
            tag = f"ch{i}"
            self.text.configure(state="normal")
            self.text.insert("end", f"{ch.title}\n", ("link", tag))
            self.text.configure(state="disabled")
            self.text.tag_bind(tag, "<Button-1>", lambda e, idx=i: self.open_chapter(idx))
            self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))
        prog = self.store.get_progress(book["book_id"])
        if prog and isinstance(prog.get("index"), int):
            self.read_index = min(prog["index"], len(chapters) - 1)
        self._ins("\n", "body")
        self._link("从头开始读\n", lambda: self.open_chapter(0))
        if prog and isinstance(prog.get("index"), int):
            self._link(f"继续读第 {self.read_index + 1} 章\n", lambda: self.open_chapter(self.read_index))
        self._link("返回书城\n", self.show_home)
        self.text.yview_moveto(0)
        self.set_status("点击章节开始阅读")
        self._update_status()

    # ============================================================ 视图：阅读
    def open_chapter(self, index: int) -> None:
        if not self.read_chapters:
            return
        index = max(0, min(index, len(self.read_chapters) - 1))
        ch = self.read_chapters[index]
        if self.demo_mode:
            self._chapter_ok(index, demo.demo_chapter_text(ch.item_id))
            return
        if self._busy:
            return
        self._run(
            lambda: self.engine.chapter_text(ch.item_id),
            lambda text: self._chapter_ok(index, text),
            what="载入正文",
        )

    def _chapter_ok(self, index: int, raw: str) -> None:
        self._enter_view("read")
        self.read_index = index
        ch = self.read_chapters[index]
        text = html_to_text(raw) if "<" in (raw or "") else (raw or "")
        self._paras = split_paragraphs(text) or ["（本章暂无内容）"]
        # 打字机关闭时整章铺开（这是"看不全"的修复点）
        self._shown = 8 if self.typing_mode else len(self._paras)
        self.mode = "read"
        self._current_render = lambda: self._render_read(keep_progress=self.typing_mode)
        self._saved_view = self._current_render
        if self.read_book:
            self.store.set_progress(self.read_book["book_id"], index, ch.item_id, ch.title)
        self._render_read(keep_progress=self.typing_mode)

    def _render_read(self, keep_progress: bool = False) -> None:
        self.mode = "read"
        ch = self.read_chapters[self.read_index] if self.read_chapters else None
        title = ch.title if ch else ""
        self._clear()
        if self.read_book:
            self._set_title(f"{self.read_book.get('title','')} - {title}")
        self._ins(f"{title}\n", "h1")
        total = len(self.read_chapters)
        self._ins(
            f"第 {self.read_index + 1} 章 / 共 {total} 章　　显示：{'打字机' if self.typing_mode else '整章'}　　"
            f"PgDn 翻页（到底自动下一章）　Ctrl+T 切换显示方式\n", "meta")
        shown = min(self._shown, len(self._paras)) if keep_progress else len(self._paras)
        for para in self._paras[:shown]:
            self._ins(para + "\n", "indent")
        if shown < len(self._paras):
            self._ins(f"…（还有 {len(self._paras) - shown} 段；敲任意键继续，Ctrl+T 直接显示整章）\n", "meta")
        self._ins("\n", "body")
        if self.read_index + 1 < total:
            self._link("下一章 ▸\n", self.next_chapter)
        if self.read_index > 0:
            self._link("◂ 上一章\n", self.prev_chapter)
        self._link("返回目录\n", lambda: self.show_toc(self.read_book, self.read_chapters))
        self._link("书城\n", self.show_home)
        self.text.yview_moveto(0)
        self.set_status("阅读中")
        self._update_status()

    def _reveal_next(self) -> None:
        if self._typing_job is not None:
            return
        if self._shown >= len(self._paras):
            self.next_chapter()
            return
        para = self._paras[self._shown]
        self._shown += 1
        self.text.configure(state="normal")
        self.text.insert("end", "", "indent")
        self.text.configure(state="disabled")
        self._type_out(para, 0)

    def _type_out(self, para: str, pos: int) -> None:
        if self.mode != "read":
            self._typing_job = None
            return
        step = 3
        chunk = para[pos:pos + step]
        self.text.configure(state="normal")
        self.text.insert("end", chunk, "indent")
        self.text.configure(state="disabled")
        pos += step
        if pos < len(para):
            self._typing_job = self.root.after(12, lambda: self._type_out(para, pos))
        else:
            self.text.configure(state="normal")
            self.text.insert("end", "\n", "indent")
            self.text.configure(state="disabled")
            self._typing_job = None
            self.text.see("end")
            self._update_status()

    def next_chapter(self) -> None:
        if self.mode != "read" or not self.read_chapters:
            return
        if self.read_index + 1 < len(self.read_chapters):
            self.open_chapter(self.read_index + 1)

    def prev_chapter(self) -> None:
        if self.mode != "read" or not self.read_chapters:
            return
        if self.read_index > 0:
            self.open_chapter(self.read_index - 1)

    # ============================================================ 字号 / 状态
    def change_font(self, delta: int) -> None:
        self.font_size = max(8, min(26, self.font_size + delta))
        self.store.set_ui("font_size", self.font_size)
        self.text.tag_configure("body", font=(BODY_FAMILY, self.font_size))
        self.text.tag_configure("indent", font=(BODY_FAMILY, self.font_size))
        self.text.tag_configure("link", font=(BODY_FAMILY, self.font_size))
        if self._current_render:
            self._current_render()

    def _update_status(self) -> None:
        try:
            # 用像素高度算页数，段落间距才不会漏算（比数行数准）
            def _px(a: str, b: str) -> int:
                r = self.text.count(a, b, "ypixels")
                if isinstance(r, (list, tuple)):
                    r = r[0]
                return int(r or 0)

            visible = max(1, self.text.winfo_height() - 92)
            total_px = _px("1.0", "end")
            if total_px > 0:
                pages = max(1, (total_px + visible - 1) // visible)
                cur = min(pages, _px("1.0", "@0,0") // visible + 1)
            else:  # 老版本 Tk 没有 ypixels，退回按行估算
                total_lines = int(self.text.index("end-1c").split(".")[0])
                pages = max(1, (total_lines + LINES_PER_PAGE - 1) // LINES_PER_PAGE)
                cur = min(pages, int(self.text.index("@0,0").split(".")[0]) // LINES_PER_PAGE + 1)
            self.st_page.configure(text=f"第 {cur} 页，共 {pages} 页")
            content = self.text.get("1.0", "end-1c")
            cjk = len(re.findall(r"[\u4e00-\u9fff]", content))
            words = len(re.findall(r"[A-Za-z0-9]+", content))
            self.st_words.configure(text=f"字数：{cjk + words}")
        except Exception:
            pass

    def set_status(self, msg: str) -> None:
        self._status_msg = msg
        if hasattr(self, "st_hint"):
            self.st_hint.configure(text=msg)

    # ============================================================ 引擎连接
    def _connect_engine(self) -> None:
        """先试本地引擎；试不出来就自动切到网页直连（官网页面 + 字体解码）。"""
        if getattr(self.engine, "kind", "") == "web":
            # 已经在网页直连上了，重新连一次就是刷新
            self.engine = TomatoEngine()
        self.set_status("正在连接引擎…")

        def work():
            try:
                self.engine.connect(
                    timeout=10.0,
                    wait_external=0.0,
                    on_status=lambda m: self.root.after(0, lambda: self.set_status(m)),
                )
                self.root.after(0, self._engine_ready)
            except Exception as exc:  # noqa: BLE001 任何异常都降级，别把界面卡死
                why = str(exc)  # except 块一结束 exc 就没了，lambda 里不能直接引用
                self.root.after(0, lambda m=why: self._use_web(m))

        threading.Thread(target=work, daemon=True).start()

    def _engine_ready(self) -> None:
        self.web_fallback = False
        self._ime_ctx = None
        self._ime_ok = False
        self.set_status("引擎已连接")
        self.root.after(2500, lambda: self.set_status(""))
        self.show_home()

    def _use_web(self, reason: str = "") -> None:
        """引擎起不来就用官网直连。这条路不需要任何本地进程，立马有真书。"""
        from .web import WebEngine

        self.engine = WebEngine()
        self.web_fallback = True
        if not self.engine.ready:
            self.set_status("网页直连不可用：缺少 data/charset.json")
            return
        self._engine_reason = reason
        self.set_status("引擎未就绪，已改用官网直连")
        self.root.after(2600, lambda: self.set_status(""))
        self.show_home()
        self._warm_library()

    def _warm_library(self) -> None:
        """后台把全站书库扫一遍存到本地；扫完刷新书城。

        第一次要 10 秒左右（108 个榜单并发抓），之后一直读
        `%LOCALAPPDATA%\\TomatoWord\\library.json`，秒开。
        """
        build = getattr(self.engine, "build_library", None)
        if build is None or self.demo_mode:
            return
        cache = getattr(self.engine, "library_path", None)
        cached = False
        try:
            cached = bool(cache and cache.exists())
        except Exception:
            cached = False
        if cached:
            return  # 已有缓存，homepage 已经用上了

        self.set_status("正在扩充书库（全站榜单扫描中，约 10 秒）…")

        def work():
            try:
                books = build()
            except Exception:
                self.root.after(0, lambda: self.set_status("书库扩充失败，仍可分类浏览"))
                return
            self.root.after(0, lambda n=len(books): self._library_ready(n))

        threading.Thread(target=work, daemon=True).start()

    def _library_ready(self, count: int) -> None:
        self.set_status(f"书库已扩充到 {count} 本")
        self.root.after(3000, lambda: self.set_status(""))
        if self.mode in ("home",):
            self.root.after(10, lambda: self.show_home())

    def _engine_failed(self, msg: str) -> None:
        self._use_web(msg)

    def _enter_demo(self) -> None:
        self.demo_mode = True
        self.set_status("演示模式（不联网）")
        self.show_document()

    def _open_engine_dir(self) -> None:
        try:
            home = self.engine.log_path.parent
            home.mkdir(parents=True, exist_ok=True)
            os.startfile(str(home))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"打不开目录：{exc}")

    def _run(self, fn, on_ok, what: str = "处理中") -> None:
        self._busy = True
        self.set_status(f"{what}…")

        def work():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                why = exc  # 同上：exc 出了 except 块就消失，先绑定再传给 lambda
                self.root.after(0, lambda e=why: self._fail(e))
                return
            self.root.after(0, lambda: self._ok(result, on_ok))

        threading.Thread(target=work, daemon=True).start()

    def _ok(self, result, on_ok):
        self._busy = False
        self.set_status("")
        on_ok(result)

    def _fail(self, exc: Exception):
        self._busy = False
        self.set_status("出错了")
        self._clear()
        self._ins("出错了\n", "h1")
        self._ins(f"{exc}\n", "body")
        self._ins("按 Ctrl+Alt+W 切回伪装文档。\n", "meta")

    # ============================================================ 退出
    def quit(self) -> None:
        try:
            self._hotkey.stop()
        except Exception:
            pass
        try:
            self.engine.shutdown()
        except Exception:
            pass
        try:
            self.store.save()
        except Exception:
            pass
        self.root.destroy()
