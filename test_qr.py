#!/usr/bin/env python3
"""Gate: the fallback QR encoder produces tags that really scan.

The fallback exists so a shop machine needs no Python packages installed to
print a tag. Four checks, each catching a different failure:

1. MESSAGE BITS carry the exact bytes intended, mode and length prefix included.
   Read back out of the matrix, so it tests the whole chain: bitstream, padding,
   block interleave, placement, masking.
2. FORMAT INFORMATION decodes to error-correction level M.
3. FUNCTION PATTERNS are geometrically correct -- finders, timing, alignment,
   and the version information for versions 7 and up. Computed from the module
   rules, NOT compared against segno: segno writes an extra 0x00 codeword before
   the pad run, which shifts a later byte into a mask-dependent cell, so a whole
   matrix comparison is not a sound oracle for a spec-correct encoder. (Two
   earlier versions of this gate failed on a correct encoder for that reason; the
   note is kept because the temptation to "just compare with segno" is strong and
   it cost real time.)
4. INDEPENDENT DECODE: OpenCV must read the rendered matrix AND the rendered PNG
   back as the original text. This is what caught the real bug here -- version
   information is placed LSB-first while format information is MSB-first, and
   getting that backwards produces a tag that looks perfect and scans as nothing.

Requires: opencv-python-headless (reader) and optionally segno (oracle):
    python3 -m pip install opencv-python-headless numpy segno
"""
import sys

import numpy as np

import minqr

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import segno
except ImportError:
    segno = None

PROBES = [
    "http://100.93.66.68:8450/j/ABCDEFGHJK",
    "http://10.0.0.5:8450/j/7KQ2MPXR9T",
    "http://192.168.1.200:8450/s/9f3c1a2b4d5e6f708192a3b4",
    "https://workshop.example.sg/j/7KQ2MPXR9T",
    "W-2026-000123",
    "x",
    "a" * 14,
    "b" * 26,
    "c" * 44,
    "d" * 64,
    "e" * 86,
    "f" * 108,
    "g" * 124,
    "h" * 154,
    "i" * 180,          # version 9, the largest this encoder supports
    "Serial 12345678 / Omega Speedmaster",
]


def to_image(matrix, scale=8, border=4):
    m = np.pad(np.array(matrix, dtype=np.uint8), border)
    big = np.kron(m, np.ones((scale, scale), dtype=np.uint8)) * 255
    return 255 - big


def read_matrix(matrix, scale=8, border=4):
    txt, _, _ = cv2.QRCodeDetector().detectAndDecode(to_image(matrix, scale, border))
    return txt


def read_png(png_bytes):
    img = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    txt, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return txt


def _decoded_format_bits(matrix):
    """Read the format information back out of the matrix, MSB first."""
    bits = 0
    for i in range(15):
        if i < 6:
            b = matrix[8][i]
        elif i == 6:
            b = matrix[8][7]
        elif i == 7:
            b = matrix[8][8]
        elif i == 8:
            b = matrix[7][8]
        else:
            b = matrix[14 - i][8]
        bits = (bits << 1) | b
    return bits


def format_payload(matrix):
    """(ec_level_bits, mask) recovered from the format information, or None."""
    bits = _decoded_format_bits(matrix)
    for cand in range(32):
        if minqr._bch15(cand) == bits:
            return (cand >> 3) & 0b11, cand & 0b111
    return None


def _mask_of(matrix):
    fp = format_payload(matrix)
    if fp is None:
        raise AssertionError("format information did not decode -- matrix is invalid")
    return fp[1]


def unmasked_flat(matrix):
    """The data-region modules in placement order, with the mask undone."""
    size = len(matrix)
    version = minqr.size_to_version(size)
    res = minqr._reserved(size, version)
    fn = minqr.MASKS[_mask_of(matrix)]
    flat, col, upward = [], size - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rng = range(size - 1, -1, -1) if upward else range(size)
        for row in rng:
            for c in (col, col - 1):
                if not res[row][c]:
                    flat.append(matrix[row][c] ^ (1 if fn(row, c) else 0))
        upward = not upward
        col -= 2
    return flat


