"""
ats_checker.py — ATS Friendliness Analyzer
Runs 14 checks on a CV PDF and produces an ATS compatibility score (0–100).
"""

import os
import re
import string
from dataclasses import dataclass, field
from typing import List, Optional

from cv_parser import CVData, parse_cv


@dataclass
class ATSCheck:
    name: str
    score: int  # 0–10
    max_score: int  # always 10
    status: str  # "pass", "warning", "fail"
    message: str
    details: List[str] = field(default_factory=list)


@dataclass
class ATSReport:
    file_name: str
    file_path: str
    overall_score: int  # 0–100
    grade: str  # A / B / C / D / F
    checks: List[ATSCheck] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


# Standard section heading labels that ATS systems recognise
STANDARD_SECTIONS = {
    "experience", "work experience", "professional experience", "employment",
    "education", "academic background", "qualifications",
    "skills", "technical skills", "core competencies",
    "projects", "personal projects",
    "certifications", "certificates", "awards", "awards & certificates",
    "languages", "language skills",
    "summary", "profile", "objective", "about me",
    "publications", "research",
    "references",
    "personal details", "personal information",
}

# Characters that commonly cause ATS parsing issues
PROBLEMATIC_CHARS = set("•·|→←↑↓★☆▪▫►◄■□●○♦♣♠♥✓✗✔✘✦✧⊕⊗⊙⊛")


def _status(score: int) -> str:
    if score >= 8:
        return "pass"
    elif score >= 5:
        return "warning"
    return "fail"


# ─────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────

def check_text_extractability(cv: CVData) -> ATSCheck:
    """Check 1: Can meaningful text be extracted from the PDF?"""
    text_len = len(cv.raw_text.strip())
    details = []

    if text_len == 0:
        score = 0
        msg = "No text could be extracted — the PDF may be image-based or corrupted."
    elif text_len < 200:
        score = 3
        msg = f"Very little text extracted ({text_len} chars). PDF may be partially image-based."
    elif text_len < 500:
        score = 6
        msg = f"Limited text extracted ({text_len} chars). Some content may be embedded in images."
    else:
        score = 10
        msg = f"Text extraction successful ({text_len} chars)."
        details.append("All content appears to be parseable text.")

    return ATSCheck("Text Extractability", score, 10, _status(score), msg, details)


def check_images(cv: CVData) -> ATSCheck:
    """Check 2: Detect embedded images (photos, logos, icons)."""
    details = []

    if cv.image_count == 0:
        score = 10
        msg = "No images detected — fully text-based CV."
    elif cv.image_count == 1:
        score = 6
        msg = "1 image detected (likely a profile photo). Some ATS systems struggle with images."
        details.append("Consider removing the photo for ATS-optimised applications.")
        details.append("German CVs commonly include photos, but US/UK-built ATS may choke on them.")
    elif cv.image_count <= 3:
        score = 4
        msg = f"{cv.image_count} images detected. ATS parsers may skip or misinterpret image regions."
    else:
        score = 2
        msg = f"{cv.image_count} images detected — high risk of ATS parsing failure."
        details.append("ATS systems cannot read text inside images.")

    return ATSCheck("Image Detection", score, 10, _status(score), msg, details)


def check_tables(cv: CVData) -> ATSCheck:
    """Check 3: Detect tables that may scramble content order."""
    details = []

    if not cv.has_tables:
        score = 10
        msg = "No tables detected — ATS-safe layout."
    elif cv.table_count <= 2:
        score = 6
        msg = f"{cv.table_count} table(s) detected. ATS may reorder table content."
        details.append("Consider using a single-column layout instead of tabular formatting.")
    else:
        score = 3
        msg = f"{cv.table_count} tables detected — high risk of content scrambling."
        details.append("Multi-table layouts are a leading cause of ATS misparses.")

    return ATSCheck("Table Detection", score, 10, _status(score), msg, details)


