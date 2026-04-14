"""
Persistent agent memory backed by Neo4j.

Models a markdown-style wiki where each page is a `Page` node and `[[wikilinks]]`
become `LINKS_TO` relationships. All pages are scoped to a single `wiki`
namespace (configured via NEO4J_MEMORY_WIKI, default "default").

Env vars (all required to enable these tools):
  NEO4J_MEMORY_URI
  NEO4J_MEMORY_USERNAME
  NEO4J_MEMORY_PASSWORD
  NEO4J_MEMORY_WIKI      - optional, defaults to "default"
"""

from __future__ import annotations

import os
import re
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

_driver: AsyncDriver | None = None
_schema_ready = False
WIKI = os.getenv("NEO4J_MEMORY_WIKI", "default")


def _normalize(target: str) -> str:
    target = target.strip()
    if not target.endswith(".md"):
        target += ".md"
    return target


def _extract_links(content: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in WIKILINK_RE.finditer(content or ""):
        t = _normalize(m.group(1))
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def _get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_MEMORY_URI"]
        user = os.environ["NEO4J_MEMORY_USERNAME"]
        password = os.environ["NEO4J_MEMORY_PASSWORD"]
        _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    return _driver


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    driver = await _get_driver()
    async with driver.session() as s:
        await s.run(
            "CREATE CONSTRAINT page_wiki_path IF NOT EXISTS "
            "FOR (p:Page) REQUIRE (p.wiki, p.path) IS UNIQUE"
        )
        await s.run(
            "CREATE FULLTEXT INDEX page_fulltext IF NOT EXISTS "
            "FOR (p:Page) ON EACH [p.path, p.content]"
        )
    _schema_ready = True


def memory_enabled() -> bool:
    return all(
        os.getenv(v)
        for v in ("NEO4J_MEMORY_URI", "NEO4J_MEMORY_USERNAME", "NEO4J_MEMORY_PASSWORD")
    )


# --- tool implementations ------------------------------------------------


async def read_file(path: str) -> Any:
    """Read a markdown page from your memory wiki. Use this to recall what
    you've previously learned about a topic, person, or the user before
    answering.

    Args:
        path: Page path, e.g. "user/profile.md".
    """
    await _ensure_schema()
    path = _normalize(path)
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "MATCH (p:Page {wiki: $wiki, path: $path}) "
            "WHERE coalesce(p.deleted, false) = false "
            "RETURN p.content AS content",
            wiki=WIKI,
            path=path,
        )
        record = await result.single()
    if record is None:
        return {"error": True, "message": f"Page not found: {path}"}
    return {"path": path, "content": record["content"] or ""}


async def write_file(path: str, content: str) -> Any:
    """Create or overwrite a page in your memory wiki. Use this to store
    durable learnings: facts about the user (preferences, goals, background,
    working style), insights from conversations, decisions made, patterns
    you've noticed, or concepts worth remembering across sessions. Parses
    `[[wikilinks]]` from content and links to those pages, auto-creating
    empty stubs as needed.

    Args:
        path: Page path, e.g. "user/profile.md".
        content: Full markdown content of the page.
    """
    await _ensure_schema()
    path = _normalize(path)
    links = _extract_links(content)
    driver = await _get_driver()
    async with driver.session() as s:
        await s.execute_write(_write_tx, path, content, links)
    return {"ok": True, "path": path, "links": links}


async def _write_tx(tx, path: str, content: str, links: list[str]) -> None:
    await tx.run(
        "MERGE (p:Page {wiki: $wiki, path: $path}) "
        "SET p.content = $content, p.deleted = false",
        wiki=WIKI,
        path=path,
        content=content,
    )
    # Drop existing outgoing links and rebuild from current content.
    await tx.run(
        "MATCH (p:Page {wiki: $wiki, path: $path})-[r:LINKS_TO]->() DELETE r",
        wiki=WIKI,
        path=path,
    )
    if links:
        await tx.run(
            "MATCH (p:Page {wiki: $wiki, path: $path}) "
            "UNWIND $links AS target "
            "MERGE (t:Page {wiki: $wiki, path: target}) "
            "ON CREATE SET t.content = '', t.deleted = false "
            "MERGE (p)-[:LINKS_TO]->(t)",
            wiki=WIKI,
            path=path,
            links=links,
        )


