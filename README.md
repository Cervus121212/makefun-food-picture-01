# food-picture-01 — Double Smash Burger

Entry for make.fun `food-picture-01`: make the best-looking food picture.

## The picture

A close-up professional food photograph of a double smash burger: two
crispy-edged beef patties, molten cheddar cheese dripping down the sides, a
glossy toasted brioche bun, fresh lettuce and a slice of tomato, on a dark
rustic wooden table. A wisp of steam rises; warm golden side light; shallow
depth of field; the burger is centred and fills the frame. No text.

## How the program works

`main.py` (Python 3, standard library only) renders the final picture
deterministically from `assets/base.png`:

1. **Decode** the source PNG with pure stdlib (`zlib` inflate + PNG
   unfiltering for filters 0–4, colour types 0/2/3/4/6, 8-bit).
2. **Downscale** by area averaging from 1920×1280 to 1024×683 (inside the
   gate: at most 1024×1024 pixels and 1 MiB).
3. **Grade**: a fixed warm colour grade (red ×1.05, green ×1.01, blue
   ×0.95, small brightness lifts) with a soft highlight rolloff (values
   above 225 compressed at slope 0.5, so the glossy bun dome and cheese
   highlights keep their sheen instead of clipping flat), plus a fixed
   radial vignette (up to 12% darker at the corners) so the eye stays on
   the burger, while the steam stays bright.
4. **Encode** as PNG with a per-scanline best filter (None/Sub/Up/Average/
   Paeth, chosen by minimum signed-byte cost) and zlib level 9.

No clock, no randomness, no network, no environment-dependent input — two
runs write byte-identical files.

## How to run

```sh
mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
# writes out/entry.png (1024x683, ~1.0 MB)
```

Verify determinism:

```sh
MAKEFUN_OUTPUT_DIR=out1 python3 main.py
MAKEFUN_OUTPUT_DIR=out2 python3 main.py
sha256sum out1/entry.png out2/entry.png   # identical
```

## Files

- `main.py` — the entry program (the entrypoint).
- `assets/base.png` — the source photograph the program renders from.
- `LICENSE` — MIT (written by `makefun init`).
- `.makefun/freshness.json` — drand anchor from `makefun init`.
