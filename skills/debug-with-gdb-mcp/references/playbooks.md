# gdb-mcp Playbooks

This file contains copy-ready payloads for the current v2 gdb-mcp interface. Use the action-based family tools directly; historical one-tool-per-operation names are retired.

## Fast Decision Map

| Situation | Primary tools | Follow-up |
| --- | --- | --- |
| New live debug session | `gdb_session_start`, `gdb_breakpoint_manage(action="create")`, `gdb_execution_manage(action="run")` | `gdb_context_query(action="backtrace")`, `gdb_inspect_query(action="variables")` |
| Live launch with custom env, cwd, or argv | `gdb_session_start` with `args`, `env`, `working_dir` | later `gdb_execution_manage(action="run", execution.args=...)` if rerun args change |
| Background launch and inspect later | `gdb_execution_manage(action="run", execution.wait.until="acknowledged")` | `gdb_execution_manage(action="wait_for_stop" | "interrupt")`, then thread and frame inspection |
| Attach to running process | `gdb_session_start`, `gdb_attach_process` | `gdb_session_query(action="status")`, `gdb_context_query(action="threads" | "backtrace")` |
| Core dump analysis | `gdb_session_start` with `core` | `gdb_context_query(action="threads" | "backtrace")`, `gdb_inspect_query(action="evaluate")` |
| Program appears stuck | `gdb_session_query(action="status")`, `gdb_execution_manage(action="interrupt")` | `gdb_context_query(action="threads" | "backtrace")` |
| Need source or assembly around a stop | `gdb_inspect_query(action="source" | "disassembly")` | `gdb_execution_manage(action="finish")`, `gdb_context_query(action="frame")`, focused register or variable reads |
| Flaky crash reproduction | `gdb_run_until_failure` | `capture` bundle plus expression and memory snapshots |
| Fork-heavy behavior | `gdb_inferior_manage(action="set_follow_fork_mode" | "set_detach_on_fork")`, `gdb_breakpoint_manage(action="create", breakpoint.kind="catch")` | `gdb_session_query(action="status")`, `gdb_inferior_query(action="list")`, `gdb_inferior_manage(action="select")` |
| Manual inferior lifecycle | `gdb_inferior_manage(action="create")`, `gdb_inferior_query(action="list")`, `gdb_inferior_manage(action="select")` | `gdb_inferior_manage(action="remove")` |

## Startup Checklist

Run immediately after `gdb_session_start`:

1. Check `status`.
2. Check `target_loaded`.
3. Check `warnings`.
4. Check `execution_state`.
5. Check `env_output` and `init_output` when startup configuration matters.

Treat these outcomes as hard gates:

- `target_loaded=false`: fix target path, symbols, or core inputs first.
- warning about missing symbols: continue only if limited inspection is acceptable.
- unexpected `execution_state=running`: use `gdb_execution_manage(action="interrupt")` before inspection.

## Startup Recipes

### Live Launch with Environment, Cwd, and Argv

`gdb_session_start`

```json
{
  "program": "/path/to/app",
  "args": ["--mode", "stress"],
  "working_dir": "/path/to/workdir",
  "env": {
    "LD_LIBRARY_PATH": "/opt/app/lib",
    "APP_CONFIG": "debug",
    "FEATURE_X": "1"
  }
}
```

### Later Rerun with Different Args

`gdb_execution_manage(action="run")`

```json
{
  "session_id": 1,
  "action": "run",
  "execution": {
    "args": ["--mode", "stress", "--seed", "42"]
  }
}
```

### Background Launch Then Wait Later

`gdb_execution_manage(action="run")`

```json
{
  "session_id": 1,
  "action": "run",
  "execution": {
    "wait": {
      "until": "acknowledged",
      "timeout_sec": 1
    }
  }
}
```

Then synchronize with `gdb_execution_manage(action="wait_for_stop")`:

```json
{
  "session_id": 1,
  "action": "wait_for_stop",
  "execution": {
    "timeout_sec": 30
  }
}
```

### Empty Session Bootstrap for Attach

`gdb_session_start`

```json
{
  "init_commands": []
}
```

Then attach with `gdb_attach_process`:

