# aura-agents-mcp

MCP server for the [Neo4j Aura Agents API](https://neo4j.com/docs/aura/platform/api/specification/#tag/Agents) (v2beta1).

Exposes tools for creating, listing, updating, deleting, and invoking Aura Agents through any MCP-compatible client (Claude Desktop, Claude Code, etc.).

## Prerequisites

- Python 3.10+
- Neo4j Aura API credentials ([create them here](https://console.neo4j.io/#account/api-credentials))

## Installation

```bash
pip install .
```

Or for development:

```bash
pip install -e .
```

## Configuration

Set the following environment variables:

| Variable | Required | Description |
|---|---|---|
| `AURA_CLIENT_ID` | Yes | Aura API client ID |
| `AURA_CLIENT_SECRET` | Yes | Aura API client secret |
| `AURA_BASE_URL` | No | Override API base URL |
| `NEO4J_MEMORY_URI` | No | Bolt URI for the agent's persistent memory store. When set together with the username and password below, the memory wiki tools are registered. |
| `NEO4J_MEMORY_USERNAME` | No | Username for the memory store. |
| `NEO4J_MEMORY_PASSWORD` | No | Password for the memory store. |
| `NEO4J_MEMORY_WIKI` | No | Namespace for memory pages (default `default`). Lets a single backing instance host multiple wikis. |

## Usage

### Standalone

```bash
aura-agents-mcp
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aura-agents": {
      "command": "aura-agents-mcp",
      "env": {
        "AURA_CLIENT_ID": "your-client-id",
        "AURA_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add aura-agents -- aura-agents-mcp
```

### Run directly from GitHub with `uvx`

If you'd rather not install the package, you can run it straight from the repo with [`uv`](https://docs.astral.sh/uv/):

```json
{
  "mcpServers": {
    "aura_agents": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/tomasonjo/aura-agents-mcp",
        "aura-agents-mcp"
      ],
      "env": {
        "AURA_CLIENT_ID": "",
        "AURA_CLIENT_SECRET": "",
        "NEO4J_MEMORY_URI": "bolt://localhost:7687",
        "NEO4J_MEMORY_USERNAME": "neo4j",
        "NEO4J_MEMORY_PASSWORD": "password"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|---|---|
| `list_databases` | List all databases across every organization, project, and instance |
| `list_agents` | List all agents in a project |
| `get_agent` | Fetch a single agent by ID |
| `create_agent` | Create a new agent (defaults to a text2cypher tool) |
| `update_agent` | Update an existing agent (merge semantics) |
| `delete_agent` | Delete an agent |
| `invoke_agent` | Invoke an agent with a prompt |
| `get_schema` | Get the schema of a Neo4j database |

### Memory tools

Registered only when `NEO4J_MEMORY_URI`, `NEO4J_MEMORY_USERNAME`, and `NEO4J_MEMORY_PASSWORD` are set. They give the agent a persistent markdown-style wiki backed by a separate Neo4j instance — pages are `Page` nodes, `[[wikilinks]]` become `LINKS_TO` edges, and a full-text index covers `path` and `content`. Suggested page conventions: `user/profile.md`, `entities/<name>.md`, `concepts/<name>.md`, `learnings/<topic>.md`, `log.md`.

| Tool | Description |
|---|---|
| `read_memory` | Read a stored memory page |
| `write_memory` | Save or overwrite a memory; syncs `LINKS_TO` edges from `[[wikilinks]]` |
| `append_memory` | Append to an existing memory; adds new wikilink edges |
| `list_memories` | List memory paths under a prefix |
| `search_memory` | Full-text search across memories |
| `find_memory_backlinks` | Memories that link to a given memory |
| `rename_memory` | Rename a memory and rewrite `[[old]]` references |
| `delete_memory` | Soft delete a memory |