def check_fonts(cv: CVData) -> ATSCheck:
    """Check 4: Verify fonts are standard and embedded."""
    details = []
    standard_fonts = {
        "arial", "helvetica", "times", "timesnewroman", "times new roman",
        "calibri", "cambria", "garamond", "georgia", "verdana", "tahoma",
        "courier", "couriernew", "courier new", "palatino", "bookman",
        "cmr", "cmss", "cmtt", "lmroman", "lmsans",  # LaTeX Computer Modern / Latin Modern
    }

    if not cv.fonts_used:
        score = 5
        msg = "No font information could be extracted."
        details.append("Ensure fonts are properly embedded in the PDF.")
    else:
        non_standard = []
        for font in cv.fonts_used:
            font_lower = font.lower().replace("-", "").replace(" ", "")
            is_standard = any(std in font_lower for std in standard_fonts)
            if not is_standard:
                non_standard.append(font)

        if not non_standard:
            score = 10
            msg = f"All {len(cv.fonts_used)} fonts are standard and ATS-compatible."
        elif len(non_standard) <= 2:
            score = 7
            msg = f"Most fonts are standard. {len(non_standard)} non-standard font(s) detected."
            details.append(f"Non-standard fonts: {', '.join(non_standard)}")
        else:
            score = 4
            msg = f"{len(non_standard)} non-standard fonts detected — risk of garbled text."
            details.append(f"Non-standard fonts: {', '.join(non_standard)}")

        details.append(f"Fonts found: {', '.join(cv.fonts_used)}")

    return ATSCheck("Font Compatibility", score, 10, _status(score), msg, details)


def check_special_characters(cv: CVData) -> ATSCheck:
    """Check 5: Detect special/Unicode characters that ATS may garble."""
    details = []
    found_problematic = []

    for char in cv.raw_text:
        if char in PROBLEMATIC_CHARS:
            found_problematic.append(char)
        elif ord(char) > 127 and char not in "äöüÄÖÜßéèêëàâîïôûùçñ—–''""…":
            # Allow common German/European chars; flag others
            found_problematic.append(char)

    unique_problematic = set(found_problematic)

    if not unique_problematic:
        score = 10
        msg = "No problematic special characters detected."
    elif len(unique_problematic) <= 3:
        score = 7
        msg = f"{len(unique_problematic)} types of special characters detected."
        details.append(f"Characters: {' '.join(repr(c) for c in unique_problematic)}")
        details.append("Consider replacing with standard ASCII equivalents.")
    else:
        score = 4
        msg = f"{len(unique_problematic)} types of special characters found — may cause ATS parsing issues."
        details.append(f"Characters: {' '.join(repr(c) for c in list(unique_problematic)[:10])}")

    return ATSCheck("Special Characters", score, 10, _status(score), msg, details)


def check_section_headings(cv: CVData) -> ATSCheck:
    """Check 6: Verify section headings match standard ATS labels."""
    details = []
    found_standard = []
    found_nonstandard = []

    for section_name in cv.sections.keys():
        cleaned = section_name.lower().strip()
        if cleaned in STANDARD_SECTIONS or cleaned == "header":
            if cleaned != "header":
                found_standard.append(section_name)
        else:
            found_nonstandard.append(section_name)

    total = len(found_standard) + len(found_nonstandard)

    if total == 0:
        score = 3
        msg = "No recognizable section headings found."
        details.append("ATS systems rely on standard headings to parse CV sections.")
    elif not found_nonstandard:
        score = 10
        msg = f"All {len(found_standard)} section headings are ATS-standard."
        details.append(f"Sections: {', '.join(found_standard)}")
    else:
        ratio = len(found_standard) / total if total > 0 else 0
        score = max(3, int(ratio * 10))
        msg = f"{len(found_standard)}/{total} headings are ATS-standard."
        if found_nonstandard:
            details.append(f"Non-standard headings: {', '.join(found_nonstandard)}")
        details.append(f"Standard headings: {', '.join(found_standard)}")

    return ATSCheck("Section Headings", score, 10, _status(score), msg, details)


