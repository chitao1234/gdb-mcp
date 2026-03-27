# GDB MCP Remediation Plan

This document replaces the previous refactor plan. The old plan mixed broad
architectural cleanup with earlier completed work. The current priority is
more concrete: fix the audited correctness and contract issues first, then
tighten the architecture only where it directly supports those fixes.

The plan below is intentionally defect-driven. Each workstream maps back to a
specific bug, risk, or coverage gap found in the audit.

## Scope

This plan addresses the following problems:

- concurrent `start()` can launch multiple GDB processes for one session;
- `stop()` is not coordinated with in-flight transport reads and can race with
  command execution or interrupt handling;
- failed shutdown can leave session state inconsistent;
- startup builds invalid GDB argv when `program`, `args`, and `core` are used
  together;
- `env` is applied after `init_commands`, which violates the documented startup
  contract;
- MCP request models silently ignore unknown arguments;
- transport behavior around async MI records needs clearer ownership and better
  regression coverage;
- the test suite misses the most important lifecycle and startup race cases.

## Goals

- make session lifecycle operations safe under concurrency;
- make startup command construction correct for every supported argument
  combination;
- make externally documented behavior match actual execution order;
- tighten the MCP boundary so invalid requests fail early and explicitly;
- add regression tests for the failure modes that are currently uncovered;
- preserve the external tool names and response shapes unless a contract change
  is explicitly approved.

## Non-Goals

- broad renaming or module reshuffling that does not directly help the audited
  issues;
- replacing the MCP API surface;
- redesigning domain models purely for style reasons;
- adding new debugger features while lifecycle correctness is unresolved.

## Workstream 1: Session Lifecycle Serialization

This is the highest-priority workstream because it addresses process leaks,
race conditions, and state corruption.

### Problems closed

- concurrent `start()` can spawn multiple controllers;
- `stop()` can race with command reads and interrupt waits;
- failed shutdown can leave `controller`, `is_running`, and `state` out of
  sync;
- registry cleanup can remove a session that still appears logically active.

### Implementation plan

1. Introduce an explicit session lifecycle lock.
   Use a dedicated lock in the session runtime or lifecycle service for
   `start()`, `stop()`, and any operation that swaps or destroys the active
   controller.

2. Define controller ownership rules.
   `transport.start()` and `transport.exit()` should only run while the session
   lifecycle lock is held, so there is one clear owner for controller
   replacement and teardown.

3. Make transport shutdown coordination explicit.
   The transport layer should either:
   - hold the same lock while a command is being read; or
   - guard access to the controller with a stable local reference plus
     shutdown-aware state so teardown cannot invalidate in-flight reads.

4. Normalize stop semantics.
   Decide and document what the runtime state becomes when controller exit
   partially fails. The recommended policy is:
   - if the controller reference is gone, the session is not runnable anymore;
   - record shutdown failure metadata separately;
   - never leave `is_running=True` when no controller exists.

5. Tighten registry removal rules.
   `close_session()` should not treat `controller is None` as sufficient proof
   of a clean stop. Removal should depend on a coherent terminal session state.

### Suggested code changes

- add a lifecycle lock and terminal failure/shutdown metadata to
  `SessionRuntime`;
- move controller swap/clear operations behind transport methods that are safe
  under concurrency;
- update `SessionLifecycleService.stop()` to produce consistent runtime
  transitions on both success and failure;
- update `SessionRegistry.close_session()` to key off session state, not just
  controller presence.

### Acceptance criteria

- concurrent calls to `start()` on one session cannot launch more than one GDB
  process;
- `stop()` during an in-flight command or interrupt does not raise unexpected
  transport exceptions;
- session status cannot report `is_running=True` when no controller is present;
- registry removal logic cannot silently discard a logically active session.

## Workstream 2: Startup Contract and Command Construction

This workstream fixes incorrect process argv construction and aligns startup
execution order with the documented contract.

### Problems closed

- invalid argv when `program`, `args`, and `core` are supplied together;
- environment variables are applied too late relative to `init_commands`;
- startup behavior differs from what `TOOLS.md` promises.

### Implementation plan

1. Introduce one startup command builder.
   The lifecycle layer should stop hand-assembling the initial GDB argv inline.
   Add a helper that takes `program`, `args`, `core`, and `working_dir` and
   returns a valid GDB invocation for every allowed combination.

2. Define supported startup combinations explicitly.
   The recommended policy is:
   - `program` only: start with executable loaded;
   - `program + args`: use `--args executable ...`;
   - `program + core`: pass executable and core using GDB-supported ordering;
   - `program + args + core`: reject as invalid input unless the code adopts an
     explicit alternative strategy, because inferior argv is irrelevant for
     core analysis and `--args` conflicts with later debugger options.

3. Reorder startup steps to match the contract.
   The recommended startup sequence is:
   - start GDB;
   - wait for readiness;
   - apply environment variables;
   - run `init_commands`;
   - mark the session ready.

4. Clarify `target_loaded` transitions.
   Set `target_loaded` from explicit startup actions, not from string matching
   on arbitrary command text.

5. Update docs together with behavior.
   `README.md` and `TOOLS.md` should describe the final supported combinations
   and the actual execution order.

### Suggested code changes

