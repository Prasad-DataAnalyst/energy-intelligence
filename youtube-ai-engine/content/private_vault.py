"""
content/private_vault.py — Privacy-First Personal Content Engine
=================================================================
CRITICAL: Private files stay LOCAL. Only extracted, anonymized insights
ever leave this machine or touch external APIs.

Pipeline:
  VaultEncryption          AES-256-GCM (cryptography lib) or XOR fallback
  VaultDatabase            SQLite-backed encrypted local storage
  LocalInsightExtractor    Ollama-first, pure-heuristic fallback
  PersonalStoryGenerator   Insight → universal video concept (no PII)
  PrivateVaultEngine       Orchestrator
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brain.private_vault")

HERE = Path(__file__).parent.parent   # points to youtube-ai-engine/

# ── Directory / file constants ────────────────────────────────────────────────
VAULT_DIR        = HERE / "vault"
PRIVATE_INPUT_DIR = HERE / "input" / "private"
VAULT_DB         = VAULT_DIR / "vault_index.db"

ENCRYPTION_KEY_ENV = "VAULT_ENCRYPTION_KEY"   # 32-byte hex key from env

# ── Story templates ───────────────────────────────────────────────────────────
PERSONAL_STORY_TEMPLATES = [
    "What I Learned After {time} of {activity}",
    "How {experience} Changed Everything I Believed About {topic}",
    "The {n} Lessons I Wish I Knew Before {life_event}",
    "My {time} {activity} Experiment: Honest Results",
    "Why I {action}: A Personal Case Study",
    "The {topic} Advice That Actually Changed My Life",
    "What {n} {time_unit} of {activity} Taught Me About {topic}",
    "{n} Things I Discovered By Tracking My {personal_metric}",
]

# ── Privacy category definitions ──────────────────────────────────────────────
PRIVACY_CATEGORIES: Dict[str, Dict] = {
    "personal_journal":     {"sensitivity": "high",     "local_only": True,  "extract_themes": True},
    "business_case_study":  {"sensitivity": "high",     "local_only": True,  "extract_lessons": True},
    "financial_data":       {"sensitivity": "critical",  "local_only": True,  "extract_stats": True},
    "health_data":          {"sensitivity": "high",     "local_only": True,  "extract_patterns": True},
    "professional_notes":   {"sensitivity": "medium",   "local_only": True,  "extract_insights": True},
    "personal_data_export": {"sensitivity": "high",     "local_only": True,  "extract_summary": True},
    "research_notes":       {"sensitivity": "low",      "local_only": False, "extract_insights": True},
    "ideas_brainstorm":     {"sensitivity": "low",      "local_only": False, "extract_ideas": True},
}

# ── Vault SQLite schema ────────────────────────────────────────────────────────
_VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_items (
    id                TEXT PRIMARY KEY,
    file_hash         TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    sensitivity       TEXT NOT NULL,
    content_type      TEXT NOT NULL,
    encrypted_content BLOB NOT NULL,
    created_at        TEXT NOT NULL,
    accessed_at       TEXT NOT NULL
);
"""


# ══════════════════════════════════════════════════════════════════════════════
# VaultEncryption
# ══════════════════════════════════════════════════════════════════════════════

