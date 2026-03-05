#!/usr/bin/env python3
"""
sanitize_tex.py
Simple script to clean common problematic characters in .tex files before compiling.
Replaces fancy quotes, bullets, math-mode pipes/dots used as separators, and other
characters that sometimes lead to non-standard glyphs in PDFs.
"""
import sys
from pathlib import Path

REPLACEMENTS = {
    '”': '"',
    '“': '"',
    '’': "'",
    '–': '-',
    '—': '--',
    '•': '-',
    '\u00A0': ' ',  # non-breaking space
    '\u00B7': '\\textperiodcentered{}',
    '·': '\\textperiodcentered{}',
    '¨': '"',
    '\u00A8': '"',
}
# Also replace common LaTeX math separators used in the CV
SPECIAL_REPLACEMENTS = {
    '$|$': '\\textbar{}',
    '$ | $': '\\textbar{}',
    '$ |$': '\\textbar{}',
    '$| $': '\\textbar{}',
    '$\\cdot$': '\\textperiodcentered{}',
    '$ \\cdot $': '\\textperiodcentered{}',
    '$ \\cdot$': '\\textperiodcentered{}',
    '$\\cdot $': '\\textperiodcentered{}',
    '\\enspace$|$\\enspace': '\\enspace\\textbar{}\\enspace',
    '\\enspace$\\cdot$\\enspace': '\\enspace\\textperiodcentered\\enspace',
}

# Also convert math-mode uses of \textbar{} into text-mode (remove surrounding $...$)
MATH_TEXTBAR_PATTERNS = {
    '$\\textbar{}$': '\\textbar{}',
    '$ \\textbar{} $': '\\textbar{}',
    '$\\textbar{} $': '\\textbar{}',
    '$ \\textbar{}$': '\\textbar{}',
}


def sanitize_file(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    orig = text

    # Apply direct replacements for characters
    for k, v in REPLACEMENTS.items():
        text = text.replace(k, v)

    # Apply special LaTeX pattern replacements (longer tokens first)
    for k, v in SPECIAL_REPLACEMENTS.items():
        if k in text:
            text = text.replace(k, v)

    # Fix math-mode \textbar{} occurrences by removing surrounding $...$
    for k, v in MATH_TEXTBAR_PATTERNS.items():
        if k in text:
            text = text.replace(k, v)

    if text != orig:
        path.write_text(text, encoding='utf-8')
        print(f"Sanitized: {path}")
    else:
        print(f"No changes needed: {path}")


if __name__ == '__main__':
    root = Path('.').resolve()
    tex_files = list(root.rglob('*.tex'))
    if len(sys.argv) > 1:
        tex_files = [Path(p) for p in sys.argv[1:]]

    if not tex_files:
        print('No .tex files found to sanitize.')
        sys.exit(0)

    for p in tex_files:
        sanitize_file(p)

    print('Sanitization complete. Remember to recompile your .tex to produce a new PDF.')
