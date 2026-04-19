"""
cv_parser.py — PDF CV Parser
Extracts structured data from CV PDFs using pdfplumber and PyMuPDF.
"""

import re
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pdfplumber
import fitz  # PyMuPDF
import yaml


@dataclass
class ContactInfo:
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None


@dataclass
class CVData:
    file_path: str
    file_name: str
    raw_text: str
    sections: Dict[str, str] = field(default_factory=dict)
    contact_info: ContactInfo = field(default_factory=ContactInfo)
    skills: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    page_count: int = 0
    file_size_bytes: int = 0
    fonts_used: List[str] = field(default_factory=list)
    has_images: bool = False
    image_count: int = 0
    has_tables: bool = False
    table_count: int = 0
    hyperlinks: List[str] = field(default_factory=list)


def _load_config_from_project_root() -> dict:
    """Load effective config without requiring callers to pass it explicitly."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_parser_skills(config: Optional[dict]) -> List[str]:
    data = config if isinstance(config, dict) else _load_config_from_project_root()
    matching = data.get("matching", {}) if isinstance(data, dict) else {}
    skills = matching.get("cv_parser_skills", [])
    if not isinstance(skills, list):
        return []
    return [str(skill).strip().lower() for skill in skills if str(skill).strip()]


def extract_text_pdfplumber(pdf_path: str) -> tuple[str, bool, int]:
    """Extract text from PDF using pdfplumber. Returns (text, has_tables, table_count)."""
    full_text = ""
    has_tables = False
    table_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
            tables = page.find_tables()
            if tables:
                has_tables = True
                table_count += len(tables)

    return full_text.strip(), has_tables, table_count


def extract_metadata_pymupdf(pdf_path: str) -> dict:
    """Extract metadata, fonts, images, and hyperlinks using PyMuPDF."""
    doc = fitz.open(pdf_path)
    metadata = doc.metadata or {}
    page_count = doc.page_count
    fonts = set()
    image_count = 0
    hyperlinks = []

    for page in doc:
        # Fonts
        font_list = page.get_fonts(full=True)
        for font in font_list:
            font_name = font[3] if len(font) > 3 else "Unknown"
            if font_name:
                fonts.add(font_name)

        # Images
        images = page.get_images(full=True)
        image_count += len(images)

        # Links
        links = page.get_links()
        for link in links:
            uri = link.get("uri", "")
            if uri:
                hyperlinks.append(uri)

    doc.close()

    return {
        "metadata": {k: str(v) for k, v in metadata.items() if v},
        "page_count": page_count,
        "fonts": sorted(fonts),
        "image_count": image_count,
        "has_images": image_count > 0,
        "hyperlinks": hyperlinks,
    }


def extract_contact_info(text: str) -> ContactInfo:
    """Extract contact information from CV text using regex patterns."""
    contact = ContactInfo()

    # Email
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if email_match:
        contact.email = email_match.group()

    # Phone (international formats)
    phone_match = re.search(
        r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}", text
    )
    if phone_match:
        contact.phone = phone_match.group().strip()

    # LinkedIn
    linkedin_match = re.search(
        r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?", text, re.IGNORECASE
    )
    if linkedin_match:
        contact.linkedin = linkedin_match.group()

    # GitHub
    github_match = re.search(
        r"(?:https?://)?(?:www\.)?github\.com/[\w-]+/?", text, re.IGNORECASE
    )
    if github_match:
        contact.github = github_match.group()

    return contact


def extract_sections(text: str) -> Dict[str, str]:
    """Extract named sections from CV text based on common heading patterns."""
    section_patterns = [
        "profile", "summary", "objective", "about",
        "experience", "work experience", "professional experience", "employment",
        "education", "academic", "qualifications",
        "skills", "technical skills", "core competencies", "technologies",
        "projects", "personal projects", "academic projects",
        "certifications", "certificates", "awards", "awards & certificates",
        "languages", "language skills",
        "publications", "research",
        "references",
        "personal details", "personal information",
        "interests", "hobbies",
    ]

    # Build regex to find section headings (case-insensitive, line-start)
    sections = {}
    lines = text.split("\n")
    current_section = "header"
    current_content = []

    for line in lines:
        stripped = line.strip().lower()
        # Remove common formatting artifacts
        cleaned = re.sub(r"[^a-z\s&]", "", stripped).strip()

        matched_section = None
        for pattern in section_patterns:
            if cleaned == pattern or cleaned.startswith(pattern):
                matched_section = pattern
                break

        if matched_section:
            # Save previous section
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = matched_section
            current_content = []
        else:
            current_content.append(line)

    # Save the last section
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


def extract_skills(text: str, config: Optional[dict] = None) -> List[str]:
    """Extract technical skills from CV text by matching against known skills."""
    text_lower = text.lower()
    found_skills = []
    skills_bank = sorted(_get_parser_skills(config), key=len, reverse=True)

    for skill in skills_bank:
        # Use word boundary matching for short skills to avoid false positives
        if len(skill) <= 2:
            pattern = r"\b" + re.escape(skill) + r"\b"
            if re.search(pattern, text_lower):
                found_skills.append(skill.upper() if len(skill) <= 3 else skill)
        else:
            if skill in text_lower:
                found_skills.append(skill)

    return sorted(set(found_skills))


def parse_cv(pdf_path: str, config: Optional[dict] = None) -> CVData:
    """Parse a CV PDF and return structured CVData."""
    pdf_path = os.path.abspath(pdf_path)
    file_name = os.path.basename(pdf_path)
    file_size = os.path.getsize(pdf_path)

    # Extract text and table info
    raw_text, has_tables, table_count = extract_text_pdfplumber(pdf_path)

    # Extract metadata, fonts, images, links
    pymupdf_data = extract_metadata_pymupdf(pdf_path)

    # Extract structured data
    contact_info = extract_contact_info(raw_text)
    sections = extract_sections(raw_text)
    skills = extract_skills(raw_text, config=config)

    return CVData(
        file_path=pdf_path,
        file_name=file_name,
        raw_text=raw_text,
        sections=sections,
        contact_info=contact_info,
        skills=skills,
        metadata=pymupdf_data["metadata"],
        page_count=pymupdf_data["page_count"],
        file_size_bytes=file_size,
        fonts_used=pymupdf_data["fonts"],
        has_images=pymupdf_data["has_images"],
        image_count=pymupdf_data["image_count"],
        has_tables=has_tables,
        table_count=table_count,
        hyperlinks=pymupdf_data["hyperlinks"],
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python cv_parser.py <path_to_cv.pdf>")
        sys.exit(1)

    cv = parse_cv(sys.argv[1])
    print(f"File: {cv.file_name}")
    print(f"Pages: {cv.page_count}")
    print(f"Size: {cv.file_size_bytes / 1024:.1f} KB")
    print(f"Images: {cv.image_count}")
    print(f"Tables: {cv.table_count}")
    print(f"Fonts: {', '.join(cv.fonts_used)}")
    print(f"Email: {cv.contact_info.email}")
    print(f"Phone: {cv.contact_info.phone}")
    print(f"LinkedIn: {cv.contact_info.linkedin}")
    print(f"Skills found: {', '.join(cv.skills)}")
    print(f"Sections: {', '.join(cv.sections.keys())}")
    print(f"\n--- Raw Text (first 500 chars) ---")
    print(cv.raw_text[:500])
