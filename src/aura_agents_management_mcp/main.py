"""
FastMCP server for Neo4j Aura Agents API (v2beta1).

Env vars:
  AURA_CLIENT_ID       - Aura API client ID          (required)
  AURA_CLIENT_SECRET   - Aura API client secret      (required)
  AURA_BASE_URL        - override base URL           (optional)

Run:
  pip install fastmcp httpx
  python aura_agents_management_mcp.py
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
) -> Any:
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


# --- internal helpers ----------------------------------------------------


async def _resolve_org_project(
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> tuple[str, str]:
    """Return (organization_id, project_id) for *dbid*.

    If both IDs are already supplied they are returned as-is (no API calls).
    Otherwise iterates orgs → projects → instances → databases to locate them.
    Raises RuntimeError if the database cannot be found.
    """
    if organization_id and project_id:
        return organization_id, project_id
    orgs_resp = await _request("GET", "/organizations")
    orgs = orgs_resp.get("data", orgs_resp) if isinstance(orgs_resp, dict) else orgs_resp
    if not isinstance(orgs, list):
        orgs = [orgs]
    for org in orgs:
        org_id = org.get("id", "")
        projects_resp = await _request("GET", f"/organizations/{org_id}/projects")
        projects = projects_resp.get("data", projects_resp) if isinstance(projects_resp, dict) else projects_resp
        if not isinstance(projects, list):
            projects = [projects]
        for proj in projects:
            proj_id = proj.get("id", "")
            instances_resp = await _request(
                "GET", f"/organizations/{org_id}/projects/{proj_id}/instances"
            )
            instances = instances_resp.get("data", instances_resp) if isinstance(instances_resp, dict) else instances_resp
            if not isinstance(instances, list):
                instances = [instances]
            for inst in instances:
                inst_id = inst.get("id", "")
                dbs_resp = await _request(
                    "GET",
                    f"/organizations/{org_id}/projects/{proj_id}/instances/{inst_id}/databases",
                )
                dbs = dbs_resp.get("data", dbs_resp) if isinstance(dbs_resp, dict) else dbs_resp
                if not isinstance(dbs, list):
                    dbs = [dbs]
                for db in dbs:
                    if db.get("id") == dbid:
                        return org_id, proj_id
    raise RuntimeError(f"Database '{dbid}' not found in any organization/project/instance.")


# --- tools --------------------------------------------------------------

# -- databases ------------------------------------------------------------


@mcp.tool()
async def list_databases() -> Any:
    """List all databases across every organization, project, and instance.

    Returns a flat list of databases, each enriched with its parent
    organization, project, and instance details so that no separate
    lookup is needed.
    """
    result: list[dict[str, Any]] = []
    orgs_resp = await _request("GET", "/organizations")
    orgs = orgs_resp.get("data", orgs_resp) if isinstance(orgs_resp, dict) else orgs_resp
    if not isinstance(orgs, list):
        orgs = [orgs]

    for org in orgs:
        org_id = org.get("id", "")
        projects_resp = await _request("GET", f"/organizations/{org_id}/projects")
        projects = projects_resp.get("data", projects_resp) if isinstance(projects_resp, dict) else projects_resp
        if not isinstance(projects, list):
            projects = [projects]

        for proj in projects:
            proj_id = proj.get("id", "")
            instances_resp = await _request(
                "GET", f"/organizations/{org_id}/projects/{proj_id}/instances"
            )
            instances = instances_resp.get("data", instances_resp) if isinstance(instances_resp, dict) else instances_resp
            if not isinstance(instances, list):
                instances = [instances]

            for inst in instances:
                inst_id = inst.get("id", "")
                dbs_resp = await _request(
                    "GET",
                    f"/organizations/{org_id}/projects/{proj_id}/instances/{inst_id}/databases",
                )
                dbs = dbs_resp.get("data", dbs_resp) if isinstance(dbs_resp, dict) else dbs_resp
                if not isinstance(dbs, list):
                    dbs = [dbs]

                for db in dbs:
                    result.append({
                        "database": db,
                        "instance": inst,
                        "project": proj,
                        "organization": org,
                    })

    return result


# -- agents --------------------------------------------------------------


@mcp.tool()
async def list_agents(
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """List all agents for a database.

    Args:
        dbid: Target Aura database instance ID. Use list_databases to find available database IDs.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
    resp = await _request(
        "GET", f"/organizations/{organization_id}/projects/{project_id}/agents"
    )
    # Pass through errors as-is; otherwise always return a list, even when
    # the API returns nothing / null / a {data: ...} envelope. An empty
    # list is much less ambiguous than no output.
    if isinstance(resp, dict) and resp.get("error"):
        return resp
    if resp is None:
        return []
    if isinstance(resp, dict):
        if "data" in resp:
            data = resp.get("data")
            return data if isinstance(data, list) else ([] if data is None else [data])
        return [resp] if resp else []
    return resp if isinstance(resp, list) else [resp]


