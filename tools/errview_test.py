"""检查「引擎未连接」错误页：文案、可点击链接、演示模式入口。"""

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tomato_word import demo  # noqa: E402
from tomato_word.engine import TomatoEngine  # noqa: E402
from tomato_word.store import Store  # noqa: E402
from tomato_word.ui import WordApp  # noqa: E402


def main() -> int:
    root = tk.Tk()
    app = WordApp(root, TomatoEngine("__nope__.exe"), Store(), demo_mode=True)
    root.update()

    app._engine_failed("测试：找不到引擎 exe")
    root.update()

    content = app.text.get("1.0", "end-1c")
    assert "引擎未连接" in content, "缺标题"
    assert "测试：找不到引擎 exe" in content, "缺错误详情"
    assert "重新连接引擎" in content, "缺重试链接"
    assert "演示数据" in content, "缺演示模式链接"
    assert "打开引擎目录" in content, "缺打开目录链接"
    assert any(t.startswith("lnk") for t in app.text.tag_names()), "没有可点击链接 tag"
    print("[ok] 错误页文案与链接齐全")

    app._enter_demo()
    root.update()
    doc = app.text.get("1.0", "end-1c")
    assert app.mode == "doc"
    assert demo.DOC_TITLE in doc, "演示模式没回到假周报"
    assert "引擎未连接" not in doc
    print("[ok] 点「演示数据」可切到假周报")

    app._open_engine_dir()  # 不应抛异常（目录不存在时自动建）
    root.update()
    print("[ok] 打开引擎目录未抛异常")

    app.quit()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
