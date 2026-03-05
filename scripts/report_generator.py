"""
report_generator.py — HTML Dashboard Report Generator.
Produces a single-page HTML report with ATS scores, job matches, and skills gap analysis.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

from jinja2 import Template

from ats_checker import ATSReport
from cv_job_matcher import MatchResult, SkillsGapAnalysis


# ─────────────────────────────────────────────────────────────
# HTML Template (Jinja2)
# ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATSchecker Report — {{ generated_at }}</title>
    <style>
        :root {
            --bg: #0d1117;
            --surface: #161b22;
            --surface2: #1c2333;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
            --green: #3fb950;
            --yellow: #d29922;
            --red:  #f85149;
            --blue: #58a6ff;
            --purple: #bc8cff;
            --orange: #f0883e;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 24px;
        }
        .container { max-width: 1400px; margin: 0 auto; }

        /* Header */
        .header {
            text-align: center;
            padding: 32px 0;
            border-bottom: 1px solid var(--border);
            margin-bottom: 32px;
        }
        .header h1 { font-size: 2em; color: var(--blue); }
        .header p { color: var(--text-muted); margin-top: 8px; }
        .header .stats {
            display: flex; gap: 32px; justify-content: center; margin-top: 16px;
        }
        .stat-box {
            background: var(--surface);
            padding: 12px 24px;
            border-radius: 8px;
            border: 1px solid var(--border);
        }
        .stat-box .num { font-size: 1.8em; font-weight: bold; }
        .stat-box .label { font-size: 0.85em; color: var(--text-muted); }

        /* Tabs */
        .tabs {
            display: flex; gap: 0; margin-bottom: 24px;
            border-bottom: 2px solid var(--border);
        }
        .tab {
            padding: 12px 24px; cursor: pointer;
            color: var(--text-muted); border-bottom: 2px solid transparent;
            font-weight: 500; transition: all 0.2s;
            background: none; border: none; border-bottom: 2px solid transparent;
            font-size: 1em; margin-bottom: -2px;
        }
        .tab:hover { color: var(--text); }
        .tab.active { color: var(--blue); border-bottom-color: var(--blue); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* Cards */
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }
        .card h2 { color: var(--blue); margin-bottom: 16px; font-size: 1.3em; }
        .card h3 { color: var(--text); margin: 16px 0 8px; }

        /* ATS Score Gauge */
        .ats-gauge {
            display: flex; align-items: center; gap: 32px;
            padding: 24px; margin-bottom: 24px;
        }
        .gauge-circle {
            width: 160px; height: 160px; border-radius: 50%;
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            font-size: 2.5em; font-weight: bold;
            border: 6px solid;
            flex-shrink: 0;
        }
        .gauge-circle .grade { font-size: 0.5em; color: var(--text-muted); }
        .gauge-A { border-color: var(--green); color: var(--green); }
        .gauge-B { border-color: #7ee787; color: #7ee787; }
        .gauge-C { border-color: var(--yellow); color: var(--yellow); }
        .gauge-D { border-color: var(--orange); color: var(--orange); }
        .gauge-F { border-color: var(--red); color: var(--red); }
        .gauge-info { flex: 1; }
        .gauge-info h2 { margin-bottom: 8px; }

        /* Check items */
        .check-item {
            display: flex; align-items: flex-start; gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
        }
        .check-item:last-child { border-bottom: none; }
        .check-badge {
            padding: 4px 10px; border-radius: 6px;
            font-size: 0.8em; font-weight: 600; white-space: nowrap;
            min-width: 48px; text-align: center;
        }
        .badge-pass { background: rgba(63,185,80,0.15); color: var(--green); }
        .badge-warning { background: rgba(210,153,34,0.15); color: var(--yellow); }
        .badge-fail { background: rgba(248,81,73,0.15); color: var(--red); }
        .check-info { flex: 1; }
        .check-name { font-weight: 600; }
        .check-msg { color: var(--text-muted); font-size: 0.9em; margin-top: 2px; }
        .check-details { color: var(--text-muted); font-size: 0.85em; margin-top: 4px; font-style: italic; }

        /* Recommendations */
        .rec-list { list-style: none; }
        .rec-list li {
            padding: 8px 12px; margin: 4px 0;
            background: var(--surface2);
            border-radius: 6px; font-size: 0.9em;
            border-left: 3px solid;
        }
        .rec-critical { border-left-color: var(--red); }
        .rec-warning { border-left-color: var(--yellow); }

        /* Job Table */
        .job-filters {
            display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap;
        }
        .filter-btn {
            padding: 6px 14px; border-radius: 20px;
            border: 1px solid var(--border); background: var(--surface2);
            color: var(--text-muted); cursor: pointer; font-size: 0.85em;
            transition: all 0.2s;
        }
        .filter-btn:hover, .filter-btn.active {
            border-color: var(--blue); color: var(--blue); background: rgba(88,166,255,0.1);
        }
        .search-box {
            padding: 8px 14px; border-radius: 8px;
            border: 1px solid var(--border); background: var(--surface2);
            color: var(--text); font-size: 0.9em; width: 280px;
        }
        .search-box::placeholder { color: var(--text-muted); }

        table {
            width: 100%; border-collapse: collapse; font-size: 0.9em;
        }
        th {
            text-align: left; padding: 12px 8px;
            border-bottom: 2px solid var(--border);
            color: var(--text-muted); font-weight: 600;
            cursor: pointer; user-select: none;
        }
        th:hover { color: var(--blue); }
        td {
            padding: 10px 8px; border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        tr:hover td { background: var(--surface2); }
        .score-cell {
            font-weight: 700; font-size: 1em;
        }
        .score-high { color: var(--green); }
        .score-med { color: var(--yellow); }
        .score-low { color: var(--red); }
        .conf-badge {
            padding: 2px 8px; border-radius: 10px; font-size: 0.75em;
            font-weight: 600;
        }
        .conf-very-high { background: rgba(63,185,80,0.2); color: var(--green); }
        .conf-high { background: rgba(126,231,135,0.2); color: #7ee787; }
        .conf-medium { background: rgba(210,153,34,0.2); color: var(--yellow); }
        .conf-low { background: rgba(248,81,73,0.2); color: var(--red); }
        a { color: var(--blue); text-decoration: none; }
        a:hover { text-decoration: underline; }
        .tag {
            display: inline-block; padding: 2px 8px; margin: 1px 2px;
            border-radius: 4px; font-size: 0.75em;
            background: rgba(188,140,255,0.15); color: var(--purple);
        }
        .tag-missing {
            background: rgba(248,81,73,0.1); color: var(--red);
        }
        .warning-tag {
            display: inline-block; padding: 2px 8px; margin: 1px;
            border-radius: 4px; font-size: 0.75em;
            background: rgba(210,153,34,0.15); color: var(--yellow);
        }

        /* Skills Gap */
        .skills-gap-bar {
            display: flex; align-items: center; gap: 12px;
            padding: 6px 0;
        }
        .gap-label { min-width: 140px; font-size: 0.9em; }
        .gap-bar-outer {
            flex: 1; height: 20px; background: var(--surface2);
            border-radius: 4px; overflow: hidden;
        }
        .gap-bar-inner {
            height: 100%; border-radius: 4px;
            background: linear-gradient(90deg, var(--red), var(--orange));
        }
        .gap-count { min-width: 60px; text-align: right; color: var(--text-muted); font-size: 0.85em; }

        /* Multi-CV selector */
        .cv-selector {
            display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap;
        }
        .cv-btn {
            padding: 8px 16px; border-radius: 8px;
            border: 1px solid var(--border); background: var(--surface);
            color: var(--text); cursor: pointer; font-size: 0.9em;
        }
        .cv-btn.active { border-color: var(--blue); background: rgba(88,166,255,0.1); }

        /* Responsive */
        @media (max-width: 768px) {
            .header .stats { flex-direction: column; gap: 8px; }
            .ats-gauge { flex-direction: column; text-align: center; }
            .job-filters { flex-direction: column; }
            .search-box { width: 100%; }
        }

        .top-match { background: rgba(63,185,80,0.05); }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <h1>&#x1F4CB; ATSchecker Report</h1>
            <p>Generated on {{ generated_at }}</p>
            <div class="stats">
                <div class="stat-box">
                    <div class="num">{{ cv_reports|length }}</div>
                    <div class="label">CVs Analyzed</div>
                </div>
                <div class="stat-box">
                    <div class="num">{{ total_jobs }}</div>
                    <div class="label">Jobs Found</div>
                </div>
                <div class="stat-box">
                    <div class="num">{{ avg_match|round(1) }}%</div>
                    <div class="label">Avg Match Score</div>
                </div>
                <div class="stat-box">
                    <div class="num">{{ top_matches }}</div>
                    <div class="label">High-Likelihood Matches</div>
                </div>
            </div>
        </div>

        <!-- Tabs -->
        <div class="tabs">
            <button class="tab active" onclick="switchTab('ats')">&#x1F50D; ATS Analysis</button>
            <button class="tab" onclick="switchTab('jobs')">&#x1F4BC; Job Matches</button>
            <button class="tab" onclick="switchTab('gap')">&#x1F4CA; Skills Gap</button>
        </div>

        <!-- TAB 1: ATS Analysis -->
        <div id="tab-ats" class="tab-content active">
            {% for report in cv_reports %}
            <div class="card">
                <div class="ats-gauge">
                    <div class="gauge-circle gauge-{{ report.grade }}">
                        {{ report.overall_score }}
                        <div class="grade">Grade {{ report.grade }}</div>
                    </div>
                    <div class="gauge-info">
                        <h2>{{ report.file_name }}</h2>
                        <p style="color: var(--text-muted);">
                            {% if report.overall_score >= 75 %}
                                Your CV is well-optimized for ATS systems. Minor improvements possible.
                            {% elif report.overall_score >= 55 %}
                                Your CV has some ATS compatibility issues that should be addressed.
                            {% else %}
                                Significant ATS issues detected. Your CV may be filtered out by automated systems.
                            {% endif %}
                        </p>
                    </div>
                </div>

                <h3>Detailed Checks</h3>
                {% for check in report.checks %}
                <div class="check-item">
                    <span class="check-badge badge-{{ check.status }}">{{ check.score }}/{{ check.max_score }}</span>
                    <div class="check-info">
                        <div class="check-name">{{ check.name }}</div>
                        <div class="check-msg">{{ check.message }}</div>
                        {% if check.details %}
                        <div class="check-details">
                            {% for d in check.details %}{{ d }}<br>{% endfor %}
                        </div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}

                {% if report.recommendations %}
                <h3 style="margin-top: 24px;">Recommendations</h3>
                <ul class="rec-list">
                    {% for rec in report.recommendations %}
                    <li class="{% if '[CRITICAL]' in rec %}rec-critical{% else %}rec-warning{% endif %}">
                        {{ rec }}
                    </li>
                    {% endfor %}
                </ul>
                {% endif %}
            </div>
            {% endfor %}
        </div>

        <!-- TAB 2: Job Matches -->
        <div id="tab-jobs" class="tab-content">
            <div class="card">
                <h2>Job Matches — Sorted by Acceptance Likelihood</h2>

                <div class="job-filters">
                    <input type="text" class="search-box" id="jobSearch"
                           placeholder="Search jobs..." onkeyup="filterJobs()">
                    <button class="filter-btn active" onclick="filterCity(this, 'all')">All Cities</button>
                    {% for city in cities %}
                    <button class="filter-btn" onclick="filterCity(this, '{{ city }}')">{{ city }}</button>
                    {% endfor %}
                </div>

                <table id="jobTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable(0)">#</th>
                            <th onclick="sortTable(1)">Title</th>
                            <th onclick="sortTable(2)">Company</th>
                            <th onclick="sortTable(3)">Location</th>
                            <th onclick="sortTable(4)">Source</th>
                            <th onclick="sortTable(5)">Match %</th>
                            <th onclick="sortTable(6)">Likelihood</th>
                            <th>Skills</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for m in matches %}
                        <tr class="{% if loop.index <= 10 %}top-match{% endif %}" data-city="{{ m.job.location }}">
                            <td>{{ loop.index }}</td>
                            <td><a href="{{ m.job.url }}" target="_blank">{{ m.job.title }}</a></td>
                            <td>{{ m.job.company }}</td>
                            <td>{{ m.job.location }}</td>
                            <td>{{ m.job.source }}</td>
                            <td class="score-cell {% if m.overall_score >= 60 %}score-high{% elif m.overall_score >= 35 %}score-med{% else %}score-low{% endif %}">
                                {{ m.overall_score|round(1) }}%
                            </td>
                            <td>
                                <span class="conf-badge conf-{{ m.confidence|lower|replace(' ', '-') }}">
                                    {{ m.confidence }}
                                </span>
                                {% for w in m.warnings %}
                                <span class="warning-tag">{{ w }}</span>
                                {% endfor %}
                            </td>
                            <td>
                                {% for s in m.matched_skills[:5] %}
                                <span class="tag">{{ s }}</span>
                                {% endfor %}
                                {% if m.missing_skills %}
                                {% for s in m.missing_skills[:3] %}
                                <span class="tag tag-missing">{{ s }}</span>
                                {% endfor %}
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3: Skills Gap -->
        <div id="tab-gap" class="tab-content">
            <div class="card">
                <h2>Skills Gap Analysis</h2>
                <p style="color: var(--text-muted); margin-bottom: 16px;">
                    Skills frequently requested in job postings that are not present in your CV.
                    Adding these skills can significantly improve your match scores.
                </p>

                {% if gap_analysis and gap_analysis.top_missing %}
                {% set max_freq = gap_analysis.missing_skills_frequency[gap_analysis.top_missing[0]] if gap_analysis.top_missing else 1 %}
                {% for skill in gap_analysis.top_missing %}
                {% set freq = gap_analysis.missing_skills_frequency[skill] %}
                <div class="skills-gap-bar">
                    <div class="gap-label">{{ skill }}</div>
                    <div class="gap-bar-outer">
                        <div class="gap-bar-inner" style="width: {{ (freq / max_freq * 100)|round }}%;"></div>
                    </div>
                    <div class="gap-count">{{ freq }} jobs</div>
                </div>
                {% endfor %}
                {% else %}
                <p>No significant skills gaps detected (or no job descriptions available for analysis).</p>
                {% endif %}
            </div>

            {% if gap_analysis and gap_analysis.cv_skills %}
            <div class="card">
                <h2>Your Current Skills</h2>
                <p style="margin-bottom: 12px;">
                    {% for skill in gap_analysis.cv_skills %}
                    <span class="tag">{{ skill }}</span>
                    {% endfor %}
                </p>
            </div>
            {% endif %}
        </div>
    </div>

    <script>
        function switchTab(tab) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            event.target.classList.add('active');
        }

        function filterCity(btn, city) {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const rows = document.querySelectorAll('#jobTable tbody tr');
            rows.forEach(row => {
                if (city === 'all' || row.dataset.city.toLowerCase().includes(city.toLowerCase())) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        }

        function filterJobs() {
            const q = document.getElementById('jobSearch').value.toLowerCase();
            const rows = document.querySelectorAll('#jobTable tbody tr');
            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(q) ? '' : 'none';
            });
        }

        let sortDirection = {};
        function sortTable(col) {
            const table = document.getElementById('jobTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));

            sortDirection[col] = !sortDirection[col];
            const dir = sortDirection[col] ? 1 : -1;

            rows.sort((a, b) => {
                let valA = a.cells[col].textContent.trim();
                let valB = b.cells[col].textContent.trim();

                // Try numeric sort
                const numA = parseFloat(valA);
                const numB = parseFloat(valB);
                if (!isNaN(numA) && !isNaN(numB)) {
                    return (numA - numB) * dir;
                }
                return valA.localeCompare(valB) * dir;
            });

            rows.forEach(row => tbody.appendChild(row));
        }
    </script>
</body>
</html>
""")


