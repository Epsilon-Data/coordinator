"""
AST-based security scanner for researcher-submitted Python scripts.

Returns deterministic, factual findings — no LLM guessing.
The LLM only reasons over these structured results.
"""
import ast
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Union

from crewai.tools import BaseTool

from workers.ai_agent.security_constants import (
    BLOCKED_IMPORTS, SAFE_IMPORTS, DANGEROUS_CALLS, DANGEROUS_ATTRS,
    EPSILON_SDK_MODULES, SENSITIVE_ENV_VARS, SAFE_URL_DOMAINS,
)

logger = logging.getLogger(__name__)


@dataclass
class ScanFinding:
    """A single deterministic finding from the AST scanner."""
    line: int
    code: str
    category: str  # blocked_import, dangerous_call, credential_access, path_traversal, obfuscation, os_import
    severity: str  # critical, high, medium, low
    detail: str


@dataclass
class ScanReport:
    """Complete deterministic scan report."""
    safe: bool
    imports_found: List[str] = field(default_factory=list)
    blocked_imports_found: List[str] = field(default_factory=list)
    safe_imports_found: List[str] = field(default_factory=list)
    unknown_imports: List[str] = field(default_factory=list)
    findings: List[dict] = field(default_factory=list)
    total_lines: int = 0
    has_network_access: bool = False
    has_code_injection: bool = False
    has_credential_access: bool = False
    has_filesystem_escape: bool = False
    has_obfuscation: bool = False
    summary: str = ""


