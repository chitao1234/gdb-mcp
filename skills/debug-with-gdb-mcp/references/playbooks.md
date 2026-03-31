# gdb-mcp Playbooks

## Fast Decision Map

| Situation | Primary tools | Follow-up |
| --- | --- | --- |
| New live debug session | `gdb_start_session`, `gdb_set_breakpoint`, `gdb_run` | `gdb_wait_for_stop`, `gdb_get_backtrace`, `gdb_get_variables` |
| Live launch with custom env/cwd/argv | `gdb_start_session` with `args`, `env`, `working_dir` | `gdb_run` or later `gdb_run` with override args |
| Background launch and inspect later | `gdb_run` with `wait_for_stop=false` | `gdb_wait_for_stop` or `gdb_interrupt`, then thread/frame inspection |
| Attach to running process | `gdb_start_session`, `gdb_attach_process` | `gdb_get_status`, `gdb_get_threads`, `gdb_get_backtrace` |
| Core dump analysis | `gdb_start_session` with `core` | `gdb_get_threads`, `gdb_get_backtrace`, `gdb_evaluate_expression` |
| Program appears stuck | `gdb_get_status`, `gdb_interrupt` | `gdb_get_threads`, per-thread `gdb_get_backtrace` |
| Need source or assembly around a stop | `gdb_get_source_context`, `gdb_disassemble` | `gdb_finish`, `gdb_get_frame_info`, focused register or variable reads |
| Flaky crash reproduction | `gdb_run_until_failure` | `capture` bundle plus expression and memory snapshots |
| Fork-heavy behavior | `gdb_set_follow_fork_mode`, `gdb_set_detach_on_fork`, `gdb_set_catchpoint` | `gdb_get_status` (`inferior_states`), `gdb_list_inferiors`, `gdb_select_inferior` |
| Manual inferior lifecycle | `gdb_add_inferior`, `gdb_list_inferiors`, `gdb_select_inferior` | `gdb_remove_inferior` when cleanup matters |

## Startup Checklist

Run immediately after `gdb_start_session`:

1. Check `status`.
2. Check `target_loaded`.
3. Check `warnings`.
4. Check `execution_state`.
5. Check `env_output` and `init_output` when startup configuration matters.

Treat these outcomes as hard gates:

- `target_loaded=false`: fix target path, symbols, or core inputs first.
- warning about missing symbols: continue only if limited inspection is acceptable.
- unexpected `execution_state=running`: use `gdb_interrupt` before inspection.

## Startup Recipes

### Live Launch with Environment, Cwd, and Argv

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

Use this when reproducing a bug depends on relative files, a non-default library path, or feature flags. Keep this data in `args`, `working_dir`, and `env` instead of encoding it into `init_commands`.

### Later Rerun with Different Args

After the session already exists, use `gdb_run` for one-off argv overrides:

```json
{
  "session_id": 1,
  "args": ["--mode", "stress", "--seed", "42"]
}
```

### Background Launch Then Wait Later

Use this when you want the structured replacement for raw `run &`:

```json
{
  "session_id": 1,
  "wait_for_stop": false,
  "timeout_sec": 1
}
```

Then synchronize later with:

```json
{
  "session_id": 1,
  "timeout_sec": 30
}
```

### Empty Session Bootstrap for Attach

```json
{
  "init_commands": []
}
```

Then attach:

```json
{
  "session_id": 1,
  "pid": 12345
}
```

Notes:

- Attach usually leaves the process paused and inspectable.
- `env` does not rewrite the environment of a process that is already running.

## Playbook 1: Live Program Crash Triage

1. Start session:

```json
{
  "program": "/path/to/app",
  "working_dir": "/path/to/workdir"
}
```

2. Add breakpoints:

```json
{
  "session_id": 1,
  "location": "main"
}
```

3. Launch:

```json
{
  "session_id": 1
}
```

4. Block for stop:

```json
{
  "session_id": 1,
  "timeout_sec": 60
}
```

5. Inspect crash context:

- `gdb_get_threads`
- `gdb_get_backtrace` for current thread
- `gdb_get_variables` frame 0
- `gdb_get_registers` with focused selectors for lighter payloads:

```json
{
  "session_id": 1,
  "register_names": ["rip", "rsp", "rbp"],
  "include_vector_registers": false,
  "value_format": "natural"
}
```

6. Persist evidence:

