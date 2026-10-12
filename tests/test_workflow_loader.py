"""Workflow loader: valid load, missing file, schema errors with field paths,
step-type registry defense, and the shipped java-ut-repair.yaml."""

from __future__ import annotations

import pytest
from workflow_testkit import REPO_ROOT, wf_env, write_workflow  # noqa: F401

from personal_ai_control_plane.workflow import loader, steps
from personal_ai_control_plane.workflow.loader import (
    WorkflowLoadError,
    load_workflow,
    matches_trigger,
)

VALID_YAML = """
name: tiny
version: 1
description: tiny workflow
triggers:
  - on_user_message: {keywords_any: ["UT"]}
steps:
  - id: knowledge
    type: knowledge
    inputs: {query: "q"}
    timeout_seconds: 30
  - id: notify
    type: notify
    inputs: {channel: "feishu"}
    timeout_seconds: 10
"""


def test_load_valid_workflow(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "tiny", VALID_YAML)
    defn = load_workflow("tiny")
    assert defn.name == "tiny"
    assert [s.id for s in defn.steps] == ["knowledge", "notify"]
    assert defn.steps[0].timeout_seconds == 30


def test_missing_file_raises_file_not_found(wf_env):  # noqa: F811
    with pytest.raises(FileNotFoundError):
        load_workflow("does-not-exist")


def test_schema_error_carries_field_path(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "bad", VALID_YAML.replace("timeout_seconds: 30", 'timeout_seconds: "abc"'))
    with pytest.raises(WorkflowLoadError) as excinfo:
        load_workflow("bad")
    assert "steps[0].timeout_seconds" in str(excinfo.value)
    assert excinfo.value.path == "steps[0].timeout_seconds"


def test_missing_name_is_schema_error(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "noname", "version: 1\nsteps:\n  - {id: a, type: notify}\n")
    with pytest.raises(WorkflowLoadError) as excinfo:
        load_workflow("noname")
    assert "name" in str(excinfo.value)


def test_unknown_step_type_rejected(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "ghost", VALID_YAML.replace("type: knowledge", "type: nonexistent"))
    with pytest.raises(WorkflowLoadError) as excinfo:
        load_workflow("ghost")
    assert "steps[0].type" in str(excinfo.value)


def test_all_spec_step_types_are_registered(wf_env):  # noqa: F811
    spec_types = {"knowledge", "context", "discovery", "issue", "approval",
                  "forgeloop", "sync_multica", "notify"}
    assert spec_types <= set(steps.STEP_REGISTRY)
    # every step type in the shipped workflow resolves to a factory
    defn = load_workflow("java-ut-repair")
    for step in defn.steps:
        assert step.type in steps.STEP_REGISTRY, step.type


def test_java_ut_repair_loads(wf_env):  # noqa: F811
    defn = load_workflow("java-ut-repair")
    assert defn.name == "java-ut-repair"
    assert [s.id for s in defn.steps] == [
        "knowledge", "context", "discovery", "issue",
        "approval", "forgeloop", "sync_multica", "notify",
    ]
    assert defn.steps[5].requires_approval is True
    assert defn.steps[5].timeout_seconds == 600


def test_trigger_matching(wf_env):  # noqa: F811
    defn = load_workflow("java-ut-repair")
    assert matches_trigger(defn, {"user_message": "UT 挂了，帮我修"}) is True
    assert matches_trigger(defn, {"user_message": "今天天气不错"}) is False
    assert matches_trigger(
        defn, {"issue_id": "MUL-1", "issue_status": "READY_FOR_AI"}
    ) is True
    assert matches_trigger(defn, {"issue_id": "MUL-1", "issue_status": "DONE"}) is False


def test_hot_cache_returns_same_object(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "tiny", VALID_YAML)
    assert load_workflow("tiny") is load_workflow("tiny")
    loader.clear_loader_cache()
    assert load_workflow("tiny") is not None  # reload after invalidation works