async def append_file(path: str, content: str) -> Any:
    """Append to an existing page without rewriting it. Use for running logs
    (`log.md`), timelines on an entity page, or accumulating observations
    about the user over time. Adds new links for any wikilinks in the
    appended text.

    Args:
        path: Page path, e.g. "log.md".
        content: Markdown to append. A newline is inserted before it if the
            existing page does not already end with one.
    """
    await _ensure_schema()
    path = _normalize(path)
    links = _extract_links(content)
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "MERGE (p:Page {wiki: $wiki, path: $path}) "
            "ON CREATE SET p.content = '', p.deleted = false "
            "SET p.deleted = false, "
            "    p.content = CASE "
            "      WHEN coalesce(p.content, '') = '' THEN $content "
            "      WHEN right(p.content, 1) = '\n' THEN p.content + $content "
            "      ELSE p.content + '\n' + $content END "
            "RETURN p.path AS path",
            wiki=WIKI,
            path=path,
            content=content,
        )
        await result.consume()
        if links:
            await s.run(
                "MATCH (p:Page {wiki: $wiki, path: $path}) "
                "UNWIND $links AS target "
                "MERGE (t:Page {wiki: $wiki, path: target}) "
                "ON CREATE SET t.content = '', t.deleted = false "
                "MERGE (p)-[:LINKS_TO]->(t)",
                wiki=WIKI,
                path=path,
                links=links,
            )
    return {"ok": True, "path": path, "added_links": links}


async def list_files(prefix: str = "") -> Any:
    """List all page paths starting with `prefix`, sorted. Use to browse
    what you already know in a category (e.g. `entities/` to see everyone
    you've tracked, `user/` for what you know about the user).

    Args:
        prefix: Optional path prefix to filter by. Empty string lists all.
    """
    await _ensure_schema()
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "MATCH (p:Page {wiki: $wiki}) "
            "WHERE coalesce(p.deleted, false) = false AND p.path STARTS WITH $prefix "
            "RETURN p.path AS path ORDER BY p.path",
            wiki=WIKI,
            prefix=prefix,
        )
        paths = [r["path"] async for r in result]
    return {"prefix": prefix, "paths": paths}


async def search(query: str, limit: int = 10) -> Any:
    """Full-text search across your memory wiki. Use this at the start of a
    task to check whether you already have relevant knowledge stored — about
    the user, the domain, or prior decisions — before asking or assuming.

    Args:
        query: Lucene-style full-text query.
        limit: Maximum number of results.
    """
    await _ensure_schema()
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "CALL db.index.fulltext.queryNodes('page_fulltext', $query) "
            "YIELD node, score "
            "WHERE node.wiki = $wiki AND coalesce(node.deleted, false) = false "
            "RETURN node.path AS path, node.content AS content, score "
            "LIMIT $limit",
            query=query,
            wiki=WIKI,
            limit=limit,
        )
        hits = []
        async for r in result:
            content = r["content"] or ""
            snippet = content[:240] + ("..." if len(content) > 240 else "")
            hits.append({"path": r["path"], "snippet": snippet, "score": r["score"]})
    return {"query": query, "results": hits}


async def find_backlinks(path: str) -> Any:
    """Return all pages that link to this one. Use to find where an entity
    or concept has come up across your memory.

    Args:
        path: Page path to find backlinks for.
    """
    await _ensure_schema()
    path = _normalize(path)
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "MATCH (other:Page {wiki: $wiki})-[:LINKS_TO]->(p:Page {wiki: $wiki, path: $path}) "
            "WHERE coalesce(other.deleted, false) = false "
            "RETURN other.path AS path ORDER BY other.path",
            wiki=WIKI,
            path=path,
        )
        backlinks = [r["path"] async for r in result]
    return {"path": path, "backlinks": backlinks}


