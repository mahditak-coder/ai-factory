"""AF-26 Core trust unit suite (stdlib unittest, no parallel test framework)."""


def trusted_request(**kwargs):
    """Build a ToolRequest with a REAL, bounded server-issued capability.

    Tests exercise the production identity gate; this helper only mints a
    capability through the trusted server issuer boundary, it does not bypass
    validation.
    """
    from core import tool_executor as tx, tool_identity, tool_registry
    if not kwargs.get("role_id") and not kwargs.get("capability_token"):
        project = kwargs.get("project") or ""
        tool_id = kwargs.get("tool_id") or ""
        scopes = kwargs.get("scopes") or []
        spec = tool_registry.registry().get(tool_id)
        scope = spec.scope if spec else (scopes[0] if scopes else "project:read")
        kwargs["capability_token"] = tool_identity.issue_infrastructure_capability(
            project, tool_id, scope,
            issuer_key=tool_identity.server_issuer_key())
    return tx.ToolRequest(**kwargs)
