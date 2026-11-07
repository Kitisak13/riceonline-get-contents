# docx_generator.py - Formatted Word Report Generator for Rice News
# ======================================================================

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import docx
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from config import OUTPUT_DIR
from utils import logger


def split_into_paragraphs(text: str, max_chars: int = 400) -> List[str]:
    """
    Intelligently splits article text into cohesive, readable paragraphs.
    If text is already split with newlines, preserves existing paragraphs.
    If any block is excessively long (wall of text), breaks at natural sentence boundaries.
    """
    if not text:
        return []

    raw_paras = [p.strip() for p in text.split('\n') if p.strip()]
    final_paras = []

    # Matches sentence ending punctuation followed by closing quote/bracket (ASCII or Unicode)
    # and whitespace followed by a capitalized word or opening quote.
    sentence_end_pattern = re.compile(
        r'(?<!\bU\.S)(?<!\bSen)(?<!\bRep)(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bNo)(?<!\bvs)'
        r'(?<!\bapprox)(?<!\betc)(?<!\bCo)(?<!\bCorp)(?<!\bInc)(?<!\bJan)(?<!\bFeb)(?<!\bMar)'
        r'(?<!\bApr)(?<!\bJun)(?<!\bJul)(?<!\bAug)(?<!\bSep)(?<!\bOct)(?<!\bNov)(?<!\bDec)'
        r'([\.\?\!][\"\'\”\’\)]*)\s+(?=[A-Z0-9\"\'\“\‘\(\[])'
    )

    for para in raw_paras:
        if len(para) <= max_chars:
            final_paras.append(para)
            continue

        sentences = []
        last_idx = 0
        for match in sentence_end_pattern.finditer(para):
            end_idx = match.end()
            sentences.append(para[last_idx:end_idx].strip())
            last_idx = end_idx
        if last_idx < len(para):
            remaining = para[last_idx:].strip()
            if remaining:
                sentences.append(remaining)

        current_chunk = []
        current_len = 0
        for s in sentences:
            current_chunk.append(s)
            current_len += len(s)
            # Break paragraph after 2-3 sentences or roughly 280+ characters
            if current_len >= 280 or len(current_chunk) >= 3:
                final_paras.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
        if current_chunk:
            final_paras.append(" ".join(current_chunk))

    return final_paras


def add_hyperlink(paragraph, url: str, text: str, color: RGBColor = RGBColor(30, 90, 160), underline: bool = True):
    """
    Inserts a clickable XML hyperlink into a python-docx paragraph.
    """
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)

    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')

    # Font name
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), 'Times New Roman')
    rFonts.set(qn('w:hAnsi'), 'Times New Roman')
    rPr.append(rFonts)

    # Color
    c = OxmlElement('w:color')
    c.set(qn('w:val'), f"{color[0]:02X}{color[1]:02X}{color[2]:02X}")
    rPr.append(c)

    # Underline
    if underline:
        u = OxmlElement('w:u')
        u.set(qn('w:val'), 'single')
        rPr.append(u)

    # Size (9.5 pt = 19 half-points)
    sz = OxmlElement('w:sz')
    sz.set(qn('w:val'), '19')
    rPr.append(sz)

    new_run.append(rPr)
    
    t = OxmlElement('w:t')
    t.text = text
    new_run.append(t)

    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink


def set_font_style(
    run: Any,
    font_name: str = 'Times New Roman',
    size: int = 11,
    bold: bool = False,
    italic: bool = False,
    color: Optional[RGBColor] = None
) -> None:
    """Sets explicit font styling."""
    run.font.name = font_name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = color


def create_report_document(
    processed_articles: List[Dict[str, Any]],
    output_filename: Optional[str] = None
) -> str:
    """
    Creates a formatted .docx document from the processed articles.
    Returns the absolute path to the generated file.
    """
    if not output_filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        output_filename = f"RiceNews_Report_{timestamp}.docx"

    output_path = os.path.join(OUTPUT_DIR, output_filename)
    logger.info(f"📄 Generating Word document report: {output_path}")

    doc = Document()

    # Set document base default font to Times New Roman
    style_normal = doc.styles['Normal']
    style_normal.font.name = 'Times New Roman'
    style_normal.font.size = Pt(11)

    # Document Header Title
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after = Pt(8)
    run_title = title_p.add_run("Rice Online News Daily Report")
    set_font_style(run_title, size=18, bold=True, color=RGBColor(26, 77, 46))

    subtitle_p = doc.add_paragraph()
    subtitle_p.paragraph_format.space_after = Pt(16)
    run_sub = subtitle_p.add_run(f"Generated on {datetime.now().strftime('%B %d, %Y - %H:%M')}")
    set_font_style(run_sub, size=10, italic=True, color=RGBColor(100, 100, 100))

    success_count = 0

    for idx, item in enumerate(processed_articles, 1):
        headline = item.get("headline", "Untitled Headline")
        url = item.get("URL") or item.get("url", "")
        source = item.get("source", "Unknown Source")
        
        # Ensure date is never blank
        date_str = item.get("date") or datetime.now().strftime("%Y-%m-%d")
        content = (item.get("content") or "").strip()

        # Headline paragraph (no numbering prefix)
        p_headline = doc.add_paragraph()
        p_headline.paragraph_format.space_before = Pt(14)
        p_headline.paragraph_format.space_after = Pt(4)
        run_h = p_headline.add_run(headline)
        set_font_style(run_h, size=12, bold=True)

        # Meta paragraph (Date only)
        p_meta = doc.add_paragraph()
        p_meta.paragraph_format.space_after = Pt(6)
        run_meta = p_meta.add_run(f"Date: {date_str}")
        set_font_style(run_meta, size=9.5, italic=True, color=RGBColor(100, 100, 100))

        # Content paragraphs (intelligently formatted with natural paragraph separation)
        if content:
            paragraphs = split_into_paragraphs(content)
            for para in paragraphs:
                para = para.strip()
                if para:
                    p_content = doc.add_paragraph()
                    p_content.paragraph_format.space_after = Pt(6)
                    p_content.paragraph_format.line_spacing = 1.15
                    run_c = p_content.add_run(para)
                    set_font_style(run_c, size=11)
            success_count += 1
        else:
            p_empty = doc.add_paragraph()
            p_empty.paragraph_format.space_after = Pt(4)
            run_empty = p_empty.add_run("[Article content could not be retrieved]")
            set_font_style(run_empty, size=10, italic=True, color=RGBColor(150, 150, 150))

        # Clickable Hyperlink (URL directly without 'Link:' prefix)
        if url:
            p_url = doc.add_paragraph()
            p_url.paragraph_format.space_after = Pt(14)
            add_hyperlink(p_url, url, url)

    doc.save(output_path)
    logger.info(f"✅ Report saved successfully: {output_path} ({success_count}/{len(processed_articles)} articles with content)")
    return output_path