@mcp.tool()
async def get_agent(
    agent_id: str,
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Fetch a single agent by ID.

    Args:
        agent_id: The agent UUID.
        dbid: Target Aura database instance ID. Use list_databases to find available database IDs.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
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
        dbid: Target Aura database instance ID. Use list_databases to find available database IDs.
        description: Optional agent description.
        tools: Optional list of tool definitions. Defaults to one text2cypher
            tool. Each tool dict must have `type`, `name`, `description`, and
            `enabled`. Supported tool types:

            - text2cypher: Converts natural language to Cypher queries.
              No extra config needed.
              Example: {"type": "text2cypher", "name": "query",
                        "description": "Query the database", "enabled": true}

            - cypherTemplate: Runs a predefined Cypher template with parameters.
              Requires a `config` object with `template` (Cypher string using
              $param placeholders) and `parameters` (list of parameter defs
              with `name`, `data_type`, and `description`).
              Example: {"type": "cypherTemplate", "name": "find-movies",
                        "description": "Find movies by title", "enabled": true,
                        "config": {"template": "MATCH (m:Movie) WHERE m.title
                        CONTAINS $title RETURN m", "parameters": [{"name":
                        "title", "data_type": "string", "description": "Movie
                        title to search for"}]}}

            - similaritySearch: Performs vector similarity search.
              Requires a `config` object with `provider`, `model`, `index`,
              and `top_k`.
              Example: {"type": "similaritySearch", "name": "search",
                        "description": "Vector search", "enabled": true,
                        "config": {"provider": "openai", "model":
                        "text-embedding-ada-002", "index": "my-vector-index",
                        "top_k": 10}}

        system_prompt: Optional system prompt.
        is_private: Whether the agent is private (default False).
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
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
    )


@mcp.tool()
async def update_agent(
    agent_id: str,
    dbid: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    new_dbid: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    system_prompt: Optional[str] = None,
    is_private: Optional[bool] = None,
    enabled: Optional[bool] = None,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Update an existing agent.

    Only `agent_id` and `dbid` are required. Any other field you omit is
    carried over from the agent's current configuration (the API uses PUT,
    so this tool fetches the existing agent first and merges your changes).

    Args:
        agent_id: The agent UUID to update.
        dbid: Current Aura database instance ID of the agent. Use list_databases to find available database IDs.
        name: Agent display name.
        description: Agent description.
        new_dbid: New target Aura database instance ID (to move the agent to a different database).
        tools: List of tool definitions. Replaces all existing tools.
            Supported types: text2cypher, cypherTemplate, similaritySearch.
            See create_agent for tool schema details and examples.
        system_prompt: System prompt.
        is_private: Whether the agent is private.
        enabled: Whether the agent is enabled.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
    current = await _request(
        "GET", f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}"
    )
    if isinstance(current, dict) and current.get("error"):
        return current

    # Start from the full current agent and overlay only the fields the
    # caller explicitly set. This preserves any field we don't know about
    # (e.g. avatar_color, avatar_icon) instead of silently dropping it.
    # Read-only / server-managed fields are stripped before PUT.
    READ_ONLY = {"id", "created_at", "updated_at", "status"}
    body: dict[str, Any] = {
        k: v for k, v in current.items() if k not in READ_ONLY
    }
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if new_dbid is not None:
        body["dbid"] = new_dbid
    if is_private is not None:
        body["is_private"] = is_private
    if tools is not None:
        body["tools"] = tools
    if system_prompt is not None:
        body["system_prompt"] = system_prompt
    if enabled is not None:
        body["enabled"] = enabled

    return await _request(
        "PUT",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}",
        json=body,
    )


@mcp.tool()
async def delete_agent(
    agent_id: str,
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Delete an agent by ID.

    Args:
        agent_id: The agent UUID to delete.
        dbid: Aura database instance ID. Use list_databases to find available database IDs.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
    return await _request(
        "DELETE",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}",
    )


