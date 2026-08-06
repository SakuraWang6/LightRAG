# Simplifying artifact-export scheduling (issue #3516)

Source RFC: https://github.com/HKUDS/LightRAG/issues/3516, section
"Export jobs, mailbox, and pipeline scheduling". This note redesigns that
section only; the rest of the RFC (artifact identity, orphan rotation,
exact deletion, authorization model) is unaffected.

## Problem with the current design

The RFC schedules ZIP building as a job *owned by the document-ingestion
pipeline*: exports are claimed and built only by the pipeline owner,
`pipeline_status.busy` is held for the duration of the build, requests
are woken via the `pipeline_ingress` mailbox, and jobs are claimed in
bounded batches (`MAX_DOWNLOAD_JOBS_PER_CYCLE`) between quiescence
checks. That coupling causes three problems:

1. **Wrong blast radius.** Downloading one document's artifact sets
   `pipeline_status.busy = True` for the whole workspace, throttling or
   refusing unrelated uploads/scans/enqueues via the same guard used by
   `/documents/clear` and `/documents/{doc_id}` deletion.
2. **Manufactured fairness problem.** Because builds run inside the
   pipeline owner, the RFC then needs bounded fair interleaving so
   "continuous exports cannot starve document work" and vice versa —
   machinery that exists only to undo the coupling just introduced.
3. **Wrong abstraction.** The mailbox exists so the ingestion loop learns
   "there is more `doc_status` work" without polling. An export job does
   not need to wake a shared scheduler; it needs to run once,
   independently, as soon as it is admitted.

The redesign below removes pipeline ownership entirely: export building
becomes its own small, self-contained subsystem that never touches
`pipeline_status` or `pipeline_ingress`, and is compatible with — but
independent of — the existing insert/delete pipeline.

## Components

```text
ArtifactExportJobStore  = source of truth for job records (per track_id)
Per-artifact keyed lock = get_storage_keyed_lock(["{doc_id}:{kind}"], namespace="artifact_export")
Local ZIP cache         = /artifact_exports/{track_id}.partial -> {track_id}.zip
```

`ArtifactExportJobStore` is not a new subsystem with its own lock and
multiprocess plumbing — it is a shared, workspace-scoped dict obtained
via `get_namespace_data("artifact_export_jobs", workspace=...)` and mutated
only under `get_namespace_lock("artifact_export_jobs", workspace=...)`: the
exact mechanism `pipeline_status` already uses
(`initialize_pipeline_status()` / `get_namespace_data("pipeline_status",
...)` in `lightrag/kg/shared_storage.py`), pointed at a new namespace
name instead of a new class hierarchy. This gets single-process vs.
Manager-backed multi-worker support and per-workspace isolation for
free; no Hub, no explicit `BaseProxy`, no second parallel shared-state
mechanism alongside the one the rest of the pipeline already relies on.

An `initialize_artifact_export_status(workspace=None)` bootstrap, called
at the same lifespan point as `initialize_pipeline_status()`, seeds the
namespace once per process with three top-level keys — `jobs`,
`inflight`, and `running_builds` — exactly as `initialize_pipeline_status()`
seeds `busy`, `scanning`, `pending_enqueues`, and the rest into one
namespace dict. `jobs` and `inflight` are seeded as `manager.dict()`
under multiprocess mode (a plain `{}` otherwise) — the same trick
`pipeline_status.history_messages` already uses as a `manager.list()` —
specifically so `namespace["jobs"][track_id] = record` is a live
mutation through a real proxy and propagates across workers, rather than
a copy-on-read no-op: a value read out of a `manager.dict()` is a plain,
disconnected copy, so mutating it in place would silently not persist
(the same hazard `test_json_doc_status_copy_on_read.py` /
`test_json_kv_copy_on_read.py` already pin down for other storage). Every
job update therefore replaces a record wholesale —
`namespace["jobs"][track_id] = new_record`, never a mutation of an
existing record dict's individual keys — which is also exactly the shape
CAS-by-version-check-then-write needs anyway.

