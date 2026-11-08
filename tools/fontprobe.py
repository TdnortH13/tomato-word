"""探针：番茄网页正文的字体混淆能否用字形比对解出来。

思路：混淆字体把一部分汉字换成私用区码位（U+E000..），字形本身还是那个
汉字的轮廓。于是把每个私用区字形渲染成位图，拿去和系统字体里的候选汉字
逐像素比对，相似度最高的就是原字。

这一步只做「判据」：找出哪个系统字体跟番茄的混淆字体同源，以及最佳匹配
的分数分布。分数能到 0.9 以上且唯一，说明可行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFont

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
SIZE = 64          # 渲染分辨率
BOX = 40           # 归一化后的画布边长

CANDIDATE_FONTS = [
    ("simsun", "C:/Windows/Fonts/simsun.ttc", 0),
    ("simhei", "C:/Windows/Fonts/simhei.ttf", 0),
    ("msyh",   "C:/Windows/Fonts/msyh.ttc", 0),
    ("kaiti",  "C:/Windows/Fonts/simkai.ttf", 0),
]


def han_chars() -> list[str]:
    """GB2312 一级汉字（3755），覆盖绝大多数正文用字。"""
    out = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                out.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return out


def render(font: ImageFont.FreeTypeFont, ch: str) -> np.ndarray | None:
    """把一个字形渲染成 BOX×BOX 的归一化灰度图。"""
    try:
        mask = font.getmask(ch, mode="L")
    except Exception:
        return None
    if mask.size[0] == 0 or mask.size[1] == 0:
        return None
    img = Image.frombytes("L", mask.size, bytes(mask))
    bbox = img.getbbox()
    if not bbox:
        return None
    img = img.crop(bbox)
    # 等比缩放到 BOX 画布中心
    w, h = img.size
    scale = BOX / max(w, h)
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    canvas = Image.new("L", (BOX, BOX), 0)
    canvas.paste(img, ((BOX - img.size[0]) // 2, (BOX - img.size[1]) // 2))
    arr = np.asarray(canvas, dtype=np.float32).ravel()
    n = np.linalg.norm(arr)
    return arr / n if n else None


def main() -> int:
    font_path = WORK / "font.otf"
    if not font_path.exists():
        print("缺 font.otf", file=sys.stderr)
        return 1

    from fontTools.ttLib import TTFont
    cmap = TTFont(str(font_path)).getBestCmap()
    pua = sorted(c for c in cmap if 0xE000 <= c <= 0xF8FF)
    print(f"混淆码位: {len(pua)}")

    obf_font = ImageFont.truetype(str(font_path), SIZE)
    obf = []
    used = []
    for cp in pua:
        v = render(obf_font, chr(cp))
        if v is not None:
            obf.append(v)
            used.append(cp)
    mat_obf = np.stack(obf)
    print(f"可渲染: {len(used)}")

    chars = han_chars()
    print(f"候选汉字: {len(chars)}")

    lines = []
    for name, path, idx in CANDIDATE_FONTS:
        if not Path(path).exists():
            lines.append(f"{name}: 字体不存在 {path}")
            continue
        try:
            cf = ImageFont.truetype(path, SIZE, index=idx)
        except Exception as e:  # noqa: BLE001
            lines.append(f"{name}: 加载失败 {e}")
            continue
        cand, keep = [], []
        for ch in chars:
            v = render(cf, ch)
            if v is not None:
                cand.append(v)
                keep.append(ch)
        mat_c = np.stack(cand)          # (N, D)
        sim = mat_obf @ mat_c.T         # (M, N) 余弦
        best = sim.argmax(axis=1)
        scores = sim[np.arange(len(best)), best]
        # 次优分（用于判断唯一性）
        tmp = sim.copy()
        tmp[np.arange(len(best)), best] = -1
        second = tmp.max(axis=1)

        lines.append("")
        lines.append(f"== {name} ==")
        lines.append(f"  平均最佳分 {scores.mean():.3f}  中位 {np.median(scores):.3f}  "
                     f">0.9 的占 {(scores > 0.9).mean() * 100:.1f}%")
        lines.append(f"  平均领先次优 {(scores - second).mean():.3f}")
        order = np.argsort(-scores)[:12]
        for i in order:
            lines.append(f"  U+{used[i]:04X} -> {keep[best[i]]}  score {scores[i]:.3f}  "
                         f"second {second[i]:.3f}")

    out = WORK / "probe_out.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-30:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())