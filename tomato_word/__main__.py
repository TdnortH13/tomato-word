"""入口：python -m tomato_word [--demo] [--check] [--engine PATH]"""

from __future__ import annotations

import argparse
import json
import sys


def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tomato-word", description="番茄小说 Word 伪装阅读器")
    ap.add_argument("--demo", action="store_true", help="不启动引擎，用内置示例书预览界面")
    ap.add_argument("--check", action="store_true", help="自检：连引擎并跑一次搜索，然后退出")
    ap.add_argument("--engine", metavar="PATH", help="指定番茄超市引擎 exe 的路径")
    ap.add_argument("--query", default="", help="配合 --check：要搜索的关键词")
    args = ap.parse_args(argv)

    from .engine import EngineError, TomatoEngine

    if args.check:
        eng = TomatoEngine(args.engine)
        print(f"引擎 exe：{eng.exe_path or '未找到'}")
        try:
            eng.connect(on_status=lambda m: print(f"  · {m}"))
        except EngineError as exc:
            print(f"[失败] {exc}")
            return 2
        print(f"引擎地址：{eng.base}  token={eng.token[:8]}…")
        try:
            books = eng.search(args.query or "剑来")
            print(f"搜索命中 {len(books)} 条：")
            for b in books[:5]:
                print(f"  - {b.title} / {b.author} / {b.book_id}")
            if books:
                chs = eng.directory(books[0].book_id)
                print(f"《{books[0].title}》目录 {len(chs)} 章，首章：{chs[0].title if chs else '—'}")
                if chs:
                    text = eng.chapter_text(chs[0].item_id)
                    print(f"首章正文 {len(text)} 字，开头：{text[:60]!r}")
        except EngineError as exc:
            print(f"[接口失败] {exc}")
            return 3
        finally:
            eng.shutdown()
        return 0

    import tkinter as tk

    _dpi_aware()

    from .store import Store
    from .ui import WordApp

    root = tk.Tk()
    root.title("文档1 - Word")
    store = Store()
    app = WordApp(root, TomatoEngine(args.engine), store, demo_mode=args.demo)
    root.protocol("WM_DELETE_WINDOW", app.quit)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
