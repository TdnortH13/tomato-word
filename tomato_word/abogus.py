"""a_bogus 签名（移植自 tomato-supermarket core/src/sign/abogus.rs）。

番茄官网搜索/推荐等 API 需要 a_bogus 参数，服务端才返回数据；缺失时
/api/author/search/search_book/v1 会返回 200 但 body 长度为 0。
"""

import random
import time

M32 = 0xFFFFFFFF

_TABLES = {
    "s0": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
    "s1": "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
    "s2": "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
    "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
    "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe=",
}

_INIT = [1937774191, 1226093241, 388252375, 0xDA8A0600,
         0xA96F30BC, 372324522, 0xE38DEE4D, 0xB0FB0E4E]

WINDOW_ENV = "1536|747|1536|834|0|30|0|0|1536|834|1536|864|1525|747|24|24|Win32"


def _rotl(e, r):
    r %= 32
    if r == 0:
        return e & M32
    return ((e << r) | (e >> (32 - r))) & M32


def _tj(j):
    return 2043430169 if j < 16 else 2055708042


def _ff(j, x, y, z):
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(j, x, y, z):
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & M32) & z)


def _prepare(block):
    w = [0] * 132
    for i in range(16):
        w[i] = (((block[i * 4] & 0xFF) << 24)
                | ((block[i * 4 + 1] & 0xFF) << 16)
                | ((block[i * 4 + 2] & 0xFF) << 8)
                | (block[i * 4 + 3] & 0xFF))
    for i in range(16, 68):
        t = w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15)
        t = (t ^ _rotl(t, 15) ^ _rotl(t, 23)) & M32
        w[i] = (t ^ _rotl(w[i - 13], 7) ^ w[i - 6]) & M32
    for i in range(64):
        w[i + 68] = (w[i] ^ w[i + 4]) & M32
    return w


def _compress(reg, block):
    w = _prepare(block)
    a = list(reg)
    for j in range(64):
        ss1 = _rotl((_rotl(a[0], 12) + a[4] + _rotl(_tj(j), j)) & M32, 7)
        ss2 = ss1 ^ _rotl(a[0], 12)
        tt1 = (_ff(j, a[0], a[1], a[2]) + a[3] + ss2 + w[j + 68]) & M32
        tt2 = (_gg(j, a[4], a[5], a[6]) + a[7] + ss1 + w[j]) & M32
        a[3] = a[2]
        a[2] = _rotl(a[1], 9)
        a[1] = a[0]
        a[0] = tt1
        a[7] = a[6]
        a[6] = _rotl(a[5], 19)
        a[5] = a[4]
        a[4] = (tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)) & M32
    return [reg[i] ^ a[i] for i in range(8)]


def sm3(data):
    """SM3 摘要，返回 32 字节。"""
    reg = list(_INIT)
    a = list(data)
    size = len(a) * 8
    a.append(128)
    f = len(a) % 64
    if f > 56:
        while f < 64:
            a.append(0)
            f += 1
        f = 0
    while f < 56:
        a.append(0)
        f += 1
    for i in range(7, -1, -1):
        a.append((size >> (i * 8)) & 255)
    off = 0
    while off + 64 <= len(a):
        reg = _compress(reg, a[off:off + 64])
        off += 64
    out = bytearray(32)
    for i in range(8):
        c = reg[i]
        out[i * 4 + 3] = c & 255
        c >>= 8
        out[i * 4 + 2] = c & 255
        c >>= 8
        out[i * 4 + 1] = c & 255
        c >>= 8
        out[i * 4] = c & 255
    return bytes(out)


def rc4(plain, key):
    s = list(range(256))
    j = 0
    n = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % n]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(plain))
    i = j = 0
    for k in range(len(plain)):
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out[k] = (plain[k] ^ s[(s[i] + s[j]) % 256]) & 0xFF
    return bytes(out)


def _encrypt(data, typ):
    dict_ = _TABLES.get(typ) or _TABLES["s0"]
    res = []
    n = len(data)
    i = 0
    while i < n:
        b0 = data[i]
        b1 = data[i + 1] if i + 1 < n else 0
        b2 = data[i + 2] if i + 2 < n else 0
        comb = (b0 << 16) | (b1 << 8) | b2
        for jj in range(4):
            if i * 8 + jj * 6 <= n * 8:
                idx = (comb >> (18 - jj * 6)) & 63
                if idx < len(dict_):
                    res.append(dict_[idx])
            elif len(dict_) > 64:
                res.append(dict_[64])
        i += 3
    return "".join(res)


