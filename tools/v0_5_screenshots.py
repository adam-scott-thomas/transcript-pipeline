# tools/v0_5_screenshots.py
# =============================================================================
# Take 1920×1080 + 600px-wide screenshots of every HTML in out/v0_5_check/.
# 600px width emulates Skool's feed-card downscale — text legibility at that
# size is the readability bar.
# =============================================================================

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image


OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "v0_5_check"


def _file_url(p: Path) -> str:
    return p.resolve().as_uri()


def main() -> int:
    htmls = sorted(OUT_DIR.glob("*.html"))
    if not htmls:
        print(f"no HTMLs found under {OUT_DIR}", file=sys.stderr)
        return 1

    print(f"capturing {len(htmls)} pages × 2 viewports = {len(htmls) * 2} shots")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for html in htmls:
            stem = html.stem
            # 1920×1080 — full canvas, full-page screenshot
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                       device_scale_factor=1)
            page = ctx.new_page()
            page.goto(_file_url(html))
            # let marked.js + highlight.js + fonts settle
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(0.6)
            shot = OUT_DIR / f"{stem}_1920x1080.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"  {shot.name}")
            ctx.close()

            # 600px wide — Skool feed downscale: PIL-resize the 1920×1080
            # capture to 600px wide. This matches what a video player does
            # when an mp4 is embedded in a 600px-wide feed card.
            full_shot = OUT_DIR / f"{stem}_1920x1080.png"
            with Image.open(full_shot) as im:
                ratio = 600 / im.width
                new_h = int(im.height * ratio)
                im_small = im.resize((600, new_h), Image.LANCZOS)
                small_shot = OUT_DIR / f"{stem}_600w.png"
                im_small.save(small_shot)
            print(f"  {small_shot.name} (PIL downscale from 1920w)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
