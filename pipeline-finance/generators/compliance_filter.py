"""
Compliance filter — scans scripts for financial advice language,
guaranteed returns, or other liability-triggering phrases.
Uses both rule-based detection and Claude AI review.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic

from config.settings import settings
from config.prompts import COMPLIANCE_REVIEW_PROMPT
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    passed: bool
    risk_level: str            # "low" | "medium" | "high"
    issues: list[str]
    suggested_fixes: list[str]
    disclaimer_present: bool
    flagged_phrases: list[tuple[str, int]]   # (phrase, char_position)
    reviewed_by: str           # "rule_engine" | "claude" | "both"
    reviewed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def summary(self) -> str:
        status = "PASSED ✅" if self.passed else "FAILED ❌"
        return (
            f"Compliance Check: {status} | Risk: {self.risk_level.upper()} | "
            f"Issues: {len(self.issues)} | Disclaimer: {'Present' if self.disclaimer_present else 'MISSING'}"
        )


# ── Rule-based patterns ────────────────────────────────────────────────────
HARD_BLOCK_PATTERNS = [
    (r"\bguaranteed?\s+return\b", "Guaranteed return claim"),
    (r"\b100\s*%\s+safe\b", "100% safe claim"),
    (r"\brisk[\s-]?free\b", "Risk-free claim"),
    (r"\bcan'?t\s+lose\b", "Can't lose claim"),
    (r"\byou\s+should\s+(buy|sell|invest|short)\b", "Direct buy/sell advice"),
    (r"\bmy\s+recommendation\s+is\s+to\s+(buy|sell)\b", "Explicit recommendation"),
    (r"\bwill\s+(definitely|certainly|absolutely)\s+(go|rise|fall|crash)\b", "Definitive price prediction"),
    (r"\bbuy\s+this\s+stock\s+now\b", "Direct buy instruction"),
    (r"\bthis\s+is\s+(not\s+)?financial\s+advice\b", None),   # disclosure, not a flag
]

SOFT_WARN_PATTERNS = [
    (r"\b(I|we)\s+(think|believe|predict)\s+(?:it|this|the\s+stock)\s+will\b", "Soft price prediction"),
    (r"\bgoing\s+to\s+(the\s+moon|moon)\b", "Speculative hype language"),
    (r"\beach\s+investor\s+should\b", "Potentially advisory language"),
    (r"\bperfect\s+(investment|buy|opportunity)\b", "Absolute superlative claim"),
]

DISCLAIMER_PATTERNS = [
    r"(not|no)\s+financial\s+advice",
    r"educational\s+purposes?\s+only",
    r"do\s+your\s+own\s+research",
    r"consult\s+a\s+(financial|professional)",
    r"investment\s+decisions",
]


def _rule_based_check(script: str) -> tuple[list[str], list[str], list[tuple[str, int]], bool]:
    """Returns (issues, warnings, flagged_phrases, disclaimer_present)."""
    script_lower = script.lower()
    issues = []
    flagged: list[tuple[str, int]] = []

    for pattern, label in HARD_BLOCK_PATTERNS:
        if label is None:
            continue
        for match in re.finditer(pattern, script_lower):
            issues.append(f"[HARD BLOCK] {label}: '{match.group()}'")
            flagged.append((match.group(), match.start()))

    warnings = []
    for pattern, label in SOFT_WARN_PATTERNS:
        for match in re.finditer(pattern, script_lower):
            warnings.append(f"[WARNING] {label}: '{match.group()}'")
            flagged.append((match.group(), match.start()))

    # Also check the settings block list
    for phrase in settings.compliance_block_phrases:
        if phrase.lower() in script_lower:
            idx = script_lower.index(phrase.lower())
            issues.append(f"[BLOCKED PHRASE] '{phrase}'")
            flagged.append((phrase, idx))

    disclaimer_present = any(re.search(p, script_lower) for p in DISCLAIMER_PATTERNS)

    return issues, warnings, flagged, disclaimer_present


def _claude_review(script: str) -> dict:
    """Use Claude to do a deeper semantic compliance review."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = COMPLIANCE_REVIEW_PROMPT.substitute(script=script[:4000])  # truncate for token efficiency

    try:
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            temperature=0.1,  # low temp for consistent compliance analysis
            messages=[{"role": "user", "content": prompt}],
        )
        record(response, "compliance_filter")
        raw = response.content[0].text
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as exc:
        logger.error("Claude compliance review failed: %s", exc)

    return {"passed": True, "issues": [], "suggested_fixes": [], "risk_level": "low", "disclaimer_present": False}


