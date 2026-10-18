# README screenshots

The images in `docs/images` come from a real run (Gazebo + SITL), not mock-ups.
To refresh them, stop any running simulation first (they use the same ports):

```bash
mkdir -p /tmp/shots && cp docs/screenshots/demo.yaml /tmp/shots/
uv run python docs/screenshots/shoot.py en /tmp/shots     # English UI, also grabs camera frames
uv run python docs/screenshots/shoot.py tr /tmp/shots     # Turkish UI
# Fira Code: https://github.com/tonsky/FiraCode/releases (put the .ttf files in /tmp/fonts)
uv run --no-project --with resvg-py --with pillow python docs/screenshots/render.py /tmp/shots /tmp/fonts
```
