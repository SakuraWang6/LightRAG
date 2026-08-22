# Local service management

## Backups

Run `./scripts/lightrag-backup` before a storage or model configuration change.
It creates a timestamped archive below `var/backups/`, including the file
storage and uploaded source directory. Once PostgreSQL is configured, the same
command also writes a compressed `pg_dump` database backup:

```bash
./scripts/lightrag-backup
./scripts/lightrag-backup --files
./scripts/lightrag-backup --database
```

The archive and database dump are independent recovery artifacts. Keep the
source file archive: LightRAG does not directly migrate a completed index from
file storage to PostgreSQL, so documents are re-indexed after the switch.

The service stores its `tiktoken` tokenizer assets in `var/tiktoken-cache/`.
This prevents DOCX parsing and tests from depending on a later internet download.

`scripts/lightrag-service` manages the repository's local LightRAG server. It
uses the settings in `.env`, writes runtime state below `var/`, and does not
remove document or graph storage.

Run the commands from the repository root:

```bash
./scripts/lightrag-service start
./scripts/lightrag-service status
./scripts/lightrag-service logs
./scripts/lightrag-service stop
./scripts/lightrag-service restart
```

`start` runs the service in the background, so closing the terminal does not
send it a hangup signal. Open the WebUI at `http://127.0.0.1:9621/webui/` once
the command reports that the health check has succeeded. `logs -f` follows the
server log while diagnosing startup or model-provider errors.

## Persistent macOS service

For automatic startup after login and automatic restart after an unexpected
crash, install the user-level LaunchAgent once:

```bash
./scripts/lightrag-service install
```

After that, the same `start`, `stop`, and `restart` commands operate on the
LaunchAgent. Remove it when it is no longer wanted:

```bash
./scripts/lightrag-service uninstall
```

The agent is user-scoped; it does not require `sudo` and it does not expose the
service beyond the `HOST` configured in `.env`.

When local reranking is configured, `install` also creates a second
`ai.lightrag.local-reranker` LaunchAgent. Both processes restart after a crash
or next login. `restart` waits for each previous LaunchAgent to finish unloading
before starting the replacement.

## PostgreSQL storage

This local deployment uses PostgreSQL with pgvector for all four LightRAG
storage roles:

```text
PGKVStorage · PGVectorStorage · PGTableGraphStorage · PGDocStatusStorage
```

`PGTableGraphStorage` keeps graph nodes and edges in ordinary PostgreSQL tables,
so no Apache AGE extension or separate graph database is required. Connection
settings and the database password are stored only in the ignored `.env` file.
The Homebrew database service is independent of LightRAG and is managed with:

```bash
brew services list | rg postgresql
brew services restart postgresql@18
```

## Conda and reranker overrides

The scripts default to the current `lightrag-memory-eval` Conda environment.
If Conda is installed elsewhere, copy the optional local settings template and
set the executable or environment name:

```bash
cp scripts/lightrag-service.local.env.example scripts/lightrag-service.local.env
```

The current LightRAG configuration expects a separate local reranker at
`127.0.0.1:8000`. The manager reports its availability in `status`. It starts
that process only when `LIGHTRAG_RERANK_COMMAND` and
`LIGHTRAG_RERANK_PROCESS_MATCH` are explicitly supplied in the local settings
file; this prevents the manager from stopping an unrelated process that happens
to use port 8000.

The included `scripts/local_reranker_service.py` exposes the Cohere-compatible
`/rerank` endpoint required by LightRAG and loads the configured Jina model on
Apple MPS when available. The first launch downloads its model once; after that
it runs from the local Hugging Face cache.