def data_codewords(matrix):
    """De-interleaved DATA codewords (no error-correction bytes)."""
    size = len(matrix)
    version = minqr.size_to_version(size)
    ec_n, b1, d1, b2, d2 = minqr.M_TABLE[version]
    flat = unmasked_flat(matrix)
    cw = [int("".join(map(str, flat[i:i + 8])), 2) for i in range(0, len(flat) - 7, 8)]
    lens = [d1] * b1 + [d2] * b2
    blocks, pos = [[] for _ in lens], 0
    for i in range(max(lens)):
        for bi, L in enumerate(lens):
            if i < L:
                blocks[bi].append(cw[pos]); pos += 1
    return [v for blk in blocks for v in blk]


def message_bytes(matrix):
    """(mode_bits, length, payload) read back out of the matrix."""
    cw = data_codewords(matrix)
    bits = []
    for v in cw:                       # every data codeword, not just the first few
        for i in range(7, -1, -1):
            bits.append((v >> i) & 1)
    mode = "".join(map(str, bits[:4]))
    length = int("".join(map(str, bits[4:12])), 2)
    payload = bits[12:12 + length * 8]
    return mode, length, bytes(int("".join(map(str, payload[i:i + 8])), 2)
                               for i in range(0, len(payload), 8))


def expected_function_modules(version):
    """The function pattern from the rules: finders, timing, alignment, dark
    module, and version information for version 7 and up."""
    size = version * 4 + 17
    m = [[None] * size for _ in range(size)]

    def finder(r0, c0):
        for i in range(7):
            for j in range(7):
                edge = i in (0, 6) or j in (0, 6)
                core = 2 <= i <= 4 and 2 <= j <= 4
                m[r0 + i][c0 + j] = 1 if (edge or core) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)
    for i in range(8, size - 8):
        m[6][i] = 1 - (i % 2)
        m[i][6] = 1 - (i % 2)
    for r in minqr.ALIGN.get(version, ()):
        for c in minqr.ALIGN.get(version, ()):
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    m[r + i][c + j] = 1 if max(abs(i), abs(j)) != 1 else 0
    m[size - 8][8] = 1
    if version >= 7:
        vb = minqr._bch18(version)
        for i in range(18):
            b = (vb >> i) & 1              # version info is LSB-first
            m[i // 3][size - 11 + i % 3] = b
            m[size - 11 + i % 3][i // 3] = b
    return m


def function_mismatches(matrix):
    """Where the matrix disagrees with the rule-derived function pattern."""
    size = len(matrix)
    version = minqr.size_to_version(size)
    exp = expected_function_modules(version)
    bad = []
    for r in range(size):
        for c in range(size):
            if exp[r][c] is not None and matrix[r][c] != exp[r][c]:
                bad.append((r, c, matrix[r][c], exp[r][c]))
    return bad


def main():
    if cv2 is None:
        print("SKIP: needs opencv-python-headless")
        print("      python3 -m pip install opencv-python-headless numpy")
        return 1
    problems = []

    print("1. message bits, read back out of the rendered matrix")
    print("2. format information declares error-correction level M")
    print("3. function patterns match the rules (not another encoder)")
    for t in PROBES:
        mine = minqr.matrix(t)
        v = minqr.size_to_version(len(mine))
        mode, length, payload = message_bytes(mine)
        msg_ok = (mode == "0100" and length == len(t.encode()) and payload == t.encode())
        fp = format_payload(mine)
        ec_ok = fp is not None and fp[0] == 0b00
        bad_fn = function_mismatches(mine)
        ok = msg_ok and ec_ok and not bad_fn
        print(f"   {'ok ' if ok else 'DIFFER'} v{v:<2} size={len(mine):<3} "
              f"msg={msg_ok} ec_M={ec_ok} func_bad={len(bad_fn)} "
              f"mask={fp[1] if fp else '?'}  {t[:32]!r}")
        if not msg_ok:
            problems.append(f"{t[:40]!r}: message read back as mode={mode} "
                            f"len={length} payload={payload[:24]!r}")
        if not ec_ok:
            problems.append(f"{t[:40]!r}: format info does not decode to EC level M")
        if bad_fn:
            problems.append(f"{t[:40]!r}: {len(bad_fn)} function modules wrong, "
                            f"first {bad_fn[0]}")

    if segno is not None:
        print("\n   (cross-check with segno: version and decodability only, since its "
              "pad run differs)")
        for t in PROBES:
            mine = minqr.matrix(t)
            sq = segno.make(t, error="m", micro=False, mode="byte")
            same_v = sq.version == minqr.size_to_version(len(mine))
            print(f"      {'ok ' if same_v else 'DIFFER'} v{sq.version} vs "
                  f"{minqr.size_to_version(len(mine))}  {t[:36]!r}")
            if not same_v:
                problems.append(f"{t[:40]!r}: version differs from segno")

    print("\n4. independent decode with OpenCV (rendered matrix and rendered PNG)")
    for t in PROBES:
        m_ok = read_matrix(minqr.matrix(t)) == t
        p_ok = read_png(minqr.png_bytes(t, scale=6, border=4)) == t
        print(f"   {'ok ' if (m_ok and p_ok) else 'FAIL'} matrix={m_ok} png={p_ok}"
              f"  {t[:32]!r}")
        if not m_ok:
            problems.append(f"{t[:40]!r}: OpenCV could not read the matrix")
        if not p_ok:
            problems.append(f"{t[:40]!r}: OpenCV could not read the PNG")

    print("\n5. injection: each mutation must be caught by the check it targets, and")
    print("   must be shown to have actually applied")
    t = PROBES[0]
    base = minqr.matrix(t)

    mut = [row[:] for row in base]
    mut[2][2] ^= 1
    applied, caught = mut != base, bool(function_mismatches(mut))
    print(f"   finder module flipped   -> function check fails: applied={applied} caught={caught}")
    if not applied:
        problems.append("finder mutation was a no-op")
    if not caught:
        problems.append("finder mutation did not trip the function check")

    mut = [row[:] for row in base]
    mut[8][0] ^= 1
    applied, caught = mut != base, format_payload(mut) != format_payload(base)
    print(f"   format bit flipped      -> format check fails:   applied={applied} caught={caught}")
    if not applied:
        problems.append("format mutation was a no-op")
    if not caught:
        problems.append("format mutation did not trip the format check")

    mut = [row[:] for row in base]
    size0 = len(mut)
    mut[size0 - 4][size0 - 4] ^= 1
    applied = mut != base
    _, _, got = message_bytes(mut)
    caught = got != t.encode()
    print(f"   payload byte flipped    -> message check fails:  applied={applied} caught={caught}")
    if not applied:
        problems.append("payload mutation was a no-op")
    if not caught:
        problems.append("payload mutation did not trip the message check")

    # Version information only exists from version 7, so mutate a v7+ tag.
    t7 = PROBES[13]
    base7 = minqr.matrix(t7)
    s7 = len(base7)
    mut = [row[:] for row in base7]
    mut[s7 - 11][3] ^= 1                             # version info, second copy
    applied, caught = mut != base7, bool(function_mismatches(mut))
    print(f"   version info flipped    -> function check fails: applied={applied} caught={caught}")
    if not applied:
        problems.append("version-info mutation was a no-op")
    if not caught:
        problems.append("version-info mutation did not trip the function check "
                        "(this is the bug that made every large tag unscannable)")

    # A small corruption is NOT a valid injection: EC level M repairs it. Measured:
    # a 6x6 block was fully recovered. Corrupt past the budget or prove nothing.
    mut = [row[:] for row in base]
    size = len(mut)
    res = minqr._reserved(size, minqr.size_to_version(size))
    flipped = 0
    for r in range(size):
        for c in range(size):
            if not res[r][c] and r > size // 2:
                mut[r][c] ^= 1
                flipped += 1
    applied, caught = mut != base, read_matrix(mut) != t
    print(f"   {flipped} data modules flipped (past EC) -> reader fails: "
          f"applied={applied} caught={caught}")
    if not applied:
        problems.append("scramble mutation was a no-op")
    if not caught:
        problems.append("tag survived corruption far past the EC budget -- "
                        "the reader check is blind")

    control = [row[:] for row in base]
    print(f"   no-op control           -> applied={control != base} (must be False)")
    if control != base:
        problems.append("no-op control was not a no-op")

    other = read_matrix(minqr.matrix(PROBES[4]))
    print(f"   different payload       -> decodes to its own text = {other == PROBES[4]}")
    if other != PROBES[4]:
        problems.append("a different payload did not decode to its own text")

    print()
    if problems:
        print(f"FAIL -- {len(problems)} problem(s)")
        for p in problems:
            print("   -", p)
        return 1
    print(f"PASS -- {len(PROBES)} probes: message bits and EC level read back correctly, "
          f"function patterns match the rules, every probe decodes under OpenCV, "
          f"all injections behave.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
