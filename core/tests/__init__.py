"""AF-26 Core trust unit suite (stdlib unittest, no parallel test framework)."""


def trusted_request(**kwargs):
    """Build a ToolRequest with a REAL, bounded server-issued capability.

    Tests exercise the production identity gate; this helper only mints a
    capability through the trusted server issuer boundary, it does not bypass
    validation.
    """
    from core import panelauth, tool_executor as tx, tool_identity, tool_registry
    if not kwargs.get("role_id") and not kwargs.get("capability_token"):
        project = kwargs.get("project") or ""
        tool_id = kwargs.get("tool_id") or ""
        scopes = kwargs.get("scopes") or []
        spec = tool_registry.registry().get(tool_id)
        scope = spec.scope if spec else (scopes[0] if scopes else "project:read")
        kwargs["capability_token"] = tool_identity.issue_service_capability(
            "infrastructure", project, tool_id, scope,
            session_id=trusted_session())
    return tx.ToolRequest(**kwargs)


_TEST_PASSWORD = "phase04-local-test-only"


def trusted_session() -> str:
    """Provision a real server-side panel session for tests.

    No password file is created, so other auth tests retain their own setup;
    validation still goes through the production principal_for_session().
    """
    import secrets as _secrets
    import time as _time
    from core import panelauth
    global _TEST_SESSION
    if ("_TEST_SESSION" not in globals() or
            not panelauth.principal_for_session(_TEST_SESSION)):
        token = "test-session-" + _secrets.token_urlsafe(12)
        panelauth._sessions[token] = {"exp": _time.time() + 86400,
                                      "csrf": "test-csrf",
                                      "created": _time.time()}
        _TEST_SESSION = token
    return _TEST_SESSION


_RUN_TOKENS: dict = {}


def specialist_run_token(project: str, role: str) -> str:
    """Create/reuse a real canonical lease token for test role requests."""
    from core import specialist_engine as se
    key = (str(se._STORE), str(project), str(role))
    if key not in _RUN_TOKENS:
        _RUN_TOKENS[key] = se.start_run(project, role)["token"]
    return _RUN_TOKENS[key]
