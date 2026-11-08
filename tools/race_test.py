"""抢跑测试：引擎在本机会在 core 起来后立刻因命名管道受限而退出，
所以用 10ms 级轮询去抓那个"活着"的瞬间，验证真实接口能不能打通。

用法：python tools/race_test.py [关键词]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tomato_word.engine import READY_RE, engine_home, find_engine_exe, html_to_text  # noqa: E402

QUERY = sys.argv[1] if len(sys.argv) > 1 else "剑来"


def main() -> int:
    exe = find_engine_exe()
    if not exe:
        print("找不到引擎 exe")
        return 2
    home = engine_home()
    log = home / "rust.log"
    try:
        log.unlink()
    except Exception:
        pass

    env = os.environ.copy()
    env["FQPC_HOME"] = str(home)
    print(f"引擎：{exe}")
    print(f"运行目录：{home}")
    proc = subprocess.Popen([str(exe)], env=env, cwd=str(exe.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    port = token = None
    t0 = time.time()
    while time.time() - t0 < 20:
        try:
            hits = READY_RE.findall(log.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            hits = []
        if hits:
            port, token = int(hits[-1][0]), hits[-1][1]
            break
        if proc.poll() is not None:
            break
        time.sleep(0.01)

    if not port:
        print(f"没抓到就绪行（进程退出码 {proc.poll()}）")
        return 3
    print(f"抓到 token（{time.time()-t0:.2f}s）：127.0.0.1:{port} / {token[:10]}…")

    def api(path: str, timeout: float = 2.0):
        url = f"http://127.0.0.1:{port}{path}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    ok = 0
    try:
        q = urllib.parse.quote(QUERY)
        payload = api(f"/api/v1/search?query={q}&tab_type=3&count=10&offset=0&page=1")
        tabs = payload.get("search_tabs") or (payload.get("data") or {}).get("search_tabs") or []
        books = []
        for tab in tabs:
            if int(tab.get("tab_type") or 0) != 3:
                continue
            for item in tab.get("data") or []:
                books += item.get("book_data") or []
        print(f"搜索「{QUERY}」→ {len(books)} 条")
        for b in books[:5]:
            print(f"  - {b.get('book_name')} / {b.get('author')} / id={b.get('book_id')}")
        ok += 1
        if books:
            bid = books[0]["book_id"]
            d = api(f"/api/v1/books/{bid}/directory", timeout=3.0)
            data = d.get("data") or {}
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            vols = inner.get("chapterListWithVolume") or []
            chs = [c for v in vols for c in (v or [])]
            print(f"《{books[0].get('book_name')}》目录 {len(chs)} 章，首章：{chs[0].get('title') if chs else '—'}")
            ok += 1
            if chs:
                c = api(f"/api/v1/chapters/{chs[0]['itemId']}", timeout=3.0)
                content = ((c.get("data") or {}).get("content") or "")
                text = html_to_text(content) if "<" in content else content
                print(f"首章正文 {len(text)} 字：{text[:80]!r}")
                ok += 1
    except Exception as exc:
        print(f"请求失败（引擎大概已经退出了）：{type(exc).__name__}: {exc}")
    finally:
        if proc.poll() is None:
            proc.terminate()
    print(f"通过 {ok}/3 步")
    return 0 if ok >= 2 else 4


if __name__ == "__main__":
    raise SystemExit(main())