```json
{
  "session_id": 1,
  "pid": 12345,
  "timeout_sec": 30
}
```

## Playbook 1: Live Program Crash Triage

1. Start session with `gdb_session_start`:

```json
{
  "program": "/path/to/app",
  "working_dir": "/path/to/workdir"
}
```

2. Add a breakpoint with `gdb_breakpoint_manage(action="create")`:

```json
{
  "session_id": 1,
  "action": "create",
  "breakpoint": {
    "kind": "code",
    "location": "main"
  }
}
```

3. Launch with `gdb_execution_manage(action="run")`:

```json
{
  "session_id": 1,
  "action": "run",
  "execution": {}
}
```

4. If needed, block for a later stop with `gdb_execution_manage(action="wait_for_stop")`.

5. Inspect crash context with:

- `gdb_context_query(action="threads")`
- `gdb_context_query(action="backtrace")`
- `gdb_inspect_query(action="variables")`
- `gdb_inspect_query(action="registers")`

Register example:

```json
{
  "session_id": 1,
  "action": "registers",
  "query": {
    "register_names": ["rip", "rsp", "rbp"],
    "include_vector_registers": false,
    "value_format": "natural"
  }
}
```

6. Persist evidence with `gdb_capture_bundle`:

```json
{
  "session_id": 1,
  "output_dir": "/tmp/gdb-captures",
  "bundle_name": "live-crash",
  "include_threads": true,
  "include_backtraces": true,
  "include_frame": true,
  "include_variables": true,
  "include_registers": true,
  "include_stop_history": true,
  "include_transcript": true
}
```

## Playbook 2: Core Dump Workflow

Start with `program` and `core` together:

```json
{
  "program": "/path/to/app",
  "core": "/tmp/core.12345",
  "init_commands": [
    "set sysroot /opt/sysroot",
    "set solib-search-path /opt/sysroot/lib:/opt/sysroot/usr/lib"
  ]
}
```

Then:

1. Run `gdb_context_query(action="threads")`.
2. Run `gdb_context_query(action="backtrace")` for suspicious threads.
3. Run `gdb_context_manage(action="select_thread" | "select_frame")` when you need persistent context.
4. Run `gdb_inspect_query(action="evaluate")` for candidate root-cause variables.

Constraints:

- Do not pass `args` when using `core`.
- Expect startup to be `paused`.
- Prefer `program + core` together for best symbol and locals fidelity.

## Playbook 3: Attach to a Running Process

1. Start an empty session with `gdb_session_start`:

```json
{
  "init_commands": []
}
```

2. Attach with `gdb_attach_process`:

```json
{
  "session_id": 1,
  "pid": 12345,
  "timeout_sec": 30
}
```

3. Confirm stop state with `gdb_session_query(action="status")`.
4. Get thread inventory with `gdb_context_query(action="threads")`.
5. Pull a first backtrace with `gdb_context_query(action="backtrace")`.
6. Resume only if you explicitly want the attached process running again.

## Playbook 4: Hang Investigation

1. Call `gdb_session_query(action="status")`.
2. If running, call `gdb_execution_manage(action="interrupt")`.
3. Call `gdb_context_query(action="threads")`.
4. Call `gdb_context_query(action="backtrace")` for each interesting thread id.
5. Identify lock waits, futex syscalls, or deadlock cycles.
6. Capture a bundle with backtraces and registers.

Useful capture expressions:

- `"errno"`
- `"pthread_self()"`
- `"*global_state"`

## Playbook 5: Fork and Exec Analysis

1. Set fork policy before run with `gdb_inferior_manage`:

```json
{
  "session_id": 1,
  "action": "set_follow_fork_mode",
  "inferior": {
    "mode": "child"
  }
}
```

```json
{
  "session_id": 1,
  "action": "set_detach_on_fork",
  "inferior": {
    "enabled": false
  }
}
```

2. Add catchpoints with `gdb_breakpoint_manage(action="create")`:

```json
{
  "session_id": 1,
  "action": "create",
  "breakpoint": {
    "kind": "catch",
    "event": "fork"
  }
}
```

```json
{
  "session_id": 1,
  "action": "create",
  "breakpoint": {
    "kind": "catch",
    "event": "exec"
  }
}
```

