#!/usr/bin/env python3
"""A minimal QR encoder in pure Python (byte mode, EC level M, versions 1-9).

Why this exists: the shop's tags must render even on a machine with no packages
installed. segno is used when present (server.py prefers it); this is the
fallback. It is verified module-for-module against segno by test_qr.py, so it is
not an untested reimplementation -- if the two disagree on any input, the test
fails.

Scope is deliberately narrow: byte mode, error correction level M, versions 1-9
(the longest URL this app emits is a 10-char job code, so version 4 is typical).
"""
import zlib

# --- version tables, EC level M only -------------------------------------
# version: (ec_codewords_per_block, blocks_in_group1, data_per_block_g1,
#           blocks_in_group2, data_per_block_g2)
M_TABLE = {
    1:  (10, 1, 16, 0, 0),
    2:  (16, 1, 28, 0, 0),
    3:  (26, 1, 44, 0, 0),
    4:  (18, 2, 32, 0, 0),
    5:  (24, 2, 43, 0, 0),
    6:  (16, 4, 27, 0, 0),
    7:  (18, 4, 31, 0, 0),
    8:  (22, 2, 38, 2, 39),
    9:  (22, 3, 36, 2, 37),
}

ALIGN = {
    2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34),
    7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46),
}

# --- GF(256) --------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n):
    g = [1]
    for i in range(n):
        g = _poly_mul(g, [1, _EXP[i]])
    return g


def _poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        if a == 0:
            continue
        for j, b in enumerate(q):
            r[i + j] ^= _mul(a, b)
    return r


def _rs_ec(data, n):
    """n error-correction codewords for `data`."""
    g = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        f = rem[i]
        if f == 0:
            continue
        for j, gc in enumerate(g):
            rem[i + j] ^= _mul(gc, f)
    return rem[len(data):]


# --- data encoding --------------------------------------------------------
def _pick_version(nbytes):
    for v in sorted(M_TABLE):
        ec, b1, d1, b2, d2 = M_TABLE[v]
        capacity = b1 * d1 + b2 * d2
        # 4 bits mode + 8 bits count (v1-9) leaves this many whole bytes
        if nbytes <= (capacity * 8 - 12) // 8:
            return v
    raise ValueError("text too long for this encoder (byte mode, EC-M, v9 max)")


