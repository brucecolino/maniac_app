"""Render mockups.html to a multi-page A4-landscape PDF.

Pipeline:
  mockups.html → Playwright (one PNG per .page at 2x DPR) → ReportLab → PDF.

Output: C:\\Users\\markt\\Desktop\\maniac-app\\Maniac_GUI_Redesign.pdf
Run:    python generate_pdf.py
"""
from __future__ import annotations
import os
import sys
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT     = Path(__file__).resolve().parent
HTML     = ROOT / "mockups.html"
OUT_DIR  = ROOT / "out"
PDF_PATH = ROOT.parent / "Maniac_GUI_Redesign.pdf"

PAGE_IDS = [f"p{i}" for i in range(1, 16)]
VIEWPORT = {"width": 1280, "height": 780}
DPR = 2  # retina

PAGE_TITLES = {
    "p1":  ("Cover", None),
    "p2":  ("Problems today — audit of the current UI", None),
    "p3":  ("Design tokens — color, typography, spacing, radii", None),
    "p4":  ("Component library — with the new single-button Player Cycle", None),
    "p5":  ("Main window · empty state", "logo mark visible in titlebar"),
    "p6":  ("Wizard Download · popup", "replaces Screenshot 2026-04-24 040938.png"),
    "p7":  ("Downloader · active download", "replaces Screenshot 2026-04-24 042045.png"),
    "p8":  ("Analizza · sidebar with Anteprime + cycle-button inline", "replaces Screenshot 2026-04-24 041017.png + 042611.png"),
    "p9":  ("Tags · cycle-button replaces 5-chip pill", "replaces Screenshot 2026-04-24 041402.png (sidebar)"),
    "p10": ("Cronologia · destructive action moved to overflow menu", "replaces Screenshot 2026-04-24 041542.png + 041108.png"),
    "p11": ("Impostazioni · tile-grid tabs (File-panel continuity)", "tile tabs match the File panel; Applica su = cycle button"),
    "p12": ("Multi-player · four identical playback bars", "existing bar style preserved; all 4 players normalized"),
    "p13": ("File + Impostazioni · same visual, two contexts", "tile primitive used in both; user memory travels"),
    "p14": ("Wizard AI · compact 3×2 grid, no scroll", "6 analysis modes with icons, single modal <460px wide"),
    "p15": ("Icon strategy — decision flow & concept map", "how to pick / request icons consistently across the app"),
}


def shoot_pages() -> dict[str, Path]:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    png_paths: dict[str, Path] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DPR,
        )
        page = context.new_page()
        page.goto(HTML.as_uri(), wait_until="networkidle")
        # Wait for web fonts so Inter renders and we don't get FOUC/metric shift.
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)
        for pid in PAGE_IDS:
            el = page.locator(f"#{pid}")
            out = OUT_DIR / f"{pid}.png"
            el.screenshot(path=str(out))
            png_paths[pid] = out
            print(f"  [ok] {pid}.png")
        browser.close()
    return png_paths


def build_pdf(png_paths: dict[str, Path]) -> None:
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(str(PDF_PATH), pagesize=landscape(A4))
    c.setTitle("Maniac 2.0 — Interface Redesign")
    c.setAuthor("Maniac Design")
    c.setSubject("GUI redesign spec")

    MARGIN = 14 * mm
    HEADER_H = 14 * mm
    FOOTER_H = 8 * mm

    for i, pid in enumerate(PAGE_IDS, start=1):
        title, caption = PAGE_TITLES[pid]
        img_path = png_paths[pid]
        img = ImageReader(str(img_path))
        iw, ih = img.getSize()

        if pid == "p1":
            # Cover: full-bleed image, no header.
            target_w = page_w
            target_h = page_h
            scale = min(target_w / iw, target_h / ih)
            draw_w = iw * scale
            draw_h = ih * scale
            x = (page_w - draw_w) / 2
            y = (page_h - draw_h) / 2
            c.setFillColorRGB(0.06, 0.06, 0.09)
            c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
            c.drawImage(img, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
        else:
            # Header band.
            c.setFillColorRGB(0.95, 0.95, 0.96)
            c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

            c.setFillColorRGB(0.1, 0.1, 0.15)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(MARGIN, page_h - MARGIN + 2, f"Maniac 2.0 · Interface Redesign")
            c.setFillColorRGB(0.4, 0.4, 0.5)
            c.setFont("Helvetica", 9)
            c.drawRightString(page_w - MARGIN, page_h - MARGIN + 2, f"Page {i} of {len(PAGE_IDS)}")

            c.setFillColorRGB(0.08, 0.08, 0.12)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(MARGIN, page_h - MARGIN - 10, title)
            if caption:
                c.setFillColorRGB(0.45, 0.45, 0.55)
                c.setFont("Helvetica-Oblique", 9)
                c.drawString(MARGIN, page_h - MARGIN - 24, caption)

            # Image area.
            avail_w = page_w - 2 * MARGIN
            avail_h = page_h - HEADER_H - FOOTER_H - MARGIN
            scale = min(avail_w / iw, avail_h / ih)
            draw_w = iw * scale
            draw_h = ih * scale
            x = (page_w - draw_w) / 2
            y = FOOTER_H + (avail_h - draw_h) / 2

            # Subtle frame.
            c.setStrokeColorRGB(0.85, 0.83, 0.88)
            c.setLineWidth(0.5)
            c.rect(x - 2, y - 2, draw_w + 4, draw_h + 4, fill=0, stroke=1)
            c.drawImage(img, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

            c.setFillColorRGB(0.5, 0.5, 0.6)
            c.setFont("Helvetica", 8)
            c.drawString(MARGIN, FOOTER_H - 4, "Maniac Design · generated from mockups.html")
            c.drawRightString(page_w - MARGIN, FOOTER_H - 4, "render @ 2x DPR · 1280×780 viewport")

        c.showPage()
    c.save()


def main() -> int:
    if not HTML.exists():
        print(f"[err] mockups.html not found at {HTML}", file=sys.stderr)
        return 1
    print("[1/2] rendering HTML pages with Playwright...")
    pngs = shoot_pages()
    print("[2/2] composing PDF with ReportLab...")
    build_pdf(pngs)
    size_kb = PDF_PATH.stat().st_size / 1024
    print(f"[ok] wrote {PDF_PATH}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
