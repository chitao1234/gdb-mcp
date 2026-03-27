# GDB MCP Workflow Improvement Plan

## Objective

Upgrade `gdb-mcp` from a solid session-oriented inspection API into a first-class
workflow debugger for flaky failures, race hunting, and repeatable forensic
capture.

The current server already provides good low-level primitives:

- session lifecycle management
- structured execution and inspection calls
- multi-session inventory
- typed payloads and typed transport/session boundaries

What it lacks is server-side orchestration. Today, complex debugging workflows
require too many client round trips and do not produce reproducible artifact
bundles as naturally as scripted `gdb --batch` plus shell automation.

## Current Problems

The current interface is materially weaker than scripted GDB for the debugging
workflows we care about most:

1. Multi-step workflows are non-atomic.
   The client must issue many separate calls for continue, stop inspection,
   backtrace, locals, registers, and artifact collection.

2. Repeat-until-failure automation is poor.
   There is no first-class way to run a scenario hundreds of times until a
   signal, assertion, timeout, or text pattern appears.

3. Stop events are not first-class objects.
   The runtime records current execution state, but it does not preserve a
   structured stop history, hooks, or transcripts.

4. Artifact capture is chat-oriented instead of file-oriented.
   Rich debugging evidence should be written to disk as a reproducible bundle,
   not only returned through MCP text output.

5. Multi-process and multi-inferior workflows are weak.
   Separate sessions help, but they are not a replacement for explicit
   inferior/fork handling inside one debugger instance.

6. Auditability is weaker than committed GDB scripts.
   We need deterministic manifests, capture outputs, and replay-friendly
   workflow definitions.

## Design Principles

1. Keep the current low-level tools.
   `gdb_execute_command` and the existing structured primitives remain the
   escape hatch and the building blocks. We are adding workflow tools, not
   replacing primitive tools.

2. Make workflow tools structured, not stringly typed.
   `gdb_batch` should execute a typed list of steps, not an opaque blob of raw
   GDB text.

3. Prefer file-first artifacts over chat-first output.
   Capture-oriented tools should return paths and manifests, with optional
   summaries in the response.

4. Put orchestration inside the server.
   The server should handle stop capture, loop control, failure triggers, and
   artifact generation so MCP clients do not need fragile polling loops.

5. Preserve permission boundaries.
   Privileged operations such as process attach, function calls, memory reads,
   or artifact writes should remain explicitly modeled in the tool surface.

6. Make the workflow layer replayable.
   Workflow definitions and capture manifests should be stable enough to commit
   to the repo and rerun later.

## Implementation Order

### Phase 1: Add Session Event and Artifact Foundations

Build the internal state needed for workflow automation before adding new MCP
tools.

Primary changes:

- Add typed `StopEvent` and stop-reason metadata to the domain layer.
- Extend `SessionRuntime` to store:
  - `last_stop_event`
  - bounded `stop_history`
  - optional transcript metadata
  - artifact root/config
  - workflow lock separate from the existing lifecycle lock
- Update `SessionCommandRunner` so stop notifications produce structured stop
  events instead of only mutating `execution_state` and `stop_reason`.
- Preserve enough command/result metadata to support later artifact export.

Files likely touched:

- `src/gdb_mcp/domain/models.py`
- `src/gdb_mcp/session/runtime.py`
- `src/gdb_mcp/session/command_runner.py`
- `src/gdb_mcp/session/service.py`
- `src/gdb_mcp/transport/mi_models.py`

Acceptance criteria:

- The runtime exposes structured stop-event data.
- Stop history is bounded and deterministic.
- Existing tools keep their current behavior and tests continue to pass.

### Phase 2: Implement `gdb_batch`

Add the highest-value workflow tool first.

Purpose:

- execute a sequence of structured steps atomically within one session
- prevent unrelated calls from interleaving while the batch is active
- reduce client round trips during race debugging and stop-time inspection

Initial scope:

- session-scoped tool
- executes a validated list of steps
- each step maps to an existing structured tool or an explicit command step
- returns per-step structured results
- optional fail-fast behavior
- optional stop capture behavior when a step transitions the inferior to stopped

Important constraints:

- do not implement this as "raw GDB script text"
- do not bypass existing validation and permission boundaries
- use a workflow lock so one session cannot receive conflicting concurrent calls