```json
{
  "session_id": 1,
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

Start with `program` and `core` together, then set symbol paths in `init_commands` after core load:

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

1. Run `gdb_get_threads`.
2. Run `gdb_get_backtrace` for suspicious threads.
3. Run `gdb_select_thread` + `gdb_select_frame` when you need persistent context.
4. Run `gdb_evaluate_expression` for candidate root-cause variables.

Constraints:

- Do not pass `args` when using `core`.
- Expect startup to be `paused`.
- Prefer `program + core` together for best symbol/locals fidelity; core-only sessions can have weaker symbol resolution.

## Playbook 3: Attach to a Running Process

1. Start an empty session:

```json
{
  "init_commands": []
}
```

2. Attach to the PID:

```json
{
  "session_id": 1,
  "pid": 12345
}
```

3. Confirm stop state with `gdb_get_status`.
4. Get thread inventory with `gdb_get_threads`.
5. Pull a first backtrace with `gdb_get_backtrace`.
6. Resume only if you explicitly want the attached process running again.

## Playbook 4: Hang Investigation

1. Call `gdb_get_status`.
2. If running, call `gdb_interrupt`.
3. Call `gdb_get_threads`.
4. Call `gdb_get_backtrace` for each thread id.
5. Identify lock waits, futex syscalls, or deadlock cycles.
6. Capture a bundle with backtraces and registers.

Useful capture expressions:

- `"errno"`
- `"pthread_self()"`
- `"*global_state"`

## Playbook 5: Fork and Exec Analysis

1. Set fork policy before run:

```json
{
  "session_id": 1,
  "mode": "child"
}
```

```json
{
  "session_id": 1,
  "enabled": false
}
```

2. Add catchpoints:

```json
{
  "session_id": 1,
  "kind": "fork"
}
```

```json
{
  "session_id": 1,
  "kind": "exec"
}
```

3. Resume and wait:

- `gdb_continue`
- `gdb_wait_for_stop` with optional `stop_reasons`

4. Read multi-inferior runtime state from `gdb_get_status`:

- Confirm `current_inferior_id`.
- Inspect `inferior_states` to see running/paused/exited transitions for each inferior.

5. Enumerate inferiors with `gdb_list_inferiors`.
6. Switch with `gdb_select_inferior` before thread/frame inspection.

## Playbook 6: Focused Source and Assembly Inspection

1. Stop where you want code context.
2. Read source around the current frame:

```json
{
  "session_id": 1,
  "context_before": 3,
  "context_after": 3
}
```

3. Read mixed source and assembly for the same stop:

```json
{
  "session_id": 1,
  "mode": "mixed",
  "instruction_count": 24
}
```

4. If the current frame is a helper and you want the caller next, use:

```json
{
  "session_id": 1,
  "timeout_sec": 30
}
```

5. Re-run `gdb_get_source_context` or `gdb_disassemble` after the stop changes.

## Playbook 7: Manual Inferior Lifecycle

1. Add a new inferior:

```json
{
  "session_id": 1,
  "executable": "/path/to/helper",
  "make_current": true
}
```

2. Confirm inventory and current selection with `gdb_list_inferiors`.
3. Switch later with `gdb_select_inferior` if needed.
4. Remove the extra inferior when done:

```json
{
  "session_id": 1,
  "inferior_id": 2
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
      "tool": "gdb_set_breakpoint",
      "arguments": {
        "location": "critical_path"
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
    "bundle_name_prefix": "flaky-critical-path",
    "include_threads": true,
    "include_backtraces": true,
    "include_variables": true
  }
}
```

When you need a deterministic single output directory name (no iteration suffix), use `capture.bundle_name` instead of `bundle_name_prefix`:

```json
{
  "startup": {
    "program": "/path/to/app"
  },
  "max_iterations": 20,
  "failure": {
    "stop_reasons": ["signal-received"]
  },
  "capture": {
    "bundle_name": "latest-signal-failure"
  }
}
```

## `gdb_batch` Template

Use `gdb_batch` when strict ordering and one-shot orchestration are needed:

```json
{
  "session_id": 1,
  "fail_fast": true,
  "capture_stop_events": true,
  "steps": [
    {
      "tool": "gdb_set_breakpoint",
      "arguments": {
        "location": "main"
      },
      "label": "set-main-breakpoint"
    },
    {
      "tool": "gdb_run",
      "arguments": {}
    },
    {
      "tool": "gdb_wait_for_stop",
      "arguments": {
        "timeout_sec": 10
      }
    },
    {
      "tool": "gdb_get_backtrace",
      "arguments": {
        "max_frames": 20
      }
    }
  ]
}
```

For lightweight one-off batches, `steps` also accepts shorthand strings:

```json
{
  "session_id": 1,
  "steps": [
    "gdb_get_status",
    "gdb_get_threads"
  ]
}
```

## Common Failure Modes

- Calling `gdb_continue` while already running.
- Calling `gdb_step` or `gdb_next` while not paused.
- Ignoring startup `warnings` and then trusting variable output.
- Hiding launch configuration inside `init_commands` instead of `args`, `env`, or `working_dir`.
- Using raw `run &` instead of `gdb_run(wait_for_stop=false)` and losing structured state transitions.
- Forgetting that attach sessions keep the target's preexisting environment.
- Using raw `disassemble`, `list`, or `add-inferior` flows instead of `gdb_disassemble`, `gdb_get_source_context`, or `gdb_add_inferior`.
- Using only raw `gdb_execute_command` and losing structured outputs.
- Forgetting `gdb_stop_session` and leaking debugger sessions.
