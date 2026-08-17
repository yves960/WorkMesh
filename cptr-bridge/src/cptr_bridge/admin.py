"""Admin UI routes (server-rendered Jinja2, no JS frameworks).

Sections: /admin/profiles, /admin/routing, /admin/forge-loop.
Success/error feedback uses a top flash banner driven by query params
(``?msg=...&kind=ok|err``) so the pages stay pure HTML + redirects.
"""
from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from . import forgeconfig_store, profile_service, routing_store

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).with_name("templates"))
)

router = APIRouter(prefix="/admin", include_in_schema=False)


def _redirect(url: str, msg: str, kind: str = "ok") -> RedirectResponse:
    separator = "&" if "?" in url else "?"
    quoted = urllib.parse.quote(msg)
    return RedirectResponse(f"{url}{separator}msg={quoted}&kind={kind}", status_code=303)


def _base_context(request: Request) -> dict:
    msg = request.query_params.get("msg", "")
    return {
        "request": request,
        "flash_msg": msg,
        "flash_kind": request.query_params.get("kind", "ok") if msg else "",
    }


def _lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


@router.get("")
async def admin_home() -> RedirectResponse:
    return RedirectResponse(url="/admin/profiles", status_code=303)


# ---------------------------------------------------------------- profiles


@router.get("/profiles")
async def profiles_page(request: Request):
    try:
        items = profile_service.list_profiles()
        profiles_error = ""
    except RuntimeError as error:
        items, profiles_error = [], str(error)
    return _TEMPLATES.TemplateResponse(
        request,
        "profiles.html",
        {**_base_context(request), "profiles": items, "profiles_error": profiles_error},
    )


def _form_to_document(form: dict) -> dict:
    verifier_type = (form.get("verifierType") or "").strip()
    document: dict = {
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
    }
    max_iterations = (form.get("maxIterations") or "").strip()
    if max_iterations:
        document["maxIterations"] = int(max_iterations)
    allowed = _lines(form.get("allowedPathSuffixes"))
    if allowed:
        document["allowedPathSuffixes"] = allowed
    bypass = _lines(form.get("forbiddenAddedPatterns"))
    if bypass:
        document["forbiddenAddedPatterns"] = bypass
    required = _lines(form.get("requiredPaths"))
    if required:
        document["requiredPaths"] = required
    if verifier_type:
        verifier: dict = {"type": verifier_type}
        arguments = _lines(form.get("verifierArguments"))
        if arguments:
            verifier["arguments"] = arguments
        pass_reason = (form.get("passReason") or "").strip()
        if pass_reason:
            verifier["passReason"] = pass_reason
        fail_reason = (form.get("failReason") or "").strip()
        if fail_reason:
            verifier["failReason"] = fail_reason
        document["verifier"] = verifier
    if form.get("requireVerifierPass"):
        document["completion"] = {"requireVerifierPass": True}
    return document


