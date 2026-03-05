#!/usr/bin/env python3
"""
compile_and_check.py

Compiles `new_CV_copilot/main.tex` into PDF, copies the PDF into `cvs/` (removing
any existing PDFs there), and runs the ATS checker (`scripts/main.py ats`).

Usage: python scripts/compile_and_check.py
"""
import shutil
import subprocess
import sys
from pathlib import Path


def find_compiler():
    # Prefer pdflatex, then xelatex, then lualatex
    from shutil import which

    for cmd in ("pdflatex", "xelatex", "lualatex"):
        if which(cmd):
            return cmd
    return None


def run_compile(tex_dir: Path, tex_file: str, compiler: str) -> bool:
    # Run the compiler twice to resolve references
    try:
        for i in range(2):
            print(f"Running {compiler} (pass {i+1})...")
            subprocess.run([compiler, "-interaction=nonstopmode", "-halt-on-error", tex_file], cwd=tex_dir, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Compilation failed: {e}")
        return False


def main():
    project_root = Path(__file__).resolve().parents[1]
    tex_dir = project_root / "new_CV_copilot"
    tex_path = tex_dir / "main.tex"

    if not tex_path.exists():
        print(f"Error: {tex_path} not found.")
        sys.exit(1)

    compiler = find_compiler()
    if not compiler:
        print("No LaTeX compiler found on PATH (pdflatex/xelatex/lualatex). Install TeX Live or MacTeX and try again.")
        sys.exit(1)

    # Sanitize .tex before compiling to avoid math-mode symbols and weird glyphs
    sanitizer = project_root / "scripts" / "sanitize_tex.py"
    if sanitizer.exists():
        try:
            print("Sanitizing .tex files...")
            subprocess.run([sys.executable, str(sanitizer), str(tex_path)], cwd=project_root, check=True)
        except subprocess.CalledProcessError:
            print("Sanitizer failed — continuing to compilation anyway.")

    ok = run_compile(tex_dir, tex_path.name, compiler)
    if not ok:
        sys.exit(2)

    generated_pdf = tex_dir / "main.pdf"
    if not generated_pdf.exists():
        print(f"Compilation finished but {generated_pdf} not found.")
        sys.exit(3)

    # Ensure cvs folder exists and remove existing PDFs
    cvs_dir = project_root / "cvs"
    cvs_dir.mkdir(parents=True, exist_ok=True)

    for old in cvs_dir.glob("*.pdf"):
        try:
            old.unlink()
            print(f"Removed old PDF: {old.name}")
        except Exception as e:
            print(f"Could not remove {old}: {e}")

    dest = cvs_dir / generated_pdf.name
    shutil.copy2(generated_pdf, dest)
    print(f"Copied new PDF to: {dest}")

    # Run ATS checker via project CLI
    python_exe = sys.executable
    cmd = [python_exe, str(project_root / "scripts" / "main.py"), "ats"]
    print("Running ATS checker...")
    try:
        subprocess.run(cmd, cwd=project_root, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ATS checker failed: {e}")
        sys.exit(4)


if __name__ == "__main__":
    main()
