"""Task 2: discovery — keyword intent + workspace scan + surefire parsing.

Covers: 5 intents, fallback, surefire parsing, multi-failure ordering,
mvn fallback (ok/fail/timeout), 404, and the API route.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from personal_ai_control_plane import discovery
from personal_ai_control_plane.api import app
from personal_ai_control_plane.discovery import (
    _match_intent,
    discover_from_user_message,
)

client = TestClient(app)


def _make_project(tmp_path: Path, *, pom: str = "com.demo", artifact: str = "demo") -> Path:
    project = tmp_path / "demo-java-ut"
    (project / "src").mkdir(parents=True)
    (project / "pom.xml").write_text(
        f"<project><groupId>{pom}</groupId>"
        f"<artifactId>{artifact}</artifactId><version>0.0.1</version></project>"
    )
    return project


def _write_surefire(project: Path, content: str, name: str = "TEST-com.demo.X.txt") -> None:
    reports = project / "target" / "surefire-reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / name).write_text(content)


# ---- intent keyword table ---------------------------------------------------

def test_intent_keywords_all_five():
    cases = {
        "UT 挂了，帮我修": ("FIX_TEST", "java-ut-fixer"),
        "checkstyle check 报警": ("FIX_STYLE", "codecheck-fixer"),
        "SAST 发现一个 security 漏洞 CVE": ("FIX_SECURITY", "sast-fixer"),
        "依赖升级到新版 spring": ("UPGRADE_DEPS", "dependency-upgrader"),
        "SCA 组件漏洞": ("FIX_SCA", "sca-upgrader"),
    }
    for msg, expected in cases.items():
        assert _match_intent(msg) == expected


def test_intent_first_match_wins():
    # contains both "test" (FIX_TEST) and "check" (FIX_STYLE): FIX_TEST wins
    assert _match_intent("test check 失败")[0] == "FIX_TEST"


def test_intent_miss_falls_back_to_default(tmp_path):
    project = _make_project(tmp_path)
    _write_surefire(project, "Tests run: 1, Failures: 0\n")
    result = discover_from_user_message("随便聊聊", str(tmp_path))
    assert result.recommended_profile == "java-ut-fixer"
    assert result.discovery_confidence <= 0.3  # lowest confidence on miss


# ---- surefire parsing ------------------------------------------------------

def test_surefire_report_parsing(tmp_path):
    project = _make_project(tmp_path)
    _write_surefire(
        project,
        "Tests run: 3, Failures: 1\n"
        "com.demo.UserServiceTest#testLogin FAILED\n"
        "NullPointerException at line 42\n",
    )
    result = discover_from_user_message("UT 挂了", str(tmp_path))
    assert result.failing_tests == ["com.demo.UserServiceTest#testLogin"]
    assert result.recommended_target == "com.demo.UserServiceTest#testLogin"
    # 蔚 2026-11-24 P3-2 新契约：仅 txt 正则命中降 0.7（XML 结构化才配 0.9）
    assert result.discovery_confidence == 0.7
    assert "testLogin" in result.failure_summary


def test_surefire_real_format_parsing(tmp_path):
    project = _make_project(tmp_path)
    _write_surefire(
        project,
        "Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\n"
        "testLogin(com.demo.UserServiceTest)  Time elapsed: 0.01 s  <<< FAILURE!\n",
    )
    result = discover_from_user_message("test failed", str(tmp_path))
    assert result.failing_tests == ["com.demo.UserServiceTest#testLogin"]


def test_multiple_failed_tests_first_wins(tmp_path):
    project = _make_project(tmp_path)
    _write_surefire(
        project,
        "com.demo.AServiceTest#testA FAILED\n"
        "com.demo.BServiceTest#testB FAILED\n"
        "com.demo.CServiceTest#testC FAILED\n"
    )
    result = discover_from_user_message("测试失败", str(tmp_path))
    assert result.failing_tests[0] == "com.demo.AServiceTest#testA"
    assert result.recommended_target == "com.demo.AServiceTest#testA"
    assert len(result.failing_tests) == 3


# ---- mvn fallback ----------------------------------------------------------

class _Proc:
    def __init__(self, stdout: str, stderr: str = ""):
        self.stdout, self.stderr = stdout, stderr


def test_mvn_fallback_runs_and_finds_no_failures(tmp_path, monkeypatch):
    _make_project(tmp_path)  # no surefire reports on disk
    monkeypatch.setattr(
        discovery.subprocess, "run", lambda *a, **k: _Proc("Tests run: 5, Failures: 0")
    )
    result = discover_from_user_message("UT 挂了", str(tmp_path))
    assert result.discovery_confidence == 0.5
    assert result.failing_tests == []
    assert result.recommended_target == ""


def test_mvn_fallback_exception_degrades(tmp_path, monkeypatch):
    _make_project(tmp_path)

    def boom(*a, **k):
        raise OSError("mvn not installed")

    monkeypatch.setattr(discovery.subprocess, "run", boom)
    result = discover_from_user_message("UT 挂了", str(tmp_path))
    assert result.discovery_confidence == 0.3
    assert result.failing_tests == []


def test_mvn_timeout_does_not_hang(tmp_path, monkeypatch):
    _make_project(tmp_path)

    def slow(*a, **k):
        # Emulate subprocess.run honoring `timeout`: a 0s budget expires now.
        raise subprocess.TimeoutExpired(cmd="mvn test", timeout=k.get("timeout", 0))

    monkeypatch.setattr(discovery.subprocess, "run", slow)
    monkeypatch.setattr(discovery, "MVN_TIMEOUT_SECONDS", 0)  # force timeout fast
    started = time.monotonic()
    result = discover_from_user_message("test red", str(tmp_path))
    assert time.monotonic() - started < 5
    assert result.discovery_confidence == 0.3


def test_no_project_returns_404(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    resp = client.post(
        "/api/v1/discovery", json={"user_message": "UT 挂了", "workspace_root": str(empty)}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no project found under workspace_root"


# ---- API route -------------------------------------------------------------

def test_api_route_requires_fields():
    resp = client.post("/api/v1/discovery", json={"user_message": "", "workspace_root": ""})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "user_message and workspace_root required"


def test_api_route_full_roundtrip(tmp_path):
    project = _make_project(tmp_path, artifact="demo-java-ut")
    _write_surefire(
        project,
        "com.demo.UserServiceTest#testLogin FAILED\nNullPointerException at line 42\n",
    )
    resp = client.post(
        "/api/v1/discovery",
        json={"user_message": "UT 挂了，帮我修", "workspace_root": str(tmp_path)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_path"] == str(project)
    assert data["project_name"] == "demo-java-ut"
    assert data["build_tool"] == "maven"
    assert data["language"] == "java"
    assert "com.demo.UserServiceTest#testLogin" in data["failing_tests"]
    assert data["recommended_target"] == "com.demo.UserServiceTest#testLogin"
    assert data["recommended_profile"] == "java-ut-fixer"
    assert data["discovery_confidence"] == 0.7  # txt-only 新契约（蔚 2026-11-24 P3-2）


def test_surefire_xml_structural_parse(tmp_path, monkeypatch):
    """蔚 2026-11-24 P3-2：XML 走结构化——<failure> 元素此前逐行正则一行都匹配不上。"""
    from personal_ai_control_plane.discovery import _parse_surefire_reports

    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-com.example.demo.OrderServiceTest.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<testsuite name="com.example.demo.OrderServiceTest" tests="2" failures="1">'
        '<testcase classname="com.example.demo.OrderServiceTest" name="testShip" time="0.01">'
        '<failure message="assertion failed" type="java.lang.AssertionError">boom</failure>'
        "</testcase>"
        '<testcase classname="com.example.demo.OrderServiceTest" name="testCancel" time="0.01"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    failing, summary, stats = _parse_surefire_reports(tmp_path)
    assert failing == ["com.example.demo.OrderServiceTest#testShip"]
    assert "XML" in summary
    assert stats["xml_hits"] == 1 and stats["txt_hits"] == 0


def test_txt_only_hits_downgrade_confidence(tmp_path, monkeypatch):
    """蔚 2026-11-24 P3-2：仅 txt 正则命中——降置信度 + 来源注明，手植 mock 不冒充高置信。"""
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-com.demo.UserServiceTest.txt").write_text(
        "Tests run: 1, Failures: 1\ncom.demo.UserServiceTest#testLogin FAILED\n",
        encoding="utf-8",
    )
    from personal_ai_control_plane.discovery import _parse_surefire_reports

    failing, summary, stats = _parse_surefire_reports(tmp_path)
    assert failing == ["com.demo.UserServiceTest#testLogin"]
    assert stats["xml_hits"] == 0 and stats["txt_hits"] == 1
    # 端到端 txt-only 降级真锁：把 tmp 做成 workspace 可扫的项目
    import shutil as _sh

    ws = tmp_path / "ws-root"
    ws.mkdir()
    _sh.copytree(tmp_path / "target", ws / "target", dirs_exist_ok=True)
    (ws / "pom.xml").write_text(
        "<project><groupId>com.demo</groupId><artifactId>demo</artifactId><version>0</version></project>",
        encoding="utf-8",
    )
    result = discover_from_user_message("帮我修失败的 UT", workspace_root=str(ws))
    assert result.discovery_confidence == 0.7
    assert "仅 .txt 正则命中" in result.failure_summary


def test_forgeloop_project_path_fallback_chain(tmp_path, monkeypatch):
    """蔚 2026-11-24 P3-1：inputs.project_path 回退链——无 discovery 步的流不再 400。"""
    import asyncio
    import types

    from personal_ai_control_plane.workflow import steps as wf_steps

    captured = {}

    async def fake_post(path, payload):
        if path == "/api/v1/execution/plan":
            captured["plan_payload"] = payload
            return {"plan_id": "plan-test-1"}
        if path == "/api/v1/execution/confirm":
            return {"status": "launched", "execution_id": "loop-exec-1", "run_id": "run-1"}
        return {}

    monkeypatch.setattr(wf_steps, "_post", fake_post)
    ctx = types.SimpleNamespace(
        context={},
        inputs={"project_path": "/Users/sy/Projects/self/demo-java-ut"},
    )
    result = asyncio.run(wf_steps.forgeloop_step(ctx))
    assert result["result"] == "launched"
    # plan 载荷的 discovery.project_path 必须来自 inputs 回退链
    assert captured["plan_payload"]["discovery"]["project_path"] == "/Users/sy/Projects/self/demo-java-ut"