@mcp.tool()
async def invoke_agent(
    agent_id: str,
    input: Union[str, list[dict]],
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Invoke an agent with a prompt.

    Args:
        agent_id: The ID of the agent to invoke.
        input: Either a plain string (single user message) or a list of
            `{"role": "user", "content": "..."}` dicts.
        dbid: Aura database instance ID. Use list_databases to find available database IDs.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
    return await _request(
        "POST",
        f"/organizations/{organization_id}/projects/{project_id}/agents/{agent_id}/invoke",
        json={"input": input},
    )


# -- schema ----------------------------------------------------------------


@mcp.tool()
async def get_schema(
    dbid: str,
    organization_id: str = "",
    project_id: str = "",
) -> Any:
    """Get the schema of a Neo4j database, including fulltext and vector indexes.

    Creates a temporary cypher_template agent, invokes it to fetch the schema
    together with the fulltext/vector indexes in a single query, then deletes
    the agent in the background.

    Args:
        dbid: Target Aura database instance ID. Use list_databases to find available database IDs.
        organization_id: Optional organization UUID (skips auto-resolution if provided with project_id).
        project_id: Optional project UUID (skips auto-resolution if provided with organization_id).
    """
    organization_id, project_id = await _resolve_org_project(dbid, organization_id, project_id)
    base = f"/organizations/{organization_id}/projects/{project_id}/agents"

    # 1. Create a temporary cypherTemplate agent with tools for schema and indexes.
    # SHOW INDEXES can't be composed with regular Cypher, so it lives in its own
    # tool. The system prompt instructs the agent to always call both tools for
    # any schema request so a single invoke returns both results.
    system_prompt = (
        "You are a Neo4j schema retrieval assistant. Whenever the user asks for "
        "the database schema, you MUST call BOTH of the available tools, in this "
        "order:\n"
        "  1. `get_schema` — returns the node/relationship schema from "
        "apoc.meta.schema().\n"
        "  2. `get_indexes` — returns the fulltext and vector indexes.\n"
        "Always call both tools, even if the user only mentions 'schema'. Never "
        "skip `get_indexes`. After both tool calls complete, return the raw "
        "results from both tools so the caller can see the schema and the "
        "fulltext/vector indexes together.\n\n"
        "Example question: 'Fetch the database schema.'\n"
        "Expected behaviour: call `get_schema`, then call `get_indexes`, then "
        "return both results."
    )

    agent = await _request(
        "POST",
        base,
        json={
            "name": "_schema_probe",
            "description": "Temporary agent for schema retrieval",
            "dbid": dbid,
            "is_private": False,
            "system_prompt": system_prompt,
            "tools": [
                {
                    "type": "cypherTemplate",
                    "name": "get_schema",
                    "description": "Fetch the node/relationship schema via apoc.meta.schema().",
                    "enabled": True,
                    "config": {
                        "template": "CALL apoc.meta.schema() YIELD value RETURN value",
                        "parameters": [],
                    },
                },
                {
                    "type": "cypherTemplate",
                    "name": "get_indexes",
                    "description": "Fetch fulltext and vector indexes.",
                    "enabled": True,
                    "config": {
                        "template": (
                            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options "
                            "WHERE type IN ['FULLTEXT', 'VECTOR'] "
                            "RETURN name, type, labelsOrTypes, properties, options"
                        ),
                        "parameters": [],
                    },
                },
            ],
        },
    )
    if isinstance(agent, dict) and agent.get("error"):
        return agent

    agent_id = agent.get("id")
    if not agent_id:
        return {"error": True, "message": "Failed to get agent ID from creation response."}

    # 2. Single invoke; the system prompt ensures the agent calls both tools.
    try:
        resp = await _request(
            "POST",
            f"{base}/{agent_id}/invoke",
            json={"input": "Fetch the database schema."},
        )
    except Exception as e:
        # Still try to clean up the agent
        asyncio.create_task(_delete_agent_background(base, agent_id))
        return {"error": True, "message": str(e)}

    # 3. Fire-and-forget deletion of the temporary agent
    asyncio.create_task(_delete_agent_background(base, agent_id))

    # 4. Extract results for both tool calls from the single invoke response.
    by_tool = _extract_tool_records(resp)
    schema_records = by_tool.get("get_schema") or []
    indexes_records = by_tool.get("get_indexes") or []

    schema: Any = None
    if schema_records:
        first = schema_records[0]
        schema = first["value"] if isinstance(first, dict) and "value" in first else first

    return {
        "schema": schema if schema is not None else resp,
        "indexes": indexes_records,
    }


def _extract_schema_records(resp: Any) -> Any:
    """Pull the schema dict out of an invoke response, or return None."""
    blocks: list[Any] = []
    if isinstance(resp, list):
        blocks = resp
    elif isinstance(resp, dict):
        for key in ("content", "output", "messages"):
            v = resp.get(key)
            if isinstance(v, list):
                blocks = v
                break
    for block in blocks:
        if not isinstance(block, dict):
            continue
        output = block.get("output")
        if isinstance(output, dict):
            records = output.get("records")
            if isinstance(records, list) and records:
                first = records[0]
                if isinstance(first, dict) and "value" in first:
                    return first["value"]
                return first
    return None


def _extract_tool_records(resp: Any) -> dict[str, Any]:
    """Return a mapping of tool name -> records from an invoke response.

    Walks every content block in the response and, for each block that looks
    like a tool result (has `output.records`), associates the records with the
    tool name found on the block (trying several common field names).
    """
    blocks: list[Any] = []
    if isinstance(resp, list):
        blocks = resp
    elif isinstance(resp, dict):
        for key in ("content", "output", "messages"):
            v = resp.get(key)
            if isinstance(v, list):
                blocks = v
                break
    out: dict[str, Any] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        output = block.get("output")
        if not isinstance(output, dict):
            continue
        records = output.get("records")
        if not isinstance(records, list):
            continue
        name = (
            block.get("name")
            or block.get("tool_name")
            or block.get("tool")
            or output.get("name")
            or output.get("tool_name")
        )
        if name:
            out[name] = records
    return out


async def _delete_agent_background(base: str, agent_id: str) -> None:
    """Delete an agent, suppressing any errors."""
    try:
        await _request("DELETE", f"{base}/{agent_id}")
    except Exception:
        pass


def main():
    from . import memory

    if memory.memory_enabled():
        memory.register(mcp)
    mcp.run()


if __name__ == "__main__":
    main()