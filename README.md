# 番茄文档 · TomatoWord

把番茄小说伪装成一个 Word 2019 窗口，在电脑上悄悄读。老板键一按，周报变小说。

纯 Python + Tkinter 手绘 Word 界面，零第三方依赖；书籍数据来自番茄小说官网（`fanqienovel.com`）的真实页面，自带字体混淆解码。

> 本项目不托管、不分发任何书籍内容。所有正文实时从官网抓取，付费章节只有官网提供的试读部分。

---

## 它长什么样

一个 Word 2019 的窗口：蓝色标题栏、Ribbon 选项卡（开始 / 插入 / 设计 / 布局 / 引用 / 邮件 / 审阅 / 视图 / 帮助）、灰色工作区、居中的白纸、底部状态栏带页码和字数。

- `Ctrl+Alt+W` — **老板键**，在「第三季度项目进展周报（草稿）」和「正在读的书」之间瞬间切换
- 阅读态随便敲键盘，正文会一个词一个词「打」出来（像真的在写文档）
- 状态栏写的是「第 1 页，共 3 页｜字数：4425｜阅读中」，Anything 看起来都像 Word

## 功能

| | |
|---|---|
| 书城 | 全站 108 个榜单扫出来的 **562 本**，16 个真实分类 |
| 搜索 | 本地书库按书名 + 作者匹配；粘 book_id 或官网链接直接开书 |
| 目录 | 单本最多 351 章，按章号自然排序 |
| 正文 | **整章铺开**，`PgDn/PgUp` 章内翻屏，翻到底自动进下一章 |
| 阅读 | 字号 `Ctrl+±`，右侧细滚动条，滚轮可用 |
| 打字机 | `Ctrl+T` 切换「整章铺开 / 敲字吐字」两种模式 |
| 书架 | `Ctrl+O`，进度自动存盘 |
| 伪装 | 演示稿与真实阅读态一键切换 |

## 关键技术点

### 1. 字体混淆解码

