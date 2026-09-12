"""
framework/code_validator.py — AST Safety Validator
===================================================
Validates AI-generated code patches before application.

Checks:
  1. Valid Python syntax (compile())
  2. No dangerous builtins (eval, exec, __import__ with dynamic args)
  3. No shell injection (os.system, subprocess with shell=True)
  4. No path traversal (writes/reads outside project root)
  5. No network calls to non-whitelisted domains
  6. Public API preservation (exported names unchanged)
  7. No infinite loops (basic static analysis)
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

log = logging.getLogger("framework.code_validator")

PROJECT_ROOT = Path(__file__).parent.parent

# Built-in names that are never safe to call with dynamic (non-literal) arguments
_DANGEROUS_BUILTINS: Set[str] = {"eval", "exec", "compile", "__import__"}

# Hard-coded allow-list of network domains considered safe for the engine
_WHITELISTED_DOMAINS: Set[str] = {
    "api.anthropic.com",
    "api.openai.com",
    "api.elevenlabs.io",
    "api.replicate.com",
    "api.pexels.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
    "youtube.googleapis.com",
}


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Outcome of a CodeValidator.validate() call."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


# ── AST helpers ───────────────────────────────────────────────────────────────

def _is_literal(node: ast.expr) -> bool:
    """Return True if *node* is a compile-time constant / literal."""
    return isinstance(node, ast.Constant)


def _attr_chain(node: ast.expr) -> str:
    """Convert  a.b.c  attribute chains back to a dotted string, e.g. 'os.system'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attr_chain(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _has_keyword(call: ast.Call, name: str, truthy: bool = True) -> bool:
    """Return True if *call* has keyword argument *name* with a matching
    truthy/falsy constant.  Non-constant values are conservatively flagged.
    """
    for kw in call.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant):
                return bool(kw.value.value) == truthy
            return True  # non-constant: assume worst case
    return False


def _top_level_names(tree: ast.Module) -> Set[str]:
    """Return names defined at module top level (functions, classes, assignments)."""
    names: Set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


# ── Validator class ───────────────────────────────────────────────────────────