def check_contact_info(cv: CVData) -> ATSCheck:
    """Check 7: Verify contact information is parseable."""
    details = []
    found = 0
    total_expected = 3  # email, phone, LinkedIn

    if cv.contact_info.email:
        found += 1
        details.append(f"✓ Email: {cv.contact_info.email}")
    else:
        details.append("✗ No email address found")

    if cv.contact_info.phone:
        found += 1
        details.append(f"✓ Phone: {cv.contact_info.phone}")
    else:
        details.append("✗ No phone number found")

    if cv.contact_info.linkedin:
        found += 1
        details.append(f"✓ LinkedIn: {cv.contact_info.linkedin}")
    else:
        details.append("✗ No LinkedIn URL found")

    if cv.contact_info.github:
        details.append(f"✓ GitHub: {cv.contact_info.github}")

    score = int((found / total_expected) * 10)
    msg = f"{found}/{total_expected} key contact fields detected."

    return ATSCheck("Contact Info Parsability", score, 10, _status(score), msg, details)


def check_file_size(cv: CVData) -> ATSCheck:
    """Check 8: File size should be under 2 MB."""
    size_mb = cv.file_size_bytes / (1024 * 1024)
    details = [f"File size: {size_mb:.2f} MB"]

    if size_mb <= 1:
        score = 10
        msg = f"File size ({size_mb:.2f} MB) is well within ATS limits."
    elif size_mb <= 2:
        score = 8
        msg = f"File size ({size_mb:.2f} MB) is acceptable but on the larger side."
    elif size_mb <= 5:
        score = 5
        msg = f"File size ({size_mb:.2f} MB) exceeds recommended 2 MB limit."
        details.append("Some ATS systems reject files over 2 MB.")
    else:
        score = 2
        msg = f"File size ({size_mb:.2f} MB) is too large — may be rejected."
        details.append("Compress images or reduce content to get under 2 MB.")

    return ATSCheck("File Size", score, 10, _status(score), msg, details)


def check_page_count(cv: CVData) -> ATSCheck:
    """Check 9: Optimal page count (1–2 pages)."""
    details = [f"Page count: {cv.page_count}"]

    if cv.page_count == 1:
        score = 10
        msg = "Single-page CV — ideal for most applications."
    elif cv.page_count == 2:
        score = 9
        msg = "Two-page CV — acceptable for 2+ years of experience."
    elif cv.page_count == 3:
        score = 6
        msg = "Three pages — consider condensing. Some ATS systems truncate after page 2."
    else:
        score = 3
        msg = f"{cv.page_count} pages — too long. ATS may truncate or penalize."
        details.append("Aim for 1–2 pages maximum.")

    return ATSCheck("Page Count", score, 10, _status(score), msg, details)


def check_text_order(cv: CVData) -> ATSCheck:
    """Check 10: Validate that extracted text follows a logical order."""
    details = []
    text = cv.raw_text.lower()

    # Expected order of key sections
    expected_order = ["profile", "experience", "education", "skills", "projects"]
    positions = {}

    for section in expected_order:
        pos = text.find(section)
        if pos >= 0:
            positions[section] = pos

    if len(positions) < 2:
        score = 5
        msg = "Insufficient section markers to validate text order."
        details.append("ATS may struggle to identify CV structure.")
    else:
        ordered_sections = sorted(positions.keys(), key=lambda s: positions[s])
        # Check if profile/summary comes before experience
        is_logical = True
        issues = []

        if "experience" in positions and "education" in positions:
            if positions["education"] < positions["experience"]:
                # Education before experience is acceptable for students/fresh grads
                pass

        if "profile" in positions and "experience" in positions:
            if positions["profile"] > positions["experience"]:
                is_logical = False
                issues.append("Profile/Summary should appear before Experience.")

        if "skills" in positions and len(positions) > 1:
            # Skills at the very beginning (before profile) can be odd
            pass

        if is_logical and not issues:
            score = 10
            msg = "Text order is logical and ATS-parseable."
            details.append(f"Detected order: {' → '.join(ordered_sections)}")
        else:
            score = 5
            msg = "Some sections appear in an unexpected order."
            details.extend(issues)
            details.append(f"Detected order: {' → '.join(ordered_sections)}")

    return ATSCheck("Text Order", score, 10, _status(score), msg, details)


