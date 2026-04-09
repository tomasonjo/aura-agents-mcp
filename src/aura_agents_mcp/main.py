"""
FastMCP server for Neo4j Aura Agents API (v2beta1).

Env vars:
  AURA_CLIENT_ID       - Aura API client ID          (required)
  AURA_CLIENT_SECRET   - Aura API client secret      (required)
  AURA_BASE_URL        - override base URL           (optional)

Run:
  pip install fastmcp httpx
  python aura_agents_mcp.py
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional, Union

import httpx
from fastmcp import FastMCP

BASE_URL = os.getenv("AURA_BASE_URL", "https://api.neo4j.io/v2beta1")
TOKEN_URL = "https://api.neo4j.io/oauth/token"
CLIENT_ID = os.getenv("AURA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("AURA_CLIENT_SECRET", "")

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


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Any:
    token = await _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
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


# --- tools --------------------------------------------------------------

# -- organizations / projects / instances --------------------------------


@mcp.tool()
async def list_organizations() -> Any:
    """Get a list of all organizations the caller has access to."""
    return await _request("GET", "/organizations")


@mcp.tool()
async def get_organization(
    organization_id: str,
) -> Any:
    """Get an organization by its ID.

    Args:
        organization_id: Organization UUID.
    """
    return await _request("GET", f"/organizations/{organization_id}")


@mcp.tool()
async def list_projects(
    organization_id: str,
) -> Any:
    """List all projects in an organization.

    Args:
        organization_id: Organization UUID.
    """
    return await _request("GET", f"/organizations/{organization_id}/projects")


@mcp.tool()
async def list_instance_ip_filters(
    instance_id: str,
    organization_id: str,
    project_id: str,
) -> Any:
    """Returns a list of IP filters for an instance.

    Args:
        instance_id: The Aura instance ID.
        organization_id: Organization UUID.
        project_id: Project UUID.
    """
    return await _request(
        "GET",
        f"/organizations/{organization_id}/projects/{project_id}/instances/{instance_id}/ip-filters",
    )


# -- agents --------------------------------------------------------------


@mcp.tool()
async def list_agents(
    organization_id: str,
    project_id: str,
) -> Any:
    """List all agents in a project."""
    return await _request("GET", f"/organizations/{organization_id}/projects/{project_id}/agents")


@mcp.tool()
async def get_agent(
    agent_id: str,
    organization_id: str,
    project_id: str,
) -> Any:
    """Fetch a single agent by ID."""
    return await _request("GET", f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}")


@mcp.tool()
async def create_agent(
    name: str,
    dbid: str,
    description: str = "",
    tools: Optional[list[dict]] = None,
    system_prompt: Optional[str] = None,
    is_private: bool = False,
    organization_id: str = "",
    project_id: str = "",
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
        organization_id: Aura org UUID.
        project_id: Aura project UUID.
    """
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
        "POST",
        f"/organizations/{organization_id}/projects/{project_id}/agents",
        json=body,
        extra_headers={"Organization-Id": organization_id},
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
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Update an existing agent.

    Only `agent_id` is required. Any field you omit is carried over from the
    agent's current configuration (the API uses PUT, so this tool fetches the
    existing agent first and merges your changes).
    """
    current = await _request(
        "GET", f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}"
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
        "PUT",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}",
        json=body,
        extra_headers={"Organization-Id": organization_id},
    )


@mcp.tool()
async def delete_agent(
    agent_id: str,
    organization_id: str,
    project_id: str,
) -> Any:
    """Delete an agent by ID."""
    return await _request(
        "DELETE",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}",
        extra_headers={"Organization-Id": organization_id},
    )


@mcp.tool()
async def invoke_agent(
    agent_id: str,
    input: Union[str, list[dict]],
    organization_id: str,
    project_id: str,
) -> Any:
    """Invoke an agent with a prompt.

    Args:
        agent_id: The ID of the agent to invoke.
        input: Either a plain string (single user message) or a list of
            `{"role": "user", "content": "..."}` dicts.
        organization_id: Organization UUID.
        project_id: Project UUID.
    """
    return await _request(
        "POST",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}/invoke",
        json={"input": input},
        extra_headers={"Organization-Id": organization_id},
    )


# -- schema ----------------------------------------------------------------


@mcp.tool()
async def get_schema(
    dbid: str,
    organization_id: str,
    project_id: str,
) -> Any:
    """Get the schema of a Neo4j database.

    Creates a temporary cypher_template agent, invokes it to fetch the schema,
    then deletes the agent in the background.

    Args:
        dbid: Target Aura database instance ID.
        organization_id: Organization UUID.
        project_id: Project UUID.
    """
    base = f"/organizations/{organization_id}/projects/{project_id}/agents"

    # 1. Create a temporary cypher_template agent
    agent = await _request(
        "POST",
        base,
        json={
            "name": "_schema_probe",
            "description": "Temporary agent for schema retrieval",
            "dbid": dbid,
            "is_private": False,
            "tools": [
                {
                    "type": "cypher_template",
                    "name": "get_schema",
                    "description": "Fetch the database schema.",
                    "cypher_template": "CALL apoc.meta.schema() YIELD value RETURN value",
                    "enabled": True,
                }
            ],
        },
        extra_headers={"Organization-Id": organization_id},
    )
    if isinstance(agent, dict) and agent.get("error"):
        return agent

    agent_id = agent.get("id")
    if not agent_id:
        return {"error": True, "message": "Failed to get agent ID from creation response."}

    # 2. Invoke the agent to fetch the schema
    try:
        schema = await _request(
            "POST",
            f"{base}/{agent_id}/invoke",
            json={"input": "Fetch the database schema."},
            extra_headers={"Organization-Id": organization_id},
        )
    except Exception as e:
        # Still try to clean up the agent
        asyncio.create_task(_delete_agent_background(base, agent_id, organization_id))
        return {"error": True, "message": str(e)}

    # 3. Fire-and-forget deletion of the temporary agent
    asyncio.create_task(_delete_agent_background(base, agent_id, organization_id))

    return schema


async def _delete_agent_background(base: str, agent_id: str, organization_id: str) -> None:
    """Delete an agent, suppressing any errors."""
    try:
        await _request("DELETE", f"{base}/{agent_id}", extra_headers={"Organization-Id": organization_id})
    except Exception:
        pass


# -- instances -------------------------------------------------------------


@mcp.tool()
async def list_instances(
    organization_id: str,
    project_id: str,
) -> Any:
    """Returns a list of instances in a project.

    Args:
        organization_id: Organization UUID.
        project_id: Project UUID.
    """
    return await _request(
        "GET", f"/organizations/{organization_id}/projects/{project_id}/instances"
    )


@mcp.tool()
async def list_databases(
    instance_id: str,
    organization_id: str,
    project_id: str,
) -> Any:
    """Returns a list of databases for an instance.

    Args:
        instance_id: The Aura instance ID.
        organization_id: Organization UUID.
        project_id: Project UUID.
    """
    return await _request(
        "GET",
        f"/organizations/{organization_id}/projects/{project_id}/instances/{instance_id}/databases",
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()