def _int_or_none(raw: str) -> int | None:
    raw = (raw or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def _handle_profile_post(
    request: Request, original_name: str | None
) -> RedirectResponse:
    form = dict(await request.form())
    name = str(form.get("name") or original_name or "").strip()
    back = f"/admin/profiles/{original_name or name}/edit"
    try:
        max_raw = form.get("maxIterations", "")
        parsed_max = _int_or_none(str(max_raw))
        if str(max_raw).strip() and parsed_max is None:
            raise profile_service.ProfileError("maxIterations 必须是整数")
        form["maxIterations"] = str(parsed_max or "")
        document = _form_to_document(form)
        profile_service.save_profile(name, document)
        if original_name and original_name != name:
            profile_service.delete_profile(original_name)
    except profile_service.ProfileError as error:
        return _redirect(back, str(error), "err")
    except (ValueError, TypeError) as error:
        return _redirect(back, f"表单数据非法: {error}", "err")
    return _redirect("/admin/profiles", f"profile「{name}」已保存")


@router.get("/profiles/new")
async def profile_new_page(request: Request):
    return _TEMPLATES.TemplateResponse(
        request, "profile_form.html",
        {**_base_context(request), "mode": "new", "profile": {}, "name": ""},
    )


@router.post("/profiles/new")
async def profile_new_submit(request: Request):
    return await _handle_profile_post(request, None)


@router.get("/profiles/{name}/edit")
async def profile_edit_page(request: Request, name: str):
    try:
        profile = profile_service.load_profile(name)
    except profile_service.ProfileError as error:
        return _redirect("/admin/profiles", str(error), "err")
    return _TEMPLATES.TemplateResponse(
        request,
        "profile_form.html",
        {**_base_context(request), "mode": "edit", "profile": profile, "name": name},
    )


@router.post("/profiles/{name}/edit")
async def profile_edit_submit(request: Request, name: str):
    return await _handle_profile_post(request, name)


@router.post("/profiles/{name}/delete")
async def profile_delete_submit(name: str):
    try:
        profile_service.delete_profile(name)
    except profile_service.ProfileError as error:
        return _redirect("/admin/profiles", str(error), "err")
    return _redirect("/admin/profiles", f"profile「{name}」已删除")


# ---------------------------------------------------------------- routing


@router.get("/routing")
async def routing_page(request: Request):
    routing_store.ensure_default()
    routing = routing_store.load_routing()
    try:
        known = {meta.name for meta in profile_service.list_profiles()}
    except RuntimeError:
        known = set()
    rules = [
        {**rule, "missing": rule.get("profile") not in known if known else False}
        for rule in routing["rules"]
    ]
    return _TEMPLATES.TemplateResponse(
        request,
        "routing.html",
        {**_base_context(request), "rules": rules, "profiles": sorted(known)},
    )


@router.post("/routing/add")
async def routing_add(request: Request):
    form = await request.form()
    keywords = _lines(str(form.get("keywords", "")))
    profile = str(form.get("profile", "")).strip()
    try:
        known = {meta.name for meta in profile_service.list_profiles()}
    except RuntimeError:
        known = set()
    try:
        routing_store.add_rule({"keywords": keywords, "profile": profile}, known)
    except routing_store.RoutingError as error:
        return _redirect("/admin/routing", str(error), "err")
    return _redirect("/admin/routing", f"规则已添加: {profile}")


@router.post("/routing/delete/{index}")
async def routing_delete(index: int):
    try:
        routing_store.delete_rule(index)
    except routing_store.RoutingError as error:
        return _redirect("/admin/routing", str(error), "err")
    return _redirect("/admin/routing", "规则已删除")


# ---------------------------------------------------------------- forge-loop


@router.get("/forge-loop")
async def forge_loop_page(request: Request):
    try:
        current = forgeconfig_store.current_settings()
        error = ""
    except forgeconfig_store.ForgeConfigError as err:
        current = {
            "webhookUrl": "",
            "globalMaxIterations": 10,
            "dryRunDefault": False,
            "configPath": "",
        }
        error = str(err)
    return _TEMPLATES.TemplateResponse(
        request,
        "forge_loop.html",
        {**_base_context(request), "current": current, "config_error": error},
    )


@router.post("/forge-loop")
async def forge_loop_submit(request: Request):
    form = await request.form()
    webhook = str(form.get("webhookUrl", ""))
    parsed = _int_or_none(str(form.get("globalMaxIterations", "")))
    dry_run = bool(form.get("dryRunDefault"))
    try:
        if parsed is None:
            raise forgeconfig_store.ForgeConfigError("globalMaxIterations 必须是整数")
        forgeconfig_store.save_settings(webhook, parsed, dry_run)
    except forgeconfig_store.ForgeConfigError as error:
        return _redirect("/admin/forge-loop", str(error), "err")
    return _redirect("/admin/forge-loop", "ForgeLoop 全局配置已保存")
