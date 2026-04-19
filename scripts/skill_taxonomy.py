"""
skill_taxonomy.py — Smart skill understanding for ATSchecker.

Replaces the three duplicate flat skill lists in config.yaml with a
structured taxonomy that understands:
  - Skill synonyms (PyTorch = torch, ETL = data pipeline)
  - Skill categories (programming languages, frameworks, concepts)
  - Parent/child relationships (PySpark → Spark → Big Data)
  - Word-boundary-aware extraction (so "r" doesn't match "your")
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class SkillInfo:
    """Rich metadata about a skill."""
    canonical: str                          # Canonical display name
    aliases: List[str] = field(default_factory=list)
    category: str = ""                      # programming, framework, tool, concept, cloud, database, etc.
    parent: str = ""                        # Parent skill/concept
    related: List[str] = field(default_factory=list)
    implies: List[str] = field(default_factory=list)  # If you know X, you likely know Y
    min_word_boundary: bool = True          # Require word boundaries for matching
    regex_pattern: Optional[str] = None     # Custom regex for this skill


# ─── The Taxonomy ────────────────────────────────────────────────────
# Each key is the canonical lowercase skill name.

TAXONOMY: Dict[str, SkillInfo] = {
    # ── Programming Languages ──
    "python": SkillInfo("Python", aliases=["python3", "python 3", "cpython"], category="language",
                        related=["django", "flask", "fastapi", "pandas", "numpy"]),
    "java": SkillInfo("Java", aliases=[], category="language",
                      related=["spring", "spring boot", "maven", "gradle"],
                      min_word_boundary=True),
    "javascript": SkillInfo("JavaScript", aliases=["js", "ecmascript", "es6", "es2015+"], category="language",
                            related=["react", "node.js", "typescript"]),
    "typescript": SkillInfo("TypeScript", aliases=["ts"], category="language", parent="javascript"),
    "c++": SkillInfo("C++", aliases=["cpp", "c plus plus"], category="language",
                     regex_pattern=r"\bc\+\+\b|\bcpp\b"),
    "c#": SkillInfo("C#", aliases=["csharp", "c sharp"], category="language",
                    regex_pattern=r"\bc#\b|\bcsharp\b"),
    "go": SkillInfo("Go", aliases=["golang"], category="language", min_word_boundary=True),
    "rust": SkillInfo("Rust", aliases=[], category="language", min_word_boundary=True),
    "scala": SkillInfo("Scala", aliases=[], category="language", related=["spark"]),
    "kotlin": SkillInfo("Kotlin", aliases=[], category="language", related=["android"]),
    "swift": SkillInfo("Swift", aliases=[], category="language", related=["ios"]),
    "r": SkillInfo("R", aliases=["r language", "r programming", "rstudio", "r-project"],
                   category="language", min_word_boundary=True,
                   regex_pattern=r"\bR\b(?:\s+(?:programming|language|studio))?|(?:^|\s)R(?:\s+|$)|\brstudio\b|\br-project\b"),
    "sql": SkillInfo("SQL", aliases=["structured query language", "sql queries"], category="language"),
    "bash": SkillInfo("Bash", aliases=["shell", "shell scripting", "sh", "zsh"], category="language"),
    "matlab": SkillInfo("MATLAB", aliases=[], category="language"),
    "php": SkillInfo("PHP", aliases=[], category="language"),
    "ruby": SkillInfo("Ruby", aliases=[], category="language", related=["rails"]),
    "html": SkillInfo("HTML", aliases=["html5"], category="language"),
    "css": SkillInfo("CSS", aliases=["css3", "scss", "sass", "less"], category="language"),

    # ── Frameworks & Libraries ──
    "react": SkillInfo("React", aliases=["reactjs", "react.js"], category="framework", parent="javascript"),
    "angular": SkillInfo("Angular", aliases=["angularjs", "angular.js"], category="framework", parent="javascript"),
    "vue": SkillInfo("Vue", aliases=["vuejs", "vue.js"], category="framework", parent="javascript"),
    "node.js": SkillInfo("Node.js", aliases=["nodejs", "node"], category="framework", parent="javascript"),
    "django": SkillInfo("Django", aliases=[], category="framework", parent="python"),
    "flask": SkillInfo("Flask", aliases=[], category="framework", parent="python"),
    "fastapi": SkillInfo("FastAPI", aliases=["fast api"], category="framework", parent="python"),
    "spring": SkillInfo("Spring", aliases=["spring framework"], category="framework", parent="java"),
    "spring boot": SkillInfo("Spring Boot", aliases=["springboot"], category="framework", parent="java"),
    ".net": SkillInfo(".NET", aliases=["dotnet", "dot net", "asp.net"], category="framework",
                      regex_pattern=r"\.net\b|dotnet|asp\.net"),
    "express": SkillInfo("Express", aliases=["expressjs", "express.js"], category="framework", parent="node.js"),

    # ── Data / ML Libraries ──
    "pandas": SkillInfo("Pandas", aliases=[], category="data_library", parent="python"),
    "numpy": SkillInfo("NumPy", aliases=["np"], category="data_library", parent="python"),
    "scikit-learn": SkillInfo("Scikit-learn", aliases=["sklearn", "scikit learn"], category="ml_library", parent="python"),
    "pytorch": SkillInfo("PyTorch", aliases=["torch"], category="ml_library", parent="python",
                         related=["deep learning"]),
    "tensorflow": SkillInfo("TensorFlow", aliases=["tf"], category="ml_library", parent="python",
                            related=["deep learning", "keras"]),
    "keras": SkillInfo("Keras", aliases=[], category="ml_library", parent="tensorflow"),
    "langchain": SkillInfo("LangChain", aliases=["lang chain"], category="ml_library", parent="python",
                           related=["llm", "rag"]),

    # ── Big Data & Data Engineering ──
    "spark": SkillInfo("Apache Spark", aliases=["apache spark"], category="big_data",
                       related=["pyspark", "scala", "hadoop"]),
    "pyspark": SkillInfo("PySpark", aliases=["py spark"], category="big_data",
                         parent="spark", implies=["python", "spark"]),
    "hadoop": SkillInfo("Hadoop", aliases=["apache hadoop", "hdfs", "mapreduce"], category="big_data"),
    "kafka": SkillInfo("Apache Kafka", aliases=["apache kafka"], category="big_data",
                       related=["data streaming", "event driven"]),
    "airflow": SkillInfo("Apache Airflow", aliases=["apache airflow"], category="data_engineering",
                         related=["etl", "data pipeline", "orchestration"]),
    "dbt": SkillInfo("dbt", aliases=["data build tool"], category="data_engineering"),
    "flink": SkillInfo("Apache Flink", aliases=["apache flink"], category="big_data"),

    # ── ETL / Data Pipeline (KEY SEMANTIC GROUP) ──
    "etl": SkillInfo("ETL", aliases=["elt", "extract transform load", "data pipeline", "data pipelines",
                                     "datenpipeline", "etl pipeline", "etl pipelines",
                                     "data integration", "datenintegration"],
                     category="concept", parent="data_engineering"),
    "data engineering": SkillInfo("Data Engineering", aliases=["data engineer", "datenengineering",
                                                                "dateningenieur"],
                                  category="role_concept", implies=["sql", "python", "etl"]),

    # ── Databases ──
    "postgresql": SkillInfo("PostgreSQL", aliases=["postgres", "psql"], category="database"),
    "mysql": SkillInfo("MySQL", aliases=["maria", "mariadb"], category="database"),
    "mongodb": SkillInfo("MongoDB", aliases=["mongo"], category="database"),
    "redis": SkillInfo("Redis", aliases=[], category="database"),
    "elasticsearch": SkillInfo("Elasticsearch", aliases=["elastic", "elastic search", "es"], category="database"),
    "cassandra": SkillInfo("Cassandra", aliases=["apache cassandra"], category="database"),
    "dynamodb": SkillInfo("DynamoDB", aliases=["dynamo db"], category="database", parent="aws"),
    "neo4j": SkillInfo("Neo4j", aliases=[], category="database"),
    "sqlite": SkillInfo("SQLite", aliases=[], category="database"),
    "oracle": SkillInfo("Oracle", aliases=["oracle db"], category="database"),
    "solr": SkillInfo("Apache Solr", aliases=["apache solr"], category="database"),

    # ── Cloud ──
    "aws": SkillInfo("AWS", aliases=["amazon web services", "amazon cloud"], category="cloud",
                     related=["ec2", "s3", "lambda", "cloudwatch"]),
    "azure": SkillInfo("Azure", aliases=["microsoft azure", "ms azure"], category="cloud"),
    "gcp": SkillInfo("GCP", aliases=["google cloud", "google cloud platform"], category="cloud"),
    "lambda": SkillInfo("AWS Lambda", aliases=["aws lambda"], category="cloud", parent="aws"),
    "ec2": SkillInfo("EC2", aliases=["aws ec2"], category="cloud", parent="aws"),
    "s3": SkillInfo("S3", aliases=["aws s3"], category="cloud", parent="aws"),
    "cloudwatch": SkillInfo("CloudWatch", aliases=["aws cloudwatch"], category="cloud", parent="aws"),

    # ── DevOps & Tools ──
    "docker": SkillInfo("Docker", aliases=["container", "containerization"], category="devops"),
    "kubernetes": SkillInfo("Kubernetes", aliases=["k8s", "kube"], category="devops",
                            related=["docker", "container orchestration"]),
    "terraform": SkillInfo("Terraform", aliases=["tf", "infrastructure as code"], category="devops"),
    "ansible": SkillInfo("Ansible", aliases=[], category="devops"),
    "ci/cd": SkillInfo("CI/CD", aliases=["continuous integration", "continuous deployment",
                                         "continuous delivery", "cicd", "ci cd"],
                       category="devops", regex_pattern=r"\bci\s*/\s*cd\b|\bcicd\b|\bcontinuous\s+(?:integration|delivery|deployment)\b"),
    "jenkins": SkillInfo("Jenkins", aliases=[], category="devops"),
    "gitlab": SkillInfo("GitLab CI", aliases=["gitlab ci", "gitlab ci/cd"], category="devops"),
    "github actions": SkillInfo("GitHub Actions", aliases=["github action"], category="devops"),
    "git": SkillInfo("Git", aliases=["version control", "vcs"], category="tool", min_word_boundary=True),
    "maven": SkillInfo("Maven", aliases=["apache maven"], category="tool", parent="java"),
    "gradle": SkillInfo("Gradle", aliases=[], category="tool", parent="java"),
    "linux": SkillInfo("Linux", aliases=["ubuntu", "debian", "centos", "rhel", "fedora"], category="tool"),
    "nginx": SkillInfo("Nginx", aliases=["nginx"], category="tool"),
    "jira": SkillInfo("Jira", aliases=["atlassian jira"], category="tool"),

    # ── AI / ML / Data Science Concepts ──
    "machine learning": SkillInfo("Machine Learning", aliases=["ml", "maschinelles lernen"],
                                  category="concept", related=["deep learning", "nlp", "computer vision"]),
    "deep learning": SkillInfo("Deep Learning", aliases=["dl", "neural networks", "neural network"],
                               category="concept", parent="machine learning",
                               related=["pytorch", "tensorflow"]),
    "nlp": SkillInfo("NLP", aliases=["natural language processing", "text mining",
                                     "sprachverarbeitung", "natürliche sprachverarbeitung"],
                     category="concept", parent="machine learning"),
    "computer vision": SkillInfo("Computer Vision", aliases=["cv", "image recognition",
                                                              "object detection", "bildverarbeitung"],
                                 category="concept", parent="machine learning"),
    "data science": SkillInfo("Data Science", aliases=["data scientist", "datenwissenschaft"],
                              category="role_concept"),
    "llm": SkillInfo("LLM", aliases=["large language model", "large language models",
                                     "language model", "generative ai", "gen ai"],
                     category="concept", parent="machine learning"),
    "rag": SkillInfo("RAG", aliases=["retrieval augmented generation", "retrieval-augmented generation"],
                     category="concept", parent="llm"),

    # ── Architecture & API ──
    "rest api": SkillInfo("REST API", aliases=["rest", "restful", "restful api", "rest apis"],
                          category="concept",
                          regex_pattern=r"\brest(?:ful)?\s*(?:api)?\b"),
    "graphql": SkillInfo("GraphQL", aliases=["graph ql"], category="concept"),
    "grpc": SkillInfo("gRPC", aliases=["grpc"], category="concept"),
    "microservices": SkillInfo("Microservices", aliases=["microservice", "micro services",
                                                          "mikroservices", "service oriented"],
                               category="concept"),
    "protobuf": SkillInfo("Protobuf", aliases=["protocol buffers", "protocol buffer"], category="concept"),

    # ── Methodology ──
    "agile": SkillInfo("Agile", aliases=["agile methodology", "agile development"], category="methodology"),
    "scrum": SkillInfo("Scrum", aliases=["scrum master", "scrum methodology"], category="methodology", parent="agile"),
}


# ─── Build lookup indexes ────────────────────────────────────────────

def _build_alias_index() -> Dict[str, str]:
    """Map every alias (and canonical name) → canonical name."""
    index = {}
    for canonical, info in TAXONOMY.items():
        index[canonical] = canonical
        for alias in info.aliases:
            index[alias.lower()] = canonical
    return index

ALIAS_INDEX: Dict[str, str] = _build_alias_index()


def _build_regex_patterns() -> Dict[str, re.Pattern]:
    """Pre-compile regex patterns for each skill."""
    patterns = {}
    for canonical, info in TAXONOMY.items():
        if info.regex_pattern:
            patterns[canonical] = re.compile(info.regex_pattern, re.IGNORECASE)
        else:
            # Build a pattern from canonical + aliases
            variants = [re.escape(canonical)] + [re.escape(a) for a in info.aliases]
            # Sort by length descending to match longest first
            variants.sort(key=len, reverse=True)
            pattern = "|".join(variants)
            if info.min_word_boundary:
                patterns[canonical] = re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
            else:
                patterns[canonical] = re.compile(rf"(?:{pattern})", re.IGNORECASE)
    return patterns

SKILL_PATTERNS: Dict[str, re.Pattern] = _build_regex_patterns()


# ─── Extraction ──────────────────────────────────────────────────────

@dataclass
class ExtractedSkill:
    """A skill found in text with context."""
    canonical: str
    display: str
    category: str
    confidence: float   # 0.0 – 1.0
    source: str         # "cv" or "job"
    context: str = ""   # surrounding sentence


def extract_skills_from_text(
    text: str,
    source: str = "cv",
) -> List[ExtractedSkill]:
    """
    Extract skills from text using the taxonomy.
    Returns a deduplicated list of ExtractedSkill objects.
    Much smarter than the old substring-search approach.
    """
    found: Dict[str, ExtractedSkill] = {}
    text_lower = text.lower()

    for canonical, pattern in SKILL_PATTERNS.items():
        matches = list(pattern.finditer(text if TAXONOMY[canonical].regex_pattern else text_lower))
        if matches:
            # Get context (surrounding words) from first match
            first = matches[0]
            start = max(0, first.start() - 40)
            end = min(len(text), first.end() + 40)
            context = text[start:end].strip()

            # Confidence based on match count and context
            base_confidence = min(1.0, 0.6 + 0.1 * len(matches))

            info = TAXONOMY[canonical]
            found[canonical] = ExtractedSkill(
                canonical=canonical,
                display=info.canonical,
                category=info.category,
                confidence=base_confidence,
                source=source,
                context=context,
            )

            # Also add implied skills at lower confidence
            for implied in info.implies:
                if implied not in found:
                    found[implied] = ExtractedSkill(
                        canonical=implied,
                        display=TAXONOMY.get(implied, SkillInfo(implied)).canonical,
                        category=TAXONOMY.get(implied, SkillInfo(implied)).category,
                        confidence=base_confidence * 0.7,
                        source=source,
                        context=f"Implied by {info.canonical}",
                    )

    return sorted(found.values(), key=lambda s: (-s.confidence, s.canonical))


def skills_match_score(
    cv_skills: List[str],
    job_skills: List[str],
) -> Tuple[float, List[str], List[str]]:
    """
    Compute semantic-aware skill match between CV and job.
    Returns (score 0-100, matched_skills, missing_skills).

    Unlike the old substring-match, this uses the taxonomy to understand
    that "PySpark" experience covers a "Spark" requirement, etc.
    """
    if not job_skills:
        return 50.0, [], []

    cv_set = set()
    for s in cv_skills:
        canonical = ALIAS_INDEX.get(s.lower(), s.lower())
        cv_set.add(canonical)
        # Add parent skills (if you know PySpark, you know Spark)
        info = TAXONOMY.get(canonical)
        if info and info.parent:
            cv_set.add(info.parent)
        # Add implied skills
        if info:
            for imp in info.implies:
                cv_set.add(imp)

    job_set = set()
    for s in job_skills:
        job_set.add(ALIAS_INDEX.get(s.lower(), s.lower()))

    matched = cv_set & job_set
    missing = job_set - cv_set

    # Also check for related-skill partial matches
    partial_matches = set()
    for missing_skill in list(missing):
        info = TAXONOMY.get(missing_skill)
        if info:
            related_hits = set(info.related) & cv_set
            if related_hits:
                partial_matches.add(missing_skill)

    # Score: full matches count 100%, partial matches count 50%
    full_match_count = len(matched)
    partial_count = len(partial_matches)
    total_required = len(job_set)

    effective_matches = full_match_count + partial_count * 0.5
    coverage = effective_matches / total_required if total_required else 0
    score = min(100, coverage * 100 * 1.1)

    # Remove partial matches from missing (they're close enough)
    missing = missing - partial_matches

    return score, sorted(matched | partial_matches), sorted(missing)


def get_skill_display_name(skill: str) -> str:
    """Get the pretty display name for a skill."""
    canonical = ALIAS_INDEX.get(skill.lower(), skill.lower())
    info = TAXONOMY.get(canonical)
    return info.canonical if info else skill


def get_skill_category(skill: str) -> str:
    """Get the category for a skill."""
    canonical = ALIAS_INDEX.get(skill.lower(), skill.lower())
    info = TAXONOMY.get(canonical)
    return info.category if info else ""


def get_all_skill_names() -> List[str]:
    """Return all canonical skill names in the taxonomy."""
    return sorted(TAXONOMY.keys())


def get_skills_by_category() -> Dict[str, List[str]]:
    """Group skills by category for display."""
    cats: Dict[str, List[str]] = {}
    for canonical, info in TAXONOMY.items():
        cat = info.category or "other"
        cats.setdefault(cat, []).append(info.canonical)
    return {k: sorted(v) for k, v in sorted(cats.items())}
