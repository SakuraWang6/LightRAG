# Simplifying artifact-export scheduling (issue #3516)

Source RFC: https://github.com/HKUDS/LightRAG/issues/3516, section
"Export jobs, mailbox, and pipeline scheduling". This note captures a
simplification of that section only; the rest of the RFC (artifact
identity, orphan rotation, exact deletion, authorization model) is
unaffected.

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

## Proposed model

Keep `ArtifactExportJobStore` (job records, CAS, heartbeat, terminal
tombstones, eviction — none of that is pipeline-specific). Drop pipeline
ownership of *building*: exports run in their own bounded task pool,
fully decoupled from `pipeline_status` and `pipeline_ingress`.

1. **Per-artifact lock instead of pipeline-wide `busy`.** Use the
   existing `get_storage_keyed_lock(keys, namespace="artifact_export")`
   primitive (the same mechanism already guarding entity/edge mutation,
   see `tests/pipeline/test_graph_keyed_locks.py`), keyed by
   `(doc_id, artifact_kind)`. The build task holds it only around
   resolving the input locator and reading the source bytes into the
   ZIP, and releases it as soon as that document's bytes are captured —
   the ZIP has no further dependency on the source once its bytes are
   read. `delete_document`, orphan rotation, and any retry step that
   moves `source_location`/`sidecar_location` for that same `doc_id`
   must acquire the same key before mutating the file. A download for
   doc A never blocks ingestion, scanning, or deletion of doc B.

2. **Shared in-flight map for same-artifact dedup.** A small shared
   dict, guarded by `get_namespace_lock("artifact_export_status",
   workspace=...)` (same pattern as `pipeline_status`), maps
   `(doc_id, artifact_kind) -> track_id` for jobs currently
   `queued`/`running`. A new export request checks this map first: an
   existing entry means the request returns that `track_id` (still
   `202`) instead of starting a second build. The entry is cleared on
   terminal transition. Since the ZIP filename is `<track_id>.zip`, this
   also gives every concurrent downloader the same file for free —
   naming and dedup are the same mechanism, not two.

3. **Plain semaphore instead of cycle-based scheduling.** Replace
   `MAX_DOWNLOAD_JOBS_PER_CYCLE` (a pipeline-cycle concept that no longer
   applies) with a shared counter under the same
   `artifact_export_status` lock — the same shape as the existing
   `_global_concurrency_limits` group-limit mechanism already used
   elsewhere for cross-worker bounded concurrency. `MAX_DOWNLOAD_BUILD_CONCURRENCY`
   remains the one knob; when saturated, a new request is refused
   (`429`) rather than queued behind a pipeline cycle.

4. **Result.** No mailbox message, no `pipeline_status` interaction, no
   quiescence-boundary carve-out. The only coupling left is the narrow
   per-`(doc_id, kind)` lock at the exact points where a filesystem
   mutation and a ZIP read could race — the only case where a race is
   actually possible, since every other document is untouched by an
   export request for a different `doc_id`.

## RFC edits implied

- **"Export jobs, mailbox, and pipeline scheduling"**: rewrite per the
  model above; drop "only the workspace pipeline owner may claim and
  build exports", "the pipeline remains busy while validating and
  building ZIP snapshots", the batch/cycle fairness paragraphs, and the
  mailbox paragraph.
- **Resource limits table**: drop `MAX_DOWNLOAD_JOBS_PER_CYCLE`; keep
  `MAX_DOWNLOAD_BUILD_CONCURRENCY` as a plain cross-worker semaphore, not
  "concurrent ZIP builders inside the sole pipeline owner".
- **Acceptance criteria** ("Asynchronous exports"): replace "Only the
  pipeline owner builds ZIPs, and inputs cannot change during
  validation/compression" with "Only a build holding the per-`(doc_id,
  kind)` lock may read that document's input; concurrent structural
  changes to *that* document are blocked for the build's duration, all
  other documents and ingestion are unaffected." Replace "Bounded export
  batches cannot starve document work; bounded document batches cannot
  starve queued exports" with "Export builds and document ingestion
  share no scheduling state, so neither can starve the other."
- **Expected implementation areas**: drop the `pipeline_ingress.py`
  "bounded artifact-export wake-up channel" bullet; drop "export-job
  scheduling, bounded/fair export batches" from `pipeline.py` — the only
  remaining pipeline.py touch point is acquiring the per-artifact lock
  at the existing orphan-rotation/deletion/retry sites.

Every RFC goal is preserved (bounded concurrency, bounded cache,
immutable ZIPs, `track_id`-keyed downloads, fail-closed on races); only
the pipeline-coupling subsystem is removed.
