"""Admin UI route tests (profiles / routing / forge-loop) via TestClient.

All stores are pointed at temp dirs through env vars (settings.py reads the
environment dynamically), so nothing here touches the real ForgeLoop tree.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cptr_bridge.server import app

VALID_PROFILE = {
    "name": "codecheck-fixer",
    "description": "Repair deterministic Maven code-quality gate failures.",
    "maxIterations": 10,
    "allowedPathSuffixes": ["src/main/", "src/test/"],
    "forbiddenAddedPatterns": ["CHECKSTYLE.OFF", "NOSONAR"],
    "verifier": {
        "type": "maven",
        "arguments": ["-DskipTests", "verify", "-Pcodecheck"],
        "passReason": "CODECHECK_PASSED",
        "failReason": "CODECHECK_FAILED",
    },
    "completion": {"requireVerifierPass": True},
}


@pytest.fixture()
def admin_env(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    loop_root = tmp_path / "loop-engineering"
    loop_root.mkdir()
    monkeypatch.setenv("CTR_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("CTR_STATE_DIR", str(state))
    monkeypatch.setenv("CTR_LOOP_CONFIG", str(loop_root / "config.json"))
    monkeypatch.setenv("CTR_FORGELOOP_SRC", str(tmp_path / "nonexistent"))
    return {"profiles": profiles, "state": state, "config": loop_root / "config.json"}


@pytest.fixture()
def client(admin_env):
    with TestClient(app) as test_client:
        yield test_client


def _seed_profile(profiles_dir: Path, document: dict) -> Path:
    target = profiles_dir / document["name"]
    target.mkdir(parents=True, exist_ok=True)
    path = target / "profile.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


# ------------------------------------------------------------- profiles


def test_list_profiles_shows_seeded_entry(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.get("/admin/profiles")
    assert response.status_code == 200
    assert "codecheck-fixer" in response.text
    assert "maven" in response.text


def test_new_profile_form_renders(client):
    response = client.get("/admin/profiles/new")
    assert response.status_code == 200
    assert 'action="/admin/profiles/new"' in response.text


def test_create_valid_profile(client, admin_env):
    response = client.post(
        "/admin/profiles/new",
        data={
            "name": "spotless-fixer",
            "description": "Fix formatting",
            "maxIterations": "7",
            "allowedPathSuffixes": "src/main/\nsrc/test/",
            "forbiddenAddedPatterns": "@SuppressWarnings(\"all\")",
            "verifierType": "maven",
            "verifierArguments": "-DskipTests\nverify",
            "passReason": "SPOTLESS_PASSED",
            "failReason": "SPOTLESS_FAILED",
            "requireVerifierPass": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]
    saved = json.loads(
        (admin_env["profiles"] / "spotless-fixer" / "profile.json").read_text()
    )
    assert saved["maxIterations"] == 7
    assert saved["verifier"]["type"] == "maven"
    assert saved["verifier"]["arguments"] == ["-DskipTests", "verify"]
    assert saved["completion"] == {"requireVerifierPass": True}
    assert saved["allowedPathSuffixes"] == ["src/main/", "src/test/"]


def test_create_profile_invalid_verifier_type(client, admin_env):
    response = client.post(
        "/admin/profiles/new",
        data={"name": "bad-verifier", "maxIterations": "5", "verifierType": "gradle"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]
    assert not (admin_env["profiles"] / "bad-verifier").exists()


def test_create_profile_invalid_name(client, admin_env):
    response = client.post(
        "/admin/profiles/new",
        data={"name": "Bad_Name!", "maxIterations": "5", "verifierType": "maven"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]


def test_create_profile_non_integer_iterations(client, admin_env):
    response = client.post(
        "/admin/profiles/new",
        data={"name": "bad-int", "maxIterations": "abc", "verifierType": "maven"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]


def test_update_profile(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.post(
        "/admin/profiles/codecheck-fixer/edit",
        data={
            "name": "codecheck-fixer",
            "description": "Updated description",
            "maxIterations": "12",
            "allowedPathSuffixes": "src/main/",
            "verifierType": "maven",
            "verifierArguments": "verify",
            "passReason": "PASSED",
            "failReason": "FAILED",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=ok" in response.headers["location"]
    saved = json.loads(
        (admin_env["profiles"] / "codecheck-fixer" / "profile.json").read_text()
    )
    assert saved["maxIterations"] == 12
    assert saved["description"] == "Updated description"
    assert "completion" not in saved  # unchecked box drops the key


def test_update_profile_rename_moves_directory(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.post(
        "/admin/profiles/codecheck-fixer/edit",
        data={"name": "codecheck-fixer-v2", "maxIterations": "5"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (admin_env["profiles"] / "codecheck-fixer-v2" / "profile.json").exists()
    assert not (admin_env["profiles"] / "codecheck-fixer" / "profile.json").exists()


def test_edit_form_renders_existing_values(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.get("/admin/profiles/codecheck-fixer/edit")
    assert response.status_code == 200
    assert 'value="codecheck-fixer"' in response.text
    assert "CODECHECK_PASSED" in response.text


def test_delete_profile(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.post(
        "/admin/profiles/codecheck-fixer/delete", follow_redirects=False
    )
    assert response.status_code == 303
    assert "kind=ok" in response.headers["location"]
    assert not (admin_env["profiles"] / "codecheck-fixer").exists()


def test_delete_missing_profile_errors(client):
    response = client.post("/admin/profiles/nope/delete", follow_redirects=False)
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]


def test_admin_home_redirects_to_profiles(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/profiles")


# ------------------------------------------------------------- routing


def test_routing_seeds_default_template(client, admin_env):
    response = client.get("/admin/routing")
    assert response.status_code == 200
    store = json.loads((admin_env["state"] / "routing.json").read_text())
    assert len(store["rules"]) == 5
    assert store["rules"][0]["profile"] == "codecheck-fixer"
    for needle in ("codecheck-fixer", "java-ut-fixer", "sast-fixer",
                   "sca-upgrader", "dependency-upgrader"):
        assert needle in response.text


def test_routing_add_rule(client, admin_env):
    _seed_profile(admin_env["profiles"], VALID_PROFILE)
    response = client.post(
        "/admin/routing/add",
        data={"keywords": "格式化\nspotless", "profile": "codecheck-fixer"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=ok" in response.headers["location"]
    store = json.loads((admin_env["state"] / "routing.json").read_text())
    assert {"keywords": ["格式化", "spotless"], "profile": "codecheck-fixer"} in (
        store["rules"]
    )


def test_routing_add_rule_rejects_unknown_profile(client, admin_env):
    response = client.post(
        "/admin/routing/add",
        data={"keywords": "anything", "profile": "ghost-profile"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]


def test_routing_delete_rule(client, admin_env):
    client.get("/admin/routing")  # seed defaults
    response = client.post("/admin/routing/delete/0", follow_redirects=False)
    assert response.status_code == 303
    store = json.loads((admin_env["state"] / "routing.json").read_text())
    assert len(store["rules"]) == 4
    assert store["rules"][0]["profile"] != "codecheck-fixer"


# ------------------------------------------------------------- forge-loop


def test_forge_loop_defaults_render(client):
    response = client.get("/admin/forge-loop")
    assert response.status_code == 200
    assert "globalMaxIterations" in response.text


def test_forge_loop_save_and_load(client, admin_env):
    admin_env["config"].write_text(
        json.dumps({"agent": {"command": "/bin/opencode", "protocol": "opencode"}}),
        encoding="utf-8",
    )
    response = client.post(
        "/admin/forge-loop",
        data={"webhookUrl": "https://hooks.example/loop", "globalMaxIterations": "6"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=ok" in response.headers["location"]
    saved = json.loads(admin_env["config"].read_text())
    assert saved["hooks"]["onComplete"] == "https://hooks.example/loop"
    assert saved["globalMaxIterations"] == 6
    assert saved["dryRunDefault"] is False
    assert saved["agent"]["command"] == "/bin/opencode"  # untouched keys preserved

    page = client.get("/admin/forge-loop")
    assert "https://hooks.example/loop" in page.text


def test_forge_loop_save_dry_run_flag(client, admin_env):
    response = client.post(
        "/admin/forge-loop",
        data={"globalMaxIterations": "3", "dryRunDefault": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = json.loads(admin_env["config"].read_text())
    assert saved["dryRunDefault"] is True
    assert "onComplete" not in saved["hooks"]  # empty URL disables the hook


def test_forge_loop_rejects_bad_webhook(client, admin_env):
    response = client.post(
        "/admin/forge-loop",
        data={"webhookUrl": "ftp://nope", "globalMaxIterations": "3"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]
    assert not admin_env["config"].exists()  # nothing written


def test_forge_loop_rejects_bad_iterations(client, admin_env):
    response = client.post(
        "/admin/forge-loop",
        data={"webhookUrl": "https://ok.example/x", "globalMaxIterations": "zzz"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kind=err" in response.headers["location"]
