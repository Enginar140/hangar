"""Turn the SVG screenshots into compact PNGs for the README.

    uv run --no-project --with resvg-py --with pillow python render.py OUT_DIR FONTS_DIR

FONTS_DIR must contain FiraCode-Regular.ttf and FiraCode-Bold.ttf (the font
Textual's SVGs ask for); missing symbols fall back to the system fonts.
"""

import sys
from io import BytesIO
from pathlib import Path

import resvg_py
from PIL import Image

out, fonts = Path(sys.argv[1]), sys.argv[2]
images = Path(__file__).resolve().parent.parent / "images"
for svg in sorted(out.glob("*-*.svg")):
    png = resvg_py.svg_to_bytes(svg_path=str(svg), font_dirs=[fonts], monospace_family="Fira Code", zoom=1.25)
    im = Image.open(BytesIO(bytes(png))).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        images / f"{svg.stem}.png", optimize=True)
    print("wrote", images / f"{svg.stem}.png")
nose = out / "cam-nose.png"
if nose.exists():
    Image.open(nose).convert("RGB").save(images / "camera-frame.jpg", quality=85, optimize=True)
