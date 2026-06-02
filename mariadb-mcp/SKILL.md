---
name: mariadb-mcp
description: "How to connect AI agents to MariaDB using the Model Context Protocol (MCP). Use when setting up MCP with MariaDB, connecting Claude Code, Cursor, or other AI tools to a MariaDB database, enabling natural language database queries, or building AI applications that need live database access. The MariaDB MCP Server lets agents query schemas, run read-only SQL, and perform semantic search against MariaDB."
---

# MariaDB MCP Server

*Last updated: 2026-05-25*

The Model Context Protocol (MCP) lets AI agents connect to external tools and data sources. The MariaDB MCP Server gives agents direct, controlled access to a MariaDB database — reading schemas, running queries, and optionally performing vector/semantic search — without requiring the developer to write integration code.

The server is open source (MIT), maintained by the MariaDB Foundation at [github.com/MariaDB/mcp](https://github.com/MariaDB/mcp).

> **Requires:** Python 3.11+, `uv` package manager, MariaDB server.

## What Agents Can Do

With the MariaDB MCP Server connected, an agent can:

- List databases and tables
- Read table schemas including foreign key relationships
- Run read-only SQL queries (`SELECT`, `SHOW`, `DESCRIBE`)
- Create databases
- Perform semantic/vector search on stored documents (when embedding provider is configured)

Write operations (`INSERT`, `UPDATE`, `DELETE`) are disabled by default.

## Installation

```bash
git clone https://github.com/MariaDB/mcp
cd mcp
cp .env.example .env
# Edit .env with your database credentials
```

**Minimum `.env` configuration:**
```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=myuser
DB_PASSWORD=mypassword
DB_NAME=mydatabase
MCP_READ_ONLY=true
```

**Run the server:**
```bash
uv run server.py
```

## Connecting Claude Code, Cursor, or Windsurf

Use the official server from a local clone (see Installation). Point your MCP config at `uv run server.py` and the `.env` file with database credentials.

**Claude Code** (project-scoped `.mcp.json` at the repo root):

```json
{
  "mcpServers": {
    "mariadb": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mcp",
        "run",
        "server.py"
      ],
      "envFile": "/absolute/path/to/mcp/.env"
    }
  }
}
```

Or add via CLI (replace paths):

```bash
claude mcp add mariadb -- uv --directory /absolute/path/to/mcp run server.py
```

**Cursor / VS Code** — same `mcpServers` block in MCP settings; use absolute paths.

**SSE or HTTP** — run `uv run server.py --transport sse` (or `http`) and connect to the URL; see [MariaDB/mcp README — Integration](https://github.com/MariaDB/mcp#integration---claude-desktopcursorwindsurfvscode).

> **Not official:** PyPI package `mcp-server-mariadb` (`uvx mcp-server-mariadb`) is a third-party project, not maintained by the MariaDB Foundation. Prefer `github.com/MariaDB/mcp` unless you have a specific reason to use the PyPI package.

## Available Tools

| Tool | What it does |
|---|---|
| `list_databases` | Lists all databases the user can access |
| `list_tables` | Lists tables in a database |
| `get_table_schema` | Returns column definitions and types |
| `get_table_schema_with_relations` | Includes foreign key relationships |
| `execute_sql` | Runs a read-only SQL query |
| `create_database` | Creates a new database |

## Vector & Semantic Search (Optional)

When an embedding provider is configured, additional tools are available for building RAG pipelines directly through the MCP interface:

| Tool | What it does |
|---|---|
| `create_vector_store` | Creates a vector store table |
| `insert_docs_vector_store` | Embeds and stores documents |
| `search_vector_store` | Semantic similarity search |
| `list_vector_stores` | Lists available vector stores |
| `delete_vector_store` | Removes a vector store |

**Configure an embedding provider in `.env`:**
```
EMBEDDING_PROVIDER=openai   # or gemini or huggingface
OPENAI_API_KEY=sk-...
```

For building pure vector search applications, using MariaDB's native `VECTOR` type and `VEC_DISTANCE_*` functions directly is more efficient. The MCP vector tools are useful for quick prototyping and when AI agents need to manage the vector store themselves. See the `mariadb-vector` skill for native vector SQL.

## Security: Read-Only Access

The `MCP_READ_ONLY=true` setting enforces read-only at the application level by filtering queries. For production or shared environments, also restrict at the database level:

```sql
CREATE USER 'mcp_agent'@'localhost' IDENTIFIED BY 'password';
GRANT SELECT, SHOW DATABASES ON *.* TO 'mcp_agent'@'localhost';
-- Do NOT grant INSERT, UPDATE, DELETE, DROP
```

Application-level read-only alone is not sufficient — database-level privileges are the reliable guarantee.

## Key Gotchas

- **`uv` is required**: the MCP server uses `uv` for dependency management, not `pip` directly. Install: `pip install uv` or `brew install uv`.
- **Read-only is not fully enforced in software**: rely on database-level privileges, not just `MCP_READ_ONLY=true`.
- **Vector tools are disabled** until `EMBEDDING_PROVIDER` is set in `.env`.
- **Connection pooling** defaults to 10 connections — tune `MCP_MAX_POOL_SIZE` for concurrent agent use.
- **SSL/TLS**: configure `DB_SSL_CA`, `DB_SSL_CERT`, `DB_SSL_KEY` in `.env` for remote or production databases.
- **Enterprise version**: MariaDB also offers a MariaDB Enterprise MCP Server with additional access control and multi-agent features. See [mariadb.com/products/mcp-server](https://mariadb.com/products/mcp-server/).

## Sources

- [MariaDB MCP Server — GitHub](https://github.com/MariaDB/mcp)
- [MariaDB MCP Server product page — mariadb.com](https://mariadb.com/products/mcp-server/)
- [Connect Claude Code to tools via MCP — Claude Code Docs](https://code.claude.com/docs/en/mcp)

*For topics not covered here, see the official MariaDB documentation at [mariadb.com/docs](https://mariadb.com/docs).*