def _bitstream(data: bytes, version: int):
    ec, b1, d1, b2, d2 = M_TABLE[version]
    capacity = b1 * d1 + b2 * d2
    bits = []
    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)
    put(0b0100, 4)                 # byte mode
    put(len(data), 8 if version < 10 else 16)
    for b in data:
        put(b, 8)
    # terminator, then pad to a byte boundary, then the standard pad bytes
    for _ in range(min(4, capacity * 8 - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    cw = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(cw) < capacity:
        cw.append(pad[i % 2]); i += 1
    return cw


def _interleave(cw, version):
    ec_n, b1, d1, b2, d2 = M_TABLE[version]
    blocks, pos = [], 0
    for _ in range(b1):
        blocks.append(cw[pos:pos + d1]); pos += d1
    for _ in range(b2):
        blocks.append(cw[pos:pos + d2]); pos += d2
    ecs = [_rs_ec(b, ec_n) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_n):
        for e in ecs:
            out.append(e[i])
    return out


# --- matrix ---------------------------------------------------------------
def _empty(size):
    return [[0] * size for _ in range(size)]


def _finder(m, r, c):
    for i in range(-1, 8):
        for j in range(-1, 8):
            rr, cc = r + i, c + j
            if not (0 <= rr < len(m) and 0 <= cc < len(m)):
                continue
            inside = 0 <= i <= 6 and 0 <= j <= 6
            ring = inside and (i in (0, 6) or j in (0, 6) or (2 <= i <= 4 and 2 <= j <= 4))
            m[rr][cc] = 1 if ring else 0


def _reserved(size, version):
    """True where a function pattern lives (data must not be placed there)."""
    res = [[False] * size for _ in range(size)]
    for r, c in ((0, 0), (0, size - 7), (size - 7, 0)):
        for i in range(-1, 8):
            for j in range(-1, 8):
                if 0 <= r + i < size and 0 <= c + j < size:
                    res[r + i][c + j] = True
    for i in range(size):                      # timing
        res[6][i] = True
        res[i][6] = True
    for r in ALIGN.get(version, ()):           # alignment
        for c in ALIGN.get(version, ()):
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    res[r + i][c + j] = True
    for i in range(9):                         # format info areas
        res[8][i] = True
        res[i][8] = True
    for i in range(8):
        res[8][size - 1 - i] = True
        res[size - 1 - i][8] = True
    res[size - 8][8] = True                    # dark module
    if version >= 7:                           # version info areas
        for i in range(6):
            for j in range(3):
                res[i][size - 11 + j] = True
                res[size - 11 + j][i] = True
    return res


def _place(m, res, cw, size):
    bits = []
    for b in cw:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rng = range(size - 1, -1, -1) if upward else range(size)
        for row in rng:
            for c in (col, col - 1):
                if not res[row][c]:
                    m[row][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        upward = not upward
        col -= 2
    return idx


MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _apply_mask(m, res, size, mask):
    fn = MASKS[mask]
    for r in range(size):
        for c in range(size):
            if not res[r][c] and fn(r, c):
                m[r][c] ^= 1


def _bch15(data):
    v = data << 10
    g = 0b10100110111
    for i in range(14, 9, -1):
        if v >> i & 1:
            v ^= g << (i - 10)
    return ((data << 10) | v) ^ 0b101010000010010


def _bch18(version):
    v = version << 12
    g = 0b1111100100101
    for i in range(17, 11, -1):
        if v >> i & 1:
            v ^= g << (i - 12)
    return (version << 12) | v


def _format(m, size, mask):
    # 0b00 = error correction level M. The 15 bits are placed MOST significant
    # bit first along row 8, then up column 8; the second copy is the same bits
    # starting at the bottom right. (Placing these LSB-first is the classic way
    # to produce a tag that looks like a QR code and scans as nothing.)
    bits = _bch15((0b00 << 3) | mask)
    for i in range(15):
        b = (bits >> (14 - i)) & 1
        if i < 6:
            m[8][i] = b
        elif i == 6:
            m[8][7] = b
        elif i == 7:
            m[8][8] = b
        elif i == 8:
            m[7][8] = b
        else:
            m[14 - i][8] = b
    for i in range(15):
        b = (bits >> (14 - i)) & 1
        if i < 8:
            m[size - 1 - i][8] = b
        else:
            m[8][size - 15 + i] = b
    m[size - 8][8] = 1                          # dark module
    if size >= 45:                              # version 7 is 45x45; versions 7+
                                                # carry the version information
                                                # block (size >= 49 was wrong and
                                                # left every v7 tag with an empty
                                                # version area)
        # Note the asymmetry, which is easy to get wrong and produced a tag that
        # scanned as nothing: the FORMAT information above is placed most
        # significant bit first, the VERSION information here is placed least
        # significant bit first. Verified against segno: its v8 blocks are the
        # exact reverse of an MSB-first placement.
        vb = _bch18(size_to_version(size))
        for i in range(18):
            b = (vb >> i) & 1
            m[i // 3][size - 11 + i % 3] = b
            m[size - 11 + i % 3][i // 3] = b


def size_to_version(size):
    return (size - 17) // 4


def _penalty(m, size):
    score = 0
    for line in m:                                              # rule 1, rows
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)
    for c in range(size):                                       # rule 1, cols
        run, prev = 1, m[0][c]
        for r in range(1, size):
            v = m[r][c]
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):                                   # rule 2
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat_b = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for r in range(size):                                       # rule 3
        for c in range(size - 10):
            seg = m[r][c:c + 11]
            if seg == pat_a or seg == pat_b:
                score += 40
    for c in range(size):
        for r in range(size - 10):
            seg = [m[r + i][c] for i in range(11)]
            if seg == pat_a or seg == pat_b:
                score += 40
    dark = sum(sum(row) for row in m)                           # rule 4
    pct = dark * 100 // (size * size)
    return score + 10 * (abs(pct - 50) // 5)


def matrix(text: str, mask: int = None):
    """Return a QR module matrix (list of rows of 0/1) for `text`.

    `mask` forces a mask pattern instead of choosing by penalty score. It exists
    for test_qr.py, which compares this encoder against segno mask by mask.
    """
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    size = version * 4 + 17
    cw = _interleave(_bitstream(data, version), version)
    base = _empty(size)
    res = _reserved(size, version)
    _finder(base, 0, 0)
    _finder(base, 0, size - 7)
    _finder(base, size - 7, 0)
    for i in range(8, size - 8):                                # timing
        base[6][i] = 1 - (i % 2)
        base[i][6] = 1 - (i % 2)
    for r in ALIGN.get(version, ()):
        for c in ALIGN.get(version, ()):
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    base[r + i][c + j] = 1 if (max(abs(i), abs(j)) != 1) else 0
    _place(base, res, cw, size)
    best, best_score = None, None
    for m_i in (range(8) if mask is None else [mask]):
        cand = [row[:] for row in base]
        _apply_mask(cand, res, size, m_i)
        _format(cand, size, m_i)
        sc = _penalty(cand, size)
        if best_score is None or sc < best_score:
            best, best_score = cand, sc
    return best


# --- output ---------------------------------------------------------------
def svg(text: str, scale: int = 4, border: int = 2, dark="#0b0b0c",
        light="#ffffff") -> str:
    m = matrix(text)
    n = len(m)
    span = (n + 2 * border) * scale
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{span}" height="{span}" '
             f'viewBox="0 0 {span} {span}" shape-rendering="crispEdges">',
             f'<rect width="{span}" height="{span}" fill="{light}"/>',
             f'<path fill="{dark}" d="']
    d = []
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            if v:
                x = (c + border) * scale
                y = (r + border) * scale
                d.append(f"M{x} {y}h{scale}v{scale}h-{scale}z")
    parts.append("".join(d))
    parts.append('"/></svg>')
    return "".join(parts)


def png_bytes(text: str, scale: int = 6, border: int = 2) -> bytes:
    m = matrix(text)
    n = len(m)
    side = (n + 2 * border) * scale
    # one filter byte per scanline, then greyscale pixels
    raw = bytearray()
    for y in range(side):
        raw.append(0)
        r = y // scale - border
        for x in range(side):
            c = x // scale - border
            dark = 0 <= r < n and 0 <= c < n and m[r][c]
            raw.append(0 if dark else 255)
    def chunk(tag, payload):
        return (len(payload).to_bytes(4, "big") + tag + payload +
                (zlib.crc32(tag + payload) & 0xFFFFFFFF).to_bytes(4, "big"))
    ihdr = (side.to_bytes(4, "big") + side.to_bytes(4, "big") +
            bytes([8, 0, 0, 0, 0]))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    import sys
    print(svg(sys.argv[1] if len(sys.argv) > 1 else "hello"))