番茄官网把正文里的字换成私用区（PUA）码位做反爬。本程序自带映射表
`tomato_word/data/charset.json`（372 个字符，取自 [`ying-ck/fanqienovel-downloader`](https://github.com/ying-ck/fanqienovel-downloader)）。

解码规则极简：

```python
汉字 = charset[0][码位 - 0xE3E8]
```

实测覆盖率 **100%**（2244/2244），PUA 范围 `0xE3E9..0xE55B`。解出来的正文：

> 大乾皇朝，疆域亿万里。北地，黑潭山，七月初九。统治周遭百岭之地已有千年之久的黑潭山十大部族之一'姜氏部族'，如今已经到了生死存亡的时刻。

### 2. 书库怎么从 10 本变 562 本

官网首页只给 20 本，`/rank` 给 10 本。真正的入口是**分类榜单**：

```
/rank/<gender>_<type>_<category_code>
   gender ∈ {0,1,2}    type ∈ {1,2}
   18 个 category_code
= 108 个榜单，每个 10 本，去重 562 本，6 线程扫完约 9 秒
```

扫描结果缓存在 `%LOCALAPPDATA%\TomatoWord\library.json`，第二次启动直接读。

### 3. 中文输入（Tk 的老大难）

Tk 8.6 在 Windows 上**不处理 IME/TSF** —— 没有 `ITfDocumentMgr`，不响应
`WM_IME_STARTCOMPOSITION` / `WM_IME_COMPOSITION`，现代输入法直接不激活。
`tk useinputmethods` 在 win32 上是空操作（设了返回 0，读回还是 0）。

所以做了三层兜底：

1. **`Ctrl+V` 粘贴** —— 剪贴板走 `WM_PASTE`，完全不过输入法，一定能用
2. **右键菜单** —— 粘贴 / 全选 / 清空 / **按剪贴板内容搜索**
3. **`imm32` 强挂 IME 上下文** —— 启动时和每次聚焦搜索框时尝试 `ImmCreateContext` + `ImmAssociateContext`

Ribbon 上还有 **「粘贴搜」** 按钮：在别处复制书名，点它就直接搜，一个字不用打。

## 快速开始

### 方式一：直接跑 exe

从 [Releases](../../releases) 下载 `番茄文档-v1.8.zip`，解压，双击 `番茄文档.exe`。

没装 Python 也能用，exe 自带解释器。

### 方式二：从源码跑

```bash
python -m pip install -r requirements.txt   # 实际上没有依赖，这行可以跳过
python run.py            # 真实模式，自动连官网
python run.py --demo     # 离线演示，用内置的假稿子和示例书
```

需要 Python 3.9+，只用标准库。

### 命令行参数

```
--demo          离线演示模式（不联网）
--check         自检：找引擎、测解码表、打印环境
--engine PATH   指定本地引擎 exe
--query 关键词   直接搜索某个词
```

## 自检

```bash
python tools/probe.py         # UI 几何 / 视图切换 / 翻页 / 导航栈 / 剪贴板粘贴
python tools/mock_test.py     # 假引擎的契约测试，23 项断言
python tools/errview_test.py  # 错误页与可点入口
python tools/web_ui_test.py   # 真实官网抓取（需要联网）
```

`probe.py` 会真的把窗口搭起来、跑一遍所有交互，最后打印「全部检查通过」。

## 已知限制

- **官方搜索接口接不通**。`/api/author/search/search_book/v1` 需要 APP 端的
  `X-Argus` / `X-Gorgon` / `X-Ladon` 签名 + 设备注册链。仓库里的 `tomato_word/abogus.py`
  是 web 端 `a_bogus` 签名的完整 Python 移植（416 行 Rust 机械翻译，
  `SM3('abc')` 与标准值一致），但该接口对 web 签名返回 200 + 空 body，
  所以搜索退化为本地书库匹配。
- **付费章节只有试读**。官网给的是它给的那部分，本程序不绕过。
- **本地引擎未接上**。项目原本想调 [`vdnight89/tomato-supermarket`](https://github.com/vdnight89/tomato-supermarket)
  的本地 HTTP API（`/api/v1/*`），但那个 Tauri exe 在本机启动后
  写完 `fqpc core ready` 就退出，窗口建不起来。`tomato_word/engine.py` 仍然保留完整实现，
  引擎能跑的话会自动接上并解锁听书 / 短剧 / 漫画。
- **无代码签名**。exe 是 PyInstaller 打包的未签名文件，Windows SmartScreen 会提示。

## 项目结构

```
tomato-word/
├── run.py                      入口
├── tomato_word/
│   ├── __main__.py             CLI 参数、DPI 感知
│   ├── ui.py                   Word 外观 + 所有视图（约 1400 行）
│   ├── web.py                  WebEngine：官网抓取 + PUA 解码 + 书库扫描
│   ├── engine.py               TomatoEngine：本地引擎 HTTP 客户端
│   ├── abogus.py               a_bogus 签名（Python 移植，已可用但接口不吃）
│   ├── store.py                书架 / 进度 / 字号持久化
│   ├── demo.py                 离线演示数据（假周报 + 示例书）
│   └── data/charset.json       PUA → 汉字 映射表（372 项）
└── tools/                      自检脚本
    ├── probe.py                UI 全流程断言
    ├── mock_test.py            假引擎契约测试
    ├── errview_test.py         错误页测试
    ├── web_ui_test.py          真实官网端到端
    ├── breakaway.py            Job Object / CreateProcess 诊断
    ├── dbg_hwnd.py             Win32 窗口样式诊断
    ├── fontprobe.py            字体混淆比对实验
    └── race_test.py            抢引擎启动时间窗
```

## 许可

本项目以 **GPL-3.0-only** 发布，见 [`LICENSE`](LICENSE)。

- Word 外观的灵感来自 [`SevenvxCN/word-touchfish`](https://github.com/SevenvxCN/word-touchfish)（该项目无 license，本仓库未复制其代码）
- 本地引擎部分对接 [`vdnight89/tomato-supermarket`](https://github.com/vdnight89/tomato-supermarket)（GPL-3.0，独立程序，通过 loopback HTTP 调用，不修改、不再分发）
- PUA 映射表来自 [`ying-ck/fanqienovel-downloader`](https://github.com/ying-ck/fanqienovel-downloader)