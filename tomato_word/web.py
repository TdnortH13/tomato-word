"""网页直连引擎。

番茄超市那个 Tauri 引擎在受限环境（被装进 Job Object 的进程）里建不了
WebView2 窗口，core 一起来就跟着进程一起死。但番茄小说官网本身是可读的：

- `https://fanqienovel.com/`      首页推荐
- `https://fanqienovel.com/rank`  榜单（10 本，含分类）
- `https://fanqienovel.com/page/<book_id>`    详情 + 全部章节链接
- `https://fanqienovel.com/reader/<item_id>`  正文

正文里的字会被换成私用区（PUA）码位做混淆，映射表在
`data/charset.json`（社区整理的静态表，取自 ying-ck/fanqienovel-downloader）。
映射规则就一条：`汉字 = charset[码位 - 0xE3E8]`，实测覆盖率 100%。

这个模块把上面四件事包成和 `TomatoEngine` 一样的接口，界面层不用区分。
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .engine import Book, Chapter, EngineError, html_to_text

__all__ = ["WebEngine", "PUA_BASE", "decode_pua"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_URL = "https://fanqienovel.com"
PUA_BASE = 0xE3E8  # 私用区码位起点，与 charset.json 第 0 行一一对应

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CHARSET_PATH = _DATA_DIR / "charset.json"

# 书名里也夹着混淆字，同一个表解
_TITLE_JUNK = re.compile(r"(完整版在线免费阅读|小说|番茄小说官网|_)+")


def _load_charset() -> list[str]:
    try:
        raw = json.loads(_CHARSET_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        return list(raw[0])
    return list(raw) if isinstance(raw, list) else []


_CHARSET = _load_charset()


def decode_pua(text: str) -> str:
    """把私用区码位换回真汉字。表里没有的字符原样保留。"""
    if not text or not _CHARSET:
        return text
    out = []
    n = len(_CHARSET)
    for ch in text:
        cp = ord(ch)
        if 0xE000 <= cp <= 0xF8FF:
            idx = cp - PUA_BASE
            out.append(_CHARSET[idx] if 0 <= idx < n else ch)
        else:
            out.append(ch)
    return "".join(out)


def _clean_title(raw: str) -> str:
    t = decode_pua(html.unescape(raw or ""))
    t = _TITLE_JUNK.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" _-·|")
    # `<title>书名完整版在线免费阅读_书名小说_番茄小说官网</title>` 去后缀会
    # 留下「书名 书名」，这里把重复的段折叠掉
    parts = [p for p in t.split(" ") if p]
    if len(parts) > 1 and len(set(parts)) == 1:
        return parts[0]
    dedup: list[str] = []
    for p in parts:
        if p not in dedup:
            dedup.append(p)
    return " ".join(dedup)


class WebEngine:
    """用官网页面当数据源。接口与 TomatoEngine 对齐（只实现界面用到的那几个）。"""

    kind = "web"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._book_cache: dict[str, dict] = {}
        self._index: list[Book] | None = None
        self._cats: list[tuple[str, str]] | None = None
        self._lib: list[Book] | None = None

    # -- 全站书库（缓存到本地，一次扫完反复用）----------------------------
    @property
    def library_path(self):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "TomatoWord" / "library.json"

    def load_library(self) -> list[Book]:
        """读本地书库缓存；没有再全站扫一遍。"""
        if self._lib is not None:
            return self._lib
        path = self.library_path
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                books = [
                    Book(
                        book_id=str(d.get("book_id") or ""),
                        title=str(d.get("title") or ""),
                        author=str(d.get("author") or ""),
                        abstract=str(d.get("abstract") or ""),
                        cover=str(d.get("cover") or ""),
                        tags=str(d.get("tags") or ""),
                    )
                    for d in raw.get("books") or []
                    if str(d.get("book_id") or "").isdigit()
                ]
                if books:
                    self._lib = books
                    self._index = books
                    return books
            except Exception:
                pass
        return self.build_library()

    def build_library(self, progress=None, force: bool = False) -> list[Book]:
        """把 18 个分类 × (gender 0/1/2 × type 1/2) 全扫一遍合并去重。

        这是官网页面能给的**全部**书目：约 500 本。多线程并发扫，进度回调
        形如 progress(done, total, found)。
        """
        if self._lib is not None and not force:
            return self._lib
        cats = self.categories()
        jobs: list[str] = []
        for code, _name in cats:
            short = code[4:] if code.startswith("0_1_") else code
            for g in (0, 1, 2):
                for t in (1, 2):
                    jobs.append(f"/rank/{g}_{t}_{short}")

        pool: list[Book] = []
        seen: set[str] = set()
        lock = threading.Lock()
        total = len(jobs) or 1
        done = [0]

        def worker(path: str) -> None:
            try:
                page = self._get(path)
            except Exception:
                page = ""
            got = self._extract_books(page) if page else []
            with lock:
                for b in got:
                    if b.book_id not in seen:
                        seen.add(b.book_id)
                        pool.append(b)
                done[0] += 1
                if progress:
                    try:
                        progress(done[0], total, len(pool))
                    except Exception:
                        pass

        with ThreadPoolExecutor(max_workers=6) as pool_ex:
            list(pool_ex.map(worker, jobs))

        if not pool:
            raise EngineError("全站榜单没解析出任何书目")
        # 首页那几本常常不在榜单里，补进去
        for path in ("/", "/rank"):
            try:
                for b in self._extract_books(self._get(path)):
                    if b.book_id not in seen:
                        seen.add(b.book_id)
                        pool.append(b)
            except Exception:
                continue
        self._lib = pool
        self._index = pool
        try:
            self.library_path.parent.mkdir(parents=True, exist_ok=True)
            self.library_path.write_text(
                json.dumps(
                    {
                        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "count": len(pool),
                        "books": [
                            {
                                "book_id": b.book_id,
                                "title": b.title,
                                "author": b.author,
                                "abstract": b.abstract,
                                "cover": b.cover,
                                "tags": b.tags,
                            }
                            for b in pool
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        return pool

    # -- 传输 ------------------------------------------------------------
    def _get(self, path: str) -> str:
        url = path if path.startswith("http") else BASE_URL + path
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": BASE_URL + "/",
            },
        )
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001 网络抖动就重试
                last = exc
                time.sleep(0.6 * (attempt + 1))
        raise EngineError(f"抓取失败：{url}\n{last}")

    # -- 解析 ------------------------------------------------------------
    @staticmethod
    def _book_name(inner: str) -> str:
        """从书籍卡片的 HTML 里抠书名。

        卡片结构是
        `<a href="/page/ID"><img alt="书名"><h3 class="title">书名</h3>
         <h4 class="author">作者</h4></a>`
        —— 直接取 inner text 会把书名和作者粘在一起（"部族荣光丧狐"），
        所以按 h3 > img[alt] > 纯文本 的顺序挑第一个像样的。
        """
        for pat in (
            r'<h3[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h3>',
            r'<img[^>]+alt="([^"]{1,40})"',
        ):
            m = re.search(pat, inner, re.S)
            if m:
                name = _clean_title(re.sub(r"<[^>]+>", "", m.group(1)))
                if name:
                    return name
        name = _clean_title(re.sub(r"<[^>]+>", "", inner))
        return name if len(name) <= 24 else name[:24]

    @classmethod
    def _extract_books(cls, page: str) -> list[Book]:
        """从任意页面里捞出 (book_id, 书名) 对，保持出现顺序、去重。"""
        books: list[Book] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'<a[^>]+href="/page/(\d+)"[^>]*>(.*?)</a>', page, re.S
        ):
            bid, inner = m.group(1), m.group(2)
            if bid in seen:
                continue
            seen.add(bid)
            books.append(Book(book_id=bid, title=cls._book_name(inner)))
        # 只有 id 没有标题的，也收进来（书名留空，界面会显示 id）
        for bid in re.findall(r'/page/(\d+)', page):
            if bid not in seen:
                seen.add(bid)
                books.append(Book(book_id=bid, title=""))
        return books

    @staticmethod
    def _parse_detail(book_id: str, page: str) -> dict:
        title = ""
        m = re.search(r"<title>(.*?)</title>", page, re.S)
        if m:
            title = _clean_title(m.group(1))
        if not title:
            title = f"书籍 {book_id}"
        author = ""
        m = re.search(r'"authorName"\s*:\s*"([^"]{1,40})"', page)
        if not m:
            m = re.search(r'class="[^"]*author[^"]*"[^>]*>(.*?)<', page, re.S)
        if m:
            author = decode_pua(re.sub(r"<[^>]+>", "", m.group(1)).strip())
        abstract = ""
        m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page)
        if m:
            abstract = decode_pua(html.unescape(m.group(1)))
        tags = ""
        for key in ("category", "tags", "pureCategory"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]{{1,40}})"', page)
            if m:
                tags = decode_pua(m.group(1))
                break
        return {
            "title": title,
            "author": author,
            "abstract": abstract[:200],
            "tags": tags,
        }

    @staticmethod
    def _parse_chapters(page: str) -> list[Chapter]:
        """解析目录。

        页面顶部那条 `最近更新：第351章 …` 是重复的最新章，正式列表从
        `第1章` 开始按顺序排；这里统一按章号升序，并去掉"最近更新："前缀。
        """
        raw: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'<a[^>]+href="/reader/(\d+)"[^>]*>(.*?)</a>', page, re.S
        ):
            item, inner = m.group(1), m.group(2)
            if item in seen:
                continue
            seen.add(item)
            name = _clean_title(re.sub(r"<[^>]+>", "", inner))
            name = re.sub(r"^(最近更新|最新章节)[:：]\s*", "", name).strip()
            raw.append((item, name))
        if not raw:
            raw = [(i, "") for i in dict.fromkeys(re.findall(r'/reader/(\d+)', page))]

        def chapter_no(title: str) -> int:
            m = re.search(r"第\s*(\d+)\s*章", title)
            return int(m.group(1)) if m else 10**9

        # 稳定排序：有章号的按章号，没章号的按原顺序垫底
        ordered = sorted(enumerate(raw), key=lambda p: (chapter_no(p[1][1]), p[0]))
        chapters: list[Chapter] = []
        for _, (item, name) in ordered:
            chapters.append(
                Chapter(item_id=item, title=name or f"章节 {len(chapters) + 1}")
            )
        return chapters

    # -- 对外接口（与 TomatoEngine 同名同形） ----------------------------
    def homepage(self, tab_type: int = 0, offset: int = 0, limit: int = 600) -> list[Book]:
        """书城首页：优先用本地全站书库（约 500 本），没有缓存才现扫。"""
        try:
            lib = self.load_library()
        except EngineError:
            lib = []
        if lib:
            return lib[offset:offset + limit] if offset else lib[:limit]
        pool: list[Book] = []
        seen: set[str] = set()
        for path in ("/", "/rank"):
            try:
                page = self._get(path)
            except EngineError:
                continue
            for b in self._extract_books(page):
                if b.book_id in seen:
                    continue
                seen.add(b.book_id)
                pool.append(b)
        if not pool:
            raise EngineError("官网页面上没解析出任何书目")
        self._index = pool
        return pool[:limit]

    def rank(self, rank_id: str | int = "1", limit: int = 30) -> list[Book]:
        page = self._get("/rank")
        books = self._extract_books(page)
        if not books:
            raise EngineError("榜单页没解析出书目")
        return books[:limit]

    # -- 分类 ------------------------------------------------------------
    def categories(self) -> list[tuple[str, str]]:
        """官网的分类其实是「分类榜单」：`/rank/0_1_<code>`。

        任意一个分类页的侧边就列着全部 18 个分类，抓一次缓存住。
        """
        if self._cats is not None:
            return self._cats
        page = self._get("/rank/0_1_24")  # 「快穿」，页面里带全套分类导航
        cats: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'<a[^>]+href="/rank/(0_1_\d+)"[^>]*>(.*?)</a>', page, re.S
        ):
            code, inner = m.group(1), m.group(2)
            name = _clean_title(re.sub(r"<[^>]+>", "", inner))
            if not name or code in seen or len(name) > 8:
                continue
            seen.add(code)
            cats.append((code, name))
        self._cats = cats
        return cats

    def by_category(self, code: str, limit: int = 40) -> list[Book]:
        """某个分类里的书。

        官网的分类其实是一堆榜单，路径 `/rank/<gender>_<type>_<category>`。
        同一个分类在 gender 0/1/2 和 type 1/2 下各有一份榜，合并去重之后
        一个分类能翻到三四十本，而不是首页那十本。
        """
        code = str(code)
        if code.startswith("0_1_"):
            code = code[4:]
        books: list[Book] = []
        seen: set[str] = set()
        for gender in (0, 1, 2):
            for typ in (1, 2):
                try:
                    page = self._get(f"/rank/{gender}_{typ}_{code}")
                except EngineError:
                    continue
                for b in self._extract_books(page):
                    if b.book_id not in seen:
                        seen.add(b.book_id)
                        books.append(b)
                if len(books) >= limit:
                    return books[:limit]
        if not books:
            raise EngineError(f"分类 {code} 没解析出书目")
        return books[:limit]

    def search(self, query: str, limit: int = 40) -> list[Book]:
        """在本地全站书库里搜（约 500 本，书名 + 作者）。

        官网没有开放全站搜索接口（`/api/author/search/search_book/v1` 要 APP 端
        签名，web 端的 a_bogus 它不认，带签名返回 200 但 body 为 0 字节），
        所以搜索＝已收录书库的检索，不是服务端全站检索。另外：
        - 输入纯数字 / 粘贴 `/page/<id>` 链接 → 直接按 book_id 开
        - 输入分类名 → 跳到该分类榜单
        """
        q = (query or "").strip()
        if not q:
            return []
        m = re.search(r"/page/(\d{6,})", q)
        bid = m.group(1) if m else (q if q.isdigit() and len(q) >= 6 else "")
        if bid:
            return [Book(book_id=bid, title=f"书籍 {bid}")]

        # 分类名（"玄幻""都市"这种）直接跳成分类浏览的结果
        for code, name in self.categories():
            if q in name or name in q:
                return self.by_category(code, limit=limit)

        try:
            lib = self.load_library()
        except EngineError:
            lib = self._index or []
        ql = q.lower()
        hits = [
            b for b in lib
            if ql in b.title.lower() or ql in (b.author or "").lower()
        ]
        return hits[:limit]

    def detail(self, book_id: str) -> dict:
        bid = str(book_id)
        cached = self._book_cache.get(bid)
        if cached:
            return cached
        page = self._get(f"/page/{bid}")
        info = self._parse_detail(bid, page)
        info["book_id"] = bid
        info["chapter_count"] = len(self._parse_chapters(page))
        self._book_cache[bid] = info
        return info

    def directory(self, book_id: str) -> list[Chapter]:
        page = self._get(f"/page/{book_id}")
        chaps = self._parse_chapters(page)
        if not chaps:
            raise EngineError(f"这本书（{book_id}）的目录没解析出来")
        return chaps

    def chapter_text(self, item_id: str) -> str:
        page = self._get(f"/reader/{item_id}")
        m = re.search(r'<div class="muye-reader-content[^"]*">(.*?)</div>', page, re.S)
        if not m:
            raise EngineError(f"章节 {item_id} 的正文没取到")
        text = decode_pua(html_to_text(m.group(1)))
        text = text.replace("\u3000", " ").strip()
        if not text:
            raise EngineError(f"章节 {item_id} 解码后是空的")
        return text

    # -- 兼容属性 --------------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(_CHARSET)

    def close(self) -> None:  # 保持和 TomatoEngine 一样的收尾接口
        return None