async def rename_file(old_path: str, new_path: str) -> Any:
    """Atomically rename a page; also rewrites `[[old_path]]` references to
    `[[new_path]]` in every page that links to it. Use when you've learned a
    better name for something.

    Args:
        old_path: Current page path.
        new_path: New page path.
    """
    await _ensure_schema()
    old_path = _normalize(old_path)
    new_path = _normalize(new_path)
    if old_path == new_path:
        return {"ok": True, "path": new_path, "unchanged": True}

    driver = await _get_driver()
    async with driver.session() as s:
        await s.execute_write(_rename_tx, old_path, new_path)
    return {"ok": True, "old_path": old_path, "new_path": new_path}


async def _rename_tx(tx, old_path: str, new_path: str) -> None:
    # Move content+links onto the new path. If new already exists as a stub,
    # overwrite it; otherwise create. Then delete the old node.
    await tx.run(
        "MATCH (old:Page {wiki: $wiki, path: $old_path}) "
        "MERGE (new:Page {wiki: $wiki, path: $new_path}) "
        "SET new.content = old.content, new.deleted = false "
        "WITH old, new "
        "OPTIONAL MATCH (old)-[r:LINKS_TO]->(t) "
        "FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | "
        "  MERGE (new)-[:LINKS_TO]->(t)) "
        "WITH old, new "
        "OPTIONAL MATCH (src)-[r2:LINKS_TO]->(old) "
        "FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END | "
        "  MERGE (src)-[:LINKS_TO]->(new)) "
        "DETACH DELETE old",
        wiki=WIKI,
        old_path=old_path,
        new_path=new_path,
    )
    # Rewrite [[old]] / [[old|alias]] in referencing pages' content. Match the
    # bare name without .md and the .md form for safety.
    old_bare = old_path[:-3] if old_path.endswith(".md") else old_path
    new_bare = new_path[:-3] if new_path.endswith(".md") else new_path
    await tx.run(
        "MATCH (src:Page {wiki: $wiki})-[:LINKS_TO]->(:Page {wiki: $wiki, path: $new_path}) "
        "WHERE src.content CONTAINS '[[' "
        "SET src.content = "
        "  replace( "
        "    replace( "
        "      replace(src.content, '[[' + $old_bare + ']]', '[[' + $new_bare + ']]'), "
        "      '[[' + $old_path + ']]', '[[' + $new_path + ']]' "
        "    ), "
        "    '[[' + $old_bare + '|', '[[' + $new_bare + '|' "
        "  )",
        wiki=WIKI,
        new_path=new_path,
        old_path=old_path,
        old_bare=old_bare,
        new_bare=new_bare,
    )


async def delete_file(path: str) -> Any:
    """Soft delete a page. Use when a page is obsolete or wrong; prefer
    rewriting over deleting when possible so history is preserved.

    Args:
        path: Page path to delete.
    """
    await _ensure_schema()
    path = _normalize(path)
    driver = await _get_driver()
    async with driver.session() as s:
        result = await s.run(
            "MATCH (p:Page {wiki: $wiki, path: $path}) "
            "SET p.deleted = true RETURN p.path AS path",
            wiki=WIKI,
            path=path,
        )
        record = await result.single()
    if record is None:
        return {"error": True, "message": f"Page not found: {path}"}
    return {"ok": True, "path": path}


def register(mcp) -> None:
    """Register memory tools on the FastMCP server."""
    mcp.tool()(read_file)
    mcp.tool()(write_file)
    mcp.tool()(append_file)
    mcp.tool()(list_files)
    mcp.tool()(search)
    mcp.tool()(find_backlinks)
    mcp.tool()(rename_file)
    mcp.tool()(delete_file)