Two *different* lock mechanisms serve two different jobs, and the design
only works if they stay separate:

- `get_namespace_lock("artifact_export_jobs", workspace=...)` guards the job
  store's own bookkeeping only: job records, the single-flight `inflight`
  map, and the `running_builds` counter, all in one namespace so
  admission (§Request flow) can check and update all three atomically in
  one critical section — the same reason `pipeline_status` keeps `busy`,
  `scanning`, and `pending_enqueues` under one lock instead of three.
- The **per-artifact keyed lock** (`get_storage_keyed_lock`, already used
  cross-module for entity/edge mutation) guards the one thing that is
  genuinely shared with the ingestion pipeline: the actual source/sidecar
  *file* for one `doc_id`. It has to be reachable from both
  `lightrag/pipeline.py` and wherever the export builder lives, which is
  exactly what `get_storage_keyed_lock` is for.

## Data model

One job record per `track_id`. The CAS/owner-lease/terminal-transition
shape mirrors the one already proven for `/scan` job tracking
(`lightrag/kg/scan_job_store.py`): create is idempotent on an existing
`track_id`, an update is refused on owner or version mismatch, and a
lease-expired `running` job is reaped to `failed` on any later read —
only the storage/locking underneath changes, from that module's own
`threading.Lock` + Manager-hub, to the `get_namespace_data`/
`get_namespace_lock` pair above.

```text
track_id            high-entropy, path-safe, server-generated
doc_id, kind         "source" | "parsed"
owner_token          identifies the current builder (crash detection)
status               queued | running | ready | failed | expired | cancelled
                     (+ internal-only "abandoned" reason, surfaced as "failed")
version              CAS counter
created_at / updated_at / lease_expires_at
compressed_size, uncompressed_size, file_count   (set on ready)
error_code, error_message                        (sanitized, set on failed)
```

Store-level (not per-record) bookkeeping, in the same namespace, guarded
by the same lock:

```text
inflight: {(doc_id, kind) -> track_id}   # queued/running jobs only
running_builds: int                       # <= MAX_DOWNLOAD_BUILD_CONCURRENCY
```

## Request flow

`POST /documents/{doc_id}/artifacts/{kind}/exports`:

1. Authorize, then confirm the artifact is available (Part I semantics).
   Read-only against `doc_status`/`full_docs` — no lock needed yet.
2. Under the job store's own lock:
   - `(doc_id, kind)` already in `inflight`? Return `202` with that
     existing `track_id` (join the in-flight build). **This is the same
     step that gives every concurrent requester for the same artifact the
     same `track_id`** — dedup and "same file for everyone" are one
     mechanism, not two, because the ZIP filename is `<track_id>.zip`.
   - Otherwise, check `running_builds < MAX_DOWNLOAD_BUILD_CONCURRENCY`
     and that cache capacity (bytes/count, per the RFC's existing
     resource-limits table) can be reserved.
     - Over budget → refuse immediately (`429`, or `503` if the store
       itself is unavailable). No unbounded queueing: this is the
       direct fix for "任务数量超出预算后拒绝新的下载任务" — refuse
       new work outright rather than piling it up behind a cycle.
     - Capacity available → mint `track_id`, create the job record
       (`queued`), write `inflight[(doc_id, kind)] = track_id`, increment
       `running_builds`.
3. Outside the lock, spawn the build (an ordinary `asyncio.create_task`,
   or one slot of a small bounded worker pool) and return `202` with
   `track_id` / `status_url` / `download_url`. No mailbox publish, no
   `pipeline_status` touch.

## Build task

1. CAS-transition the job to `running` (a whole-record replace under
   `get_namespace_lock("artifact_export_jobs", ...)`); start the same
   heartbeat/lease renewal `ScanJobStore.update` already does, just
   against the namespace dict instead of that module's own lock.
