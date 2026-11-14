#!/usr/bin/env python3
"""food-picture-01 entry: render the double smash burger picture.

Reads assets/base.png, downscales it with area averaging to 1024x683,
applies a fixed warm colour grade with a soft highlight rolloff and a
gentle radial vignette, and writes a deterministic PNG to
MAKEFUN_OUTPUT_DIR/entry.png (or /output/entry.png).

Python 3, standard library only. Fully deterministic: no clock, no random
numbers, no network, no environment-dependent input. Two runs write
byte-identical files.
"""

import os
import struct
import sys
import zlib

SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "base.png")

OUT_W = 1024
OUT_H = 683

# Fixed warm grade (applied after the downscale, before the vignette).
GRADE_R_MUL = 1.05
GRADE_R_ADD = 3.0
GRADE_G_MUL = 1.01
GRADE_G_ADD = 2.0
GRADE_B_MUL = 0.95
GRADE_B_ADD = 0.0

# Soft highlight rolloff: values above this knee are compressed so the
# glossy bun dome and the molten-cheese highlights keep a gradient
# instead of clipping flat. The warm grade pushes red up to ~271 before
# clamping, so without this the brightest bun highlights blow out to a
# uniform orange patch and lose the sheen detail that sells "fresh off
# the grill". Slope 0.5 caps the brightest graded value at ~248.
HIGHLIGHT_KNEE = 225.0
HIGHLIGHT_SLOPE = 0.5

# Radial vignette: corners end up this much darker than the centre.
VIGNETTE_MAX = 0.12


# ---------------------------------------------------------------- PNG decode

