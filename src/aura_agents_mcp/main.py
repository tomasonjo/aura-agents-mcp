"""
FastMCP server for Neo4j Aura Agents API (v2beta1).

Agent-friendly: only truly essential parameters are required. Everything else
has sensible defaults so an LLM can call these tools with minimal context.

Env vars:
  AURA_CLIENT_ID       - Aura API client ID          (required)
  AURA_CLIENT_SECRET   - Aura API client secret      (required)
  AURA_ORG_ID          - default organization UUID   (optional)
  AURA_PROJECT_ID      - default project UUID        (optional)
  AURA_BASE_URL        - override base URL           (optional)

Run:
  pip install fastmcp httpx
  python aura_agents_mcp.py
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional, Union

import httpx
from fastmcp import FastMCP

BASE_URL = os.getenv("AURA_BASE_URL", "https://api.neo4j.io/v2beta1")
TOKEN_URL = "https://api.neo4j.io/oauth/token"
CLIENT_ID = os.getenv("AURA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("AURA_CLIENT_SECRET", "")
DEFAULT_ORG = os.getenv("AURA_ORG_ID", "")
DEFAULT_PROJECT = os.getenv("AURA_PROJECT_ID", "")

mcp = FastMCP("aura-agents")

# --- auth ---------------------------------------------------------------

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


async def _get_token() -> str:
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] - 30 > now:
        return _token_cache["access_token"]
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("AURA_CLIENT_ID and AURA_CLIENT_SECRET are required.")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(CLIENT_ID, CLIENT_SECRET),
        )
        r.raise_for_status()
        p = r.json()
    _token_cache["access_token"] = p["access_token"]
    _token_cache["expires_at"] = now + float(p.get("expires_in", 3600))
    return _token_cache["access_token"]


async def _request(method: str, path: str, *, json: Optional[dict] = None) -> Any:
    token = await _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.request(method, f"{BASE_URL}{path}", headers=headers, json=json)
    if r.status_code == 204 or not r.content:
        return {"status": r.status_code, "ok": r.is_success}
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text}
    if not r.is_success:
        return {
            "error": True,
            "status": r.status_code,
            "request_id": r.headers.get("X-Request-Id"),
            "body": data,
        }
    return data


def _scope(org: Optional[str], project: Optional[str]) -> tuple[str, str]:
    o = org or DEFAULT_ORG
    p = project or DEFAULT_PROJECT
    if not o or not p:
        raise ValueError(
            "organization_id and project_id are required (or set "
            "AURA_ORG_ID / AURA_PROJECT_ID env vars)."
        )
    return o, p


# --- tools --------------------------------------------------------------

# -- organizations / projects / instances --------------------------------


@mcp.tool()
async def list_organizations() -> Any:
    """Get a list of all organizations the caller has access to."""
    return await _request("GET", "/organizations")


@mcp.tool()
async def get_organization(
    organization_id: Optional[str] = None,
) -> Any:
    """Get an organization by its ID.

    Args:
        organization_id: Organization UUID (defaults to AURA_ORG_ID).
    """
    o = organization_id or DEFAULT_ORG
    if not o:
        raise ValueError(
            "organization_id is required (or set AURA_ORG_ID env var)."
        )
    return await _request("GET", f"/organizations/{o}")


@mcp.tool()
async def list_projects(
    organization_id: Optional[str] = None,
) -> Any:
    """List all projects in an organization.

    Args:
        organization_id: Organization UUID (defaults to AURA_ORG_ID).
    """
    o = organization_id or DEFAULT_ORG
    if not o:
        raise ValueError(
            "organization_id is required (or set AURA_ORG_ID env var)."
        )
    return await _request("GET", f"/organizations/{o}/projects")


@mcp.tool()
async def list_instance_ip_filters(
    instance_id: str,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Returns a list of IP filters for an instance.

    Args:
        instance_id: The Aura instance ID.
        organization_id: Organization UUID (defaults to AURA_ORG_ID).
        project_id: Project UUID (defaults to AURA_PROJECT_ID).
    """
    o, p = _scope(organization_id, project_id)
    return await _request(
        "GET",
        f"/organizations/{o}/projects/{p}/instances/{instance_id}/ip-filters",
    )


# -- agents --------------------------------------------------------------


