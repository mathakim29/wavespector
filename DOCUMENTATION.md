# Asynchronous Media Processing Job System

This document describes a reusable job system for long-running media
processing tasks. It is intentionally implementation-agnostic: the same
design can support audio separation, video transcoding, image generation,
document conversion, or other work that should not block an HTTP request.

## Why use jobs?

Media processing can take seconds or minutes and may require substantial CPU,
GPU, memory, disk, or network resources. Running the work inside the request
handler creates several problems:

- HTTP requests can time out while processing continues.
- Clients cannot reliably report progress.
- Multiple requests can overload the worker host.
- Failures become difficult to retry or inspect.
- Large inputs and outputs need explicit lifecycle management.

A job system separates request acceptance from processing. The API accepts the
work, returns a job ID, and lets the client observe the job asynchronously.

## High-level flow

```text
Client
  │
  ├─ submit input and options
  ▼
API ── create job record ──► return job ID
  │
  └─ enqueue work
          │
          ▼
       Worker
          │
          ├─ validate input
          ├─ prepare/download dependencies
          ├─ process media
          ├─ write outputs
          └─ update final status
          │
          ▼
Client polls status and downloads outputs
```

The API should acknowledge a valid request quickly. The worker owns the
long-running operation.

## Job lifecycle

A useful state machine is:

```text
queued → downloading → processing → completed
   │          │             │
   └──────────┴─────────────┴──→ failed
```

Not every job needs every state. A file already available locally can move
directly from `queued` to `processing`.

### Recommended states

| State | Meaning |
| --- | --- |
| `queued` | Accepted and waiting for a worker. |
| `downloading` | Fetching a remote input or required asset. |
| `processing` | The main media operation is running. |
| `completed` | All requested outputs are available. |
| `failed` | Processing stopped with an error. |
| `cancelled` | The user or system stopped the job. |
| `expired` | Results were removed after the retention period. |

State transitions should be explicit and validated. For example, a completed
job should not return to `processing` unless it is deliberately retried as a
new attempt.

## Job record

A job record should contain enough information to inspect and recover work:

```json
{
  "id": "opaque-job-id",
  "status": "processing",
  "progress": 42,
  "input": {
    "filename": "song.mp3",
    "source": "upload"
  },
  "options": {
    "model": "selected-model"
  },
  "outputs": [],
  "logs": [
    "Job accepted",
    "Processing started"
  ],
  "error": null,
  "attempt": 1,
  "created_at": "2026-01-01T12:00:00Z",
  "started_at": "2026-01-01T12:00:03Z",
  "finished_at": null,
  "expires_at": "2026-01-02T12:00:00Z"
}
```

Important design choices:

- Use opaque IDs rather than exposing database IDs or local filesystem paths.
- Store timestamps in UTC.
- Keep user-facing errors separate from detailed internal logs.
- Record the attempt number for retries.
- Store output metadata, not only output filenames.

## API contract

### Submit a job

```http
POST /jobs
Content-Type: multipart/form-data
```

The request can contain a file, a remote URL, and processing options.

Successful submission should return `202 Accepted`:

```json
{
  "job_id": "opaque-job-id",
  "status": "queued",
  "status_url": "/jobs/opaque-job-id"
}
```

The endpoint should reject invalid input before creating a job. Examples:

- No input source was provided.
- More than one mutually exclusive source was provided.
- The requested option is not supported.
- The input exceeds size or duration limits.

### Read job status

```http
GET /jobs/{job_id}
```

Example response:

```json
{
  "job_id": "opaque-job-id",
  "status": "completed",
  "progress": 100,
  "outputs": [
    {
      "name": "vocals.wav",
      "download_url": "/jobs/opaque-job-id/outputs/vocals.wav",
      "content_type": "audio/wav",
      "size": 1234567
    }
  ]
}
```

### Download an output

```http
GET /jobs/{job_id}/outputs/{output_name}
```

Output names must be resolved from the job record. Never concatenate an
unchecked user-provided path with a filesystem root; validate names and keep
all resolved paths inside the job's output directory.

### Cancel a job

```http
POST /jobs/{job_id}/cancel
```

Cancellation should be cooperative. The worker checks for cancellation between
major steps and terminates subprocesses when safe.

### List supported options

```http
GET /models
```

If processing depends on downloadable models or presets, expose their stable
identifiers and display metadata separately from internal filenames. The
submit endpoint should validate the chosen identifier against the same catalog.

## Progress reporting