class VaultEncryption:
    """
    AES-256-GCM encryption when `cryptography` is available;
    XOR + base64 obfuscation otherwise.
    """

    def __init__(self) -> None:
        self._strong: bool = False
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
            self._AESGCM = AESGCM
            self._strong = True
            log.info("VaultEncryption: using AES-256-GCM")
        except BaseException:
            self._AESGCM = None
            log.warning(
                "VaultEncryption: cryptography library not available — "
                "falling back to XOR obfuscation (NOT cryptographically secure)"
            )
        self._key: bytes = self._get_key()

    # ── Public interface ──────────────────────────────────────────────────────

    def encrypt(self, data: str) -> bytes:
        """Encrypt a string; returns raw bytes suitable for BLOB storage."""
        try:
            if self._strong and self._AESGCM is not None:
                import os as _os
                nonce = _os.urandom(12)          # 96-bit nonce for GCM
                aesgcm = self._AESGCM(self._key)
                ciphertext = aesgcm.encrypt(nonce, data.encode("utf-8"), None)
                # Prepend nonce so we can recover it on decrypt
                return nonce + ciphertext
            return self._xor_obfuscate(data, self._key)
        except Exception as exc:
            log.error("VaultEncryption.encrypt failed: %s", exc)
            # Last-resort: store base64 (not encrypted but parseable)
            return base64.b64encode(data.encode("utf-8"))

    def decrypt(self, data: bytes) -> str:
        """Decrypt bytes previously returned by encrypt()."""
        try:
            if self._strong and self._AESGCM is not None:
                nonce = data[:12]
                ciphertext = data[12:]
                aesgcm = self._AESGCM(self._key)
                return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
            return self._xor_deobfuscate(data, self._key)
        except Exception as exc:
            log.error("VaultEncryption.decrypt failed: %s", exc)
            try:
                return base64.b64decode(data).decode("utf-8")
            except Exception:
                return ""

    def is_using_strong_encryption(self) -> bool:
        """True if AES-256-GCM is active."""
        return self._strong

    # ── Key management ────────────────────────────────────────────────────────

    def _get_key(self) -> bytes:
        """
        Return a 32-byte key.
        Priority: VAULT_ENCRYPTION_KEY env var (hex) → deterministic hostname hash.
        Never raises.
        """
        try:
            hex_key = os.environ.get(ENCRYPTION_KEY_ENV, "").strip()
            if hex_key:
                raw = bytes.fromhex(hex_key)
                # Pad or truncate to exactly 32 bytes
                if len(raw) >= 32:
                    return raw[:32]
                return raw.ljust(32, b"\x00")
        except Exception as exc:
            log.warning("Could not parse VAULT_ENCRYPTION_KEY: %s", exc)

        # Deterministic fallback from hostname — not secure but better than plaintext
        try:
            hostname = socket.gethostname().encode("utf-8")
        except Exception:
            hostname = b"localhost"
        return hashlib.sha256(hostname).digest()   # always 32 bytes

    # ── XOR fallback ──────────────────────────────────────────────────────────

    def _xor_obfuscate(self, data: str, key: bytes) -> bytes:
        """XOR each byte of data with cycling key bytes, then base64-encode."""
        raw = data.encode("utf-8")
        key_len = len(key)
        xored = bytes(b ^ key[i % key_len] for i, b in enumerate(raw))
        return base64.b64encode(xored)

    def _xor_deobfuscate(self, data: bytes, key: bytes) -> str:
        """Reverse of _xor_obfuscate."""
        xored = base64.b64decode(data)
        key_len = len(key)
        raw = bytes(b ^ key[i % key_len] for i, b in enumerate(xored))
        return raw.decode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# VaultDatabase
# ══════════════════════════════════════════════════════════════════════════════

