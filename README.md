# aura-agents-management-mcp

MCP server for the [Neo4j Aura Agents API](https://neo4j.com/docs/aura/platform/api/specification/#tag/Agents) (v2beta1).

Exposes tools for creating, listing, updating, deleting, and invoking Aura Agents through any MCP-compatible client (Claude Desktop, Claude Code, etc.).

It also ships an optional **persistent memory layer** backed by Neo4j — a markdown wiki the model writes for itself, inspired by [Andrej Karpathy's idea of an LLM-authored knowledge base](https://x.com/karpathy/status/2039805659525644595). Pages are linked with `[[wikilinks]]` that materialise as graph edges, so the model's notes about you, your projects, and lessons learned grow into a real knowledge graph it can search, traverse, and refactor over time. See [Memory tools](#memory-tools) below.

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
aura-agents-management-mcp
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aura-agents": {
      "command": "aura-agents-management-mcp",
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
claude mcp add aura-agents -- aura-agents-management-mcp
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
        "git+https://github.com/tomasonjo/aura-agents-management-mcp",
        "aura-agents-management-mcp"
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

Registered only when `NEO4J_MEMORY_URI`, `NEO4J_MEMORY_USERNAME`, and `NEO4J_MEMORY_PASSWORD` are set. They give the agent a persistent markdown-style wiki backed by a separate Neo4j instance — pages are `Page` nodes, `[[wikilinks]]` become `LINKS_TO` edges, and a full-text index covers `path` and `content`.

#### Recommended page layout

The wiki is just markdown, so any structure works — but the model gets the most leverage when pages are organised by topic, and when anything learned about a *specific* database or agent lives on its own page (keyed by id) so it can be recalled individually instead of scanning a mixed log. A good starting convention:

```
user/profile.md              # who they are, role, responsibilities
user/preferences.md          # tooling, style, do / don't

databases/<dbid>.md          # one page per Aura database
                             #   purpose, schema quirks, known-good
                             #   Cypher patterns, gotchas, indexes
databases/<dbid>/<topic>.md  # optional sub-pages for deep dives

agents/<agent_id>.md         # one page per agent
                             #   what it's for, tool list & rationale,
                             #   prompt lessons, failure modes, fixes
agents/<agent_id>/<topic>.md # optional sub-pages

entities/<name>.md           # people, orgs, services, repos
concepts/<name>.md           # domain ideas the model needs to know
learnings/<topic>.md         # cross-cutting lessons not tied to one
                             # database or agent
log.md                       # scratch / chronological notes
```

The `databases/` and `agents/` namespaces are recommendations, not rules — add other top-level folders when something doesn't fit. The point is that a future session can call `read_memory("databases/<dbid>.md")` or `read_memory("agents/<agent_id>.md")` and get exactly that context.

Cross-link liberally with `[[wikilinks]]` — every agent page should link to its `[[databases/<dbid>]]`, learnings should link to the `[[concepts/...]]` they relate to, and so on. Every link becomes a graph edge the model can traverse later via `find_memory_backlinks`.

#### A wiki the model writes for itself

The memory layer is inspired by [Andrej Karpathy's idea of an LLM-authored knowledge base](https://x.com/karpathy/status/2039805659525644595) — instead of relying solely on fine-tuning or stuffing everything into the prompt, the model curates its *own* wiki as it works. Every interaction is an opportunity to take notes: who the user is, what conventions a project follows, which approaches failed last time, what an unfamiliar acronym means. Those notes are written back as plain markdown pages and recalled on demand in future sessions.

This turns memory into a **graph of interlinked pages** rather than a flat log:

- **Pages, not transcripts.** Knowledge is organised by topic (`concepts/text2cypher.md`, `entities/acme-corp.md`, `learnings/migration-pitfalls.md`) so the model retrieves the *idea*, not the conversation it came from.
- **Wikilinks become edges.** Writing `[[concepts/cypher]]` inside a page automatically creates a `LINKS_TO` relationship in Neo4j. The wiki *is* a knowledge graph — you can traverse it, find backlinks, and discover related context the model didn't explicitly ask for.
- **Full-text + graph recall.** `search_memory` covers fuzzy lookup, `find_memory_backlinks` surfaces everything that references a page, and `list_memories` walks the namespace like a directory tree.
- **Learn and evolve.** Because the model owns the write path (`write_memory`, `append_memory`, `rename_memory`), it can refactor its own knowledge base over time — promoting a stray observation in `log.md` into a proper `learnings/` page, renaming a concept and having every backlink updated automatically, or correcting a stale fact. The wiki gets sharper with use instead of stale.
- **Portable across agents.** Because storage is just Neo4j + markdown, the same wiki can be shared between Claude Desktop, Claude Code, and any Aura agent you spin up — they all read and write the same brain.

The `NEO4J_MEMORY_WIKI` namespace lets you host multiple isolated wikis on one backing instance (e.g. one per user, project, or experiment).

#### A wiki the model writes for itself

The memory layer is inspired by [Andrej Karpathy's idea of an LLM-authored knowledge base](https://x.com/karpathy/status/2039805659525644595) — instead of relying solely on fine-tuning or stuffing everything into the prompt, the model curates its *own* wiki as it works. Every interaction is an opportunity to take notes: who the user is, what conventions a project follows, which approaches failed last time, what an unfamiliar acronym means. Those notes are written back as plain markdown pages and recalled on demand in future sessions.

This turns memory into a **graph of interlinked pages** rather than a flat log:

- **Pages, not transcripts.** Knowledge is organised by topic (`concepts/text2cypher.md`, `entities/acme-corp.md`, `learnings/migration-pitfalls.md`) so the model retrieves the *idea*, not the conversation it came from.
- **Wikilinks become edges.** Writing `[[concepts/cypher]]` inside a page automatically creates a `LINKS_TO` relationship in Neo4j. The wiki *is* a knowledge graph — you can traverse it, find backlinks, and discover related context the model didn't explicitly ask for.
- **Full-text + graph recall.** `search_memory` covers fuzzy lookup, `find_memory_backlinks` surfaces everything that references a page, and `list_memories` walks the namespace like a directory tree.
- **Learn and evolve.** Because the model owns the write path (`write_memory`, `append_memory`, `rename_memory`), it can refactor its own knowledge base over time — promoting a stray observation in `log.md` into a proper `learnings/` page, renaming a concept and having every backlink updated automatically, or correcting a stale fact. The wiki gets sharper with use instead of stale.
- **Portable across agents.** Because storage is just Neo4j + markdown, the same wiki can be shared between Claude Desktop, Claude Code, and any Aura agent you spin up — they all read and write the same brain.

The `NEO4J_MEMORY_WIKI` namespace lets you host multiple isolated wikis on one backing instance (e.g. one per user, project, or experiment).

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