3. Resume and wait with:

- `gdb_execution_manage(action="continue")`
- `gdb_execution_manage(action="wait_for_stop")`

4. Read multi-inferior runtime state from `gdb_session_query(action="status")`.
5. Enumerate inferiors with `gdb_inferior_query(action="list")`.
6. Switch with `gdb_inferior_manage(action="select")` before thread or frame inspection.

## Playbook 6: Focused Source and Assembly Inspection

1. Stop where you want code context.
2. Read source around the current frame with `gdb_inspect_query(action="source")`:

```json
{
  "session_id": 1,
  "action": "source",
  "query": {
    "location": {
      "kind": "current"
    },
    "context_before": 3,
    "context_after": 3
  }
}
```

3. Read mixed source and assembly with `gdb_inspect_query(action="disassembly")`:

```json
{
  "session_id": 1,
  "action": "disassembly",
  "query": {
    "location": {
      "kind": "current"
    },
    "mode": "mixed",
    "instruction_count": 24
  }
}
```

4. If the current frame is a helper and you want the caller next, use `gdb_execution_manage(action="finish")`.
5. Re-run source or disassembly after the stop changes.

## Playbook 7: Manual Inferior Lifecycle

1. Add a new inferior with `gdb_inferior_manage(action="create")`:

```json
{
  "session_id": 1,
  "action": "create",
  "inferior": {
    "executable": "/path/to/helper",
    "make_current": true
  }
}
```

2. Confirm inventory and current selection with `gdb_inferior_query(action="list")`.
3. Switch later with `gdb_inferior_manage(action="select")` if needed.
4. Remove the extra inferior when done:

```json
{
  "session_id": 1,
  "action": "remove",
  "inferior": {
    "inferior_id": 2
  }
}
```

## Playbook 8: Flaky Failure Campaign

Use `gdb_run_until_failure` to avoid ad-hoc loops:

```json
{
  "startup": {
    "program": "/path/to/app",
    "working_dir": "/path/to/workdir"
  },
  "setup_steps": [
    {
      "tool": "gdb_breakpoint_manage",
      "arguments": {
        "action": "create",
        "breakpoint": {
          "kind": "code",
          "location": "critical_path"
        }
      }
    }
  ],
  "run_args": "--stress --seed=42",
  "max_iterations": 100,
  "run_timeout_sec": 30,
  "failure": {
    "stop_reasons": ["signal-received", "watchpoint-trigger"]
  },
  "capture": {
    "enabled": true,
    "bundle_name_prefix": "flaky-critical-path",
    "include_threads": true,
    "include_backtraces": true,
    "include_variables": true
  }
}
```

When you need a deterministic single output directory name, use `capture.bundle_name` instead of `bundle_name_prefix`.

## `gdb_workflow_batch` Template

Use `gdb_workflow_batch` when strict ordering and one-shot orchestration are needed:

```json
{
  "session_id": 1,
  "fail_fast": true,
  "capture_stop_events": true,
  "steps": [
    {
      "tool": "gdb_breakpoint_manage",
      "arguments": {
        "action": "create",
        "breakpoint": {
          "kind": "code",
          "location": "main"
        }
      },
      "label": "set-main-breakpoint"
    },
    {
      "tool": "gdb_execution_manage",
      "arguments": {
        "action": "run",
        "execution": {}
      }
    },
    {
      "tool": "gdb_context_query",
      "arguments": {
        "action": "backtrace",
        "query": {
          "max_frames": 20
        }
      }
    }
  ]
}
```

## Common Failure Modes

- Calling `gdb_execution_manage(action="continue")` while already running
- Calling `gdb_execution_manage(action="step" | "next")` while not paused
- Ignoring startup `warnings` and then trusting variable output
- Hiding launch configuration inside `init_commands` instead of `args`, `env`, or `working_dir`
- Using raw `run &` instead of `gdb_execution_manage(action="run", execution.wait.until="acknowledged")`
- Forgetting that attach sessions keep the target's preexisting environment
- Using only raw `gdb_execute_command` and losing structured outputs
- Forgetting `gdb_session_manage(action="stop")` and leaking debugger sessions
