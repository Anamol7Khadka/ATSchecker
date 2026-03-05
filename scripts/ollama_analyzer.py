#!/usr/bin/env python3
"""
ollama_analyzer.py — Ollama LLM integration for cover letter analysis.

Uses local llama3.1:8b model via Ollama.
Only called on-demand when user selects a specific job from the dashboard.
"""


def check_ollama_available(model: str = "llama3.1:8b") -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        import ollama
        models = ollama.list()
        available = [m.model for m in models.models]
        return any(model in m or m.startswith(model.split(":")[0]) for m in available)
    except Exception:
        return False


def analyze_for_cover_letter(
    cv_text: str,
    job_title: str,
    job_company: str,
    job_location: str,
    job_description: str,
    model: str = "llama3.1:8b",
) -> dict:
    """
    Analyze a job posting against the CV and generate cover letter key points.
    Called on-demand only — not during scraping or scanning.

    Returns a dict with 'success', 'analysis' (markdown text), 'model', etc.
    """
    import ollama

    prompt = f"""You are an expert career advisor specializing in the German job market.
Analyze this job posting against the candidate's CV and provide specific,
actionable points for writing a compelling cover letter.

## CANDIDATE'S CV:
{cv_text[:4000]}

## JOB POSTING:
**Title:** {job_title}
**Company:** {job_company}
**Location:** {job_location}
**Description:**
{job_description[:3000]}

## PROVIDE YOUR ANALYSIS IN THIS EXACT FORMAT:

### 1. KEY MATCHING SKILLS
- List 3-5 skills from the CV that directly match this job's requirements

### 2. EXPERIENCE TO HIGHLIGHT
- Which specific experiences/projects from the CV are most relevant
- How to frame them for this particular role

### 3. GAPS TO ADDRESS
- Requirements in the job that the CV doesn't fully cover
- How to frame these gaps positively (willingness to learn, transferable skills)

### 4. COMPANY-SPECIFIC HOOKS
- What to mention about the company/role that shows genuine interest
- Industry-specific points to reference

### 5. SUGGESTED OPENING PARAGRAPH
- Write a strong, specific 2-3 sentence opening for the cover letter

### 6. KEY POINTS TO INCORPORATE
- 5-7 bullet points the cover letter MUST communicate
- Each should connect a CV strength to a job requirement

### 7. TONE & CULTURAL NOTES
- Formal/semi-formal recommendation for German Anschreiben
- Any cultural or market-specific advice

Be specific — reference actual content from both the CV and job posting.
Focus on actionable advice the candidate can immediately use."""

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.7, "num_predict": 2500},
        )
        return {
            "success": True,
            "analysis": response["message"]["content"],
            "model": model,
            "job_title": job_title,
            "job_company": job_company,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "job_title": job_title,
            "job_company": job_company,
        }
