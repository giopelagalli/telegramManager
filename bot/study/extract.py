from __future__ import annotations

import io
from pathlib import Path

import pypdf
from pptx import Presentation

# Markers carry their own separator: "## p.7" but "## slide 7".
PAGE_MARKER = "p."
SLIDE_MARKER = "slide "

PDF_MIMES = ("application/pdf",)
PPTX_MIMES = ("application/vnd.openxmlformats-officedocument.presentationml.presentation",)
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")


def extract_pdf(data: bytes) -> list[tuple[int, str]]:
    """(page number from 1, text) for every page; a page with no text is kept as ""."""
    reader = pypdf.PdfReader(io.BytesIO(data))
    return [(i, (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages, 1)]


def extract_pptx(data: bytes) -> list[tuple[int, str]]:
    """(slide number from 1, text) — every text frame, then the speaker notes."""
    presentation = Presentation(io.BytesIO(data))
    slides: list[tuple[int, str]] = []
    for i, slide in enumerate(presentation.slides, 1):
        parts = [shape.text_frame.text.strip() for shape in slide.shapes if shape.has_text_frame]
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"Notes: {notes}")
        slides.append((i, "\n".join(p for p in parts if p)))
    return slides


def render_pages(pages: list[tuple[int, str]], marker: str) -> str:
    """One `## p.N` / `## slide N` heading per page, empty pages skipped."""
    blocks = [f"## {marker}{n}\n{text.strip()}" for n, text in pages if text.strip()]
    return "\n\n".join(blocks)


def guess_kind(filename: str, mime: str) -> str:
    name = (filename or "").lower()
    mime = (mime or "").lower()
    if name.endswith(".pptx") or mime in PPTX_MIMES:
        return "slides"
    if name.endswith(".pdf") or mime in PDF_MIMES:
        return "chapter"
    if mime.startswith("image/") or Path(name).suffix in _IMAGE_SUFFIXES:
        return "photo"
    return "other"
