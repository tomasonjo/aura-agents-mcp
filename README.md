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