- extract startup argv construction from `SessionLifecycleService.start()`;
- validate invalid combinations before launching GDB;
- apply `env` before `init_commands`;
- replace heuristic `target_loaded` updates based on substring checks with
  explicit state transitions.

### Acceptance criteria

- startup with `program + args`, `program + core`, and `program` alone works
  deterministically;
- invalid `program + args + core` requests fail at validation or startup policy
  checks with a clear error;
- environment variables are guaranteed to be installed before any init command
  that can run or attach the inferior;
- docs and behavior match.

## Workstream 3: MCP Boundary Hardening

This workstream makes request validation stricter and keeps the external
contract honest.

### Problems closed

- typoed request fields are silently ignored;
- tool definitions and handler wiring can drift apart without a clear test;
- entrypoint logging side effects are too eager for embedded use.

### Implementation plan

1. Forbid unknown request fields.
   Add explicit Pydantic model configuration so request validation rejects extra
   keys instead of dropping them.

2. Add a tool-schema parity test.
   Verify every exported MCP tool has a dispatch path and every dispatched tool
   is present in the schema list.

3. Reduce import-time side effects in `server.py`.
   Move logging configuration behind the actual CLI/server startup path instead
   of running `basicConfig()` during module import.

4. Keep error messages stable where practical.
   Validation failures should become stricter without producing vague generic
   exceptions.

### Suggested code changes

- update all MCP argument models to forbid extras;
- add tests for typoed fields such as `workingdir` or `frameNumber`;
- add one invariant test comparing `build_tool_definitions()` with handler
  dispatch coverage;
- move logging setup into `run_server()` or a dedicated CLI-only bootstrap
  function.

### Acceptance criteria

- unknown MCP arguments produce validation errors;
- adding a tool in one place without wiring the other fails tests;
- importing `gdb_mcp.server` no longer mutates host logging configuration.

## Workstream 4: Transport Async Semantics

This workstream clarifies how MI async records are attributed and prevents the
transport layer from hiding protocol details the higher layers may need.

### Problems closed

- async MI notifications have ambiguous ownership relative to the active
  command;
- parser normalization currently drops the notification class;
- tests do not model token-less async notifications realistically.

### Implementation plan

1. Revisit response attribution rules.
   Make a deliberate distinction between:
   - command-scoped result records;
   - async notifications that happened during the command window;
   - notifications that should not satisfy command completion conditions.

2. Preserve notification metadata.
   The parsed representation should keep the async record class or message, not
   just the payload.

3. Tighten completion conditions for running commands.
   `-exec-*` completion should not be satisfied by unrelated async records
   unless they are explicitly recognized as the stop event for the command
   being awaited.

4. Add realistic transport tests.
   Include token-less async notifications interleaved with command responses.

### Suggested code changes

- refine `MiClient.send_command_and_wait_for_prompt()` attribution logic;
- update parsed MI response models to preserve notify message/class;
- add transport tests for token-less `notify` traffic and interleaved async
  output.

### Acceptance criteria

- unrelated async notifications cannot incorrectly complete an in-flight
  `-exec-*` command;
- parsed notify records retain enough information for higher-level decisions;
- regression tests cover token-less async records.

## Workstream 5: Regression Coverage

The current suite is large, but it misses the exact failure modes that matter
most.

### Required tests

- unit test: concurrent `start()` on one session cannot create two controllers;
- unit test: `stop()` racing with command read returns a controlled error or
  clean shutdown, not an unexpected exception;
- unit test: `stop()` failure leaves coherent terminal or failed state;
- unit test: `close_session()` does not remove inconsistent sessions silently;
- unit test: startup rejects or handles `program + args + core` according to
  the chosen policy;
- unit test: `env` is applied before `init_commands`;
- unit test: extra MCP request fields are rejected;
- unit test: tool schema list and handler registry stay in sync;
- transport test: token-less async notifications do not get misattributed;
- integration test: startup ordering and failure behavior remain correct with a
  real GDB instance where practical.

### Test execution target

- `pytest -q` remains green;
- new tests fail before the fixes and pass after them;
- any intentionally changed external error messages are updated in docs and
  tests together.

## Recommended Delivery Order

1. Session lifecycle serialization and stop-state cleanup.
2. Startup argv validation and `env` ordering.
3. MCP hardening for extra fields and logging bootstrap cleanup.
4. Transport async attribution fixes.
5. Regression and integration coverage expansion.

This ordering is deliberate:

- lifecycle safety eliminates the highest-risk process and state bugs first;
- startup fixes remove user-visible contract bugs next;
- MCP hardening is low-risk and reduces future support issues;
- transport async semantics may touch deeper assumptions and should land after
  the lifecycle layer is made safer;
- coverage work should be added alongside each change, with a final pass to
  close any remaining gaps.

## Rollout Notes

- Keep changes incremental. Do not combine all workstreams into one large
  refactor branch.
- Land tests with the fixes that require them, not as a final cleanup pass.
- Prefer explicit policy decisions over heuristic behavior, especially for
  startup combinations and shutdown failure states.
- If a fix requires an external contract change, update `README.md`,
  `TOOLS.md`, and MCP schema descriptions in the same change.

## Done Definition

This plan is complete when:

- the audited correctness bugs are fixed;
- the startup contract is explicit and documented;
- MCP validation rejects unknown fields;
- lifecycle and transport race regressions are covered by tests;
- `pytest -q` passes with the new coverage in place.
