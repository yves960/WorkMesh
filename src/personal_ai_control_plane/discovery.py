"""Task 2: discovery — turn a vague user message into a concrete ForgeLoop target.

Pipeline (no LLM, keyword-only):
1. intent classification via keyword table
2. workspace scan for the most recently modified pom.xml / build.gradle
3. failing-test extraction from Maven Surefire reports (no `mvn test` upfront;
   one fallback run guarded by a 60s timeout that never raises)
4. target + profile + confidence
"""

from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

MVN_TIMEOUT_SECONDS = 60
FAILURE_SUMMARY_MAX_CHARS = 500

# Keyword table — first match wins. NO LLM (hard requirement: no provider dep).
INTENT_KEYWORDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("FIX_TEST", "java-ut-fixer",
     ("ut", "测试", "test", "挂了", "失败", "red", "fail")),
    ("FIX_STYLE", "codecheck-fixer",
     ("lint", "style", "checkstyle", "check")),
    ("FIX_SCA", "sca-upgrader",
     ("组件漏洞", "sca")),
    ("FIX_SECURITY", "sast-fixer",
     ("sast", "security", "漏洞", "cve")),
    ("UPGRADE_DEPS", "dependency-upgrader",
     ("依赖升级", "upgrade", "新版")),
    ("FIX_SCA", "sca-upgrader",
     ("sca", "组件漏洞")),
]
DEFAULT_PROFILE = "java-ut-fixer"

# Surefire "*.txt" failure line shapes we understand:
#   1. mock/compact:  com.demo.UserServiceTest#testLogin FAILED
#   2. real surefire: testLogin(com.demo.UserServiceTest)  Time elapsed: ... <<< FAILURE!
_COMPACT_RE = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)#(\w+)\s+FAILED")
_REAL_RE = re.compile(
    r"^(\w+)\(([\w.$]+)\)\s*(?:Time elapsed:[^\n]*)?\s*<<<\s*(?:FAILURE|ERROR)!",
)
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".idea", ".gradle"}


@dataclass
class DiscoveryResult:
    project_path: str
    project_name: str
    build_tool: str  # "maven" / "gradle" / "unknown"
    repo_url: str | None
    language: str  # "java" / "python" / etc.
    failing_tests: list[str] = field(default_factory=list)
    failure_summary: str = ""
    recommended_target: str = ""
    recommended_profile: str = DEFAULT_PROFILE
    discovery_confidence: float = 0.0


def _match_intent(user_message: str) -> tuple[str | None, str]:
    msg = (user_message or "").lower()
    for intent, profile, keywords in INTENT_KEYWORDS:
        if any(kw in msg for kw in keywords):
            return intent, profile
    return None, DEFAULT_PROFILE


def _scan_build_files(workspace_root: Path, max_scan_depth: int) -> list[Path]:
    """Recursively find pom.xml / build.gradle under root (bounded depth)."""
    found: list[Path] = []
    roots: list[tuple[Path, int]] = [(workspace_root, 0)]
    while roots:
        current, depth = roots.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and entry.name in (
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
            ):
                found.append(Path(entry.path))
            elif (
                entry.is_dir(follow_symlinks=False)
                and entry.name not in _SKIP_DIRS
                and depth < max(0, max_scan_depth)
            ):
                roots.append((Path(entry.path), depth + 1))
    return found


def _read_pom_meta(pom: Path) -> tuple[str, str]:
    """Return (artifactId, package_prefix) from pom.xml, with sane fallbacks."""
    artifact, group = pom.parent.name, ""
    try:
        root = ET.parse(pom).getroot()
    except ET.ParseError:
        return artifact, group

    # Prefer this module's own <artifactId>/<groupId> over inherited <parent>.
    own = root.find("{http://maven.apache.org/POM/4.0.0}artifactId") or root.find("artifactId")
    parent = root.find("{http://maven.apache.org/POM/4.0.0}parent")
    parent_aid = parent.findtext(
        "{http://maven.apache.org/POM/4.0.0}artifactId"
    ) if parent is not None else None
    if own is not None and (own.text or "").strip() and own.text != parent_aid:
        artifact = own.text.strip()
    parent_gid = parent.findtext(
        "{http://maven.apache.org/POM/4.0.0}groupId"
    ) if parent is not None else None
    gid = (root.findtext("{http://maven.apache.org/POM/4.0.0}groupId")
           or root.findtext("groupId") or parent_gid or "")
    group = gid.strip()
    return artifact, group


