# Proposed full replacement text — Part I (issue #3516)

This is the complete proposed text for "Part I: document artifact and
export model" in https://github.com/HKUDS/LightRAG/issues/3516,
incorporating the export-scheduling redesign discussed in this session.
Sections untouched by that redesign are carried over unchanged from the
current RFC body; the two changed sections ("Export jobs, mailbox, and
pipeline scheduling" and "Event-driven cache expiration, eviction, and
active-download leases") are rewritten, and small cross-reference notes
are added to the sections that now interact with the per-artifact lock.
Rationale for the changes is in the sibling file
`issue-3516-artifact-export-scheduling.md`; this file is the clean draft
meant to be pasted into the issue body.

Parts II–V are unaffected and are not repeated here.

---

# Part I: document artifact and export model

## Why basename lookup is invalid

`doc_status.file_path` is a canonical, hint-stripped logical basename. It is not a physical locator.

The logical filename is unique in the current single-workspace contract, and `doc_id` is derived from that canonical filename. Therefore a canonical archive target occupied while a replacement document is being archived cannot belong to another currently valid document. It may instead be:

- a source left behind after a deleted document's incomplete filesystem cleanup;
- a scan-time alias or duplicate archived without becoming the existing document's source;
- a post-parse content duplicate;
- a historical or manually placed file.

Such a file is an orphan candidate, not an alternative source for the current `doc_id`. The server may move it aside only after a strict storage read proves that no valid document currently names that exact location. A storage error is not proof of absence and fails closed.

Scanning `__parsed__`, stripping `_001`, and returning an arbitrary match remains forbidden. Filesystem iteration order never defines document ownership.

## Artifact identity fields

The model uses four fields:

| Field | Storage | Normative meaning |
| --- | --- | --- |
| `file_path` | `doc_status` and `full_docs` | Stable canonical logical filename with parser hints removed. Used for scheduling, deduplication, display, citations, ZIP entry names, and public download filenames. Never interpreted as a physical path. |
| `metadata.source_location` | `doc_status.metadata` | Exact current location of the managed source file, stored as a POSIX path relative to the current workspace input root. |
| `metadata.source_archive_location` | `doc_status.metadata` | Exact canonical archive target determined and persisted before the managed move. It is a recovery anchor, not a directory-search hint. |
| `sidecar_location` | `full_docs` | Existing exact locator of the parser-produced `*.parsed/` directory and the only authority for the parsed artifact. |

Before archival:

```text
file_path                         = report.pdf
metadata.source_location         = report.[mineru-iet].pdf
metadata.source_archive_location = __parsed__/report.[mineru-iet].pdf
full_docs.sidecar_location       = file:///.../__parsed__/report.pdf.parsed/
```

After archival:

```text
file_path                         = report.pdf
metadata.source_location         = __parsed__/report.[mineru-iet].pdf
metadata.source_archive_location = __parsed__/report.[mineru-iet].pdf
full_docs.sidecar_location       = file:///.../__parsed__/report.pdf.parsed/
```

`source_location`, `source_archive_location`, move/recovery state, deletion journals, raw-cache locators, and internal `file://` URIs are protected implementation metadata. Ordinary document-list, pagination, tracking, logging, and audit responses must filter them. Only the artifact APIs expose designed public filenames, kinds, states, and URLs.

### Deprecate document-level `source_file`

Once `source_location` exists from enqueue time and `source_archive_location` is persisted before archival, document-level `source_file` / legacy `source_file_name` is redundant:

- parser resolution uses the exact current `source_location`;
- parser choice/options come from persisted `parse_engine`, `process_options`, and `chunk_options`;
- scan identity compares the incoming workspace-relative location with `source_location`;
- source ZIP entries and `Content-Disposition` use canonical `file_path`;
- exact deletion uses persisted locators and a deletion journal.

New documents stop writing document-level `source_file`. Fields with the same name in multimodal image payloads or parser-local variables are unrelated and remain unchanged.

If a future requirement needs the exact client-supplied filename, add an explicit `original_filename`; do not overload either locator. Version 1 uses canonical `file_path` as the public source filename.

## Source-location lifecycle and orphan rotation

1. Managed upload/scan enqueue persists the exact current `source_location` and the exact canonical `source_archive_location`.
2. Both keys are reserved long-lived metadata preserved through every status transition and retry reset.
3. After successful parsing and `full_docs` synchronization, the pipeline owner compares the two exact paths while it still owns the workspace pipeline reservation.
4. If current and target are equal and the target is the expected regular file, archival is already committed.
5. If the current source exists and the target does not, atomically move the source to the target.
6. If both exist, strictly confirm that the target is not referenced by any valid document. Only then move that orphan to an available numbered backup and move the new source into the unchanged canonical target.
7. A numbered orphan backup is not a managed document artifact and is never returned by the artifact API.
8. After the source reaches the canonical target, commit `source_location = source_archive_location` using an owner-fenced targeted metadata update.
9. A scan-time alias/already-processed duplicate that is merely archived must not overwrite the existing document's locator.
10. Every state is idempotently recoverable from the two persisted exact locations; recovery never searches suffix variants.
11. Steps 5 and 6 acquire the per-`(doc_id, artifact_kind="source")` artifact-export lock (§Export jobs and independent build scheduling) for the duration of the move, so a concurrent source-artifact export build can never observe a half-moved file. This lock has no other effect on this lifecycle and is unrelated to the workspace pipeline reservation in step 3.

Recovery table:

| Current location | Archive target | Meaning and action |
| --- | --- | --- |
| exists | absent | move not completed; archive to the exact target |
| absent | exists | move completed; commit `source_location` |
| exists | exists | prove target is orphan, rotate it, then move current source |
| absent | absent | artifact missing; fail closed and require repair |

A storage error during orphan proof, move-state read, or locator commit is an explicit recoverable inconsistency. It is never converted into a directory scan or basename guess.

## Exact artifact deletion

When `DELETE /documents/{doc_id}` is invoked with `delete_file=true`, its filesystem-cleanup branch must capture and clean exact artifact locators before removing the records that contain them:

1. under the destructive pipeline reservation, strictly read `source_location`, `source_archive_location`, `sidecar_location`, and any parser raw-cache locators;
2. persist a bounded deletion journal containing the exact internal targets;
3. remove only those exact validated files/directories;
4. verify that the targets are absent;
5. delete `doc_status` / `full_docs` and associated data;
6. clear the deletion journal only after completion.

Steps 3–4 run per artifact kind (`source`, `parsed`) under the corresponding per-`(doc_id, artifact_kind)` artifact-export lock (§Export jobs and independent build scheduling): the lock for a kind is acquired before removing that kind's files and released once its targets are confirmed absent. This keeps a concurrent export build for the *same* document from reading a file this deletion is about to remove, while never blocking an export for any other document, or a different kind of the same document once its own removal has completed. Before acquiring a contended lock, deletion first requests cancellation of any `inflight` export job on that key (§Per-artifact keyed lock) instead of waiting for it to run to completion, so a slow export cannot silently extend how long this deletion — and the `destructive_busy` state it already holds — takes to finish.

A partial failure preserves enough journal state for an idempotent retry. The `delete_file=true` branch must not call a basename variant sweep. The default `DELETE /documents` record-clear operation does not clear the workspace artifact root; any future filesystem-clear option must be separately explicit and run under the destructive reservation.

### Retention of the archived source is deliberate, not incomplete cleanup

Exact locator cleanup above describes **how** a filesystem artifact is removed once removal is requested. It does not make removal the default, and the current retention behavior is a safety property that must be preserved:

- `DELETE /documents/{doc_id}` defaults to `delete_file=false`. It deletes the storage records (`doc_status`, `full_docs`, chunks, KG contributions) and leaves the archived source file together with its sibling `.parsed/`, `.mineru_raw/`, and `.docling_raw/` directories in `/__parsed__/` untouched.
- Only `delete_file=true` removes those filesystem artifacts. The journaled exact-locator procedure above governs that branch alone; the default branch has no filesystem targets to journal.
- `DELETE /documents` (clear) currently deletes only top-level files in the configured input directory and preserves subdirectories, so `__parsed__` survives a full clear as well. If this RFC ever lets clear reach the artifact root, that must stay an explicitly requested destructive action — never an implied side effect of clearing records.

Two reasons the archive is kept:

1. **Safety.** Ingestion moves the source out of the input directory, so the copy under `__parsed__/` is the only remaining copy of the user's original file. Removing a document from the index must not silently destroy it. Deletion is reversible by default; irreversibility is opt-in.
2. **Diagnostics.** The preserved source and the parser output next to it stay inspectable after the document has left the index, which is how parse and extraction defects are reproduced.

Re-ingesting a deleted document therefore needs no export, no re-upload, and no API that reads `__parsed__`: move the archived file back to its parent input directory and rescan.

```text
mv /__parsed__/report.[mineru-iet].pdf /
POST /documents/scan
```

The scan re-derives `doc_id` from the canonical filename, re-persists `source_location` and `source_archive_location`, reuses the existing `report.pdf.parsed/` directory in place, and re-archives the source on completion. `__parsed__` is skipped by scan discovery, so nothing is re-ingested until an operator moves a file out of it. This recovery path is a local filesystem action and does not widen the non-goal *"Exposing arbitrary files below `INPUT_DIR` or `__parsed__`"*.

Consequence for the orphan rules in §**Why basename lookup is invalid** and §**Source-location lifecycle and orphan rotation**: a source retained by a default (`delete_file=false`) deletion is an *expected, correct* occupant of a canonical archive target, not evidence of incomplete cleanup. It is exactly the case step 6 rotates aside after a strict storage read proves no valid document currently names that location — a further reason basename lookup cannot define document ownership.

## Artifact availability

```http
GET /documents/{doc_id}/artifacts
```

This returns public kinds, availability/export capability, public filenames, and export-request URLs. It never returns relative internal locators, absolute paths, raw `file://` URIs, move state, or cache paths.

Required permission: `documents.artifacts.read`.

## Asynchronous ZIP export API

Source and parsed artifacts use the same three-stage export workflow.

### 1. Request an export

```http
POST /documents/{doc_id}/artifacts/{artifact_kind}/exports
```

Initial kinds:

| Kind | Authoritative input | Permission | ZIP result |
| --- | --- | --- | --- |
| `source` | `doc_status.metadata.source_location` | `documents.artifacts.source.download` | `.source.zip` containing one entry named after the document's canonical `file_path` |
| `parsed` | `full_docs.sidecar_location` | `documents.artifacts.parsed.download` | `.parsed.zip` containing a top-level `.parsed/` directory |

The endpoint validates authorization before document lookup, then either joins an in-flight export job for the same `(doc_id, artifact_kind)` or creates a new bounded export job (§Export jobs and independent build scheduling), and returns `202 Accepted` with that job's `track_id`:

```json
{
  "track_id": "artifact-...",
  "status": "queued",
  "status_url": "/documents/artifact-exports/source/artifact-...",
  "download_url": "/documents/artifact-exports/source/artifact-.../download"
}
```

A request that joins an in-flight job may receive `status: "running"` instead of `"queued"` if the join happens after the build has already started; either way the returned `track_id` is shared with every other concurrent requester for the same artifact.

### 2. Query export status

```http
GET /documents/artifact-exports/{artifact_kind}/{track_id}
```

States:

```text
queued
running
ready
failed
expired
cancelled
```

Public status fields may include `track_id`, kind, public filename, timestamps, compressed/uncompressed sizes, file count, status, and sanitized error code/message. Internal locators, cache paths, owner tokens, PIDs, and credentials are never returned.

### 3. Download a ready ZIP

```http
GET /documents/artifact-exports/{artifact_kind}/{track_id}/download
```

| State/condition | HTTP |
| --- | --- |
| `ready` and cached ZIP present | `200` |
| `queued` or `running` | `425` |
| `failed` | `409` |
| previously ready but expired/evicted | `410` |
| track ID never existed | `404` |
| serving concurrency full | `429` with `Retry-After` |
| job/cache provider unavailable | `503` |

The explicit `artifact_kind` path segment lets the route declare and enforce the kind-specific permission before track/document existence lookup. After authorization, the handler verifies that the stored job kind matches the path. A track ID is not a credential.

## Export jobs and independent build scheduling

*(Renamed from "Export jobs, mailbox, and pipeline scheduling": building is no longer owned by, or scheduled through, the document-ingestion pipeline. Concurrent downloads of the same or different artifacts need no pipeline coordination at all.)*

The export design separates three responsibilities:

```text
ArtifactExportJobStore  = source of truth for job records
Per-artifact keyed lock = narrow protection against a filesystem race, shared with ingestion
Local ZIP cache         = immutable result bytes
```

There is no mailbox and no pipeline-owned build step. A request either joins an in-flight build or starts a new one in its own bounded task, independent of `pipeline_status` and `pipeline_ingress`; the document-ingestion pipeline never learns that an export happened, and an export never sets `pipeline_status.busy`.

### Job store

`ArtifactExportJobStore` is not a new subsystem with its own lock and multiprocess plumbing. It is a shared, workspace-scoped dict obtained via `get_namespace_data("artifact_export_jobs", workspace=...)` and mutated only under `get_namespace_lock("artifact_export_jobs", workspace=...)` — the exact mechanism `pipeline_status` already uses (`initialize_pipeline_status()` / `get_namespace_data("pipeline_status", ...)` in `lightrag/kg/shared_storage.py`), pointed at a new namespace name instead of a new class hierarchy. This gets single-process vs. Manager-backed multi-worker support and per-workspace isolation for free from existing infrastructure; no Hub, no explicit `BaseProxy`, no second parallel shared-state mechanism.

An `initialize_artifact_export_status(workspace=None)` bootstrap, called at the same lifespan point as `initialize_pipeline_status()`, seeds the namespace once per process with three top-level keys — `jobs`, `inflight`, `running_builds` — exactly as `initialize_pipeline_status()` seeds `busy`, `scanning`, `pending_enqueues`, and the rest into one namespace dict. `jobs` and `inflight` are seeded as `manager.dict()` under multiprocess mode (a plain `{}` otherwise) — the same trick `pipeline_status.history_messages` already uses as a `manager.list()` — specifically so `namespace["jobs"][track_id] = record` is a live mutation through a real proxy and propagates across workers, rather than a copy-on-read no-op: a value read out of a `manager.dict()` is a plain, disconnected copy, so mutating it in place would silently not persist. Every job update therefore replaces a record wholesale — `namespace["jobs"][track_id] = new_record`, never a mutation of an existing record's individual keys — which is also exactly the shape CAS-by-version-check-then-write needs anyway.

One record per `track_id`, with the CAS/owner-lease/terminal-transition shape already proven for `/scan` job tracking (`lightrag/kg/scan_job_store.py`): create is idempotent on an existing `track_id`, an update is refused on owner or version mismatch, and a lease-expired `running` job is reaped to `failed` on any later read — only the storage/locking underneath differs, from that module's own `threading.Lock` + Manager-hub, to the `get_namespace_data`/`get_namespace_lock` pair above:

```text
track_id, doc_id, artifact_kind, owner_token
status: queued | running | ready | failed | expired | cancelled
version, created_at, updated_at, lease_expires_at
compressed_size, uncompressed_size, file_count      (set on ready)
error_code, error_message                            (sanitized, set on failed)
```

A builder whose lease expires is CAS-transitioned straight to `failed` with a sanitized `builder_lease_expired` error code (see §Crash recovery below) — the public state machine stays exactly the six states in §Query export status; there is no separate public "abandoned" state.

The namespace additionally holds, in the same dict and guarded by the same lock:

```text
inflight: {(doc_id, artifact_kind) -> track_id}   # queued/running jobs only
running_builds: int                                 # <= MAX_DOWNLOAD_BUILD_CONCURRENCY
```

Keeping `jobs`, `inflight`, and `running_builds` together under one lock is what lets admission (§Request handling) check and update all three atomically in one critical section — the same reason `pipeline_status` keeps `busy`, `scanning`, and `pending_enqueues` under one lock instead of three independent ones.

### Per-artifact keyed lock

A second, unrelated lock protects the one thing genuinely shared with the ingestion pipeline: the source/sidecar file for one `doc_id`. It uses the same keyed-lock mechanism that already serializes entity/edge mutation, keyed by `(doc_id, artifact_kind)`. A build task holds it only from resolving the input locator through reading the last input byte; §Source-location lifecycle and orphan rotation and §Exact artifact deletion acquire the same key before moving or removing that document's files. Neither side ever holds a pipeline-wide busy state on the other's behalf, and neither side blocks an operation on a *different* `doc_id`.

The parser takes the same `(doc_id, "parsed")` key at the very start of every parse attempt — before it touches an existing sidecar directory at all — and holds it until that attempt reaches a terminal outcome for the directory: either the new output is fully written and `full_docs.sidecar_location` is (re-)synced, or the attempt fails. The lock must be acquired before the first mutation, not "while writing": at least one supported external parser (mineru) clears an existing `*.parsed/` directory as its first step and only populates it afterward, so a lock taken at "start of write" would still leave a window — for the parser's entire running time, not just an instant — where the directory is empty or partial and unlocked. Acquiring the lock before that clear closes the window entirely, for the whole duration of the call to an external parser service. This guarantee does not depend on whether the parser writes files into the directory incrementally or stages-then-publishes; the lock alone is sufficient regardless of the parser's internal write strategy. During a document's first-ever parse this lock is never contended, because no `sidecar_location` is published (and so no export can pass admission) until that parse succeeds — see §Availability and half-processed content.

Deletion resolves a lock conflict actively rather than waiting it out: before `adelete_by_doc_id`'s filesystem-cleanup branch or `clear_documents` (§Exact artifact deletion) acquires a `(doc_id, artifact_kind)` lock, it first checks the job store for an `inflight` job on that key and, if one exists, requests its cancellation (an owner-checked transition to `cancelled`, the same shape as `ScanJobStore.cancel`). The build task checks for a pending cancellation between input entries — a cheap, already-existing iteration boundary — and aborts promptly, releasing the lock instead of running to completion first. Deletion then acquires the now-uncontended lock. This keeps a slow export from silently prolonging `destructive_busy` (which already blocks new enqueues for unrelated documents per the existing pipeline contract) for the export's full `MAX_DOWNLOAD_PREPARE_SECONDS` budget.

### Availability and half-processed content

"Confirm the artifact is available" in request-handling step 1 below is deliberately a cheap, advisory existence check, not a race-free guarantee — its only job is to avoid creating a build for content that was never produced:

- `source`: available whenever `doc_status.metadata.source_location` is persisted and resolves to an existing regular file. This is true from shortly after enqueue onward, because the RFC's own model guarantees the source bytes never change in place — only their location does, which the per-artifact lock already governs (§Source-location lifecycle and orphan rotation, step 11). `doc_status`'s processing stage is irrelevant here: no operation ever rewrites the source's bytes in place, so there is no in-progress state that could produce a torn or empty read.
- `parsed`: available whenever `full_docs.sidecar_location` is persisted for that `doc_id` **and** `doc_status.status == PROCESSED`. Unlike `source`, `doc_status` is part of this predicate on purpose: because a parse attempt can clear an existing sidecar directory before repopulating it (§Per-artifact keyed lock), `sidecar_location` being persisted is *not* by itself proof that the directory currently holds valid content — the metadata is unchanged across a retry even while the directory underneath is briefly empty or, if that retry fails, potentially left incomplete. Requiring `PROCESSED` excludes every one of those in-progress and failed-mid-retry cases at the cheap, advisory-check stage, before a build is ever created.

  This is a deliberate trade-off: a document that reaches `FAILED` *after* a prior successful parse (e.g. a later extraction step fails) has an intact, complete `*.parsed/` directory that this rule makes undownloadable via the export API until the document is reprocessed to `PROCESSED` again — weakening, for this one path, the kind of parse/extraction-defect diagnosis the retention rationale in §Retention of the archived source is deliberate relies on for deleted documents. The alternative (excluding only the transient `PARSING`/`PROCESSING` states, not `FAILED`) does not fully close the gap it looks like it closes: it would still admit a `FAILED` document whose last retry's clear-then-write left the directory incomplete, since nothing in that weaker rule forces `sidecar_location` to be invalidated when a retry fails. `PROCESSED`-only avoids depending on the pipeline's failure path getting that invalidation right, at the cost of the diagnostic case above. If that trade is wrong for a given deployment, the fix is on the pipeline side — never leave `sidecar_location` pointing at a directory a failed retry has touched — not a weaker availability predicate here.

  Before a document's first successful parse, neither `sidecar_location` nor `PROCESSED` holds, so the check alone already returns "unavailable" (`404`) — there is nothing yet to protect, so there is no race to resolve.

The advisory check above only decides whether it is worth creating a build at all — it is checked once, at request time, and can go stale immediately after. The actual guarantee against downloading half-processed content is the per-artifact lock (§Per-artifact keyed lock), enforced at build time, after admission, and unconditionally on both sides. If a retry starts rewriting a document's `*.parsed/` directory at the moment a client requests the `parsed` artifact, or at the moment an admitted build starts reading it, exactly one of two things happens:

1. the build's lock acquisition (§Build task, step 2) happens first, and it reads the complete pre-retry output; or
2. the retry's lock acquisition happens first, and the build blocks until the rewrite finishes, then reads the complete post-retry output.

There is no interleaving between these two outcomes — never a torn, half-written read, regardless of what the advisory check observed a moment earlier. A build that cannot acquire the lock within its own `MAX_DOWNLOAD_PREPARE_SECONDS` budget fails the job with a sanitized `artifact_busy` error rather than blocking indefinitely; the client may simply retry the export request.

A concurrent delete racing an export request the other way is likewise self-healing without extra mechanism: if `adelete_by_doc_id` has already removed a document's files and its records by the time a build re-resolves the locator (§Build task, step 3), that read fails cleanly (locator or record not found) and the job transitions to `failed` — the same handled-failure path as any other storage error, not a special case.

### Request handling

`POST /documents/{doc_id}/artifacts/{artifact_kind}/exports`:

1. authorize, then confirm the artifact is available per §Availability and half-processed content (read-only; no lock needed);
2. under the job store's own lock: if `(doc_id, artifact_kind)` is already `inflight`, return `202` with the existing `track_id` — every concurrent requester for the same artifact converges on one build and one `track_id`, which is also the ZIP's filename, so dedup and "same file for everyone" are one mechanism, not two;
3. otherwise, if `running_builds < MAX_DOWNLOAD_BUILD_CONCURRENCY` and cache capacity can be reserved, mint a `track_id`, create the job (`queued`), record it in `inflight`, and increment `running_builds`; if either budget is exhausted, refuse immediately (`429`, or `503` if the store itself is unavailable) — a refused request is never queued behind anything;
4. spawn the build as an ordinary bounded task (not a pipeline job) and return `202`.

### Build task

1. CAS-transition to `running`; start the lease heartbeat (`lease_expires_at`, renewed periodically; default lease duration `ARTIFACT_EXPORT_BUILD_LEASE_SECONDS`);
2. acquire the per-`(doc_id, artifact_kind)` lock;
3. strictly re-resolve the input locator from storage — never from anything cached at request time;
4. stream input bytes into `{track_id}.partial` under the local cache, enforcing every bound in §Resource limits and §ZIP construction and cache publication while reading, not only from an initial stat;
5. release the per-artifact lock as soon as the last input byte has been read — nothing from here on reads the source, only the ZIP file itself, so the source is free the instant its bytes are captured;
6. fsync, atomically rename `.partial` to `.zip`, CAS-transition to `ready` with final sizes;
7. under the job store's own lock, remove `(doc_id, artifact_kind)` from `inflight` and decrement `running_builds`.

A handled failure (a bound exceeded, a storage error) removes the partial file, transitions the job to `failed` with a sanitized code, and performs the same step-7 cleanup.

### Crash recovery

An unhandled crash (the worker process is killed) skips straight past that cleanup: heartbeats stop, and the build lease — not a watchdog process — is what notices. Any later read of the record (a status query, a new export request for the same key, or one of the maintenance triggers in §Event-driven cache expiration, eviction, and active-download leases) opportunistically reaps an expired-lease `running` job to `failed`, lazily, consistent with the non-goal of no periodic per-worker cleanup task; this also frees the stale `inflight`/`running_builds` bookkeeping.

The `.partial`/`.zip` filename suffix is what makes cleanup of a crash-orphaned file *safe*, not merely informative. A `.zip` exists only via the atomic rename in build-task step 6, so its presence on disk already proves completion; no record lookup is needed to trust one. A `.partial` file proves nothing by itself and must be checked against the job store before it is touched: a maintenance sweep parses `track_id` from each `{track_id}.partial` it finds and looks it up —

- record present, status `running`, lease still valid → a build is genuinely in progress; leave it;
- record missing, or present but terminal → crash debris; delete it immediately.

There is no third case, because a `.partial` file is never downloadable either way — only "is it safe to delete yet" depends on the record. Startup is the trivial instance of this rule: the job store is reconstructed empty on process start (ephemeral across restarts, per the existing non-goal), so every `.partial` and every `.zip` found at that point is unconditionally orphaned and removed.

A client polling `GET /documents/artifact-exports/{artifact_kind}/{track_id}` therefore sees `running` while the lease is alive and `failed` within one lease interval of an actual crash, with no client-side timeout logic and no need to reason about files on disk — the job record is the only contract.

## Resource limits

All values are byte counts or positive integers. Invalid/non-positive configured values fail startup; normal authorized mode does not silently become unlimited.

An environment variable is a permanent compatibility surface the moment it ships, so only two of these values are exposed that way; the rest are plain constants in `lightrag/constants.py`, following the same reasoning that module already applies to other unauthenticated-request ceilings: a resource-exhaustion bound is worth nothing if an operator who doesn't understand its purpose can misconfigure it away, and a concurrency knob whose right value depends on server hardware and this subsystem's own implementation is not something an `.env` file should be guessing at.

### Environment variables

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `MAX_DOWNLOAD_SIZE` | `3 * MAX_UPLOAD_SIZE`, or 300 MiB when upload is unlimited | Maximum completed ZIP bytes |
| `DOWNLOAD_CACHE_TTL_SECONDS` | `86400` | Age at which a ready ZIP becomes logically expired and eligible for lazy reclamation |

`MAX_DOWNLOAD_SIZE` is operator-facing for the same reason `MAX_UPLOAD_SIZE` already is: it trades directly against a deployment's disk and network budget, and raising the upload ceiling is an immediate, obvious reason to raise this one too. `DOWNLOAD_CACHE_TTL_SECONDS` is operator-facing because it is a retention/disk-usage policy, not a safety bound — how long a finished export stays around before reclamation is a legitimate per-deployment choice with no single correct value.

### Internal constants (`lightrag/constants.py`)

| Constant | Default | Meaning |
| --- | ---: | --- |
| `DEFAULT_MAX_DOWNLOAD_UNCOMPRESSED_SIZE` | `2147483648` (2 GiB) | Maximum sum of regular-file bytes before compression |
| `DEFAULT_MAX_DOWNLOAD_FILE_COUNT` | `10000` | Maximum regular files in one export |
| `DEFAULT_MAX_DOWNLOAD_DIRECTORY_DEPTH` | `16` | Maximum parsed directory depth |
| `DEFAULT_MAX_DOWNLOAD_PREPARE_SECONDS` | `600` | Maximum validation/compression time |
| `DEFAULT_MAX_DOWNLOAD_BUILD_CONCURRENCY` | `5` | Concurrent ZIP builders across the whole server (a shared counter, not scoped to any one worker or the pipeline) |
| `DEFAULT_ARTIFACT_EXPORT_BUILD_LEASE_SECONDS` | `60` | Build-lease duration; a `running` job whose lease is not renewed within this window is reaped to `failed` |
| `DEFAULT_MAX_DOWNLOAD_SERVE_CONCURRENCY` | `5` | Cross-worker concurrent ZIP responses |
| `DEFAULT_MAX_DOWNLOAD_CACHE_COUNT` | `20` | Maximum cache entries across running reservations and ready/expired objects awaiting reclamation |
| `DEFAULT_MAX_DOWNLOAD_CACHE_BYTES` | `5 * MAX_DOWNLOAD_SIZE` | Maximum reserved or physical bytes across running, partial, ready, and expired-not-yet-reclaimed results |
| `DEFAULT_MAX_DOWNLOAD_JOB_RECORDS` | `1000` | Maximum compact job/tombstone records |
| `DEFAULT_DOWNLOAD_JOB_RECORD_TTL_SECONDS` | `86400` | Age at which a terminal job record becomes eligible for lazy reclamation |

None of these has a legitimate range of "correct" per-deployment values the way `MAX_DOWNLOAD_SIZE` or the cache TTL do: each is either a hard-coded abuse/resource-exhaustion ceiling (file count, depth, prepare time), a concurrency knob tied to this subsystem's own implementation rather than deployment policy, or derived arithmetic (`DEFAULT_MAX_DOWNLOAD_CACHE_BYTES` as a multiple of `MAX_DOWNLOAD_SIZE`, the same relationship `MULTIPART_OVERHEAD_BYTES` already has to `MAX_UPLOAD_SIZE` elsewhere in `constants.py`). `DEFAULT_ARTIFACT_EXPORT_BUILD_LEASE_SECONDS` in particular is an internal crash-detection timer (§Crash recovery), never deployment policy, and belongs here even though it was listed as an environment variable earlier in this redesign — that was an oversight, corrected here. `MAX_DOWNLOAD_JOB_RECORD_TTL_SECONDS` is deliberately not the one TTL kept operator-facing either: it governs how long *bookkeeping records* linger, which has no operator-visible effect, unlike `DOWNLOAD_CACHE_TTL_SECONDS`, which governs how long the actual downloadable ZIP stays available.

(`MAX_DOWNLOAD_JOBS_PER_CYCLE` is removed outright, not merely relocated: there is no pipeline export cycle left to bound.)

A 2 GiB uncompressed limit is a safety ceiling, not a promise that a 2 GiB image artifact is downloadable. Image-heavy inputs compress poorly and will normally hit `MAX_DOWNLOAD_SIZE` first.

Before claiming/building a job, reserve one cache entry and `MAX_DOWNLOAD_SIZE` bytes. Capacity accounting includes running reservations, partial files, ready ZIPs, and expired objects awaiting physical reclamation. The builder first performs a bounded lazy-reclamation pass, then evicts the oldest eligible `ready` result by job `ready_at`. Filename ordering, filesystem iteration order, and mtime do not define age.

TTL is an eligibility boundary, not an exact wall-clock deletion guarantee. A logically expired ZIP may remain physically present until the next cache event, but it is no longer downloadable and still counts toward the hard cache bounds until reclaimed.

If capacity cannot be reserved because all candidates are actively downloading, do not delete them and do not exceed the hard bound. Leave the export queued for a bounded retry or fail it with a sanitized `cache_capacity` error.

## ZIP construction and cache publication

The cache is accessed through a small artifact-cache backend interface. Version 1 uses the single host's local directory `/artifact_exports`, not OS temporary storage. Internal names are derived only from a validated high-entropy server-generated track ID:

```text
.partial
.zip
```

Build rules:

1. resolve and validate the exact persisted input locator while holding the per-`(doc_id, artifact_kind)` artifact-export lock (§Export jobs and independent build scheduling);
2. recursively enumerate with bounded memory and no symlink following;
3. accept only regular files and ordinary directories;
4. reject symlinks, FIFO/socket/device entries, absolute/`..`/NUL entry names, duplicate entry names, and entries escaping the exact root;
5. enforce uncompressed bytes, file count, depth, and preparation timeout while reading, not only from an initial stat;
6. write `ZIP_DEFLATED` at fixed compression level 6 with ZIP64 support;
7. stop and remove the partial output once compressed bytes exceed `MAX_DOWNLOAD_SIZE`;
8. fsync the completed temporary file as required by the local cache implementation;
9. atomically rename `.partial` to `.zip`;
10. mark the job `ready` only after the final file exists with its verified size.

The artifact-export lock from rule 1 is held through rule 6 for each input entry and released once the last one is read (§Build task, step 5); rules 7–10 operate only on the ZIP file already being written and require no lock.

An interrupted partial ZIP is never downloadable. Startup removes all `.partial` files and cached ZIPs without a valid live job record (§Crash recovery), then performs a bounded expiration/reclamation pass. Version 1 export jobs are ephemeral across server restart; clients submit a new request.

A future S3-backed cache may replace the local backend without changing routes, JobStore states, leases, or `410` semantics. S3 Lifecycle may reclaim physical objects as a backstop, but it is not the authoritative job-state transition. Its deletion threshold must not precede the application expiry policy; if an object is nevertheless absent, the application atomically records the result as expired and returns `410`.

## Event-driven cache expiration, eviction, and active-download leases

Version 1 creates no periodic ZIP-cleanup task per API worker. Cleanup is a bounded, idempotent maintenance step triggered by existing artifact-cache activity:

- server startup;
- export request and cache-capacity reservation;
- successful or failed ZIP build completion;
- export-status query;
- download admission before creating a lease;
- download-lease release;
- a capacity-reservation failure before the caller retries or fails.

Any worker may initiate a maintenance pass; the shared `ArtifactExportJobStore`'s own CAS/locking (never a pipeline lock) serializes logical state transitions, the single-flight `inflight` map, and download leases. ZIP construction runs in the export subsystem's own bounded builder task, never the ingestion pipeline; cache reclamation does not need to hold the per-`(doc_id, artifact_kind)` artifact lock either, because a published ZIP is immutable and — per §Build task, step 5 — that lock is already released the moment the source bytes are captured, well before the object reaches `ready`. Maintenance therefore only ever touches the ZIP object, its job record, and its download leases; it never contends with the ingestion pipeline, and never contends with a concurrent build/mutation of the *same* document either, because by the time a `ready` object exists that lock has already been released.

Two independent lease concepts exist on the same job record, covering two different lifecycle phases, and must not be conflated:

| | Build lease | Download lease |
| --- | --- | --- |
| Fields | `owner_token`, `lease_expires_at` | `lease_id`, `track_id`, owner PID, heartbeat time |
| Held while | status is `running` (a builder is producing `.partial`) | status is `ready` and a client is mid-download |
| Purpose | detect a builder that died mid-build, so the job reaps to `failed` without a watchdog process | serving-concurrency accounting; block physical deletion while a client reads the ZIP |
| Cardinality | at most one per job | zero or more per job, one per concurrent downloader |

A job never holds both at once: a `running` job has at most a build lease and zero download leases (nothing is downloadable yet); a `ready` job has no build lease (the builder already exited cleanly or was reaped) and zero-or-more download leases.

Serving concurrency is enforced across all workers with a shared download-lease gate. A provider failure fails closed. Download admission atomically verifies that the job is `ready`, `now < expires_at`, the immutable cache object is present, and capacity is available, then creates a unique download lease containing `lease_id`, `track_id`, owner PID, and heartbeat time before opening the ZIP. Multiple clients may hold independent download leases for the same `track_id`. Response completion, cancellation, or disconnect releases only that request's lease in a cancellation-safe callback/finally; PID/heartbeat expiry reclaims download leases after worker death.

When `now >= expires_at`, a maintenance-triggering operation atomically transitions `ready -> expired`. This immediately blocks new download leases and makes status/download return `expired`/`410`. A download admitted before expiration may finish. Physical deletion occurs only when that `track_id` has no live download lease; releasing the last lease retries deletion of an already-expired object.

Capacity eviction selects the oldest eligible `ready` result by job `ready_at`, but may atomically transition and delete it only when it has no live download lease. Logical expiration or eviction retains a compact bounded tombstone. A deletion failure leaves a reclaimable expired record and is retried by a later trigger; it never makes the object downloadable again. Terminal job-record cleanup is lazy as well, removes the oldest eligible records first, and never removes queued/running jobs (which still hold their build lease) or ready records with live download leases.

If no artifact-cache activity occurs after a TTL boundary, an expired-by-age local ZIP may remain on disk until the next trigger or restart. This is intentional: cache entry and byte reservations remain hard bounds, so idle retention cannot create unbounded growth, and exact-to-the-second physical deletion is not part of the contract.

## HTTP and audit semantics

- `401`: missing or invalid credentials.
- `403`: authenticated principal lacks the required permission.
- `404`: an authorized caller requested a document/artifact/job that never existed or is unavailable without an inconsistency.
- `409`: locator, filesystem type, path, or build-state inconsistency; failed export download.
- `410`: generated result expired or was evicted.
- `413`: configured input/output/file-count/depth limit exceeded when detected synchronously; asynchronous jobs expose the equivalent sanitized limit error in `failed` status.
- `425`: export is not ready.
- `429`: serving/job-store capacity temporarily unavailable.
- `503`: authorization, job, cache, or shared-control-plane provider unavailable.

Audit events cover export request, claim, success/failure, eviction/expiry, download allow/deny/start/finish/cancel, byte counts, principal, permission, `doc_id`, `track_id`, and artifact kind. They never contain credentials, internal locators, absolute paths, cache paths, ZIP contents, or owner tokens.
