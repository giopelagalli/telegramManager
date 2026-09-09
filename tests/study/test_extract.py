import io

import pypdf
from docx import Document
from pptx import Presentation

from bot.study.extract import (
    PAGE_MARKER,
    SLIDE_MARKER,
    extract_docx,
    extract_pdf,
    extract_pptx,
    extract_text,
    guess_kind,
    render_pages,
)


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


def test_extract_pdf_numbers_pages_and_keeps_empty_ones(monkeypatch):
    class FakeReader:
        def __init__(self, stream):
            self.pages = [FakePage(" One "), FakePage(None), FakePage("Three")]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    assert extract_pdf(b"%PDF") == [(1, "One"), (2, ""), (3, "Three")]


def _deck() -> bytes:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[5])
    first.shapes.title.text = "Pointers"
    first.notes_slide.notes_text_frame.text = "Explain the stack"
    presentation.slides.add_slide(presentation.slide_layouts[6])
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def test_extract_pptx_reads_text_frames_and_notes():
    slides = extract_pptx(_deck())
    assert slides[0] == (1, "Pointers\nNotes: Explain the stack")
    assert slides[1] == (2, "")


def test_render_pages_skips_empty_pages():
    pages = [(1, "One"), (2, "  "), (3, "Three")]
    assert render_pages(pages, PAGE_MARKER) == "## p.1\nOne\n\n## p.3\nThree"
    assert render_pages(pages, SLIDE_MARKER) == "## slide 1\nOne\n\n## slide 3\nThree"
    assert render_pages([], PAGE_MARKER) == ""


def test_guess_kind():
    assert guess_kind("lecture7.pptx", "") == "slides"
    assert guess_kind("ch4.pdf", "application/pdf") == "chapter"
    assert guess_kind("", "application/pdf") == "chapter"
    assert guess_kind("notes.docx", "") == "notes"
    assert (
        guess_kind(
            "",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        == "notes"
    )
    assert guess_kind("board.jpg", "") == "photo"
    assert guess_kind("board", "image/jpeg") == "photo"
    assert guess_kind("data.zip", "application/zip") == "other"


def _doc(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_extract_docx_chunks_paragraphs_and_includes_headings():
    document = Document()
    document.add_heading("Chapter 1", level=1)
    for i in range(44):
        document.add_paragraph(f"Paragraph {i}")
    buffer = io.BytesIO()
    document.save(buffer)

    parts = extract_docx(buffer.getvalue())
    assert [n for n, _ in parts] == [1, 2]
    assert parts[0][1].startswith("Chapter 1\nParagraph 0")
    assert parts[0][1].count("\n") == 39
    assert "Paragraph 43" in parts[1][1]


def test_extract_docx_skips_blank_paragraphs():
    data = _doc(["First", "", "  ", "Second"])
    assert extract_docx(data) == [(1, "First\nSecond")]


def test_plain_text_uploads_are_notes():
    assert guess_kind("lecture4.md", "text/markdown") == "notes"
    assert guess_kind("notes.txt", "") == "notes"
    assert guess_kind("export.txt", "text/plain") == "notes"
    assert extract_text(b"  hello \xe2\x80\x94 there  ") == "hello — there"