@mcp.tool()
async def list_agents(
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """List all agents in a project. Uses env defaults if IDs omitted."""
    o, p = _scope(organization_id, project_id)
    return await _request("GET", f"/organizations/{o}/projects/{p}/agents")


@mcp.tool()
async def get_agent(
    agent_id: str,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Fetch a single agent by ID."""
    o, p = _scope(organization_id, project_id)
    return await _request("GET", f"/organizations/{o}/projects/{p}/agents/{agent_id}")


@mcp.tool()
async def create_agent(
    name: str,
    dbid: str,
    description: str = "",
    tools: Optional[list[dict]] = None,
    system_prompt: Optional[str] = None,
    is_private: bool = False,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Create a new agent.

    Only `name` and `dbid` are required. If `tools` is omitted, the agent is
    created with a single default text2cypher tool that lets it query the
    database from natural language.

    Args:
        name: Agent display name.
        dbid: Target Aura database instance ID.
        description: Optional agent description.
        tools: Optional list of tool definitions. Defaults to one text2cypher
            tool. Each tool is a dict with at least `type` and `name`; see the
            Aura API schema for cypherTemplate / similaritySearch shapes.
        system_prompt: Optional system prompt.
        is_private: Whether the agent is private (default False).
        organization_id: Aura org UUID (defaults to AURA_ORG_ID).
        project_id: Aura project UUID (defaults to AURA_PROJECT_ID).
    """
    o, p = _scope(organization_id, project_id)
    body: dict[str, Any] = {
        "name": name,
        "description": description,
        "dbid": dbid,
        "is_private": is_private,
        "tools": tools
        or [
            {
                "type": "text2cypher",
                "name": "query",
                "description": "Convert natural language to Cypher and query the database.",
                "enabled": True,
            }
        ],
    }
    if system_prompt is not None:
        body["system_prompt"] = system_prompt
    return await _request(
        "POST", f"/organizations/{o}/projects/{p}/agents", json=body
    )


@mcp.tool()
async def update_agent(
    agent_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    dbid: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    system_prompt: Optional[str] = None,
    is_private: Optional[bool] = None,
    enabled: Optional[bool] = None,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Update an existing agent.

    Only `agent_id` is required. Any field you omit is carried over from the
    agent's current configuration (the API uses PUT, so this tool fetches the
    existing agent first and merges your changes).
    """
    o, p = _scope(organization_id, project_id)
    current = await _request(
        "GET", f"/organizations/{o}/projects/{p}/agents/{agent_id}"
    )
    if isinstance(current, dict) and current.get("error"):
        return current

    body: dict[str, Any] = {
        "name": name if name is not None else current.get("name", ""),
        "description": description
        if description is not None
        else current.get("description", ""),
        "dbid": dbid if dbid is not None else current.get("dbid", ""),
        "is_private": is_private
        if is_private is not None
        else current.get("is_private", False),
        "tools": tools if tools is not None else current.get("tools", []),
    }
    if system_prompt is not None:
        body["system_prompt"] = system_prompt
    elif current.get("system_prompt"):
        body["system_prompt"] = current["system_prompt"]
    if enabled is not None:
        body["enabled"] = enabled

    return await _request(
        "PUT", f"/organizations/{o}/projects/{p}/agents/{agent_id}", json=body
    )


@mcp.tool()
async def delete_agent(
    agent_id: str,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Delete an agent by ID."""
    o, p = _scope(organization_id, project_id)
    return await _request(
        "DELETE", f"/organizations/{o}/projects/{p}/agents/{agent_id}"
    )


@mcp.tool()
async def invoke_agent(
    agent_id: str,
    input: Union[str, list[dict]],
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Invoke an agent with a prompt.

    Args:
        agent_id: Agent UUID.
        input: Either a plain string (single user message) or a list of
            `{"role": "user", "content": "..."}` dicts.
    """
    o, p = _scope(organization_id, project_id)
    return await _request(
        "POST",
        f"/organizations/{o}/projects/{p}/agents/{agent_id}/invoke",
        json={"input": input},
    )


# -- instances -------------------------------------------------------------


@mcp.tool()
async def list_instances(
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Any:
    """Returns a list of instances in a project.

    Args:
        organization_id: Organization UUID (defaults to AURA_ORG_ID).
        project_id: Project UUID (defaults to AURA_PROJECT_ID).
    """
    o, p = _scope(organization_id, project_id)
    return await _request(
        "GET", f"/organizations/{o}/projects/{p}/instances"
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()