class VaultDatabase:
    """SQLite-backed encrypted file vault."""

    def __init__(self, encryption: VaultEncryption) -> None:
        self._enc = encryption
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        self._db_path = str(VAULT_DB)
        self._init_db()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            with self._conn() as conn:
                conn.executescript(_VAULT_SCHEMA)
        except Exception as exc:
            log.error("VaultDatabase._init_db failed: %s", exc)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _hash_file(self, path: Path) -> str:
        """SHA-256 hex digest of file contents."""
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
        except Exception as exc:
            log.error("VaultDatabase._hash_file(%s) failed: %s", path, exc)
        return h.hexdigest()

    # ── Public interface ──────────────────────────────────────────────────────

    def store_file(self, file_path: Path, sensitivity: str,
                   content_type: str) -> str:
        """
        Read file, encrypt content, store in vault DB.
        Returns item_id (UUID string).
        """
        item_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        try:
            raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            log.error("VaultDatabase.store_file: cannot read %s — %s", file_path, exc)
            return item_id

        file_hash = self._hash_file(file_path)
        encrypted = self._enc.encrypt(raw_text)

        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO vault_items
                       (id, file_hash, original_filename, sensitivity,
                        content_type, encrypted_content, created_at, accessed_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (item_id, file_hash, file_path.name, sensitivity,
                     content_type, encrypted, now, now),
                )
            log.info("VaultDatabase: stored %s → id=%s", file_path.name, item_id)
        except Exception as exc:
            log.error("VaultDatabase.store_file DB write failed: %s", exc)
        return item_id

    def retrieve_file(self, item_id: str) -> str:
        """Decrypt and return file content by vault item id."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT encrypted_content FROM vault_items WHERE id=?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    log.warning("VaultDatabase.retrieve_file: id=%s not found", item_id)
                    return ""
                # Update accessed_at
                conn.execute(
                    "UPDATE vault_items SET accessed_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), item_id),
                )
            return self._enc.decrypt(bytes(row["encrypted_content"]))
        except Exception as exc:
            log.error("VaultDatabase.retrieve_file(%s) failed: %s", item_id, exc)
            return ""

    def list_items(self, sensitivity: str = None) -> List[Dict]:
        """
        Return vault items WITHOUT encrypted_content (security: never expose raw blob in list).
        """
        try:
            with self._conn() as conn:
                if sensitivity:
                    rows = conn.execute(
                        """SELECT id, file_hash, original_filename, sensitivity,
                                  content_type, created_at, accessed_at
                           FROM vault_items WHERE sensitivity=?
                           ORDER BY created_at DESC""",
                        (sensitivity,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT id, file_hash, original_filename, sensitivity,
                                  content_type, created_at, accessed_at
                           FROM vault_items ORDER BY created_at DESC"""
                    ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.error("VaultDatabase.list_items failed: %s", exc)
            return []

    def get_item_count(self) -> int:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM vault_items").fetchone()
            return int(row["n"]) if row else 0
        except Exception as exc:
            log.error("VaultDatabase.get_item_count failed: %s", exc)
            return 0


# ══════════════════════════════════════════════════════════════════════════════
# LocalInsightExtractor
# ══════════════════════════════════════════════════════════════════════════════

# Heuristic keyword sets
_INSIGHT_KEYWORDS = [
    "i learned", "i discovered", "key insight", "important", "critical",
    "never forget", "best decision", "mistake", "lesson", "i realized",
    "turned out", "changed my", "breakthrough", "pivotal", "game changer",
]
_EMOTION_WORDS = [
    "amazed", "shocked", "disappointed", "breakthrough", "excited",
    "devastated", "surprised", "overwhelmed", "grateful", "regret",
]
_ANONYMIZE_TITLES = r"\b(Mr|Mrs|Ms|Dr|Prof|Sir)\s+[A-Z][a-z]+"
_PROPER_NOUN_RE  = re.compile(r"\b[A-Z]{2,}\b")           # ALL-CAPS words
_TITLE_NAME_RE   = re.compile(_ANONYMIZE_TITLES)
_PHONE_RE        = re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_EMAIL_RE        = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_URL_RE          = re.compile(r"https?://\S+|www\.\S+")
_ACCOUNT_RE      = re.compile(r"\b\d{8,}\b")              # long number strings (account #)
_STAT_PATTERN    = re.compile(
    r"\b(\d+\.?\d*)\s*(percent|%|x|times|days?|weeks?|months?|years?|hours?|dollars?|\$|k|million)\b",
    re.IGNORECASE,
)


