"""最小同步检查：抓一次官网 → 直接喂给界面渲染 → dump 文本。

刻意不用后台线程、不跑 mainloop：网络请求先在主线程里同步做完，再把
结果当成 `books` 参数交给 `show_home(books=...)`，这样界面层只用 Tk 的
同步路径，不会被线程或事件队列搅进来。
"""

from __future__ import annotations

import io
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tomato_word.store import Store  # noqa: E402
from tomato_word.ui import WordApp  # noqa: E402
from tomato_word.web import WebEngine  # noqa: E402

LOG: list[str] = []
FAILED: list[str] = []


def ok(cond: bool, msg: str) -> None:
    LOG.append(("  OK  " if cond else "  FAIL") + "  " + msg)
    if not cond:
        FAILED.append(msg)


def main() -> int:  # noqa: C901
    web = WebEngine()
    LOG.append("charset ready: %s" % web.ready)
    ok(web.ready, "charset.json 加载成功")

    books = web.homepage(limit=6)
    LOG.append("抓到 %d 本：%s" % (len(books), [b.title for b in books[:6]]))
    ok(len(books) >= 4, "官网首页抓到 %d 本书" % len(books))
    ok(all(b.title for b in books), "每本书都有书名（不再把作者粘在书名后）")

    book = books[0]
    chapters = web.directory(book.book_id)
    LOG.append("《%s》%d 章，首章=%r" % (book.title, len(chapters), chapters[0].title))
    ok(len(chapters) > 100, "目录 %d 章" % len(chapters))
    ok("第1章" in chapters[0].title, "首章是第1章，不是「最近更新」那条")

    body = web.chapter_text(chapters[0].item_id)
    LOG.append("首章 %d 字：%r" % (len(body), body[:80]))
    ok(len(body) > 1000, "首章正文 %d 字" % len(body))
    ok(not any(0xE000 <= ord(c) <= 0xF8FF for c in body), "正文里没有残留私用区字符")

    # ---- 界面渲染 ----
    root = tk.Tk()
    root.geometry("1200x780+40+40")
    app = WordApp(root, web, Store(), demo_mode=False)
    app.web_fallback = True

    app.show_home(books)
    root.update()
    home = app.text.get("1.0", "end-1c")
    LOG.append("---- 书城屏（前 560 字） ----")
    LOG.append(home[:560])
    ok("官网直连" in home, "书城屏标注了「官网直连」")
    ok(book.title in home, f"书城屏出现了真实书名《{book.title}》")
    ok("演示数据" not in home, "书城屏不是演示数据")
    ok(home.count("\n") > 10, "书城屏渲染了 %d 行" % home.count("\n"))
    ok(len(app.text.tag_names()) > 3, "书名是可点击链接（%d 个 tag）" % len(app.text.tag_names()))

    app.read_book = app._as_dict(book)
    app.read_chapters = chapters
    app.read_index = 0
    app.show_toc(app._as_dict(book), chapters)
    root.update()
    toc = app.text.get("1.0", "end-1c")
    LOG.append("---- 目录屏（前 420 字） ----")
    LOG.append(toc[:420])
    ok("第1章" in toc, "目录屏从第1章开始")
    ok(str(len(chapters)) in toc or "共" in toc, "目录屏显示了章节总数")

    app._paras = [p for p in body.split("\n") if p.strip()]
    app.typing_mode = False
    app._render_read(keep_progress=False)
    root.update()
    read = app.text.get("1.0", "end-1c")
    LOG.append("---- 阅读屏（前 300 字） ----")
    LOG.append(read[:300])
    ok(len(read) > 500, "阅读屏铺开了 %d 字（整章，不是摘要）" % len(read))
    ok(not any(0xE000 <= ord(c) <= 0xF8FF for c in read), "阅读屏没有私用区字符")

    root.destroy()

    io.open(ROOT / "web_ui_out.txt", "w", encoding="utf-8").write("\n".join(LOG))
    print("\n".join(LOG[-4:]))
    print("\nFAILED: %d" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())