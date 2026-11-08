"""无头自检：把界面搭起来，检查布局几何与各视图渲染是否正常（不需要人眼看）。

用法：python tools/probe.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

from tomato_word import demo
from tomato_word.engine import TomatoEngine
from tomato_word.store import Store
from tomato_word.ui import WordApp

root = tk.Tk()
app = WordApp(root, TomatoEngine(None), Store(), demo_mode=True)
root.update()
root.update_idletasks()

problems: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        problems.append(msg)


def geom(w) -> str:
    return f"{w.winfo_width()}x{w.winfo_height()}@{w.winfo_x()},{w.winfo_y()}"


print("== 窗口 ==")
print("root:", root.geometry(), "ws:", geom(app.ws))
print("window title:", root.title())
print("ribbon_body:", geom(app.ribbon_body))
print("page:", geom(app.page), "text:", geom(app.text))
print("statusbar:", geom(app.statusbar), "grip:", geom(app.grip))
print("search_entry:", geom(app.search_entry))
print("search_entry class:", app.search_entry.winfo_class())
print("tk patchlevel:", root.tk.call("info", "patchlevel"))
# Windows 上 tk.Entry 的 IME 组合窗口不可靠，搜索框必须是 ttk.Entry(TEntry)
check(
    app.search_entry.winfo_class() == "TEntry",
    f"搜索框应为 ttk.Entry(TEntry)，实际 {app.search_entry.winfo_class()}",
)
check(str(root.tk.call("tk", "windowingsystem")) == "win32", "窗口系统不是 win32")

check(app.ribbon_body.winfo_height() >= 80, f"ribbon 高度异常：{app.ribbon_body.winfo_height()}")
check(app.page.winfo_width() > 400, f"纸张太窄：{app.page.winfo_width()}")
check(app.page.winfo_height() > 300, f"纸张太矮：{app.page.winfo_height()}")
check(
    app.page.winfo_y() + app.page.winfo_height() <= app.ws.winfo_height() + 1,
    "纸张超出工作区底部",
)
check(app.text.winfo_width() > 300, f"正文区太窄：{app.text.winfo_width()}")
check(app.statusbar.winfo_height() >= 20, "状态栏过矮")
check(app.grip.winfo_x() + app.grip.winfo_width() <= app.ws.winfo_width() + 1, "缩放手柄越界")

print("\n== 启动落在书城（演示模式）==")
check(app.mode == "home", f"演示模式启动应在书城，实际 {app.mode}")
home = app.text.get("1.0", "end-1c")
print(home[:150].replace("\n", " / "))
check("番茄书城" in home, "书城标题缺失")
check("长夜将尽" in home and "雾港纪事" in home, "书城推荐未列出示例书")
check(app.text.tag_ranges("link") != (), "书城里没有可点链接")

print("\n== 任务栏 / 最小化 ==")
import ctypes  # noqa: E402

user32 = ctypes.windll.user32
app._as_app_window()  # 立即执行一次，不依赖 after 计时
root.update()
hwnd = root.winfo_id()
for _ in range(8):
    parent = user32.GetParent(hwnd)
    if not parent:
        break
    hwnd = parent
ex = user32.GetWindowLongW(hwnd, -20)
print(f"top-level hwnd={hwnd} exstyle=0x{ex & 0xFFFFFFFF:08X}")
check(bool(ex & 0x00040000), "顶层窗口缺少 WS_EX_APPWINDOW（任务栏不会显示）")
check(not (ex & 0x00000080), "顶层窗口仍是 WS_EX_TOOLWINDOW（会被任务栏隐藏）")
app.minimize()
root.update()
check(bool(user32.IsIconic(hwnd)), "最小化后窗口不是最小化状态（任务栏点不回来）")
user32.ShowWindow(hwnd, 9)  # SW_RESTORE
root.update()
check(not user32.IsIconic(hwnd), "从最小化恢复失败")

print("\n== 视图：伪装文档 ==")
app.show_document()
root.update()
print("title:", root.title())
print("mode:", app.mode)
doc = app.text.get("1.0", "end-1c")
print("chars:", len(doc))
print(doc[:120].replace("\n", " / "))
check("第三季度项目进展周报" in doc, "伪装文档标题缺失")
check(app.text.tag_ranges("h1") != (), "h1 标签未应用")

print("\n== 视图：搜索结果 ==")
app.search_var.set("长夜")
app.do_search()
root.update()
print("mode:", app.mode, "| title:", root.title())
res = app.text.get("1.0", "end-1c")
print(res[:160].replace("\n", " / "))
check("长夜将尽" in res, "搜索结果未渲染")

print("\n== 视图：目录 ==")
app.open_book(demo.demo_books()[0])
root.update()
print("mode:", app.mode, "| title:", root.title())
toc = app.text.get("1.0", "end-1c")
print(toc[:160].replace("\n", " / "))
check("第一章 灯下" in toc, "目录未渲染")
check(app.mode == "toc", f"期望 toc，实际 {app.mode}")
check("从头开始读" in toc, "目录缺「从头开始读」入口")

print("\n== 视图：阅读（整章模式，默认）==")
app.open_chapter(0)
root.update()
print("mode:", app.mode, "| title:", root.title())
read = app.text.get("1.0", "end-1c")
print(read[:200].replace("\n", " / "))
check("陈默" in read, "正文未渲染")
check(not app.typing_mode, "打字机模式默认应为关")
check(app._shown == len(app._paras), f"整章模式应铺满全部段落：{app._shown}/{len(app._paras)}")
check("还有" not in read, "整章模式不该出现「还有 N 段」")
check("下一章" in read and "返回目录" in read, "正文底部缺章节导航")
check(len(app._paras) >= 12, f"示例章节太短，无法验证滚动：{len(app._paras)} 段")
_page = app.st_page.cget("text")
print("页码:", _page)
check("共 1 页" not in _page, f"长章节页码不对（内容超过一屏却说 1 页）：{_page}")

print("\n== 打字机模式开关 ==")
app.toggle_typing()
root.update()
check(app.typing_mode, "打字机模式未打开")
check(app._shown == 8, f"打字机模式应只铺 8 段：{app._shown}")
check("还有" in app.text.get("1.0", "end-1c"), "打字机模式缺「还有 N 段」提示")
app.toggle_typing()
root.update()
check(not app.typing_mode, "打字机模式未关闭")
check(app._shown == len(app._paras), "关闭打字机后应铺满整章")

print("\n== 翻页（章内滚动 → 到底换章）==")
app.open_chapter(0)
root.update()
top0 = app.text.yview()[0]
app.page_down()
root.update()
top1 = app.text.yview()[0]
print("yview:", round(top0, 3), "->", round(top1, 3))
check(top1 > top0, f"page_down 未滚动正文（{top0} -> {top1}）")
idx_before = app.read_index
steps = 0
while app.read_index == idx_before and steps < 60:
    app.page_down()
    root.update()
    steps += 1
print("chapter:", idx_before, "->", app.read_index, f"（{steps} 次翻页）")
check(app.read_index == idx_before + 1, f"滚到底后应只前进一章，实际 {app.read_index}")
app.page_up()
for _ in range(60):
    app.page_up()
    root.update()
check(app.read_index == 0, f"page_up 未能回到第一章（{app.read_index}）")

print("\n== 导航栈：返回 ==")
app.show_home()
root.update()
app.open_book(demo.demo_books()[0])
root.update()
app.go_back()
root.update()
print("after go_back:", app.mode)
check(app.mode == "home", f"返回后应回书城，实际 {app.mode}")

print("\n== 老板键：回伪装 ==")
app.open_chapter(0)
root.update()
app._on_hotkey()
root.update()
check(app.mode == "doc", f"老板键未切回 doc（{app.mode}）")
print("mode:", app.mode)
app._on_hotkey()
root.update()
check(app.mode == "read", f"老板键未切回 read（{app.mode}）")
print("mode:", app.mode)

print("\n== 翻章 ==")
before = app.read_index
app.next_chapter()
root.update()
print("chapter:", before, "->", app.read_index)
check(app.read_index == before + 1, "下一章失败")

print("\n== 字号 ==")
app.font_size = 12  # 置回基准值，避免上次运行持久化下来的字号影响断言
app.change_font(+2)
root.update()
check(app.font_size == 14, f"字号未变化：{app.font_size}")
print("font_size:", app.font_size)

print("\n== 状态栏 ==")
print(app.st_page.cget("text"), "|", app.st_words.cget("text"), "|", app.st_hint.cget("text"))
check("字数" in app.st_words.cget("text"), "字数未计算")

print("\n== 搜索框：中文输入兜底 ==")
app.search_var.set("")
root.clipboard_clear()
root.clipboard_append("玄幻")
root.update()
app._paste_into_search()
root.update()
print("粘贴后 search_var:", repr(app.search_var.get()))
check(app.search_var.get() == "玄幻", f"Ctrl+V 粘贴没进搜索框：{app.search_var.get()!r}")

app._select_all_search()
root.update()
check(app.search_entry.selection_present(), "Ctrl+A 全选无效")

app.search_var.set("")
app._search_clipboard()
root.update()
print("按剪贴板搜索 → mode:", app.mode, "| title:", root.title())
check(app.search_var.get() == "玄幻", "按剪贴板搜索没回填关键词")
check(app.mode == "results", f"按剪贴板搜索没进结果页：{app.mode}")

# 右键菜单对象能建出来（不真的弹窗）
menu = tk.Menu(root, tearoff=0)
menu.add_command(label="x", command=lambda: None)
menu.destroy()
check(True, "右键菜单构建")

print("\n== 结果 ==")
if problems:
    print(f"发现 {len(problems)} 个问题：")
    for p in problems:
        print("  [FAIL]", p)
else:
    print("全部检查通过")

root.destroy()
sys.exit(1 if problems else 0)