Files likely touched:

- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- `src/gdb_mcp/session/service.py`
- new workflow service/module under `src/gdb_mcp/session/`

Acceptance criteria:

- a client can run a multi-step debugging flow with one MCP call
- steps execute serially without interleaving from other requests
- structured results preserve per-step success/error information

### Phase 3: Implement `gdb_capture_bundle`

Add first-class forensic capture to disk.

Purpose:

- export a reproducible debugging snapshot to files
- make results useful outside the immediate chat context

Default bundle contents:

- session status
- current stop event
- all-thread backtraces
- selected thread/frame info
- locals
- registers
- selected expressions
- optional memory ranges
- transcript excerpt or command history
- manifest file describing captured artifacts

Important constraints:

- artifact outputs must be written to a caller-controlled or configured root
- response should return absolute paths and a machine-readable manifest summary
- capture should be usable both standalone and from `gdb_batch` /
  `gdb_run_until_failure`

Files likely touched:

- `src/gdb_mcp/domain/models.py`
- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- new artifact capture module under `src/gdb_mcp/session/`

Acceptance criteria:

- one tool call produces a manifest plus filesystem artifacts
- artifact layout is deterministic enough to diff or archive
- capture works for both normal breakpoints and crash/signal stops

### Phase 4: Implement `gdb_run_until_failure`

Add a campaign runner for flaky failures.

Purpose:

- run a scenario repeatedly until a failure trigger occurs
- automatically collect the first useful artifact bundle

Recommended design:

- treat this as a workflow/campaign tool, not a thin wrapper around `gdb_run`
- create fresh sessions per iteration so state does not leak across attempts
- support stop predicates such as:
  - signal stop
  - exit code
  - timeout
  - command output regex
  - explicit stop reason

Suggested inputs:

- session startup config
- optional setup batch
- iteration count / until-success-or-failure policy
- per-run timeout
- failure predicates
- capture bundle spec

Files likely touched:

- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- `src/gdb_mcp/session/registry.py`
- new workflow/campaign module under `src/gdb_mcp/session/`

Acceptance criteria:

- a flaky scenario can be run repeatedly with one tool call
- the first matching failure generates a bundle and terminates the loop
- the response includes iteration index, trigger reason, and artifact paths

### Phase 5: Add Multi-Inferior and Fork Support

Improve debugger control for daemon/test and fork-heavy scenarios.

Primary additions:

- `gdb_list_inferiors`
- `gdb_select_inferior`
- `gdb_set_follow_fork_mode`
- `gdb_set_detach_on_fork`
- stable inferior metadata in session status and inventories

Notes:

- keep `gdb_attach_process` as the basic attach primitive
- multi-session support remains useful, but it should not be the only answer to
  multi-process debugging

Files likely touched:

- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- `src/gdb_mcp/session/service.py`
- `src/gdb_mcp/session/runtime.py`
- inspection/execution modules

Acceptance criteria:

- callers can inspect and control inferiors explicitly
- fork-follow behavior is configured without raw command strings
- session summaries expose enough metadata to reason about multi-inferior state

### Phase 6: Add Watchpoint, Catchpoint, Memory, and Wait Helpers

These are useful, but lower priority than workflow automation and artifact
capture.

Potential additions:

- `gdb_set_watchpoint`
- `gdb_delete_watchpoint`
- `gdb_set_catchpoint`
- `gdb_read_memory`
- `gdb_wait_for_stop`

Notes:

- some of this functionality is available today via `gdb_execute_command`
- dedicated tools become worthwhile once batch/capture workflows are in place

Acceptance criteria:

- high-friction raw-command workflows gain stable structured wrappers
- new tools integrate naturally with `gdb_batch` and `gdb_capture_bundle`

## Immediate Next Step

Implement Phase 1 first. Without structured stop events, transcripts, and a
workflow lock, higher-level tools will be bolted on top of insufficient state.

After Phase 1, implement `gdb_batch` before anything else. It is the fastest
path to making `gdb-mcp` meaningfully better for real debugging agents.

## Non-Goals

This plan does not aim to:

- remove the existing primitive tool surface
- replace scripted GDB for every possible use case
- build a fully generic scripting language inside MCP
- overload chat responses with large forensic dumps when file artifacts are more
  appropriate