def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data):
    """Decode an 8-bit PNG into (width, height, rgb_bytes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    width = height = bit_depth = color_type = None
    palette = None
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(
                ">IIBBBBB", chunk)
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if width is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8:
        raise ValueError("only 8-bit PNGs are supported")
    if color_type not in (0, 2, 3, 4, 6):
        raise ValueError("unsupported colour type %d" % color_type)

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError("truncated IDAT data")

    # Unfilter each scanline.
    rows = []
    prev = bytearray(stride)
    off = 0
    for _ in range(height):
        ftype = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += stride + 1
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError("unknown filter type %d" % ftype)
        rows.append(bytes(line))
        prev = line

    # Convert every colour type to plain RGB bytes.
    rgb = bytearray(width * height * 3)
    if color_type == 2:
        for y, line in enumerate(rows):
            rgb[y * width * 3:(y + 1) * width * 3] = line
    elif color_type == 6:
        for y, line in enumerate(rows):
            out = y * width * 3
            for x in range(width):
                i = x * 4
                r, g, b, a = line[i], line[i + 1], line[i + 2], line[i + 3]
                rgb[out] = (r * a) // 255
                rgb[out + 1] = (g * a) // 255
                rgb[out + 2] = (b * a) // 255
                out += 3
    elif color_type == 0:
        for y, line in enumerate(rows):
            out = y * width * 3
            for x in range(width):
                v = line[x]
                rgb[out] = v
                rgb[out + 1] = v
                rgb[out + 2] = v
                out += 3
    elif color_type == 3:
        if palette is None:
            raise ValueError("palette image without PLTE")
        for y, line in enumerate(rows):
            out = y * width * 3
            for x in range(width):
                i = line[x] * 3
                rgb[out] = palette[i]
                rgb[out + 1] = palette[i + 1]
                rgb[out + 2] = palette[i + 2]
                out += 3
    elif color_type == 4:
        for y, line in enumerate(rows):
            out = y * width * 3
            for x in range(width):
                i = x * 2
                v = (line[i] * line[i + 1]) // 255
                rgb[out] = v
                rgb[out + 1] = v
                rgb[out + 2] = v
                out += 3
    return width, height, bytes(rgb)


# ------------------------------------------------------------- area downscale

def _axis_weights(src_n, dst_n):
    """For each output index, the (source index, weight) pairs that feed it.

    Area averaging: an output pixel covers src_n / dst_n source pixels, and
    each contributing source pixel is weighted by the overlap length.
    """
    scale = src_n / dst_n
    weights = []
    for j in range(dst_n):
        lo = j * scale
        hi = lo + scale
        first = int(lo)
        last = int(hi)
        if last >= src_n:
            last = src_n - 1
        pairs = []
        for i in range(first, last + 1):
            left = lo if i == first else float(i)
            right = hi if i == last else float(i + 1)
            w = right - left
            if w > 0:
                pairs.append((i, w / scale))
        weights.append(pairs)
    return weights


def downscale(rgb, src_w, src_h, dst_w, dst_h):
    """Area-average an RGB image down to (dst_w, dst_h). Separable passes."""
    xw = _axis_weights(src_w, dst_w)
    yw = _axis_weights(src_h, dst_h)

    # Horizontal pass: src_h rows of dst_w pixels, kept as floats.
    tmp = []
    for y in range(src_h):
        base = y * src_w * 3
        row = [0.0] * (dst_w * 3)
        for j, pairs in enumerate(xw):
            r = g = b = 0.0
            for i, w in pairs:
                k = base + i * 3
                r += rgb[k] * w
                g += rgb[k + 1] * w
                b += rgb[k + 2] * w
            row[j * 3] = r
            row[j * 3 + 1] = g
            row[j * 3 + 2] = b
        tmp.append(row)

    # Vertical pass: dst_h rows of dst_w pixels.
    out = bytearray(dst_w * dst_h * 3)
    for y, pairs in enumerate(yw):
        for j in range(dst_w * 3):
            acc = 0.0
            for i, w in pairs:
                acc += tmp[i][j] * w
            out[y * dst_w * 3 + j] = int(acc + 0.5)
    return bytes(out)


# ------------------------------------------------------------- grade + encode

def _rolloff(v):
    """Soft knee: compress values above HIGHLIGHT_KNEE toward white."""
    if v <= HIGHLIGHT_KNEE:
        return v
    return HIGHLIGHT_KNEE + (v - HIGHLIGHT_KNEE) * HIGHLIGHT_SLOPE


def grade(rgb, width, height):
    """Warm grade + highlight rolloff + radial vignette, in place on a copy."""
    out = bytearray(rgb)
    half_w = width / 2.0
    half_h = height / 2.0
    for y in range(height):
        ny = (y + 0.5 - half_h) / half_h
        row = y * width * 3
        for x in range(width):
            nx = (x + 0.5 - half_w) / half_w
            # 0 at the centre, 1 at the corners.
            d2 = (nx * nx + ny * ny) / 2.0
            if d2 > 1.0:
                d2 = 1.0
            vig = 1.0 - VIGNETTE_MAX * d2
            k = row + x * 3
            r = _rolloff(out[k] * GRADE_R_MUL + GRADE_R_ADD)
            g = _rolloff(out[k + 1] * GRADE_G_MUL + GRADE_G_ADD)
            b = _rolloff(out[k + 2] * GRADE_B_MUL + GRADE_B_ADD)
            out[k] = 255 if r > 255 else (0 if r < 0 else int(r + 0.5))
            out[k + 1] = 255 if g > 255 else (0 if g < 0 else int(g + 0.5))
            out[k + 2] = 255 if b > 255 else (0 if b < 0 else int(b + 0.5))
            out[k] = int(out[k] * vig + 0.5)
            out[k + 1] = int(out[k + 1] * vig + 0.5)
            out[k + 2] = int(out[k + 2] * vig + 0.5)
    return bytes(out)


def _filter_sub(line, bpp):
    out = bytearray(len(line))
    out[:bpp] = line[:bpp]
    for i in range(bpp, len(line)):
        out[i] = (line[i] - line[i - bpp]) & 0xFF
    return bytes(out)


def _filter_up(line, prev):
    return bytes((a - b) & 0xFF for a, b in zip(line, prev))


def _filter_avg(line, prev, bpp):
    out = bytearray(len(line))
    for i, v in enumerate(line):
        left = line[i - bpp] if i >= bpp else 0
        out[i] = (v - ((left + prev[i]) >> 1)) & 0xFF
    return bytes(out)


def _filter_paeth(line, prev, bpp):
    out = bytearray(len(line))
    for i, v in enumerate(line):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        out[i] = (v - _paeth(a, b, c)) & 0xFF
    return bytes(out)


def _signed_cost(buf):
    total = 0
    for b in buf:
        total += b if b < 128 else 256 - b
    return total


def encode_png(rgb, width, height):
    """Encode RGB bytes as a PNG, picking the cheapest filter per scanline."""
    stride = width * 3
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = rgb[y * stride:(y + 1) * stride]
        candidates = [
            (0, line),
            (1, _filter_sub(line, 3)),
            (2, _filter_up(line, prev)),
            (3, _filter_avg(line, prev, 3)),
            (4, _filter_paeth(line, prev, 3)),
        ]
        best_type, best_data = min(candidates, key=lambda c: _signed_cost(c[1]))
        raw.append(best_type)
        raw += best_data
        prev = line

    def chunk(ctype, payload):
        out = struct.pack(">I", len(payload)) + ctype + payload
        out += struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


def main():
    with open(SRC_PATH, "rb") as fh:
        data = fh.read()
    src_w, src_h, rgb = decode_png(data)
    small = downscale(rgb, src_w, src_h, OUT_W, OUT_H)
    final = grade(small, OUT_W, OUT_H)
    png = encode_png(final, OUT_W, OUT_H)

    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "entry.png")
    with open(out_path, "wb") as fh:
        fh.write(png)
    print("wrote %s (%dx%d, %d bytes)" % (out_path, OUT_W, OUT_H, len(png)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