def _rand4(rnd, opt):
    return [
        (rnd & 255 & 170) | (opt[0] & 85),
        (rnd & 255 & 85) | (opt[0] & 170),
        ((rnd >> 8) & 255 & 170) | (opt[1] & 85),
        ((rnd >> 8) & 255 & 85) | (opt[1] & 170),
    ]


def _rand_str():
    out = bytearray()
    for opt in ((3, 45), (1, 0), (1, 5)):
        rnd = random.randint(0, 65535)
        for b in _rand4(rnd, opt):
            out.append(b & 0xFF)
    return bytes(out)


def _rc4_bb(params, ua, start, end):
    url_hash = sm3(sm3((params + "cus").encode("utf-8")))
    cus_hash = sm3(sm3(b"cus"))
    ua_hash = sm3(_encrypt(rc4(ua.encode("utf-8"), bytes([1, 32, 14])), "s3").encode("ascii"))

    d = [0] * 73
    d[8] = 3
    d[10] = end
    d[16] = start
    d[18] = 44

    d[20] = (start >> 24) & 255
    d[21] = (start >> 16) & 255
    d[22] = (start >> 8) & 255
    d[23] = start & 255
    d[24] = start // 0x100000000
    d[25] = start // 0x10000000000

    a0, a1, a2 = 0, 1, 14
    d[26] = (a0 >> 24) & 255
    d[27] = (a0 >> 16) & 255
    d[28] = (a0 >> 8) & 255
    d[29] = a0 & 255
    d[30] = (a1 // 256) & 255
    d[31] = (a1 % 256) & 255
    d[32] = (a1 >> 24) & 255
    d[33] = (a1 >> 16) & 255
    d[34] = (a2 >> 24) & 255
    d[35] = (a2 >> 16) & 255
    d[36] = (a2 >> 8) & 255
    d[37] = a2 & 255

    d[38] = url_hash[21]
    d[39] = url_hash[22]
    d[40] = cus_hash[21]
    d[41] = cus_hash[22]
    d[42] = ua_hash[23]
    d[43] = ua_hash[24]

    d[44] = (end >> 24) & 255
    d[45] = (end >> 16) & 255
    d[46] = (end >> 8) & 255
    d[47] = end & 255
    d[48] = d[8]
    d[49] = end // 0x100000000
    d[50] = end // 0x10000000000

    d[51] = 6241
    d[52] = (d[51] >> 24) & 255
    d[53] = (d[51] >> 16) & 255
    d[54] = (d[51] >> 8) & 255
    d[55] = d[51] & 255
    d[56] = 6383
    d[57] = d[56] & 255
    d[58] = (d[56] >> 8) & 255
    d[59] = (d[56] >> 16) & 255
    d[60] = (d[56] >> 24) & 255

    we = WINDOW_ENV.encode("utf-8")
    d[64] = len(we)
    d[65] = d[64] & 255
    d[66] = (d[64] >> 8) & 255
    d[69] = 0
    d[70] = 0
    d[71] = 0

    d[72] = 0
    for k in (18, 20, 26, 30, 38, 40, 42, 21, 27, 31, 35, 39, 41, 43,
              22, 28, 32, 36, 23, 29, 33, 37, 44, 45, 46, 47, 48, 49, 50,
              24, 25, 52, 53, 54, 55, 57, 58, 59, 60, 65, 66, 70, 71):
        d[72] ^= d[k]

    order = [18, 20, 52, 26, 30, 34, 58, 38, 40, 53, 42, 21, 27, 54, 55, 31,
             35, 57, 39, 41, 43, 22, 28, 32, 60, 36, 23, 29, 33, 37, 44, 45,
             59, 46, 47, 48, 49, 50, 24, 25, 65, 66, 70, 71]
    bb = bytes([d[k] & 0xFF for k in order]) + we + bytes([d[72] & 0xFF])
    return rc4(bb, b"y")


def a_bogus(params, ua, now_ms=None):
    """按 url 查询串与 UA 生成 a_bogus 值（含尾部 '='）。"""
    rnd = _rand_str()
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    enc = _rc4_bb(params or "", ua or "", now, now)
    return _encrypt(rnd + enc, "s4") + "="