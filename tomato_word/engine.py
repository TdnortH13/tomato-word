"""番茄超市（tomato-supermarket）引擎适配层。

思路：不去重写番茄的签名/设备池/解密协议，而是把官方桌面程序当成一个本地
HTTP 引擎来用。它的 core 会在 127.0.0.1 上起一个 axum 服务，并且把带
capability token 的地址写进 <runtime>/rust.log：

    [I] 2026-11-08T07:19:34.123Z core: fqapi_core::desktop: fqpc core ready:
        http://127.0.0.1:8080/_session/<64位hex>

我们只要：启动它 -> 从日志里捞出 port+token -> 用 Authorization: Bearer 调
/api/v1/* -> 退出时关掉它。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "TomatoWord"

# rust.log 里的就绪行；token 是 32 字节随机数的 hex
READY_RE = re.compile(
    r"core ready:\s*http://127\.0\.0\.1:(\d+)/_session/([0-9a-fA-F]{64})"
)

# 引擎可执行文件常见文件名
EXE_PATTERNS = ("tomato-supermarket*.exe", "番茄超市*.exe", "tomato_supermarket*.exe")


class EngineError(RuntimeError):
    """引擎不可用（未找到 / 启动失败 / 鉴权失败）。"""


@dataclass
class Book:
    book_id: str
    title: str
    author: str = ""
    abstract: str = ""
    cover: str = ""
    tags: str = ""


@dataclass
class Chapter:
    item_id: str
    title: str
    volume: str = ""


def collect_books(node, _out: list[Book] | None = None, _depth: int = 0) -> list[Book]:
    """从任意嵌套的 JSON 里捞出所有"像一本书"的节点。

    引擎不同接口把书目放在不同位置（`book_data` / `data` / `cell_data` / 多层 tabs…），
    与其逐个猜结构，不如深度遍历，认 `book_id` + 书名。
    """
    if _out is None:
        _out = []
    if _depth > 14:
        return _out
    if isinstance(node, dict):
        bid = node.get("book_id") or node.get("bookId")
        title = node.get("book_name") or node.get("bookName") or node.get("title")
        if bid and title and str(bid).strip().isdigit():
            bid = str(bid).strip()
            if all(b.book_id != bid for b in _out):
                _out.append(
                    Book(
                        book_id=bid,
                        title=str(title).strip(),
                        author=str(node.get("author") or "").strip(),
                        abstract=str(node.get("abstract") or "").strip(),
                        cover=str(node.get("thumb_url") or node.get("expand_thumb_url") or "").strip(),
                        tags=str(node.get("pure_category_tags") or node.get("tags") or "").strip(),
                    )
                )
        for v in node.values():
            collect_books(v, _out, _depth + 1)
    elif isinstance(node, list):
        for v in node:
            collect_books(v, _out, _depth + 1)
    return _out


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

def app_dir() -> Path:
    """程序所在目录（打包成 exe 后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def engine_home() -> Path:
    """引擎自己的运行时目录（放 rust.log / config / web）。

    优先用程序目录下的 runtime/（便携，且某些受限环境只允许写程序目录）；
    不可写时退回 %LOCALAPPDATA%\\TomatoWord\\runtime。
    用我们自己的目录而不是引擎默认的 %LOCALAPPDATA%\\fqapp-pc，好处是日志
    位置确定，也不会污染用户已经装好的番茄超市。
    """
    portable = app_dir() / "runtime"
    for cand in (portable, app_data_dir() / "runtime"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".writable"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return cand
        except Exception:
            continue
    return app_data_dir() / "runtime"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        config_path().write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _search_dirs() -> list[Path]:
    home = Path.home()
    here = app_dir()
    cands = [
        Path.cwd(),
        here,
        here / "engine",
        here.parent / "engine",
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        Path(os.environ.get("LOCALAPPDATA", "")) / APP_NAME,
    ]
    return [c for c in cands if str(c) and c.is_dir()]


def find_engine_exe(explicit: str | None = None) -> Path | None:
    """按优先级找引擎 exe：显式参数 > 环境变量 > 配置 > 常见目录扫描。"""
    for raw in (explicit, os.environ.get("TOMATOWORD_ENGINE"), load_config().get("engine_path")):
        if raw:
            p = Path(raw).expanduser()
            if p.is_file():
                return p
    for d in _search_dirs():
        for pat in EXE_PATTERNS:
            try:
                hits = sorted(d.glob(pat))
            except Exception:
                hits = []
            if hits:
                return hits[0]
    return None


# --------------------------------------------------------------------------
# Win32：把引擎窗口藏起来 / 结束进程
# --------------------------------------------------------------------------

def _engine_exe_names() -> set[str]:
    """被认为是「番茄超市引擎」的可执行文件名（小写）。"""
    return {"tomato-supermarket.exe", "番茄超市.exe"}


def _process_exe_name(pid: int) -> str:
    """取进程的可执行文件名（小写），失败返回空串。"""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return Path(buf.value).name.lower()
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""


def _hide_engine_windows() -> int:
    """把所有番茄超市引擎进程的可见顶层窗口隐藏起来。

    外壳不一定是引擎的父进程（用户可以自己双击启动引擎），所以按可执行
    文件名认进程，不按 pid。返回隐藏了几个窗口。
    """
    if sys.platform != "win32":
        return 0
    names = _engine_exe_names()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hits: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if not wpid.value or not user32.IsWindowVisible(hwnd):
                return True
            if _process_exe_name(wpid.value) in names:
                hits.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        for hwnd in hits:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
        return len(hits)
    except Exception:
        return 0


def _win32_hide_window(pid: int) -> bool:
    """把属于 pid 的可见顶层窗口隐藏（SW_HIDE）。失败返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        ok = False
        for hwnd in found:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
            ok = True
        return ok
    except Exception:
        return False


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------

class TomatoEngine:
    def __init__(self, exe_path: str | Path | None = None):
        self.exe_path = find_engine_exe(str(exe_path) if exe_path else None)
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.token: str | None = None
        self.owns_process = False
        self.log_path = engine_home() / "rust.log"

    # -- 状态 ------------------------------------------------------------
    @property
    def base(self) -> str:
        if not self.port:
            raise EngineError("引擎端口未知")
        return f"http://127.0.0.1:{self.port}"

    @property
    def ready(self) -> bool:
        return bool(self.port and self.token)

    # -- 启动 / 连接 ------------------------------------------------------
    def connect(self, timeout: float = 30.0, on_status=None,
                wait_external: float = 90.0) -> None:
        """连接引擎；必要时启动它。失败抛 EngineError。"""
        def say(msg: str) -> None:
            if on_status:
                try:
                    on_status(msg)
                except Exception:
                    pass

        # 1) 也许已经有一个引擎在跑（用户自己开的，或上一轮没关掉）
        for log in self._log_candidates():
            if self._adopt_from(log) and self._ping():
                self.log_path = log
                say("已连接到正在运行的引擎")
                return

        # 2) 需要自己拉起来
        if not self.exe_path or not Path(self.exe_path).is_file():
            raise EngineError(
                "没有找到番茄超市引擎（tomato-supermarket*.exe）。\n"
                "请把 exe 放到本程序同目录的 engine\\ 下，或用环境变量 "
                "TOMATOWORD_ENGINE 指定路径。"
            )

        say("正在启动引擎…")
        env = os.environ.copy()
        env["FQPC_HOME"] = str(engine_home())
        try:
            self.proc = subprocess.Popen(
                [str(self.exe_path)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(self.exe_path).resolve().parent),
            )
            self.owns_process = True
        except OSError as exc:
            raise EngineError(f"引擎启动失败：{exc}") from exc

        deadline = time.time() + timeout
        hidden_at = 0.0
        saw_ready = False
        exited: str | None = None
        while time.time() < deadline:
            if self._adopt_from(self.log_path):
                saw_ready = True
                # 引擎窗口一出现就藏起来（它只是我们的后端）
                if time.time() - hidden_at > 0.5:
                    _hide_engine_windows()
                    if self.proc is not None:
                        _win32_hide_window(self.proc.pid)
                    hidden_at = time.time()
                if self._ping():
                    _hide_engine_windows()
                    say("引擎就绪")
                    return
            # 引擎自己起不来时不要放弃：用户可能正要手动双击它。
            # 先把进程收掉，再给一段额外的等待窗口。
            if self.proc is not None and self.proc.poll() is not None:
                code = self.proc.returncode
                exited = (
                    f"引擎 core 起来了但进程随即退出（退出码 {code}）"
                    if saw_ready
                    else f"引擎进程已退出（退出码 {code}）"
                )
                self.proc = None
                self.owns_process = False
                deadline = max(deadline, time.time() + wait_external)
                say("引擎没能自己启动，双击「启动引擎.cmd」后我会自动连上…")
            time.sleep(0.3)

        hint = (
            "引擎的窗口在本次会话里建不起来：宿主把进程关在 Job Object 里，"
            "WebView2 无法在父子进程之间传递窗口句柄（表现为 os error 5 / 拒绝访问）。\n"
            "在桌面上双击「启动引擎.cmd」（或 engine\\番茄超市.exe），"
            "等它把窗口开出来后回到这里点「重新连接引擎」即可。"
        )
        raise EngineError(f"{exited}。\n{hint}\n日志：{self.log_path}" if exited else
                          f"等待引擎就绪超时。\n{hint}\n日志：{self.log_path}")

    def _log_candidates(self) -> list[Path]:
        """可能藏着 token 的日志：我们自己的、用户环境变量的、引擎默认的。"""
        cands: list[Path] = []
        env_home = os.environ.get("FQPC_HOME")
        if env_home:
            cands.append(Path(env_home) / "rust.log")
        cands.append(engine_home() / "rust.log")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cands.append(Path(local) / "fqapp-pc" / "rust.log")
        seen, out = set(), []
        for c in cands:
            if str(c) not in seen:
                seen.add(str(c))
                out.append(c)
        return out

    def _adopt_from(self, log_path: Path) -> bool:
        """从指定日志里取最后一条就绪行，设置 port/token。"""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        hits = READY_RE.findall(text)
        if not hits:
            return False
        port, token = hits[-1]
        self.port, self.token = int(port), token
        return True

    def _read_ready_line(self) -> bool:
        """兼容旧接口：从默认日志取就绪行。"""
        return self._adopt_from(self.log_path)

    def _ping(self) -> bool:
        """便宜的自检：路由不存在会给 404，鉴权失败才是 401。"""
        if not self.ready:
            return False
        try:
            self._raw("/api/v1/__ping__", timeout=3.0)
            return True
        except _Status as st:
            return st.code != 401
        except Exception:
            return False

    def shutdown(self) -> None:
        if self.owns_process and self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    # -- HTTP ------------------------------------------------------------
    def _raw(self, path: str, params: dict | None = None, timeout: float = 25.0):
        if not self.ready:
            raise EngineError("引擎未就绪")
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                return resp.status, body
        except urllib.error.HTTPError as exc:
            raise _Status(exc.code, exc.read().decode("utf-8", "replace")) from exc
        except urllib.error.URLError as exc:
            raise EngineError(f"连不上引擎：{exc.reason}") from exc

    def api(self, path: str, params: dict | None = None, timeout: float = 25.0) -> dict:
        """调 /api/v1/*，返回解包后的 payload；code!=200 视为失败。"""
        try:
            status, body = self._raw(path, params, timeout)
        except _Status as st:
            raise EngineError(f"引擎返回 HTTP {st.code}") from st
        if status >= 400:
            raise EngineError(f"引擎返回 HTTP {status}")
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise EngineError("引擎返回的不是 JSON") from exc
        code = payload.get("code")
        if code is not None and int(code) != 200:
            raise EngineError(payload.get("message") or f"接口错误 code={code}")
        return payload

    # -- 业务接口 --------------------------------------------------------
    def search(self, query: str, tab_type: int = 3, page: int = 1, count: int = 10) -> list[Book]:
        payload = self.api(
            "/api/v1/search",
            {
                "query": query,
                "tab_type": tab_type,
                "count": count,
                "offset": (page - 1) * count,
                "page": page,
            },
        )
        tabs = payload.get("search_tabs")
        if tabs is None and isinstance(payload.get("data"), dict):
            tabs = payload["data"].get("search_tabs")
        books: list[Book] = []
        for tab in tabs or []:
            if int(tab.get("tab_type") or 0) != tab_type:
                continue
            for item in tab.get("data") or []:
                for bd in item.get("book_data") or []:
                    bid = str(bd.get("book_id") or "").strip()
                    title = (bd.get("book_name") or "").strip()
                    if not bid or not title:
                        continue
                    books.append(
                        Book(
                            book_id=bid,
                            title=title,
                            author=(bd.get("author") or "").strip(),
                            abstract=(bd.get("abstract") or "").strip(),
                            cover=(bd.get("thumb_url") or "").strip(),
                            tags=(bd.get("pure_category_tags") or "").strip(),
                        )
                    )
        return books

    def homepage(self, tab_type: int = 0, offset: int = 0, limit: int = 30) -> list[Book]:
        """书城首页推荐。字段位置随引擎版本变，用通用递归提取兜底。"""
        payload = self.api(
            "/api/v1/recommend/homepage",
            {"tab_type": tab_type, "offset": offset},
        )
        return collect_books(payload)[:limit]

    def rank(self, rank_id: str | int = "1", limit: int = 30) -> list[Book]:
        """榜单。rank_id 依引擎而定，1 一般是综合榜。"""
        payload = self.api(f"/api/v1/rank/{urllib.parse.quote(str(rank_id))}")
        return collect_books(payload)[:limit]

    def detail(self, book_id: str) -> dict:
        payload = self.api(f"/api/v1/books/{urllib.parse.quote(str(book_id))}/detail")
        data = payload.get("data") or {}
        return {
            "title": data.get("book_name") or "",
            "author": data.get("author") or "",
            "abstract": data.get("abstract") or "",
            "tags": data.get("tags") or "",
            "cover": data.get("expand_thumb_url") or data.get("thumb_url") or "",
        }

    def directory(self, book_id: str) -> list[Chapter]:
        payload = self.api(f"/api/v1/books/{urllib.parse.quote(str(book_id))}/directory")
        data = payload.get("data") or {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        volumes = inner.get("chapterListWithVolume") or []
        chapters: list[Chapter] = []
        for volume in volumes:
            if isinstance(volume, dict):          # 容错：万一不是数组的数组
                volume = volume.get("chapterList") or []
            for ch in volume or []:
                item_id = str(ch.get("itemId") or ch.get("item_id") or "").strip()
                if not item_id:
                    continue
                chapters.append(
                    Chapter(
                        item_id=item_id,
                        title=(ch.get("title") or "").strip() or f"第{len(chapters)+1}章",
                        volume=(ch.get("volume_name") or "").strip(),
                    )
                )
        return chapters

    def chapter_text(self, item_id: str) -> str:
        payload = self.api(f"/api/v1/chapters/{urllib.parse.quote(str(item_id))}")
        data = payload.get("data") or {}
        content = data.get("content")
        if isinstance(content, dict):
            content = content.get("content") or ""
        return _decode_maybe_base64(content or "")


class _Status(Exception):
    def __init__(self, code: int, body: str = ""):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.body = body


def _decode_maybe_base64(text: str) -> str:
    """正文有时是 base64url；含 < 或 > 说明已经是 HTML/纯文本。"""
    s = (text or "").strip()
    if not s or "<" in s or ">" in s or "\n" in s:
        return s
    if not re.fullmatch(r"[A-Za-z0-9_\-+/=]+", s):
        return s
    try:
        pad = "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(s.replace("+", "-").replace("/", "_") + pad)
        out = raw.decode("utf-8")
        return out
    except Exception:
        return s


# --------------------------------------------------------------------------
# 纯文本化
# --------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """把章节正文的 HTML 片段转成适合排版的纯文本。"""
    if not html:
        return ""
    s = html
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"(?i)<p[^>]*>", "", s)
    s = re.sub(r"(?i)</div\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    ents = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&ldquo;": "“", "&rdquo;": "”",
        "&hellip;": "…", "&mdash;": "—",
    }
    for k, v in ents.items():
        s = s.replace(k, v)
    s = re.sub(r"[ \t\u3000]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
