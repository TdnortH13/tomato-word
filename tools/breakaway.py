"""试试用 CREATE_BREAKAWAY_FROM_JOB 把引擎从当前 Job Object 里放出来。

如果宿主用 Job Object 限制子进程（禁止命名管道 / 限制 WebView2 的子进程创建），
breakaway 启动是唯一能绕过的原生手段。返回 0 表示真正创建了进程；
ERROR_ACCESS_DENIED(5) 表示当前 job 不允许 breakaway。
"""
from __future__ import annotations

import ctypes as C
import os
import sys
import time
from pathlib import Path

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_CONSOLE = 0x00000010
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0


class STARTUPINFO(C.Structure):
    _fields_ = [
        ("cb", C.c_ulong),
        ("lpReserved", C.c_wchar_p),
        ("lpDesktop", C.c_wchar_p),
        ("lpTitle", C.c_wchar_p),
        ("dwX", C.c_ulong), ("dwY", C.c_ulong),
        ("dwXSize", C.c_ulong), ("dwYSize", C.c_ulong),
        ("dwXCountChars", C.c_ulong), ("dwYCountChars", C.c_ulong),
        ("dwFillAttribute", C.c_ulong),
        ("dwFlags", C.c_ulong),
        ("wShowWindow", C.c_ushort),
        ("cbReserved2", C.c_ushort),
        ("lpReserved2", C.c_void_p),
        ("hStdInput", C.c_void_p),
        ("hStdOutput", C.c_void_p),
        ("hStdError", C.c_void_p),
    ]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [
        ("hProcess", C.c_void_p),
        ("hThread", C.c_void_p),
        ("dwProcessId", C.c_ulong),
        ("dwThreadId", C.c_ulong),
    ]


def launch(exe: Path, cwd: Path, env: dict[str, str], flags: int, label: str) -> None:
    si = STARTUPINFO()
    si.cb = C.sizeof(si)
    si.dwFlags = STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    pi = PROCESS_INFORMATION()

    # 环境块：UTF-16 的 KEY=VALUE\0 串联，末尾再补一个 \0
    block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"
    env_buf = C.create_unicode_buffer(block)

    k32 = C.WinDLL("kernel32", use_last_error=True)
    k32.CreateProcessW.restype = C.c_bool
    k32.CreateProcessW.argtypes = [
        C.c_wchar_p, C.c_wchar_p, C.c_void_p, C.c_void_p, C.c_bool, C.c_ulong,
        C.c_void_p, C.c_wchar_p, C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION),
    ]
    ok = k32.CreateProcessW(
        C.c_wchar_p(str(exe)),
        None,
        None, None,
        False,
        flags | CREATE_UNICODE_ENVIRONMENT,
        env_buf,
        C.c_wchar_p(str(cwd)),
        C.byref(si),
        C.byref(pi),
    )
    err = C.get_last_error()
    print(f"{label}: ok={bool(ok)} err={err} pid={pi.dwProcessId}")
    if ok:
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    exe = root / "engine" / "番茄超市.exe"
    if not exe.is_file():
        print("找不到引擎", exe, file=sys.stderr)
        return 1

    env = dict(os.environ)
    env["FQPC_HOME"] = str(root / "runtime")
    env["RUST_BACKTRACE"] = "1"

    print("== 1) breakaway ==")
    launch(exe, exe.parent, env, CREATE_BREAKAWAY_FROM_JOB, "breakaway")
    time.sleep(8)

    log = root / "runtime" / "rust.log"
    print("rust.log 存在:", log.exists())
    if log.exists():
        print(log.read_text(encoding="utf-8", errors="replace")[-400:])

    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq 番茄超市.exe"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    print("tasklist:", out.strip().replace("\n", " | "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())