Progress should represent meaningful work rather than arbitrary elapsed time.
For a multi-stage task, calculate progress from weighted stages:

```text
download       0–15%
preparation   15–25%
processing    25–90%
finalization  90–100%
```

If accurate progress is unavailable, return a stage and an indeterminate
progress value instead of reporting misleading precision.

Clients can poll with a one- to two-second interval. For larger systems,
Server-Sent Events or WebSockets can stream status changes, but the status
endpoint should remain available as the source of truth.

## Storage model

Each job should have an isolated workspace:

```text
jobs/
└── <job-id>/
    ├── input/
    ├── working/
    ├── output/
    └── metadata.json
```

Recommended rules:

- Generate job directories from server-created IDs.
- Sanitize uploaded filenames before storing them.
- Keep temporary files separate from downloadable outputs.
- Enforce file size, duration, and disk-usage limits.
- Delete expired workspaces with a scheduled cleanup task.
- Use object storage for large or durable outputs.

## Worker execution

The worker should perform these steps:

1. Load the job record.
2. Acquire a concurrency slot or resource lock.
3. Mark the job as started.
4. Prepare the input and dependencies.
5. Run the processor with timeouts and resource limits.
6. Validate that expected outputs exist.
7. Persist output metadata.
8. Mark the job completed or failed.
9. Release resources in a `finally` block.

External commands should be invoked with argument arrays rather than shell
strings. Capture stdout and stderr into bounded logs, and avoid exposing
secrets or full local paths in client-visible errors.

## Model and asset caching

Downloaded models should live in a dedicated cache separate from job output.
Before using a cached asset:

1. Check whether the file exists.
2. Verify its checksum when a published checksum is available.
3. Download to a temporary filename.
4. Verify the temporary file.
5. Atomically rename it into the cache.

If loading still fails, remove the affected asset and retry once. A retry
limit is important: repeatedly downloading a broken asset can create an
infinite loop and overload the upstream host.

For multiple workers, protect downloads with a lock so two workers do not
write the same model simultaneously.

## Reliability and retries

Retry only failures that are likely to be transient:

- Network timeout
- Temporary upstream failure
- Interrupted download
- Corrupt cache entry

Do not automatically retry invalid input, unsupported formats, or deterministic
processor errors. Use exponential backoff with jitter for remote operations.

Every retry should record:

- Attempt number
- Reason
- Start and end time
- Whether the same input or a fresh workspace was used

## Security considerations

- Restrict upload size, duration, and accepted content types.
- Treat remote URLs as untrusted input and protect against SSRF.
- Disable URL access to private network ranges where appropriate.
- Prevent path traversal in filenames and output names.
- Run media tools with least privilege.
- Apply subprocess timeouts and memory/CPU limits.
- Do not expose raw exception traces to clients.
- Add authentication, authorization, rate limiting, and quotas for shared deployments.

## Persistence and scaling

An in-memory job dictionary is suitable for a prototype but loses state on
restart and cannot coordinate multiple API processes. Production systems
typically use:

- A database for job metadata and state transitions.
- A queue such as Redis, RabbitMQ, SQS, or a managed task service.
- Dedicated workers for CPU- or GPU-heavy processing.
- Object storage for inputs and outputs.
- A cleanup worker for retention and failed-job recovery.

The API and worker should be independently restartable. A worker should be
able to find jobs stuck in `processing` and mark them for recovery after a
heartbeat timeout.

## Observability

Log each job with its job ID and attempt number. Useful metrics include:

- Jobs submitted, completed, failed, cancelled, and expired
- Queue depth and oldest queued job
- Processing duration by model and input duration
- Retry count and failure reason
- Model download duration and cache hit rate
- CPU, memory, GPU, disk, and network usage

Use structured logs and correlation IDs so a client request can be traced
through submission, worker execution, and output delivery.

## Client integration pattern

A minimal client should:

1. Submit the job.
2. Store the returned job ID.
3. Poll the status URL.
4. Render state and progress.
5. Show a retry or support action on failure.
6. Render output download links on completion.

The client should stop polling after a terminal state and should handle API
timeouts with a bounded retry strategy of its own.

## Testing checklist

- Submit a valid file.
- Submit a valid remote URL.
- Submit neither source.
- Submit both sources.
- Submit an unsupported model or option.
- Upload an invalid or oversized file.
- Simulate a worker exception.
- Simulate a corrupt model cache.
- Cancel a queued and active job.
- Restart the API during processing.
- Expire and clean up an old job.
- Download an output with an invalid filename.
- Run multiple jobs concurrently.
