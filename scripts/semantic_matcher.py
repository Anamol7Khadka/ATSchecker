"""
semantic_matcher.py — 3-tier semantic matching with graceful degradation.

Tier 1 (Best):  Sentence Transformer embeddings (paraphrase-multilingual-MiniLM-L12-v2)
Tier 2 (Good):  Ollama local embeddings (nomic-embed-text)
Tier 3 (Base):  BM25-style TF-IDF + rapidfuzz fuzzy matching

Understands meaning, not just words:
  "Funktionale Sicherheit" ↔ "Functional Safety" → ~92% similarity
  "V-Modell" ↔ "V-Model lifecycle" → ~88% similarity

Used as a "rescue mechanism" in cv_job_matcher.py — when taxonomy matching
scores low but semantic similarity is high, the job gets rescued.
"""

import re
from typing import List, Optional, Tuple

# Stopwords for both German and English (used by Tier 3 fallback)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "or", "she", "that", "the",
    "to", "was", "were", "will", "with", "you", "your", "this", "they",
    "und", "der", "die", "das", "ein", "eine", "ist", "sind", "für", "mit",
    "auf", "den", "dem", "des", "von", "zu", "im", "wir", "sie", "ich",
    "nicht", "oder", "auch", "als", "nach", "bei", "über", "wie", "wenn",
    "noch", "kann", "mehr", "sehr", "nur", "zur", "zum", "es", "aber",
}


class SemanticMatcher:
    """3-tier semantic matching with graceful degradation."""

    def __init__(
        self,
        cv_text: str,
        yaml_skills: List[str],
        yaml_keywords: List[str],
    ):
        self.cv_text = cv_text or ""
        self.yaml_skills = yaml_skills or []
        self.yaml_keywords = yaml_keywords or []

        # Build combined profile document
        self._profile_doc = self._build_profile_doc()

        # Detect and initialize the best available tier
        self.tier = "none"
        self._init_tier()

    def _build_profile_doc(self) -> str:
        """Combine all profile signals into one rich text document."""
        parts = [
            self.cv_text,
            " ".join(self.yaml_skills * 3),     # boost skill terms
            " ".join(self.yaml_keywords * 2),   # boost search keywords
        ]
        return " ".join(parts)

    def _init_tier(self):
        """Detect and initialize the best available method."""
        # Tier 1: Sentence Transformers
        if self._try_init_embeddings():
            self.tier = "embeddings"
            return

        # Tier 2: Ollama embeddings
        if self._try_init_ollama():
            self.tier = "ollama"
            return

        # Tier 3: BM25 + rapidfuzz (always available)
        self._init_bm25_fuzzy()
        self.tier = "bm25_fuzzy"

    def _try_init_embeddings(self) -> bool:
        """Try to load sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer, util
            self._st_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            self._st_util = util
            self._profile_embedding = self._st_model.encode(
                self._profile_doc[:8000],  # model has token limit
                convert_to_tensor=True,
                show_progress_bar=False,
            )
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def _try_init_ollama(self) -> bool:
        """Try to use Ollama for embeddings."""
        try:
            import ollama
            # Check if Ollama is running and has an embedding model
            models = ollama.list()
            model_names = [m.model for m in models.models]

            # Prefer nomic-embed-text, fall back to any embed model
            self._ollama_embed_model = None
            for preferred in ["nomic-embed-text", "mxbai-embed-large", "all-minilm"]:
                if any(preferred in m for m in model_names):
                    self._ollama_embed_model = preferred
                    break

            if not self._ollama_embed_model:
                return False

            # Compute profile embedding
            response = ollama.embed(
                model=self._ollama_embed_model,
                input=self._profile_doc[:8000],
            )
            self._ollama_profile_vector = response["embeddings"][0]
            self._ollama = ollama
            return True
        except Exception:
            return False

    def _init_bm25_fuzzy(self):
        """Initialize BM25-style TF-IDF + rapidfuzz."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                max_features=5000,
                stop_words=list(_STOPWORDS),
                sublinear_tf=True,  # approximates BM25 term saturation
            )
            self._vectorizer.fit([self._profile_doc])
            self._profile_tfidf = self._vectorizer.transform([self._profile_doc])
        except Exception:
            self._vectorizer = None
            self._profile_tfidf = None

        # Prepare terms for fuzzy matching
        self._fuzzy_terms = list(set(
            [s.lower() for s in self.yaml_skills if len(s) > 2] +
            [k.lower() for k in self.yaml_keywords if len(k) > 2]
        ))

    def score(self, job_text: str) -> float:
        """Return 0-100 semantic similarity between profile and job text."""
        if not job_text or not job_text.strip():
            return 0.0

        if self.tier == "embeddings":
            return self._score_embeddings(job_text)
        elif self.tier == "ollama":
            return self._score_ollama(job_text)
        else:
            return self._score_bm25_fuzzy(job_text)

    def _score_embeddings(self, job_text: str) -> float:
        """Score using sentence-transformer cosine similarity."""
        try:
            job_embedding = self._st_model.encode(
                job_text[:8000],
                convert_to_tensor=True,
                show_progress_bar=False,
            )
            sim = self._st_util.cos_sim(self._profile_embedding, job_embedding).item()
            return min(100.0, max(0.0, sim * 100 * 1.3))
        except Exception:
            return 0.0

    def _score_ollama(self, job_text: str) -> float:
        """Score using Ollama embeddings."""
        try:
            response = self._ollama.embed(
                model=self._ollama_embed_model,
                input=job_text[:8000],
            )
            job_vector = response["embeddings"][0]

            # Cosine similarity
            import math
            dot = sum(a * b for a, b in zip(self._ollama_profile_vector, job_vector))
            mag_a = math.sqrt(sum(a * a for a in self._ollama_profile_vector))
            mag_b = math.sqrt(sum(b * b for b in job_vector))

            if mag_a == 0 or mag_b == 0:
                return 0.0
            sim = dot / (mag_a * mag_b)
            return min(100.0, max(0.0, sim * 100 * 1.3))
        except Exception:
            return 0.0

    def _score_bm25_fuzzy(self, job_text: str) -> float:
        """Score using BM25-style TF-IDF + rapidfuzz."""
        scores = []

        # Component 1: TF-IDF cosine similarity
        if self._vectorizer and self._profile_tfidf is not None:
            try:
                from sklearn.metrics.pairwise import cosine_similarity
                job_tfidf = self._vectorizer.transform([job_text])
                sim = cosine_similarity(self._profile_tfidf, job_tfidf)[0][0]
                scores.append(min(100.0, sim * 100 * 1.5))
            except Exception:
                pass

        # Component 2: Fuzzy term matching
        if self._fuzzy_terms:
            try:
                from rapidfuzz import fuzz
                job_lower = job_text.lower()
                hits = 0
                for term in self._fuzzy_terms:
                    if fuzz.token_set_ratio(term, job_lower) > 75:
                        hits += 1
                fuzzy_score = (hits / max(len(self._fuzzy_terms), 1)) * 100
                scores.append(fuzzy_score)
            except ImportError:
                # rapidfuzz not available — simple substring check
                job_lower = job_text.lower()
                hits = sum(1 for t in self._fuzzy_terms if t in job_lower)
                scores.append((hits / max(len(self._fuzzy_terms), 1)) * 100)

        if not scores:
            return 0.0

        # Weighted combination: TF-IDF 60%, fuzzy 40%
        if len(scores) == 2:
            return scores[0] * 0.6 + scores[1] * 0.4
        return scores[0]