2. Acquire `get_storage_keyed_lock(["{doc_id}:{kind}"], namespace="artifact_export")`.
3. Strictly re-resolve the input locator (`source_location` /
   `sidecar_location`) fresh from storage — never trust anything cached
   from the request step (same "never trust copied state, re-read the
   source of truth" principle the RFC already applies to the mailbox).
4. Stream/copy bytes into `{track_id}.partial` under
   `/artifact_exports/`, enforcing every bound the RFC already specifies
   unchanged (uncompressed bytes, file count, depth, prepare-time
   timeout, `MAX_DOWNLOAD_SIZE` on compressed output, no symlinks, no
   escaping entries).
5. **Release the per-artifact lock as soon as all input bytes are read
   into the (still-partial) ZIP.** Nothing after this point reads the
   source — fsync, atomic rename, and the final size stat touch only the
   ZIP file — so it is safe to let a waiting delete/rotation/retry on
   this `doc_id` proceed immediately. This is the precise version of
   "一旦zip完成即可释放产物，因为zip是脱离产物的独立存在": the lock's
   release point is defined by *last source read*, not by *ZIP fully
   published*, which is what makes the lock's held duration as short as
   possible without weakening the guarantee.
6. fsync, atomically rename `.partial` → `.zip`, CAS-transition the job
   to `ready` with final sizes.
7. Under the job store's lock: remove `(doc_id, kind)` from `inflight`,
   decrement `running_builds`.
8. On any handled failure (bound exceeded, storage error): remove the
   partial file, CAS-transition to `failed` with a sanitized error code,
   then do the same step-7 cleanup. An unhandled crash (worker killed)
   skips straight to the lease mechanism below.

## Crash recovery (file suffix + shared record, not the file alone)

This is the direct answer to "根据共享变量来判断文件名产物是否处于处理中
还是因为程序崩溃遗留的半成品":

- Every `running` record carries `owner_token` + `lease_expires_at`,
  renewed each heartbeat — identical to `ScanJobStore.SCAN_JOB_LEASE_SECONDS`
  / `_maybe_abandon_locked`. If the builder dies mid-build, heartbeats
  stop and the lease simply expires; no separate watchdog process is
  needed.
- Any later read of that record (a status query, a new export request
  for the same key, or one of the RFC's existing maintenance triggers —
  startup, export request, build completion, status query, download
  admission/release) opportunistically reaps an expired-lease `running`
  job to `failed` ("owner presumed dead"), lazily — consistent with the
  RFC's non-goal of no periodic per-worker cleanup task.
- **The `.partial` vs `.zip` suffix is what makes this safe, not just
  informative.** A `.zip` file is only ever produced by the atomic
  rename in build-task step 6, so its existence *is* completion — no
  record lookup is required to trust a `.zip`. A `.partial` file's
  legitimacy, however, must be checked against the shared record before
  anything decides to delete or serve it: on the maintenance sweep, for
  each `{track_id}.partial` found on disk, look up that `track_id` in
  the job store —
  - record exists, status `running`, lease still valid → genuinely
    in-progress; leave it alone.
  - record missing, or present but terminal/abandoned → crash-orphaned
    half-finished file; delete it immediately. There is no ambiguity to
    resolve here, because a `.partial` file is never downloadable in
    either case — only the "is it safe to delete yet" question depends
    on the record.
  - Startup is the simplest case of this rule: the job store is
    reconstructed empty on process start (ephemeral across restarts, per
    the RFC's existing non-goal), so *no* record can ever match — every
    `.partial` and every `.zip` found at startup is unconditionally
    orphaned and removed, exactly as the RFC already specifies.
- **Client-visible outcome:** a client polling
  `GET /documents/artifact-exports/{kind}/{track_id}` sees `running`
  while the lease is alive and `failed` within one lease interval of an
  actual crash — with no client-side timeout logic of its own. The
  client never inspects file suffixes; the job-record status is the only
  contract, and it now updates itself even when the builder disappears
  without a trace.

## Event-driven cache expiration, eviction, and active-download leases

This RFC section also assumed pipeline-owned building and needs the same
treatment, plus one clarification the crash-recovery design above makes
necessary: there are now two distinct lease concepts on a job record,
and they must not be conflated.

- The RFC's original caveat — "does not reuse the pipeline feeder's
  bounded mailbox wait as a timer" — is now moot rather than merely
  true: exports never touch the mailbox at all under this redesign, so
  there is nothing to rule out. Drop the sentence instead of restating
  it as a non-goal.
- "No periodic cleanup signal is published to the pipeline mailbox" is
  the same kind of stale reference — remove it.
- "ZIP construction remains pipeline-owned; cache reclamation does not
  need to hold the pipeline reservation because published ZIPs are
  immutable" no longer describes the system. Replace with: **ZIP
  construction runs in the export subsystem's own bounded builder task,
  never the ingestion pipeline; cache reclamation does not need to hold
  the per-`(doc_id, kind)` artifact lock either**, because a published
  ZIP is immutable and — per the build-task design above — that lock is
  already released the moment the source bytes are captured, well
  before the object reaches `ready`. Maintenance therefore only ever
  touches the ZIP object, its job record, and its download leases; it
  never contends with the ingestion pipeline, and never contends with a
  concurrent mutation of the *same* document either, because by the time
  a `ready` object exists the artifact lock has already been released.

**Two independent lease concepts live on the same job record, covering
two different lifecycle phases:**

| | Build lease | Download lease |
| --- | --- | --- |
| Fields | `owner_token`, `lease_expires_at` | `lease_id`, `track_id`, owner PID, heartbeat time |
| Held while | status is `running` (a builder is producing `.partial`) | status is `ready` and a client is mid-download |
| Purpose | detect a builder that died mid-build, so the job reaps to `failed` without a watchdog process | serving-concurrency accounting; block physical deletion while a client reads the ZIP |
| Cardinality | at most one per job | zero or more per job, one per concurrent downloader |

A job never holds both at once: a `running` job has at most a build
lease and zero download leases (nothing is downloadable yet); a `ready`
job has no build lease (the builder already exited cleanly or was
reaped) and zero-or-more download leases. This falls out of the state
machine, not an extra invariant to enforce separately.

Everything else in this RFC section is unchanged by the redesign:
serving concurrency is still enforced across all workers with a shared
download-lease gate that fails closed on a provider failure; download
admission still atomically verifies `ready` + `now < expires_at` +
object-present + capacity before creating a download lease; `now >=
expires_at` still atomically transitions `ready -> expired`, blocking
new download leases while a download admitted before expiration may
finish; physical deletion still requires zero live download leases (not
build leases — those are already gone by the time an object is `ready`);
capacity eviction still picks the oldest eligible `ready` result by
`ready_at` and only deletes it once it has no live download lease;
terminal job-record cleanup stays lazy, oldest-first, and never touches
`queued`/`running` jobs (which still hold their build lease) or `ready`
records with live download leases; and an expired-by-age local ZIP may
still sit on disk until the next trigger or restart, since idle
retention cannot exceed the hard cache bounds either way.

## Compatibility with the existing insert/delete pipeline

- Zero changes to `pipeline_status`, `pipeline_ingress`, or the
  quiescence loop. Ingestion enqueue/processing stays entirely unaware
  that exports exist.
- The only new obligation on the ingestion side: any code path that
  moves or removes a document's `source_location` /
  `source_archive_location` / `sidecar_location` for a given `doc_id` —
  concretely, `adelete_by_doc_id`'s (future, per Part I) filesystem
  cleanup branch, the orphan-rotation move during archive commit, and any
  FAILED-retry step that re-touches the source — must acquire the same
  `get_storage_keyed_lock(["{doc_id}:{kind}"], namespace="artifact_export")`
  before mutating that file. Same key, same lock: whichever side (export
  build vs. document mutation) arrives first wins a short, bounded race;
  the other waits at most one ZIP build or one file move — never the
  pipeline-wide `busy` duration, and never for a *different* `doc_id`.
- No coupling in the other direction: an in-flight export never sets
  `pipeline_status.busy`, so uploads/scans/enqueues for any document
  (including a fresh upload of the very document being exported) proceed
  exactly as if no export were running.
- `clear_documents` (`DELETE /documents`) is unaffected in the same way:
  it already reserves the pipeline's destructive-busy state for its own
  reasons; it additionally takes the per-artifact lock, per `doc_id`,
  only at the point it actually removes that document's files — which
  is a no-op today (no filesystem clear implemented yet) and becomes
  active in lockstep with Part I.
- **The parser itself, not only delete/rotation, must take the lock —
  and take it before the first mutation, not "while writing."** A retry
  reuses an existing `*.parsed/` directory in place rather than writing a
  fresh one, and at least one supported external parser (mineru) clears
  that directory as its very first step, before writing anything back.
  A lock acquired only "while writing" would still leave the directory
  empty and unlocked for the parser's entire running time — potentially
  minutes for an external HTTP-based parser — not just a brief instant.
  The parser must take the `(doc_id, "parsed")` lock at the start of
  every parse attempt, before touching an existing directory at all, and
  hold it until the attempt reaches a terminal outcome (new output fully
  written and `sidecar_location` re-synced, or the attempt fails). This
  is uncontended on a document's first parse, since no `sidecar_location`
  exists yet for any export to have been admitted against.
- **Deletion cancels before it waits.** Blindly blocking on a contended
  lock would let a slow export (bounded by `MAX_DOWNLOAD_PREPARE_SECONDS`,
  up to 600s by default) silently extend how long a delete holds
  `destructive_busy` — which already blocks unrelated enqueues — for that
  entire window. Instead, deletion checks the job store's `inflight` map
  first and requests cancellation of a conflicting job (the build checks
  for it between input entries and aborts promptly) before acquiring the
  now-uncontended lock.

## Availability check vs. correctness guarantee

Two questions this design must answer, and where the answer actually
lives:

1. **How does a request know a `doc_id`'s artifact is available, so a
   client is never handed a pipeline's half-finished output?** The
   request-time check ("confirm the artifact is available" in Request
   handling, step 1) is deliberately cheap and advisory, not race-free.
   `source` is available once `source_location` is persisted; its bytes
   never change in place, so `doc_status`'s stage is irrelevant. `parsed`
   is available once `full_docs.sidecar_location` is persisted **and**
   `doc_status.status == PROCESSED` — here `doc_status` deliberately *is*
   part of the predicate, because `sidecar_location` alone is not proof
   of current content: a retry can clear that same directory while
   leaving the metadata unchanged, so requiring `PROCESSED` excludes
   every in-progress or failed-mid-retry case before a build is even
   created. The cost is that a document that reached `FAILED` after a
   *prior* successful parse (its `*.parsed/` directory is intact) becomes
   undownloadable via this API until it's reprocessed to `PROCESSED`
   again — a deliberate trade against the parse/extraction-defect
   diagnosis path in §Retention of the archived source is deliberate,
   accepted because the weaker alternative (exclude only `PARSING`) does
   not actually close the gap: it would still admit a `FAILED` document
   whose last failed retry left the directory incomplete, since nothing
   in that weaker rule invalidates `sidecar_location` on failure. Before
   a document's first successful parse, neither condition holds, so the
   check alone already returns `404` with nothing to race against.

   The advisory check is not the guarantee, though — it's checked once
   and can go stale immediately. The actual guarantee is the per-artifact
   lock, enforced at build time on both sides unconditionally: if a retry
   starts rewriting `parsed` output at the moment of a request or an
   admitted build's read, the build's lock acquisition either happens
   first (reads the complete prior output) or second (blocks, then reads
   the complete new output) — never a torn read either way, regardless of
   what the advisory check saw a moment earlier. A build that cannot
   acquire the lock within its `MAX_DOWNLOAD_PREPARE_SECONDS` budget fails
   with a sanitized `artifact_busy` error instead of hanging.

2. **How is a delete prevented from removing an artifact mid-zip?** This
   was already covered by the per-`(doc_id, artifact_kind)` lock: the
   build task holds it while reading; `adelete_by_doc_id`'s
   filesystem-cleanup branch and orphan rotation acquire the same key
   before mutating those files. What was missing until this revision was
   the cancel-first refinement above, so that guarantee doesn't come at
   the cost of a slow export quietly prolonging a delete's
   `destructive_busy` window.

## Env vars vs. internal constants

An environment variable is a permanent compatibility surface the moment
it ships — removing or renaming one later breaks deployments that set
it. Most of the resource-limits table is not deployment policy at all:
it's either a hard-coded abuse/resource-exhaustion ceiling (file count,
depth, prepare time) or a concurrency knob tied to this subsystem's own
implementation and server hardware, not something an operator has a
legitimate reason to retune from an `.env` file. `lightrag/constants.py`
already draws exactly this line for other unauthenticated-request
ceilings (`DEFAULT_MAX_INGEST_BODY_BYTES` is explicitly "Not an env
knob"; `MULTIPART_OVERHEAD_BYTES` is a fixed derived value with no
override at all), so the export limits should follow the same
convention rather than introducing a second one.

Only two values stay operator-facing:

- `MAX_DOWNLOAD_SIZE` — trades directly against a deployment's disk and
  network budget, the same reason `MAX_UPLOAD_SIZE` already is an env
  var; raising the upload ceiling is an immediate reason to raise this
  one too.
- `DOWNLOAD_CACHE_TTL_SECONDS` — a retention/disk-usage policy with no
  single correct value, not a safety bound.

Everything else — `MAX_DOWNLOAD_UNCOMPRESSED_SIZE`,
`MAX_DOWNLOAD_FILE_COUNT`, `MAX_DOWNLOAD_DIRECTORY_DEPTH`,
`MAX_DOWNLOAD_PREPARE_SECONDS`, `MAX_DOWNLOAD_BUILD_CONCURRENCY`,
`ARTIFACT_EXPORT_BUILD_LEASE_SECONDS`, `MAX_DOWNLOAD_SERVE_CONCURRENCY`,
`MAX_DOWNLOAD_CACHE_COUNT`, `MAX_DOWNLOAD_CACHE_BYTES` (a multiple of
`MAX_DOWNLOAD_SIZE`, the same relationship `MULTIPART_OVERHEAD_BYTES`
has to `MAX_UPLOAD_SIZE`), `MAX_DOWNLOAD_JOB_RECORDS`, and
`DOWNLOAD_JOB_RECORD_TTL_SECONDS` (bookkeeping-record retention, with no
operator-visible effect, unlike the cache TTL that governs the actual
downloadable ZIP) — become `DEFAULT_*` constants in
`lightrag/constants.py` instead. `ARTIFACT_EXPORT_BUILD_LEASE_SECONDS`
was listed as an environment variable earlier in this redesign; that was
an oversight — it is an internal crash-detection timer (§Crash recovery),
never deployment policy, and belongs with the rest.

## RFC edits implied

- **"Export jobs, mailbox, and pipeline scheduling"**: replace with the
  model above; drop "only the workspace pipeline owner may claim and
  build exports", "the pipeline remains busy while validating and
  building ZIP snapshots", the batch/cycle fairness paragraphs, and the
  mailbox paragraph.
- **Resource limits table**: drop `MAX_DOWNLOAD_JOBS_PER_CYCLE` outright
  (no pipeline export cycle left to bound); split the rest into two env
  vars (`MAX_DOWNLOAD_SIZE`, `DOWNLOAD_CACHE_TTL_SECONDS`) and everything
  else as `lightrag/constants.py` constants — see §Env vars vs. internal
  constants above. `MAX_DOWNLOAD_BUILD_CONCURRENCY` moves there too, as a
  plain cross-worker counter, not "concurrent ZIP builders inside the
  sole pipeline owner".
- **Acceptance criteria** ("Asynchronous exports"): replace "Only the
  pipeline owner builds ZIPs, and inputs cannot change during
  validation/compression" with "Only a build holding the per-`(doc_id,
  kind)` lock may read that document's input; concurrent structural
  changes to *that* document are blocked for the build's duration, all
  other documents and ingestion are unaffected." Replace "Bounded export
  batches cannot starve document work; bounded document batches cannot
  starve queued exports" with "Export builds and document ingestion
  share no scheduling state, so neither can starve the other." Add: "A
  builder that dies mid-build is detected by lease expiry within one
  lease interval, without a client-side timeout or a periodic sweep
  task; a crash-orphaned `.partial` file is only ever deleted after its
  job record is confirmed missing or terminal."
- **Expected implementation areas**: drop the `pipeline_ingress.py`
  "bounded artifact-export wake-up channel" bullet; drop "export-job
  scheduling, bounded/fair export batches" from `pipeline.py` — the only
  remaining `pipeline.py`/`lightrag.py` touch point is acquiring the
  per-artifact lock at the existing orphan-rotation/deletion/retry sites.
  Add the `artifact_export_jobs` namespace helpers (create/update/
  set_status/get/cancel, an `initialize_artifact_export_status()`
  bootstrap) to `lightrag/kg/shared_storage.py` alongside
  `initialize_pipeline_status()` — not a new module, and not a
  `ScanJobStoreHub`-style Hub/Proxy pair, since `get_namespace_data`/
  `get_namespace_lock` already provide the single-process/Manager-backed
  split generically.
  Replace "policy profiles and every export/cache limit" for
  `lightrag/api/config.py`, `constants`, and `env.example` with: only
  `MAX_DOWNLOAD_SIZE` and `DOWNLOAD_CACHE_TTL_SECONDS` are new
  `config.py`/`env.example` entries; every other export/cache limit is a
  new `DEFAULT_*` constant in `lightrag/constants.py`, not a
  `config.py`/`env.example` entry at all.
- **"Event-driven cache expiration, eviction, and active-download
  leases"**: drop the pipeline-mailbox-timer caveat and the "no periodic
  signal to the pipeline mailbox" sentence (both moot, not just true);
  replace "ZIP construction remains pipeline-owned; cache reclamation
  does not need to hold the pipeline reservation" with "ZIP construction
  runs in the export subsystem's own bounded builder task; cache
  reclamation does not need to hold the per-artifact lock, which is
  already released before the object reaches `ready`". Add the
  build-lease/download-lease distinction table so the crash-recovery
  lease introduced above and the RFC's existing download lease are never
  conflated. The rest of the section (serving-concurrency gate,
  `expired`/`410` transition, eviction-by-`ready_at`, lazy terminal
  cleanup, idle-retention note) is unchanged.
- **"Export jobs and independent build scheduling"**: add the
  "Availability and half-processed content" subsection above, and extend
  the per-artifact-lock paragraph with the parser's write obligation and
  the delete-cancels-first refinement.
- **"Exact artifact deletion"**: append the cancel-first sentence to the
  per-kind-lock paragraph.
- **Acceptance criteria** ("Asynchronous exports"): also add "A `parsed`
  export request is admitted only when `doc_status.status == PROCESSED`
  in addition to `sidecar_location` being persisted; the per-artifact
  lock, not this check, is what actually prevents a build from reading a
  half-written `parsed` directory if the two race" and "Deletion
  requests cancellation of a conflicting in-flight export before
  acquiring its lock, rather than blocking on it for the export's full
  prepare-time budget."

Every RFC goal is preserved (bounded concurrency, bounded cache,
immutable ZIPs, `track_id`-keyed downloads, fail-closed on races,
crash-safe cleanup); only the pipeline-coupling subsystem is replaced by
a self-contained job store plus a narrow per-artifact lock.