class CodeValidator:
    """Runs a battery of static-analysis checks on a Python source string.

    Attributes
    ----------
    PROJECT_ROOT : Path
        Root directory of the engine.  Literal file-path strings outside this
        tree are flagged as path-traversal risks.
    DANGEROUS_CALLS : set[str]
        Builtin names whose dynamic use is forbidden.
    SHELL_PATTERNS : set[str]
        Full dotted call chains considered shell-injection vectors.
    """

    PROJECT_ROOT: Path = PROJECT_ROOT
    DANGEROUS_CALLS: Set[str] = _DANGEROUS_BUILTINS
    SHELL_PATTERNS: Set[str] = {
        "os.system",
        "os.popen",
        "subprocess.Popen",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
    }

    # Patched file must be at least this fraction of the original (size sanity)
    MIN_SIZE_RATIO: float = 0.30

    # ── Primary entry-point ───────────────────────────────────────────────────

    def validate(
        self,
        source_code: str,
        original_source: str = "",
        filename: str = "<patch>",
    ) -> ValidationResult:
        """Run all safety checks on *source_code*.

        Returns a ValidationResult; valid=True only when no errors are found.
        Warnings are informational and do not block application.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # ① Syntax — must pass before we can build an AST
        errors.extend(self._check_syntax(source_code, filename))
        if errors:
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        try:
            tree = ast.parse(source_code, filename=filename)
        except SyntaxError as exc:
            errors.append(f"Syntax error (ast.parse): {exc}")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        # ② Dangerous builtins
        errors.extend(self._check_dangerous_builtins(tree))

        # ③ Shell injection
        errors.extend(self._check_shell_injection(tree))

        # ④ Path traversal
        errors.extend(self._check_path_traversal(tree))

        # ⑤ Network calls (warning only — non-whitelisted domains)
        warnings.extend(self._check_network_calls(tree))

        # ⑥ API preservation (errors if public names are removed)
        if original_source:
            errors.extend(self._check_api_preservation(original_source, source_code))

        # ⑦ Infinite loops (warning only)
        warnings.extend(self._check_infinite_loops(tree))

        if errors:
            log.warning("CodeValidator: %d error(s) in %s: %s", len(errors), filename, errors)
        else:
            log.debug("CodeValidator: %s passed all checks (%d warning(s))", filename, len(warnings))

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_syntax_only(
        self, source: str, filename: str = "<patch>"
    ) -> ValidationResult:
        """Lightweight syntax-only check — fast path when a full scan is not needed."""
        errs = self._check_syntax(source, filename)
        return ValidationResult(valid=len(errs) == 0, errors=errs)

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_syntax(self, source: str, filename: str) -> List[str]:
        """Check 1: valid Python syntax via compile()."""
        errors: List[str] = []
        try:
            compile(source, filename, "exec")
        except SyntaxError as exc:
            msg = f"Syntax error at line {exc.lineno}: {exc.msg}"
            if exc.text:
                msg += f" → {exc.text.strip()!r}"
            errors.append(msg)
        except ValueError as exc:
            errors.append(f"ValueError during compile: {exc}")
        return errors

    def _check_dangerous_builtins(self, tree: ast.AST) -> List[str]:
        """Check 2: flag eval/exec/compile/__import__ with non-literal arguments.

        Calls where every argument is a compile-time literal constant are
        permitted; anything else is rejected.
        """
        errors: List[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Resolve the called name (bare name or attribute last segment)
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue

            if name not in self.DANGEROUS_CALLS:
                continue

            # Allow only if every positional + keyword arg is a literal constant
            has_dynamic = any(not _is_literal(arg) for arg in node.args)
            has_dynamic = has_dynamic or any(
                not _is_literal(kw.value)
                for kw in node.keywords
                if kw.arg is not None
            )

            if has_dynamic:
                lineno = getattr(node, "lineno", "?")
                errors.append(
                    f"Dangerous builtin '{name}' called with non-literal arguments "
                    f"(line {lineno})"
                )
        return errors

    def _check_shell_injection(self, tree: ast.AST) -> List[str]:
        """Check 3: detect os.system calls and subprocess calls with shell=True."""
        errors: List[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _attr_chain(node.func)
            lineno = getattr(node, "lineno", "?")

            # os.system / os.popen — always executes via a shell
            if chain in ("os.system", "os.popen"):
                errors.append(
                    f"Shell injection risk: {chain}() call at line {lineno}. "
                    "Use subprocess with a list of arguments instead."
                )
                continue

            # subprocess.* called with shell=True
            subprocess_funcs = {
                "subprocess.Popen",
                "subprocess.run",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
            }
            if chain in subprocess_funcs and _has_keyword(node, "shell", truthy=True):
                errors.append(
                    f"Shell injection risk: {chain}(…, shell=True) at line {lineno}. "
                    "Pass a list of arguments and omit shell=True."
                )

        return errors

    def _check_path_traversal(self, tree: ast.AST) -> List[str]:
        """Check 4: detect literal path strings that escape the project root."""
        errors: List[str] = []
        file_io_names = {
            "open", "read_text", "write_text", "read_bytes", "write_bytes",
            "Path", "os.open", "shutil.copy", "shutil.move", "shutil.copy2",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _attr_chain(node.func)
            leaf = chain.split(".")[-1] if chain else ""
            if leaf not in file_io_names and chain not in file_io_names:
                continue

            for arg in node.args:
                if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                    continue
                raw = arg.value
                try:
                    candidate = Path(raw).resolve()
                    root_resolved = self.PROJECT_ROOT.resolve()
                    if not str(candidate).startswith(str(root_resolved)):
                        lineno = getattr(node, "lineno", "?")
                        errors.append(
                            f"Path traversal risk: literal path {raw!r} resolves "
                            f"outside project root (line {lineno})"
                        )
                except (OSError, ValueError):
                    pass  # Unresolvable paths — not flagged

        return errors

    def _check_network_calls(self, tree: ast.AST) -> List[str]:
        """Check 5 (warning): hard-coded URLs pointing to non-whitelisted domains."""
        warnings: List[str] = []
        try:
            from urllib.parse import urlparse
        except ImportError:
            return warnings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            val: str = node.value
            if not (val.startswith("http://") or val.startswith("https://")):
                continue
            try:
                domain = urlparse(val).netloc.lower().split(":")[0]
            except Exception:  # noqa: BLE001
                continue
            if domain and domain not in _WHITELISTED_DOMAINS:
                lineno = getattr(node, "lineno", "?")
                warnings.append(
                    f"Non-whitelisted domain in literal URL: {domain!r} "
                    f"at line {lineno} — review before deploying"
                )
        return warnings

    def _check_api_preservation(
        self, original_source: str, new_source: str
    ) -> List[str]:
        """Check 6: error if public module-level names present in original are missing
        from the patch (any public name removed = API breakage).
        """
        errors: List[str] = []
        original_names = self._public_names(original_source)
        new_names = self._public_names(new_source)
        removed = original_names - new_names
        for name in sorted(removed):
            errors.append(
                f"Public API broken: '{name}' exists in original but is absent "
                "from the patched source"
            )
        return errors

    def _check_infinite_loops(self, tree: ast.AST) -> List[str]:
        """Check 7 (warning): flag ``while True:`` bodies with no exit path."""
        warnings: List[str] = []
        _exit_calls = {"sys.exit", "exit", "quit", "os._exit"}

        for node in ast.walk(tree):
            if not isinstance(node, ast.While):
                continue

            # Only flag `while True:` / `while 1:` patterns
            test = node.test
            is_always_true = isinstance(test, ast.Constant) and bool(test.value)
            if not is_always_true:
                continue

            # Check for any exit path within the loop body
            has_exit = any(
                isinstance(n, (ast.Break, ast.Return, ast.Raise))
                for n in ast.walk(node)
            )
            if not has_exit:
                for n in ast.walk(node):
                    if isinstance(n, ast.Call) and _attr_chain(n.func) in _exit_calls:
                        has_exit = True
                        break

            if not has_exit:
                lineno = getattr(node, "lineno", "?")
                warnings.append(
                    f"Possible infinite loop at line {lineno}: "
                    "'while True' body has no break / return / raise / sys.exit"
                )

        return warnings

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _public_names(source: str) -> Set[str]:
        """Return the set of public (no leading underscore) module-level names."""
        names: Set[str] = set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return names
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and not node.target.id.startswith("_")
                ):
                    names.add(node.target.id)
        return names
