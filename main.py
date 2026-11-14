#!/usr/bin/env python3
"""food-picture-01 entry: a deterministic food-picture program (Python 3 stdlib only).

It reads the photograph in assets/base.png (a double smash burger with molten
cheddar, generated in this session), then renders the final picture with a
fixed pipeline and writes it to $MAKEFUN_OUTPUT_DIR/entry.png (or /output):

  1. decode the source PNG (pure stdlib: zlib inflate + PNG unfiltering),
  2. area-average downscale to 1024x683 (fits the 1024x1024 gate),
  3. a fixed warm grade (slightly warmer highlights, gentle S-curve lift),
  4. a fixed radial vignette (up to 10% darker at the corners),
  5. re-encode as PNG with a per-scanline best filter (None/Sub/Up/Average/Paeth)
     chosen by minimum sum of absolute signed-byte cost, zlib level 9.

Every step is fixed arithmetic on fixed inputs: no clock, no randomness, no
network, no environment-dependent input. Two runs write byte-identical files.

Run locally:  mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import math
import os
import struct
import sys
import zlib

# --- fixed render parameters (tuned once, then frozen) -----------------------
OUT_W, OUT_H = 1024, 683          # fits the gate (<=1024 x <=1024, <=1 MiB)
WARM_R, WARM_G, WARM_B = 1.045, 1.0, 0.955   # warm colour grade, per channel
LIFT_R, LIFT_G, LIFT_B = 2.0, 1.0, -1.0      # small brightness offsets
VIGNETTE = 0.10                   # max corner darkening (fraction)
ZLIB_LEVEL = 9
ASSET = "assets/base.png"


# --- PNG decoding (8-bit, non-interlaced) -----------------------------------
def read_png(path):
    """Return (width, height, colour_type, decompressed IDAT bytes)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file: %s" % path)
    pos = 8
    idat = bytearray()
    width = height = bit_depth = colour_type = None
    palette = None
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, colour_type, _c, _f, interlace = \
                struct.unpack(">IIBBBBB", chunk)
            if bit_depth != 8 or interlace != 0:
                raise ValueError("only 8-bit non-interlaced PNGs supported")
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if width is None:
        raise ValueError("missing IHDR")
    return width, height, colour_type, palette, zlib.decompress(bytes(idat))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def unfilter(width, height, colour_type, palette, raw):
    """Undo PNG scanline filters; return (rgb_bytes, channels)."""
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    stride = width * channels
    pixels = bytearray(width * height * channels)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        elif ftype != 0:
            raise ValueError("unknown PNG filter %d" % ftype)
        pixels[y * stride:(y + 1) * stride] = line
        prev = line
    if colour_type == 3:  # palette -> RGB
        if palette is None:
            raise ValueError("palette image without PLTE")
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            idx = pixels[i] * 3
            rgb[i * 3:i * 3 + 3] = palette[idx:idx + 3]
        return bytes(rgb), 3
    if colour_type == 0:  # grey -> RGB
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            v = pixels[i]
            rgb[i * 3:i * 3 + 3] = bytes((v, v, v))
        return bytes(rgb), 3
    if colour_type == 4:  # grey+alpha -> RGB (drop alpha)
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            v = pixels[i * 2]
            rgb[i * 3:i * 3 + 3] = bytes((v, v, v))
        return bytes(rgb), 3
    if colour_type == 6:  # RGBA -> RGB (drop alpha; source is opaque)
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            rgb[i * 3:i * 3 + 3] = pixels[i * 4:i * 4 + 3]
        return bytes(rgb), 3
    return bytes(pixels), 3  # colour type 2: RGB already


# --- render: downscale + grade + vignette -----------------------------------
def render(rgb, src_w, src_h):
    """Area-average downscale to OUT_W x OUT_H with the fixed grade."""
    out = bytearray(OUT_W * OUT_H * 3)
    sx = src_w / OUT_W
    sy = src_h / OUT_H
    cx, cy = OUT_W / 2.0, OUT_H / 2.0
    max_d = math.hypot(cx, cy)
    for oy in range(OUT_H):
        y0 = int(oy * sy)
        y1 = max(y0 + 1, int((oy + 1) * sy))
        for ox in range(OUT_W):
            x0 = int(ox * sx)
            x1 = max(x0 + 1, int((ox + 1) * sx))
            r = g = b = 0
            n = 0
            for yy in range(y0, y1):
                base = (yy * src_w + x0) * 3
                for xx in range(x0, x1):
                    i = base + (xx - x0) * 3
                    r += rgb[i]
                    g += rgb[i + 1]
                    b += rgb[i + 2]
                    n += 1
            r = min(255.0, (r / n) * WARM_R + LIFT_R)
            g = min(255.0, (g / n) * WARM_G + LIFT_G)
            b = max(0.0, (b / n) * WARM_B + LIFT_B)
            d = math.hypot(ox - cx, oy - cy) / max_d
            v = 1.0 - VIGNETTE * d * d
            o = (oy * OUT_W + ox) * 3
            out[o] = max(0, min(255, int(r * v)))
            out[o + 1] = max(0, min(255, int(g * v)))
            out[o + 2] = max(0, min(255, int(b * v)))
    return bytes(out)


# --- PNG encoding (RGB, per-row best filter) --------------------------------
def _filter_cost(data):
    return sum(b if b < 128 else 256 - b for b in data)


def encode_png(rgb, width, height):
    stride = width * 3
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = rgb[y * stride:(y + 1) * stride]
        candidates = [( _filter_cost(line), 0, line)]
        sub = bytearray(stride)
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            sub[i] = (line[i] - a) & 0xFF
        candidates.append((_filter_cost(sub), 1, bytes(sub)))
        up = bytes((line[i] - prev[i]) & 0xFF for i in range(stride))
        candidates.append((_filter_cost(up), 2, up))
        avg = bytearray(stride)
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            avg[i] = (line[i] - ((a + prev[i]) >> 1)) & 0xFF
        candidates.append((_filter_cost(avg), 3, bytes(avg)))
        paeth = bytearray(stride)
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            paeth[i] = (line[i] - _paeth(a, b, c)) & 0xFF
        candidates.append((_filter_cost(paeth), 4, bytes(paeth)))
        _, ftype, filtered = min(candidates, key=lambda item: item[0])
        raw.append(ftype)
        raw += filtered
        prev = line
    compressed = zlib.compress(bytes(raw), ZLIB_LEVEL)

    def chunk(ctype, payload):
        out = struct.pack(">I", len(payload)) + ctype + payload
        return out + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(here, ASSET)
    width, height, colour_type, palette, raw = read_png(src_path)
    rgb, _channels = unfilter(width, height, colour_type, palette, raw)
    final = render(rgb, width, height)
    png = encode_png(final, OUT_W, OUT_H)
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "entry.png")
    with open(out_path, "wb") as fh:
        fh.write(png)
    print("wrote %s (%d bytes, %dx%d)" % (out_path, len(png), OUT_W, OUT_H))


if __name__ == "__main__":
    main()