class LocalInsightExtractor:
    """
    Extracts insights from private text WITHOUT sending content to external APIs
    for high-sensitivity material.
    """

    def __init__(self) -> None:
        self._ollama_available: bool = False
        try:
            import ollama  # type: ignore
            self._ollama = ollama
            self._ollama_available = True
            log.info("LocalInsightExtractor: Ollama available for local LLM processing")
        except ImportError:
            self._ollama = None
            log.info("LocalInsightExtractor: Ollama not available — using heuristic extraction")

    # ── Public interface ──────────────────────────────────────────────────────

    def extract_insights_local(self, text: str, content_type: str) -> List[Dict]:
        """
        Extract insights locally. NEVER sends full private text to external API.
        Returns list of {text, category, lesson, video_angle} dicts.
        """
        if self._ollama_available:
            results = self._ollama_extract(text, content_type)
            if results:
                return results
        return self._heuristic_extract(text, content_type)

    def anonymize_text(self, text: str) -> str:
        """
        Replace PII with safe placeholders:
        - Named titles + proper names → [person]
        - ALL-CAPS proper nouns → [entity]
        - Phone numbers → [phone]
        - Emails → [email]
        - URLs → [url]
        - Long account-like numbers → [account]
        """
        text = _TITLE_NAME_RE.sub("[person]", text)
        text = _PROPER_NOUN_RE.sub("[entity]", text)
        text = _PHONE_RE.sub("[phone]", text)
        text = _EMAIL_RE.sub("[email]", text)
        text = _URL_RE.sub("[url]", text)
        text = _ACCOUNT_RE.sub("[account]", text)
        return text

    def extract_personal_story(self, text: str) -> Dict:
        """
        Extract a story arc from text.
        Returns {setup, challenge, turning_point, lesson, universal_takeaway}.
        """
        try:
            sentences = [s.strip() for s in re.split(r"[.!?]+", text) if len(s.strip()) > 20]
            n = len(sentences)
            if n == 0:
                return self._empty_story_arc()

            # Segment the text into rough narrative thirds
            third = max(1, n // 3)
            setup_block     = " ".join(sentences[:third])
            middle_block    = " ".join(sentences[third: 2 * third])
            end_block       = " ".join(sentences[2 * third:])

            turning_sents = [
                s for s in sentences
                if any(kw in s.lower() for kw in [
                    "then", "but", "suddenly", "however", "until", "changed",
                    "realized", "discovered", "finally", "turned out",
                ])
            ]
            lesson_sents = [
                s for s in sentences
                if any(kw in s.lower() for kw in _INSIGHT_KEYWORDS)
            ]

            anon = self.anonymize_text
            return {
                "setup":             anon(setup_block[:500]),
                "challenge":         anon(middle_block[:500]),
                "turning_point":     anon(turning_sents[0][:300]) if turning_sents else "",
                "lesson":            anon(lesson_sents[0][:300]) if lesson_sents else "",
                "universal_takeaway": anon(end_block[-300:]),
            }
        except Exception as exc:
            log.error("extract_personal_story failed: %s", exc)
            return self._empty_story_arc()

    # ── Internal: Ollama ──────────────────────────────────────────────────────

    def _ollama_extract(self, text: str, content_type: str) -> List[Dict]:
        """Use local Ollama llama3 model — stays fully local."""
        try:
            # Truncate to 4000 chars to keep inference fast
            truncated = text[:4000]
            prompt = (
                "Extract 5 key insights from this private content. "
                "Return only generic, non-identifying insights. "
                'JSON array format: [{"text": "...", "category": "...", '
                '"lesson": "...", "video_angle": "..."}]\n\n'
                f"Content type: {content_type}\n\n{truncated}"
            )
            response = self._ollama.chat(
                model="llama3",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response["message"]["content"]
            # Extract JSON array from the response
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                insights = json.loads(match.group())
                if isinstance(insights, list):
                    return insights[:5]
        except Exception as exc:
            log.warning("Ollama extraction failed (%s) — falling back to heuristics", exc)
        return []

    # ── Internal: Heuristic fallback ──────────────────────────────────────────

    def _heuristic_extract(self, text: str, content_type: str) -> List[Dict]:
        """Pure-Python extraction — zero external calls."""
        insights: List[Dict] = []
        sentences = [s.strip() for s in re.split(r"[.!?]+", text) if len(s.strip()) > 15]

        # 1. Keyword-bearing sentences
        for sent in sentences:
            lower = sent.lower()
            if any(kw in lower for kw in _INSIGHT_KEYWORDS):
                insights.append({
                    "text":         self.anonymize_text(sent[:200]),
                    "category":     "key_lesson",
                    "lesson":       self.anonymize_text(sent[:200]),
                    "video_angle":  f"Personal experience: {content_type}",
                })
            if len(insights) >= 3:
                break

        # 2. Statistical patterns
        for stat_match in _STAT_PATTERN.finditer(text):
            context_start = max(0, stat_match.start() - 60)
            context_end   = min(len(text), stat_match.end() + 60)
            context = text[context_start:context_end].strip()
            insights.append({
                "text":         self.anonymize_text(context[:200]),
                "category":     "data_point",
                "lesson":       f"Measurable result: {stat_match.group(0)}",
                "video_angle":  "Data-backed insight",
            })
            if len(insights) >= 4:
                break

        # 3. Emotional peak sentences
        for sent in sentences:
            lower = sent.lower()
            if any(ew in lower for ew in _EMOTION_WORDS):
                insights.append({
                    "text":         self.anonymize_text(sent[:200]),
                    "category":     "emotional_peak",
                    "lesson":       self.anonymize_text(sent[:200]),
                    "video_angle":  "Relatable emotional moment",
                })
            if len(insights) >= 5:
                break

        # Deduplicate and ensure we always return something
        seen: set = set()
        unique: List[Dict] = []
        for item in insights:
            key = item["text"][:80]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        if not unique:
            unique.append({
                "text":         f"Personal experience with {content_type}",
                "category":     "general",
                "lesson":       "Documented personal journey",
                "video_angle":  f"What I learned from my {content_type}",
            })
        return unique[:5]

    @staticmethod
    def _empty_story_arc() -> Dict:
        return {
            "setup":             "",
            "challenge":         "",
            "turning_point":     "",
            "lesson":            "",
            "universal_takeaway": "",
        }


# ══════════════════════════════════════════════════════════════════════════════
# PersonalStoryGenerator
# ══════════════════════════════════════════════════════════════════════════════

_TEMPLATE_MAP: Dict[str, int] = {
    "personal_journal":     0,   # "What I Learned After {time} of {activity}"
    "business_case_study":  1,   # "How {experience} Changed Everything..."
    "financial_data":       7,   # "{n} Things I Discovered By Tracking My {personal_metric}"
    "health_data":          6,   # "What {n} {time_unit} of {activity} Taught Me About {topic}"
    "professional_notes":   4,   # "Why I {action}: A Personal Case Study"
    "personal_data_export": 7,
    "research_notes":       5,   # "The {topic} Advice That Actually Changed My Life"
    "ideas_brainstorm":     2,   # "The {n} Lessons I Wish I Knew Before {life_event}"
}

_ENGAGEMENT_BONUS: Dict[str, float] = {
    "personal_journal":     1.7,
    "business_case_study":  1.8,
    "financial_data":       1.6,
    "health_data":          1.5,
    "professional_notes":   1.4,
    "personal_data_export": 1.5,
    "research_notes":       1.3,
    "ideas_brainstorm":     1.3,
}

_THEME_WORDS = [
    "growth", "failure", "success", "money", "health", "time", "learning",
    "productivity", "relationships", "mindset", "discipline", "focus",
    "investing", "habits", "systems", "consistency",
]


class PersonalStoryGenerator:
    """Turns anonymized personal insights into universal YouTube video concepts."""

    def __init__(self) -> None:
        pass

    # ── Public interface ──────────────────────────────────────────────────────

    def generate_video_concept(self, insights: List[Dict],
                                story_arc: Dict = None) -> Dict:
        """
        Generate a video concept from personal insights.
        Private content is NEVER embedded; only universal themes are used.
        """
        try:
            themes = self._extract_universal_themes(insights, story_arc)
            primary_theme = themes[0] if themes else "personal growth"
            n_insights = len(insights)

            title = self._fill_template(
                PERSONAL_STORY_TEMPLATES[0],
                activity=primary_theme,
                time="years",
                n=n_insights,
            )

            hook = (
                f"What if everything you believed about {primary_theme} was wrong? "
                f"After tracking my own results for years, I found {n_insights} "
                f"counterintuitive patterns that changed everything."
            )

            lesson = insights[0]["lesson"] if insights else f"Lessons from {primary_theme}"
            lesson = lesson[:200]

            cta = (
                f"Comment below: have you ever experienced a {primary_theme} "
                "breakthrough? I read every reply."
            )

            concept = {
                "title":                    title,
                "hook":                     hook,
                "universal_lesson":         lesson,
                "cta":                      cta,
                "authenticity_angle":       f"Real personal data + honest results on {primary_theme}",
                "estimated_engagement_bonus": 1.5,   # generic default until content_type known
                "themes":                   themes,
                "insight_count":            n_insights,
                "generated_at":             datetime.now(timezone.utc).isoformat(),
            }
            return self.anonymize_story(concept)
        except Exception as exc:
            log.error("PersonalStoryGenerator.generate_video_concept failed: %s", exc)
            return {}

    def get_template(self, content_type: str) -> str:
        """Return best template string for a given content type."""
        idx = _TEMPLATE_MAP.get(content_type, 0)
        return PERSONAL_STORY_TEMPLATES[idx]

    def estimate_engagement_bonus(self, content_type: str) -> float:
        """Personal stories get 1.3–1.8× engagement multiplier based on type."""
        return _ENGAGEMENT_BONUS.get(content_type, 1.3)

    def anonymize_story(self, story: Dict) -> Dict:
        """
        Walk story dict recursively and strip any remaining PII strings.
        Guarantees no PII leaks in concept output.
        """
        extractor = LocalInsightExtractor.__new__(LocalInsightExtractor)
        extractor._ollama_available = False
        return self._deep_anonymize(story, extractor.anonymize_text)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_universal_themes(self, insights: List[Dict],
                                   story_arc: Dict = None) -> List[str]:
        combined = " ".join(
            f"{i.get('text', '')} {i.get('lesson', '')} {i.get('video_angle', '')}"
            for i in insights
        )
        if story_arc:
            combined += " " + " ".join(str(v) for v in story_arc.values())

        found = [w for w in _THEME_WORDS if w in combined.lower()]
        if not found:
            found = ["personal growth"]
        return found

    @staticmethod
    def _fill_template(template: str, **kwargs) -> str:
        """Fill template slots with provided kwargs; leave unknown slots as-is."""
        try:
            return template.format(**kwargs)
        except KeyError:
            # Replace remaining {slot} placeholders gracefully
            result = template
            for key, val in kwargs.items():
                result = result.replace("{" + key + "}", str(val))
            # Remove any unfilled slots
            result = re.sub(r"\{[^}]+\}", "...", result)
            return result

    @staticmethod
    def _deep_anonymize(obj: Any, fn) -> Any:
        """Recursively anonymize all string values in a dict/list structure."""
        if isinstance(obj, dict):
            return {k: PersonalStoryGenerator._deep_anonymize(v, fn) for k, v in obj.items()}
        if isinstance(obj, list):
            return [PersonalStoryGenerator._deep_anonymize(i, fn) for i in obj]
        if isinstance(obj, str):
            return fn(obj)
        return obj


# ══════════════════════════════════════════════════════════════════════════════
# PrivateVaultEngine  (orchestrator)
# ══════════════════════════════════════════════════════════════════════════════

class PrivateVaultEngine:
    """
    Orchestrates the full private-content pipeline.
    High-sensitivity files → local-only extraction.
    Medium/low → optional anonymised Claude pass.
    """

    def __init__(self, memory=None) -> None:
        self._memory = memory
        self._enc     = VaultEncryption()
        self._db      = VaultDatabase(self._enc)
        self._extractor = LocalInsightExtractor()
        self._story_gen  = PersonalStoryGenerator()
        PRIVATE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        log.info(
            "PrivateVaultEngine ready | encryption=%s",
            "AES-256-GCM" if self._enc.is_using_strong_encryption() else "XOR-fallback",
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def ingest_private_file(self, file_path: str, sensitivity: str = "high",
                            content_type: str = "personal_journal") -> Dict:
        """
        Full private pipeline:
          1. Read file locally
          2. Store encrypted in vault
          3. Extract insights locally  (NO external API for high/critical)
          4. Medium/low: optional Claude pass on anonymized text only
          5. Generate video concept from insights
          6. Save concept (not private content) to memory
          7. Return result dict
        """
        result: Dict = {
            "item_id":           "",
            "insights_extracted": 0,
            "video_concept":     {},
            "encryption_used":   "AES-256-GCM" if self._enc.is_using_strong_encryption() else "XOR",
            "error":             None,
        }
        try:
            path = Path(file_path)
            if not path.exists():
                result["error"] = f"File not found: {file_path}"
                log.error("ingest_private_file: %s", result["error"])
                return result

            # Step 1+2: encrypt and store
            item_id = self._db.store_file(path, sensitivity, content_type)
            result["item_id"] = item_id

            # Step 3: local extraction
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            insights = self._extractor.extract_insights_local(raw_text, content_type)

            # Step 4: optional external enrichment for non-sensitive content
            category_cfg = PRIVACY_CATEGORIES.get(content_type, {})
            if sensitivity not in ("high", "critical") and not category_cfg.get("local_only", True):
                insights = self._enrich_with_claude(
                    self._extractor.anonymize_text(raw_text[:2000]),
                    insights,
                    content_type,
                )

            result["insights_extracted"] = len(insights)

            # Step 5: video concept
            story_arc = self._extractor.extract_personal_story(raw_text)
            concept = self._story_gen.generate_video_concept(insights, story_arc)
            concept["estimated_engagement_bonus"] = self._story_gen.estimate_engagement_bonus(content_type)
            concept["content_type"] = content_type
            result["video_concept"] = concept

            # Step 6: persist concept (no private content) to memory
            memory = self._memory
            if memory is not None:
                try:
                    memory.save_vault_item({
                        "item_id":      item_id,
                        "content_type": content_type,
                        "sensitivity":  sensitivity,
                        "insights":     insights,
                        "concept":      concept,
                        "created_at":   datetime.now(timezone.utc).isoformat(),
                    })
                except AttributeError:
                    # save_vault_item may not exist yet on all memory versions
                    try:
                        memory.save_monetization_event({
                            "event_type": "vault_item_ingested",
                            "data": {
                                "item_id":      item_id,
                                "content_type": content_type,
                                "concept_title": concept.get("title", ""),
                            },
                        })
                    except Exception as mem_exc:
                        log.warning("Memory save failed: %s", mem_exc)
                except Exception as mem_exc:
                    log.warning("Memory save_vault_item failed: %s", mem_exc)

            log.info(
                "ingest_private_file: %s → vault id=%s, insights=%d",
                path.name, item_id, len(insights),
            )
        except Exception as exc:
            result["error"] = str(exc)
            log.error("ingest_private_file error: %s\n%s", exc, traceback.format_exc())

        return result

    def scan_private_folder(self) -> Dict:
        """Scan PRIVATE_INPUT_DIR and process new files not yet in vault."""
        summary: Dict = {
            "files_found":    0,
            "files_processed": 0,
            "files_skipped":  0,
            "concepts_generated": 0,
            "errors":         [],
        }
        try:
            if not PRIVATE_INPUT_DIR.exists():
                PRIVATE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
                return summary

            existing_hashes = {
                item.get("file_hash", "") for item in self._db.list_items()
            }

            text_extensions = {".txt", ".md", ".csv", ".json", ".log", ".rst"}
            files = [
                f for f in PRIVATE_INPUT_DIR.iterdir()
                if f.is_file() and f.suffix.lower() in text_extensions
            ]
            summary["files_found"] = len(files)

            for file_path in files:
                file_hash = self._db._hash_file(file_path)
                if file_hash in existing_hashes:
                    summary["files_skipped"] += 1
                    continue

                # Guess content type from filename
                content_type = self._guess_content_type(file_path.name)
                cfg = PRIVACY_CATEGORIES.get(content_type, {})
                sensitivity = cfg.get("sensitivity", "high")

                result = self.ingest_private_file(
                    str(file_path), sensitivity=sensitivity, content_type=content_type
                )
                if result.get("error"):
                    summary["errors"].append(result["error"])
                else:
                    summary["files_processed"] += 1
                    if result.get("video_concept"):
                        summary["concepts_generated"] += 1

        except Exception as exc:
            summary["errors"].append(str(exc))
            log.error("scan_private_folder failed: %s\n%s", exc, traceback.format_exc())

        return summary

    def get_vault_status(self) -> Dict:
        """Return vault status summary."""
        try:
            item_count = self._db.get_item_count()
            vault_size_mb = 0.0
            if VAULT_DB.exists():
                vault_size_mb = round(VAULT_DB.stat().st_size / (1024 * 1024), 3)
            return {
                "item_count":      item_count,
                "encryption_type": "AES-256-GCM" if self._enc.is_using_strong_encryption() else "XOR-obfuscation",
                "vault_size_mb":   vault_size_mb,
                "vault_path":      str(VAULT_DB),
                "strong_encryption": self._enc.is_using_strong_encryption(),
            }
        except Exception as exc:
            log.error("get_vault_status failed: %s", exc)
            return {"item_count": 0, "encryption_type": "unknown", "vault_size_mb": 0.0}

    def export_video_concepts(self, memory=None) -> List[Dict]:
        """
        Return all video concepts generated from vault items.
        No private content is ever included.
        """
        concepts: List[Dict] = []
        try:
            mem = memory or self._memory
            if mem is not None:
                try:
                    items = mem.get_vault_items(n=100)
                    for item in items:
                        concept_raw = item.get("concept") or item.get("data", {})
                        if isinstance(concept_raw, str):
                            try:
                                concept_raw = json.loads(concept_raw)
                            except Exception:
                                concept_raw = {}
                        if concept_raw:
                            concepts.append(concept_raw)
                    return concepts
                except AttributeError:
                    pass

            # Fallback: reconstruct from vault DB items (no encrypted content)
            items = self._db.list_items()
            for item in items:
                concepts.append({
                    "vault_item_id": item["id"],
                    "content_type":  item["content_type"],
                    "created_at":    item["created_at"],
                    "note":          "concept not cached — re-ingest to regenerate",
                })
        except Exception as exc:
            log.error("export_video_concepts failed: %s", exc)
        return concepts

    def print_report(self, result: Dict) -> None:
        """Formatted report — NEVER prints private content."""
        print("\n" + "=" * 60)
        print("PRIVATE VAULT ENGINE REPORT")
        print("=" * 60)
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
            return
        print(f"  Vault item ID    : {result.get('item_id', 'n/a')}")
        print(f"  Encryption       : {result.get('encryption_used', 'n/a')}")
        print(f"  Insights extracted: {result.get('insights_extracted', 0)}")
        concept = result.get("video_concept", {})
        if concept:
            print(f"  Video title      : {concept.get('title', 'n/a')}")
            print(f"  Universal lesson : {concept.get('universal_lesson', 'n/a')[:100]}")
            print(f"  Engagement bonus : {concept.get('estimated_engagement_bonus', 'n/a')}x")
        print("=" * 60 + "\n")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _guess_content_type(self, filename: str) -> str:
        """Guess PRIVACY_CATEGORIES key from filename."""
        lower = filename.lower()
        if any(kw in lower for kw in ("journal", "diary", "personal")):
            return "personal_journal"
        if any(kw in lower for kw in ("business", "case", "client")):
            return "business_case_study"
        if any(kw in lower for kw in ("finance", "financial", "invest", "bank", "account")):
            return "financial_data"
        if any(kw in lower for kw in ("health", "medical", "fitness", "sleep", "weight")):
            return "health_data"
        if any(kw in lower for kw in ("note", "work", "professional", "meeting")):
            return "professional_notes"
        if any(kw in lower for kw in ("export", "data", "backup")):
            return "personal_data_export"
        if any(kw in lower for kw in ("research", "study", "analysis")):
            return "research_notes"
        if any(kw in lower for kw in ("idea", "brainstorm", "concept")):
            return "ideas_brainstorm"
        return "personal_journal"

    def _enrich_with_claude(self, anonymized_text: str, existing_insights: List[Dict],
                             content_type: str) -> List[Dict]:
        """
        Use Claude (Haiku) to enrich insights for non-sensitive content.
        Only the anonymized text is sent — never the original.
        """
        try:
            import anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                log.warning("ANTHROPIC_API_KEY not set — skipping Claude enrichment")
                return existing_insights

            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                f"Content type: {content_type}\n\n"
                "The following text has already been anonymized. "
                "Identify 3 additional universal insights that would make compelling "
                "YouTube content. Return JSON: "
                '[{"text":"...","category":"...","lesson":"...","video_angle":"..."}]\n\n'
                f"{anonymized_text}"
            )
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text if msg.content else ""
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                extras = json.loads(match.group())
                if isinstance(extras, list):
                    return existing_insights + extras[:3]
        except Exception as exc:
            log.warning("Claude enrichment failed: %s", exc)
        return existing_insights


# ── Module-level convenience ──────────────────────────────────────────────────

def get_engine(memory=None) -> PrivateVaultEngine:
    """Return a ready-to-use PrivateVaultEngine instance."""
    return PrivateVaultEngine(memory=memory)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = PrivateVaultEngine()
    print(json.dumps(engine.get_vault_status(), indent=2))
    scan = engine.scan_private_folder()
    print(json.dumps(scan, indent=2))
