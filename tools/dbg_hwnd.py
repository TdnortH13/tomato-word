"""调试：看 Tk 无边框窗口的 HWND / 扩展样式，以及 WS_EX_APPWINDOW 有没有设上。"""

from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

from tomato_word.engine import TomatoEngine
from tomato_word.store import Store
from tomato_word.ui import WordApp

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20


def style_of(h) -> str:
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    return f"0x{ex & 0xFFFFFFFF:08X} APP={bool(ex & 0x40000)} TOOL={bool(ex & 0x80)}"


root = tk.Tk()
app = WordApp(root, TomatoEngine(None), Store(), demo_mode=True)
root.update()
root.update_idletasks()
root.after(700, root.quit)
root.mainloop()

wid = root.winfo_id()
parent = user32.GetParent(wid)
print("winfo_id:", wid, style_of(wid))
print("GetParent:", parent, style_of(parent) if parent else "(0)")

found = []


@ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
def enum_proc(hwnd, lparam):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        found.append(hwnd)
    return True


user32.EnumWindows(enum_proc, 0)
print("本进程顶层窗口：")
for h in found:
    print("  ", h, style_of(h))

# 手动再设一次，看返回值
for h in [wid, parent] + found:
    if not h:
        continue
    old = user32.GetWindowLongW(h, GWL_EXSTYLE)
    user32.SetWindowLongW(h, GWL_EXSTYLE, (old & ~0x80) | 0x40000)
    err = ctypes.get_last_error()
    print(f"set on {h}: now {style_of(h)} lasterr={err}")

print("_taskbar_hwnd:", getattr(app, "_taskbar_hwnd", None))
root.destroy()