class ASTSecurityScanner(BaseTool):
    name: str = "AST Security Scanner"
    description: str = "Parse Python source code using AST and return deterministic security findings"

    def _run(self, script_content: str, script_filename: str = "main.py") -> ScanReport:
        """Scan Python source code and return factual findings."""
        if not script_content.strip():
            return ScanReport(
                safe=True,
                summary="Empty script — no code to analyze.",
            )

        try:
            tree = ast.parse(script_content)
        except SyntaxError as e:
            return ScanReport(
                safe=False,
                summary=f"Script has syntax error at line {e.lineno}: {e.msg}",
                findings=[asdict(ScanFinding(
                    line=e.lineno or 0,
                    code=str(e.text or ""),
                    category="syntax_error",
                    severity="high",
                    detail=f"Cannot parse script: {e.msg}",
                ))],
            )

        findings: List[ScanFinding] = []
        imports_found: List[str] = []
        blocked: List[str] = []
        safe: List[str] = []
        unknown: List[str] = []
        lines = script_content.splitlines()

        for node in ast.walk(tree):
            # --- Check imports ---
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._check_import(node, lines, imports_found, blocked, safe, unknown, findings)

            # --- Check function calls ---
            elif isinstance(node, ast.Call):
                self._check_call(node, lines, findings)

            # --- Check attribute access (os.environ, etc.) ---
            elif isinstance(node, ast.Attribute):
                self._check_attribute_access(node, lines, findings)

            # --- Check string literals for suspicious patterns ---
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self._check_string_literal(node, lines, findings)

        # --- Check for open() with path traversal ---
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self._check_file_access(node, lines, findings)

        has_network = any(f.category == "blocked_import" and "network" in f.detail.lower() for f in findings)
        has_injection = any(f.category == "dangerous_call" for f in findings)
        has_creds = any(f.category == "credential_access" for f in findings)
        has_escape = any(f.category == "path_traversal" for f in findings)
        has_obfusc = any(f.category == "obfuscation" for f in findings)

        is_safe = len(findings) == 0

        # Build summary
        if is_safe:
            summary = (
                f"Script '{script_filename}' passed all checks. "
                f"{len(imports_found)} imports found, all safe. "
                f"{len(lines)} lines of code, no dangerous patterns detected."
            )
        else:
            counts = {}
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            parts = [f"{v} {k}" for k, v in sorted(counts.items())]
            summary = (
                f"Script '{script_filename}' has {len(findings)} finding(s): {', '.join(parts)}. "
                f"Blocked imports: {blocked if blocked else 'none'}."
            )

        return ScanReport(
            safe=is_safe,
            imports_found=imports_found,
            blocked_imports_found=blocked,
            safe_imports_found=safe,
            unknown_imports=unknown,
            findings=[asdict(f) for f in findings],
            total_lines=len(lines),
            has_network_access=has_network,
            has_code_injection=has_injection,
            has_credential_access=has_creds,
            has_filesystem_escape=has_escape,
            has_obfuscation=has_obfusc,
            summary=summary,
        )

    def _get_line(self, lines: list, lineno: int) -> str:
        if 0 < lineno <= len(lines):
            return lines[lineno - 1].strip()
        return ""

    def _get_top_module(self, module_name: str) -> str:
        """Get the top-level module name (e.g. 'os.path' -> 'os')."""
        return module_name.split(".")[0]

    def _check_import(self, node, lines, imports_found, blocked, safe, unknown, findings):
        modules = []
        if isinstance(node, ast.Import):
            modules = [(alias.name, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [(node.module, node.module)]

        for full_name, display_name in modules:
            imports_found.append(full_name)
            top = self._get_top_module(full_name)

            # Epsilon SDK — always safe
            if full_name in EPSILON_SDK_MODULES or top == "generated":
                safe.append(full_name)
                continue

            # Check blocked
            if full_name in BLOCKED_IMPORTS or top in BLOCKED_IMPORTS:
                blocked.append(full_name)
                is_network = top in {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "paramiko", "ftplib", "smtplib", "telnetlib"}
                category = "blocked_import"
                if is_network:
                    detail = f"Network-capable module '{full_name}' — potential data exfiltration"
                    severity = "critical"
                elif top in {"subprocess", "commands", "pty"}:
                    detail = f"Code execution module '{full_name}' — arbitrary command execution"
                    severity = "critical"
                elif top in {"pickle", "marshal", "shelve", "jsonpickle"}:
                    detail = f"Dangerous deserialization module '{full_name}'"
                    severity = "high"
                elif top in {"ctypes", "cffi"}:
                    detail = f"Low-level system access module '{full_name}'"
                    severity = "high"
                else:
                    detail = f"Blocked module '{full_name}'"
                    severity = "high"

                findings.append(ScanFinding(
                    line=node.lineno,
                    code=self._get_line(lines, node.lineno),
                    category=category,
                    severity=severity,
                    detail=detail,
                ))
                continue

            # Check safe
            if full_name in SAFE_IMPORTS or top in SAFE_IMPORTS:
                safe.append(full_name)
                continue

            # os module — safe for os.path, flagged for os.system etc.
            if top == "os":
                # os.path is fine, os itself needs watching but isn't blocked
                safe.append(full_name)
                continue

            # Unknown — not blocked, not explicitly safe
            unknown.append(full_name)

    def _check_call(self, node: ast.Call, lines: list, findings: List[ScanFinding]):
        # Direct calls: eval(), exec(), etc.
        if isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_CALLS:
                findings.append(ScanFinding(
                    line=node.lineno,
                    code=self._get_line(lines, node.lineno),
                    category="dangerous_call",
                    severity="critical",
                    detail=f"Dangerous built-in '{node.func.id}()' — arbitrary code execution",
                ))

        # Attribute calls: os.system(), base64.b64decode(), etc.
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            # Get the object name
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                if (obj_name, attr_name) in DANGEROUS_ATTRS:
                    # Determine severity
                    if obj_name == "os" and attr_name in ("environ", "getenv"):
                        # Check if accessing sensitive vars
                        if node.args and isinstance(node.args[0], ast.Constant):
                            env_var = node.args[0].value
                            if env_var in SENSITIVE_ENV_VARS:
                                findings.append(ScanFinding(
                                    line=node.lineno,
                                    code=self._get_line(lines, node.lineno),
                                    category="credential_access",
                                    severity="critical",
                                    detail=f"Accessing sensitive environment variable '{env_var}'",
                                ))
                            return
                        findings.append(ScanFinding(
                            line=node.lineno,
                            code=self._get_line(lines, node.lineno),
                            category="credential_access",
                            severity="high",
                            detail=f"Accessing environment via {obj_name}.{attr_name}()",
                        ))
                    elif obj_name == "base64":
                        findings.append(ScanFinding(
                            line=node.lineno,
                            code=self._get_line(lines, node.lineno),
                            category="obfuscation",
                            severity="high",
                            detail=f"Base64 encoding/decoding via {obj_name}.{attr_name}() — possible data obfuscation",
                        ))
                    else:
                        findings.append(ScanFinding(
                            line=node.lineno,
                            code=self._get_line(lines, node.lineno),
                            category="dangerous_call",
                            severity="critical",
                            detail=f"Dangerous call {obj_name}.{attr_name}()",
                        ))

            # getattr on modules (obfuscation pattern)
            if attr_name == "__getattr__" or (isinstance(node.func, ast.Attribute) and node.func.attr == "getattr"):
                findings.append(ScanFinding(
                    line=node.lineno,
                    code=self._get_line(lines, node.lineno),
                    category="obfuscation",
                    severity="high",
                    detail="Dynamic attribute access — possible obfuscation of dangerous calls",
                ))

        # Check for getattr() as a direct call
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            # getattr(os, 'system') pattern
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                attr_target = node.args[1].value
                if attr_target in ("system", "popen", "exec", "environ", "getenv"):
                    findings.append(ScanFinding(
                        line=node.lineno,
                        code=self._get_line(lines, node.lineno),
                        category="obfuscation",
                        severity="critical",
                        detail=f"getattr() used to access '{attr_target}' — obfuscated dangerous call",
                    ))

    def _check_attribute_access(self, node: ast.Attribute, lines: list, findings: List[ScanFinding]):
        """Check for dangerous attribute access like os.environ (even without a call)."""
        if isinstance(node.value, ast.Name):
            obj_name = node.value.id
            attr_name = node.attr
            if obj_name == "os" and attr_name == "environ":
                findings.append(ScanFinding(
                    line=node.lineno,
                    code=self._get_line(lines, node.lineno),
                    category="credential_access",
                    severity="high",
                    detail="Accessing os.environ — could expose sensitive environment variables",
                ))

    def _check_string_literal(self, node: ast.Constant, lines: list, findings: List[ScanFinding]):
        val = node.value
        if not isinstance(val, str) or len(val) < 10:
            return

        # Check for path traversal in string literals
        if "../" in val or val.startswith("/etc/") or val.startswith("/proc/"):
            findings.append(ScanFinding(
                line=node.lineno,
                code=self._get_line(lines, node.lineno),
                category="path_traversal",
                severity="high",
                detail=f"Suspicious path in string literal: '{val[:50]}'",
            ))

        # Check for URLs (possible exfiltration targets)
        if val.startswith("http://") or val.startswith("https://"):
            # Ignore common schema URLs
            if any(domain in val for domain in SAFE_URL_DOMAINS):
                return
            findings.append(ScanFinding(
                line=node.lineno,
                code=self._get_line(lines, node.lineno),
                category="obfuscation",
                severity="medium",
                detail=f"URL found in code: '{val[:80]}' — potential exfiltration target",
            ))

    def _check_file_access(self, node: ast.Call, lines: list, findings: List[ScanFinding]):
        """Check open() calls for path traversal."""
        if not isinstance(node.func, ast.Name) or node.func.id != "open":
            return
        if not node.args:
            return
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            path = arg.value
            if "../" in path or path.startswith("/"):
                if not path.startswith("./"):
                    findings.append(ScanFinding(
                        line=node.lineno,
                        code=self._get_line(lines, node.lineno),
                        category="path_traversal",
                        severity="high",
                        detail=f"File access with suspicious path: '{path}'",
                    ))