def check_keyword_density(cv: CVData) -> ATSCheck:
    """Check 11: Score presence of role-relevant keywords."""
    details = []

    # Key technical keywords for Data Engineering / Backend / Software roles
    important_keywords = {
        "python", "java", "sql", "data", "engineering", "backend",
        "software", "aws", "cloud", "docker", "api", "database",
        "spark", "hadoop", "etl", "machine learning", "pipeline",
        "development", "agile", "testing", "ci/cd", "git",
        "microservices", "rest", "linux", "kubernetes",
    }

    text_lower = cv.raw_text.lower()
    found = set()
    for kw in important_keywords:
        if kw in text_lower:
            found.add(kw)

    ratio = len(found) / len(important_keywords)
    score = min(10, int(ratio * 12))  # Slight bonus for high coverage

    msg = f"{len(found)}/{len(important_keywords)} role-relevant keywords found."
    if found:
        details.append(f"Found: {', '.join(sorted(found))}")
    missing = important_keywords - found
    if missing:
        details.append(f"Consider adding: {', '.join(sorted(missing))}")

    return ATSCheck("Keyword Density", score, 10, _status(score), msg, details)


def check_hyperlinks(cv: CVData) -> ATSCheck:
    """Check 12: Validate hyperlinks are well-formed."""
    details = []

    if not cv.hyperlinks:
        score = 7
        msg = "No hyperlinks detected. Consider adding LinkedIn/GitHub links."
    else:
        valid = 0
        invalid = []
        for url in cv.hyperlinks:
            if re.match(r"https?://[\w.-]+", url):
                valid += 1
            elif url.startswith("mailto:"):
                valid += 1
            else:
                invalid.append(url)

        if not invalid:
            score = 10
            msg = f"All {valid} hyperlinks are well-formed."
        else:
            score = 7
            msg = f"{valid}/{len(cv.hyperlinks)} links are valid. {len(invalid)} may be malformed."
            details.append(f"Potentially malformed: {', '.join(invalid[:5])}")

        details.append(f"Total links found: {len(cv.hyperlinks)}")

    return ATSCheck("Hyperlink Validation", score, 10, _status(score), msg, details)


def check_pdf_validity(cv: CVData) -> ATSCheck:
    """Check 13: Validate PDF is not encrypted/corrupted."""
    import fitz

    details = []
    try:
        doc = fitz.open(cv.file_path)
        is_encrypted = doc.is_encrypted
        needs_pass = doc.needs_pass
        doc.close()

        if is_encrypted or needs_pass:
            score = 0
            msg = "PDF is encrypted or password-protected — ATS cannot parse it."
        else:
            score = 10
            msg = "PDF is valid, unencrypted, and parseable."
    except Exception as e:
        score = 0
        msg = f"PDF validation failed: {str(e)}"
        details.append("The file may be corrupted.")

    return ATSCheck("PDF Validity", score, 10, _status(score), msg, details)