# ─────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────

def generate_report(
    cv_reports: List[ATSReport],
    matches: List[MatchResult],
    gap_analysis: Optional[SkillsGapAnalysis],
    cities: List[str],
    output_dir: str,
) -> str:
    """
    Generate an HTML dashboard report.

    Args:
        cv_reports: List of ATS reports (one per analyzed CV).
        matches: List of job match results.
        gap_analysis: Skills gap analysis object.
        cities: Target cities for filtering.
        output_dir: Directory to save the report.

    Returns:
        Path to the generated HTML file.
    """
    os.makedirs(output_dir, exist_ok=True)

    now = datetime.now()
    generated_at = now.strftime("%B %d, %Y at %H:%M")
    filename = f"report_{now.strftime('%Y-%m-%d_%H-%M')}.html"
    output_path = os.path.join(output_dir, filename)

    # Compute summary statistics
    total_jobs = len(matches)
    avg_match = (
        sum(m.overall_score for m in matches) / len(matches) if matches else 0
    )
    top_matches = sum(1 for m in matches if m.overall_score >= 55)

    html = HTML_TEMPLATE.render(
        generated_at=generated_at,
        cv_reports=cv_reports,
        matches=matches,
        gap_analysis=gap_analysis,
        cities=cities,
        total_jobs=total_jobs,
        avg_match=avg_match,
        top_matches=top_matches,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Report saved to: {output_path}")
    return output_path