def check_compliance(script: str, use_ai: bool = True) -> ComplianceResult:
    """
    Run full compliance check on a script.
    Rule engine runs first; AI review adds semantic depth.
    """
    logger.info("Running compliance check (AI: %s)", use_ai)

    issues, warnings, flagged, disclaimer = _rule_based_check(script)
    all_issues = issues + warnings
    fixes: list[str] = []
    ai_risk = "low"

    if not disclaimer:
        all_issues.append("[CRITICAL] Disclaimer missing from script")
        fixes.append(f"Add disclaimer: '{settings.disclaimer_text}'")

    reviewed_by = "rule_engine"

    if use_ai:
        ai_result = _claude_review(script)
        if ai_result.get("issues"):
            for issue in ai_result["issues"]:
                if issue not in all_issues:
                    all_issues.append(f"[AI] {issue}")
        if ai_result.get("suggested_fixes"):
            fixes.extend(ai_result["suggested_fixes"])
        ai_risk = ai_result.get("risk_level", "low")
        reviewed_by = "both"

    # Determine overall risk level
    if issues:  # hard blocks = high risk
        risk = "high"
    elif len(warnings) >= 3 or ai_risk == "high":
        risk = "medium"
    elif warnings or ai_risk == "medium":
        risk = "low"
    else:
        risk = "low"

    passed = len(issues) == 0 and disclaimer

    result = ComplianceResult(
        passed=passed,
        risk_level=risk,
        issues=all_issues,
        suggested_fixes=fixes,
        disclaimer_present=disclaimer,
        flagged_phrases=flagged,
        reviewed_by=reviewed_by,
    )

    logger.info("Compliance result: %s", result.summary)
    return result


# ── ComplianceFilter class ────────────────────────────────────────────────────

class ComplianceFilter:
    """Mandatory compliance gate for all DriftWire326 scripts."""

    AI_DISCLOSURE = "Narration is AI-generated."

    def check_blocked_phrases(self, script: str) -> list[str]:
        """Return list of blocked-phrase violations."""
        violations: list[str] = []
        script_lower = script.lower()
        for phrase in settings.compliance_block_phrases:
            if phrase.lower() in script_lower:
                violations.append(f"Blocked phrase: '{phrase}'")
        for pattern, label in HARD_BLOCK_PATTERNS:
            if label is None:
                continue
            if re.search(pattern, script_lower):
                violations.append(f"Hard block: {label}")
        return violations

    def check_required_phrases(self, script: str) -> bool:
        """Return True if disclaimer patterns are present."""
        sl = script.lower()
        return any(re.search(p, sl) for p in DISCLAIMER_PATTERNS)

    def inject_disclaimer(self, script: str) -> str:
        """Append disclaimer block if not already present."""
        if not self.check_required_phrases(script):
            return (script.rstrip()
                    + f"\n\n[DISCLAIMER]\n{settings.disclaimer_text}\n")
        return script

    def inject_ai_disclosure(self, script: str) -> str:
        """Append AI disclosure line if not already present."""
        if self.AI_DISCLOSURE not in script:
            return script.rstrip() + f"\n\n{self.AI_DISCLOSURE}\n"
        return script

    def run_promise_match(self, title: str, script: str) -> bool:
        """True if ≥60% of significant title words appear in the script."""
        words = set(re.findall(r'\b[A-Za-z]{4,}\b', title.lower()))
        if not words:
            return True
        sl = script.lower()
        matches = sum(1 for w in words if w in sl)
        return (matches / len(words)) >= 0.6

    def filter(self, script: str, title: str = "") -> dict:
        """
        Mandatory gate method.
        Returns {passed: bool, script: str, flags: list[str]}
        """
        flags: list[str] = []
        modified = script

        blocked = self.check_blocked_phrases(script)
        flags.extend(blocked)

        if not self.check_required_phrases(script):
            flags.append("Disclaimer missing — injecting automatically")
            modified = self.inject_disclaimer(modified)

        if self.AI_DISCLOSURE not in modified:
            modified = self.inject_ai_disclosure(modified)

        if title and not self.run_promise_match(title, script):
            flags.append(f"Promise mismatch: '{title[:50]}' not well-delivered")

        hard_fails = [f for f in flags if "Hard block" in f or "Blocked phrase" in f]
        passed = len(hard_fails) == 0

        if flags:
            self.log_all_flags(flags, title)

        return {"passed": passed, "script": modified, "flags": flags}

    def log_all_flags(self, flags: list[str], title: str = "") -> None:
        """Log all compliance flags to the pipeline logger (→ driftwire326.log)."""
        label = title[:40] if title else "unknown"
        for flag in flags:
            logger.warning("COMPLIANCE [%s]: %s", label, flag)


def auto_fix_script(script: str, result: ComplianceResult) -> str:
    """
    Attempt automatic fixes for common compliance issues.
    Hard blocks require manual review — this handles soft fixes.
    """
    fixed = script

    # Add disclaimer if missing
    if not result.disclaimer_present:
        fixed = fixed.rstrip() + f"\n\n[DISCLAIMER]\n{settings.disclaimer_text}\n"
        logger.info("Auto-added disclaimer to script")

    # Replace soft-block phrases
    replacements = {
        "you should buy": "investors may want to watch",
        "you should sell": "investors may consider reassessing",
        "can't lose": "historically performed well",
        "risk free": "lower-risk",
        "guaranteed return": "historical average return",
    }
    for bad, good in replacements.items():
        if bad.lower() in fixed.lower():
            fixed = re.sub(re.escape(bad), good, fixed, flags=re.IGNORECASE)
            logger.info("Auto-replaced '%s' → '%s'", bad, good)

    return fixed