def check_header_footer_content(cv: CVData) -> ATSCheck:
    """Check 14: Check if critical info is in header/footer regions (via y-coordinates)."""
    import pdfplumber

    details = []
    header_footer_text = ""
    main_text = ""

    try:
        with pdfplumber.open(cv.file_path) as pdf:
            for page in pdf.pages:
                height = page.height
                header_thresh = height * 0.08  # Top 8%
                footer_thresh = height * 0.92  # Bottom 8%

                if page.chars:
                    for char in page.chars:
                        y = char.get("top", 0)
                        if y < header_thresh or y > footer_thresh:
                            header_footer_text += char.get("text", "")
                        else:
                            main_text += char.get("text", "")

        # Check if critical info (email, phone) appears ONLY in header/footer
        hf_lower = header_footer_text.lower()
        main_lower = main_text.lower()

        critical_in_hf_only = False
        if cv.contact_info.email:
            email_lower = cv.contact_info.email.lower()
            if email_lower in hf_lower and email_lower not in main_lower:
                critical_in_hf_only = True
                details.append("Email appears only in header/footer region.")

        if critical_in_hf_only:
            score = 5
            msg = "Some critical info is only in header/footer — ATS may miss it."
            details.append("Many ATS systems skip header and footer content.")
        else:
            score = 10
            msg = "Critical information is in the main body — ATS-safe."

    except Exception:
        score = 7
        msg = "Could not perform header/footer analysis."

    return ATSCheck("Header/Footer Content", score, 10, _status(score), msg, details)


# ─────────────────────────────────────────────────────────────
# Main ATS analysis
# ─────────────────────────────────────────────────────────────

def run_ats_check(cv: CVData) -> ATSReport:
    """Run all 14 ATS checks and produce a comprehensive report."""
    checks = [
        check_text_extractability(cv),
        check_images(cv),
        check_tables(cv),
        check_fonts(cv),
        check_special_characters(cv),
        check_section_headings(cv),
        check_contact_info(cv),
        check_file_size(cv),
        check_page_count(cv),
        check_text_order(cv),
        check_keyword_density(cv),
        check_hyperlinks(cv),
        check_pdf_validity(cv),
        check_header_footer_content(cv),
    ]

    # Calculate overall score (weighted average)
    total = sum(c.score for c in checks)
    max_total = sum(c.max_score for c in checks)
    overall = int((total / max_total) * 100) if max_total > 0 else 0

    # Grade
    if overall >= 90:
        grade = "A"
    elif overall >= 75:
        grade = "B"
    elif overall >= 60:
        grade = "C"
    elif overall >= 45:
        grade = "D"
    else:
        grade = "F"

    # Compile recommendations from failed/warning checks
    recommendations = []
    for check in checks:
        if check.status == "fail":
            recommendations.append(f"[CRITICAL] {check.name}: {check.message}")
            recommendations.extend([f"  → {d}" for d in check.details])
        elif check.status == "warning":
            recommendations.append(f"[WARNING] {check.name}: {check.message}")
            recommendations.extend([f"  → {d}" for d in check.details])

    return ATSReport(
        file_name=cv.file_name,
        file_path=cv.file_path,
        overall_score=overall,
        grade=grade,
        checks=checks,
        recommendations=recommendations,
    )


def analyze_pdf(pdf_path: str) -> ATSReport:
    """Convenience function: parse CV and run ATS check."""
    cv = parse_cv(pdf_path)
    return run_ats_check(cv)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ats_checker.py <path_to_cv.pdf>")
        sys.exit(1)

    report = analyze_pdf(sys.argv[1])
    print(f"\n{'='*60}")
    print(f"ATS REPORT: {report.file_name}")
    print(f"{'='*60}")
    print(f"Overall Score: {report.overall_score}/100 (Grade: {report.grade})")
    print(f"{'='*60}")

    for check in report.checks:
        icon = {"pass": "✓", "warning": "⚠", "fail": "✗"}[check.status]
        print(f"  {icon} {check.name}: {check.score}/{check.max_score} — {check.message}")
        for d in check.details:
            print(f"      {d}")

    if report.recommendations:
        print(f"\n{'='*60}")
        print("RECOMMENDATIONS:")
        print(f"{'='*60}")
        for rec in report.recommendations:
            print(f"  {rec}")
