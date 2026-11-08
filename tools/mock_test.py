"""用假引擎验证 engine.py 的解析逻辑（真实引擎在本机跑不起来时也能验证）。

假引擎严格模仿 core 的行为：
- 只认 Authorization: Bearer <token>，否则 401 {"success":false,"error":"session required"}
- /api/v1/search 返回 search_tabs 包裹结构
- /api/v1/books/{id}/directory 返回 chapterListWithVolume（数组的数组）
- /api/v1/chapters/{id} 的 data.content 是 base64url 编码的 HTML
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tomato_word.engine import TomatoEngine, collect_books, html_to_text  # noqa: E402

TOKEN = "0123456789abcdef" * 4
PORT = 18099

HTML = "<p>第一段正文，用来验证 HTML 转纯文本。</p><p>第二段正文，含&ldquo;引号&rdquo;与&amp;符号。</p>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._json(401, {"success": False, "error": "session required"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path

        if path == "/api/v1/search":
            qy = (q.get("query") or [""])[0]
            return self._json(200, {
                "code": 200,
                "search_tabs": [{
                    "tab_type": 3,
                    "title": "小说",
                    "data": [
                        {"book_data": [{
                            "book_id": "7001",
                            "book_name": f"测试书名·{qy}",
                            "author": "测试作者",
                            "abstract": "这是一段简介。",
                            "thumb_url": "http://example.invalid/a.jpg",
                            "pure_category_tags": "都市,悬疑",
                        }]},
                        {"book_data": [{"book_id": "7002", "book_name": "第二本"}]},
                        {"book_data": [{"book_name": "没有 id 的脏数据"}]},
                    ],
                }, {
                    "tab_type": 8,
                    "title": "漫画",
                    "data": [{"book_data": [{"book_id": "m1", "book_name": "漫画不该被选"}]}],
                }],
            })

        if path == "/api/v1/rank/1":
            return self._json(200, {"code": 200, "data": {"books": [
                {"book_id": "8003", "book_name": "榜单书", "author": "丙"},
                {"book_id": "8003", "book_name": "榜单书（重复）"},
            ]}})

        if path == "/api/v1/recommend/homepage":
            return self._json(200, {"code": 200, "data": {"tabs": [
                {"tab_type": 0, "cell_name": "推荐", "cell_data": [
                    {"book_data": [{"book_id": "8001", "book_name": "首页书一", "author": "甲",
                                    "abstract": "首页简介", "thumb_url": "http://example.invalid/c.jpg"}]},
                    {"book_data": [{"book_id": "8002", "book_name": "首页书二", "author": "乙"}]},
                ]},
                {"tab_type": 0, "cell_name": "短剧", "video_data": [
                    {"item_id": "v1", "title": "短剧条目不该被当成书"},
                ]},
            ]}})

        if path == "/api/v1/books/7001/directory":
            return self._json(200, {"code": 200, "data": {"data": {"chapterListWithVolume": [
                [
                    {"itemId": "c1", "title": "第一章 起", "volume_name": "第一卷"},
                    {"itemId": "c2", "title": "第二章 承", "volume_name": "第一卷"},
                ],
                [
                    {"itemId": "c3", "title": "第三章 转", "volume_name": "第二卷"},
                ],
            ]}}})

        if path == "/api/v1/books/7001/detail":
            return self._json(200, {"code": 200, "data": {
                "book_name": "测试书名", "author": "测试作者", "abstract": "简介",
                "tags": "都市,悬疑", "expand_thumb_url": "http://example.invalid/b.jpg",
            }})

        if path == "/api/v1/chapters/c1":
            enc = base64.urlsafe_b64encode(HTML.encode("utf-8")).decode().rstrip("=")
            return self._json(200, {"code": 200, "data": {"content": enc}})

        if path == "/api/v1/chapters/c2":
            return self._json(200, {"code": 200, "data": {"content": "<p>已经是明文，不需要 base64。</p>"}})

        if path == "/api/v1/books/404/directory":
            return self._json(200, {"code": 1001, "message": "书籍不存在"})

        return self._json(404, {"code": 404, "message": "not found"})


problems: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        problems.append(msg)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = TomatoEngine()
    eng.port, eng.token = PORT, TOKEN

    print("== 鉴权 ==")
    check(eng._ping(), "正确 token 能通过自检")
    eng.token = "f" * 64
    check(not eng._ping(), "错误 token 被判为未就绪")
    eng.token = TOKEN

    print("== 搜索 ==")
    books = eng.search("剑来")
    check(len(books) == 2, f"只取小说 tab 且有 book_id 的条目，实际 {len(books)} 条")
    check(books[0].book_id == "7001" and books[0].author == "测试作者", "字段解析正确")
    check(books[0].tags == "都市,悬疑", "标签解析正确")
    check(all(b.book_id != "m1" for b in books), "漫画 tab 被正确忽略")

    print("== 书城首页 / 榜单 ==")
    home = eng.homepage()
    check(len(home) == 2, f"首页只收真书，实际 {len(home)} 本")
    check(home[0].book_id == "8001" and home[0].author == "甲", "首页字段解析正确")
    check(all(b.book_id != "v1" for b in home), "没有 book_id 的视频条目被忽略")
    rk = eng.rank("1")
    check(len(rk) == 1 and rk[0].title == "榜单书", f"榜单按 book_id 去重（期望 1 本），实际 {len(rk)} 本")
    check(all(b.book_id.isdigit() for b in home + rk), "只认纯数字 book_id（章节 item_id 不误判）")
    nested = {"a": [{"b": {"c": [{"book_id": 9001, "book_name": "深层书"}]}}]}
    check([b.title for b in collect_books(nested)] == ["深层书"], "深层嵌套也能捞出书目")

    print("== 详情 ==")
    d = eng.detail("7001")
    check(d["title"] == "测试书名" and d["tags"] == "都市,悬疑", "详情解析正确")

    print("== 目录 ==")
    chs = eng.directory("7001")
    check(len(chs) == 3, f"三层卷结构展平成 3 章，实际 {len(chs)}")
    check(chs[0].item_id == "c1" and chs[0].volume == "第一卷", "章节字段正确")
    check(chs[2].title == "第三章 转", "第三章标题正确")

    print("== 正文 ==")
    t1 = eng.chapter_text("c1")
    check("第一段正文" in t1 and "<p>" in t1, "base64 正文已解码")
    txt = html_to_text(t1)
    check("第一段正文，用来验证 HTML 转纯文本。" in txt, "HTML 转纯文本正确")
    check("“引号”" in txt and "&" in txt, "实体转义正确")
    check("<p>" not in txt, "标签已清除")
    t2 = eng.chapter_text("c2")
    check("已经是明文" in t2, "明文正文不被误当 base64")

    print("== 错误传播 ==")
    try:
        eng.directory("404")
        check(False, "code!=200 应该抛 EngineError")
    except Exception as exc:
        check("书籍不存在" in str(exc), f"错误信息透传：{exc}")

    print("== 未就绪保护 ==")
    eng.port = None
    try:
        eng.search("x")
        check(False, "未连接时应抛错")
    except Exception as exc:
        check("未就绪" in str(exc) or "端口" in str(exc), f"未连接时抛错：{exc}")

    srv.shutdown()
    print("\n" + ("全部通过" if not problems else f"{len(problems)} 项失败"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