def _detect_language(project_path: Path, build_tool: str) -> str:
    if build_tool in ("maven", "gradle"):
        return "java"
    for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (project_path / marker).exists():
            return "python"
    return "unknown"


def _detect_repo_url(project_path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(project_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = (out.stdout or "").strip()
    return url or None


def _parse_surefire_reports(project_path: Path) -> tuple[list[str], str]:
    """Extract failing tests from target/surefire-reports/*.txt (and *.xml)."""
    report_dir = project_path / "target" / "surefire-reports"
    failing: list[str] = []
    summary_lines: list[str] = []
    if not report_dir.is_dir():
        return failing, ""
    txts = sorted(report_dir.glob("*.txt")) + sorted(report_dir.glob("TEST-*.xml"))
    for report in txts:
        try:
            content = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            match = _COMPACT_RE.search(line)
            if match:
                test = f"{match.group(1)}#{match.group(2)}"
                if test not in failing:
                    failing.append(test)
                summary_lines.append(line.strip())
                continue
            real = _REAL_RE.search(line)
            if real:
                method, fqn = real.group(1), real.group(2)
                test = f"{fqn}#{method}"
                if test not in failing:
                    failing.append(test)
                summary_lines.append(line.strip())
    summary = "\n".join(summary_lines)[:FAILURE_SUMMARY_MAX_CHARS]
    return failing, summary


def _run_mvn_tests(project_path: Path) -> tuple[list[str], str, bool]:
    """Fallback: run `mvn test` once (60s timeout). Never raises."""
    try:
        proc = subprocess.run(
            ["mvn", "test", "-q", "-Dtest=*"],
            cwd=str(project_path), capture_output=True, text=True,
            timeout=MVN_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # includes TimeoutExpired: bail out, caller degrades gracefully
        return [], "", False
    output = "\n".join((proc.stdout or "") + "\n" + (proc.stderr or ""))  # 2>&1
    tail = "\n".join(output.splitlines()[-200:])  # tail -200
    failing, _ = _parse_surefire_reports(project_path)
    if failing:
        return failing, tail[:FAILURE_SUMMARY_MAX_CHARS], True
    # fall back to parsing the mvn console itself
    failing = _COMPACT_RE.findall(tail)
    console_failing = [f"{cls}#{m}" for cls, m in failing]
    error_lines = [
        line for line in tail.splitlines()
        if "FAILED" in line or "ERROR" in line or "FAILURE" in line
    ][:20]
    return console_failing, "\n".join(error_lines)[:FAILURE_SUMMARY_MAX_CHARS], True


def discover_from_user_message(
    user_message: str,
    workspace_root: str,
    max_scan_depth: int = 5,
) -> DiscoveryResult:
    intent, profile = _match_intent(user_message)
    root = Path(workspace_root).expanduser()

    build_files = _scan_build_files(root, max_scan_depth)
    if not build_files:
        raise FileNotFoundError(f"no project found under {workspace_root}")
    # Most recently modified build file wins (the project the user touched last).
    chosen = max(build_files, key=lambda p: p.stat().st_mtime)
    project_path = chosen.parent
    build_tool = "maven" if chosen.name == "pom.xml" else "gradle"

    project_name, _package_prefix = _read_pom_meta(chosen)
    language = _detect_language(project_path, build_tool)
    repo_url = _detect_repo_url(project_path)

    failing_tests: list[str] = []
    failure_summary = ""
    confidence = 0.3  # baseline: nothing ran

    from_reports = _parse_surefire_reports(project_path)
    if from_reports[0]:
        failing_tests, failure_summary = from_reports
        confidence = 0.9  # Surefire reports found — best evidence
    else:
        # Fallback: one guarded `mvn test` run. Errors never propagate.
        failing_tests, failure_summary, ran = _run_mvn_tests(project_path)
        confidence = 0.7 if failing_tests else (0.5 if ran else 0.3)

    # Target: first failing test (class#method), else empty.
    recommended_target = failing_tests[0] if failing_tests else ""

    if intent is None:
        # Intent miss: keep the default profile but flag low confidence.
        confidence = min(confidence, 0.25)

    return DiscoveryResult(
        project_path=str(project_path),
        project_name=project_name,
        build_tool=build_tool,
        repo_url=repo_url,
        language=language,
        failing_tests=failing_tests,
        failure_summary=failure_summary,
        recommended_target=recommended_target,
        recommended_profile=profile,
        discovery_confidence=confidence,
    )


def discovery_result_to_dict(result: DiscoveryResult) -> dict:
    return asdict(result)
