# GDB MCP Server Usage Guide

This guide shows how an AI assistant can drive the current v2 gdb-mcp interface. All tool names and payloads below match the implemented `*_query`, `*_manage`, and dedicated workflow tools described in [TOOLS.md](../TOOLS.md).

## Prerequisites

1. Build the sample program: `cd examples && make`
2. Configure the MCP server in your client
3. Give the assistant access to the gdb-mcp tools

## Quick Mental Model

- `gdb_session_start` creates a session and returns `session_id`
- `gdb_session_query` and `gdb_session_manage` cover session inventory and lifecycle
- `gdb_execution_manage` controls run, continue, interrupt, step, next, finish, and wait
- `gdb_breakpoint_manage` and `gdb_breakpoint_query` cover code breakpoints, watchpoints, and catchpoints
- `gdb_context_query`, `gdb_context_manage`, and `gdb_inspect_query` cover threads, frames, locals, expressions, memory, source, and disassembly

## Example Workflow 1: Basic Debugging Session

### Goal

Load a program, stop at `main`, inspect locals, then continue.

### Step 1: Start the session

User prompt:

```text
Start a GDB session with the program at examples/sample_program
```

AI will call `gdb_session_start` with:

```json
{
  "program": "examples/sample_program"
}
```

### Step 2: Set a breakpoint

User prompt:

```text
Set a breakpoint at the main function
```

AI will call `gdb_breakpoint_manage` with:

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

### Step 3: Run the program

User prompt:

```text
Run the program
```

AI will call `gdb_execution_manage` with:

```json
{
  "session_id": 1,
  "action": "run",
  "execution": {}
}
```

### Step 4: Inspect local variables

User prompt:

```text
Show me all the local variables
```

AI will call `gdb_inspect_query` with:

```json
{
  "session_id": 1,
  "action": "variables",
  "query": {}
}
```

### Step 5: Continue execution

User prompt:

```text
Continue until the next breakpoint or completion
```

AI will call `gdb_execution_manage` with:

```json
{
  "session_id": 1,
  "action": "continue",
  "execution": {}
}
```

## Example Workflow 2: Thread Analysis

### Goal

Analyze thread behavior in a multi-threaded program.

### Step 1: Start and stop in the worker function

User prompt:

```text
Start a GDB session with examples/sample_program, set a breakpoint at worker_thread,
and run to it
```

AI will call:

1. `gdb_session_start`
2. `gdb_breakpoint_manage(action="create")`
3. `gdb_execution_manage(action="run")`

Representative breakpoint payload:

```json
{
  "session_id": 1,
  "action": "create",
  "breakpoint": {
    "kind": "code",
    "location": "worker_thread"
  }
}
```

### Step 2: Inspect threads

User prompt:

```text
How many threads are there and what is each one doing?
```

AI will call:

1. `gdb_context_query` with `action="threads"`
2. `gdb_context_query` with `action="backtrace"` for the interesting thread ids

Representative backtrace payload:

```json
{
  "session_id": 1,
  "action": "backtrace",
  "query": {
    "thread_id": 2,
    "max_frames": 20
  }
}
```

### Step 3: Inspect one thread in detail

User prompt:

```text
Show me the local variables for thread 2 in the top frame
```

AI will call `gdb_inspect_query` with:

```json
{
  "session_id": 1,
  "action": "variables",
  "query": {
    "context": {
      "thread_id": 2,
      "frame": 0
    }
  }
}
```

## Example Workflow 3: Core Dump Analysis

### Goal

Analyze a crashed program from a core dump.

### Setup

```bash
ulimit -c unlimited
./examples/sample_program &
PID=$!
sleep 1
kill -ABRT $PID
```

### Step 1: Load the executable and core

User prompt:

```text
Load the executable examples/sample_program and the core dump at core.12345
```

AI will call `gdb_session_start` with:

```json
{
  "program": "examples/sample_program",
  "core": "core.12345"
}
```

### Step 2: Investigate the crash

User prompt:

```text
Tell me how this program crashed: how many threads were there, what was each thread doing,
and what were the key global values?
```

AI will call:

1. `gdb_context_query(action="threads")`
2. `gdb_context_query(action="backtrace")` for suspicious threads
3. `gdb_inspect_query(action="evaluate")` for important globals

Representative evaluate payload:

```json
{
  "session_id": 1,
  "action": "evaluate",
  "query": {
    "expression": "counter"
  }
}
```

## Example Workflow 4: Initialization Scripts and Symbol Paths

### Goal

Start with a GDB script or custom symbol-path configuration.

User prompt:

```text
Start a debugging session using the initialization script at examples/setup.gdb
```

AI will call `gdb_session_start` with:

```json
{
  "init_commands": [
    "source examples/setup.gdb"
  ]
}
```

Then it can confirm setup with:

1. `gdb_session_query(action="status")`
2. `gdb_breakpoint_query(action="list")`

If symbol paths matter, use `init_commands` such as:

```json
{
  "program": "examples/sample_program",
  "init_commands": [
    "set sysroot /opt/custom/sysroot",
    "set solib-search-path /opt/custom/libs"
  ]
}
```

## Example Workflow 5: Conditional Breakpoints

### Goal

Stop only when a specific condition becomes true.

User prompt:

```text
Start debugging examples/sample_program. Set a breakpoint at worker_thread, but only
when counter is greater than 5.
```

AI will call:

1. `gdb_session_start`
2. `gdb_breakpoint_manage(action="create")`

Conditional breakpoint payload:

```json
{
  "session_id": 1,
  "action": "create",
  "breakpoint": {
    "kind": "code",
    "location": "worker_thread",
    "condition": "counter > 5"
  }
}
```

## Example Workflow 6: Mutex or Hang Investigation

### Goal

Let the program run in the background, then pause and inspect what each thread is doing.

User prompt:

```text
Debug examples/sample_program. Let it run for a bit, then pause it and tell me which
threads are blocked on mutexes.
```

AI will call:

1. `gdb_session_start`
2. `gdb_execution_manage(action="run")` with `wait.until="acknowledged"`
3. `gdb_execution_manage(action="interrupt")`
4. `gdb_context_query(action="threads")`
5. `gdb_context_query(action="backtrace")` for the interesting threads

Background run payload:

```json
{
  "session_id": 1,
  "action": "run",
  "execution": {
    "wait": {
      "until": "acknowledged"
    }
  }
}
```

## Advanced Techniques

### Examining Memory

AI will call `gdb_inspect_query(action="memory")` with:

```json
{
  "session_id": 1,
  "action": "memory",
  "query": {
    "address": "0x12345678",
    "count": 64
  }
}
```

### Assembly-Level Debugging

AI will call:

1. `gdb_inspect_query(action="disassembly")`
2. `gdb_inspect_query(action="registers")`

Representative disassembly payload:

```json
{
  "session_id": 1,
  "action": "disassembly",
  "query": {
    "location": {
      "kind": "current"
    },
    "instruction_count": 24,
    "mode": "mixed"
  }
}
```

### Source Context Inspection

AI will call `gdb_inspect_query(action="source")` with:

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

### Stepping Out of the Current Frame

AI will call `gdb_execution_manage(action="finish")` with:

```json
{
  "session_id": 1,
  "action": "finish",
  "execution": {}
}
```

### Calling Functions

AI will call `gdb_call_function` with:

```json
{
  "session_id": 1,
  "function_call": "calculate_sum(array, 5)",
  "timeout_sec": 30
}
```

### Multi-Inferior Workflows

AI will call:

1. `gdb_inferior_manage(action="create")`
2. `gdb_inferior_query(action="list")`
3. `gdb_inferior_manage(action="select")`
4. `gdb_inferior_manage(action="remove")`

Representative create payload:

```json
{
  "session_id": 1,
  "action": "create",
  "inferior": {
    "executable": "/tmp/helper",
    "make_current": true
  }
}
```

## Workflow Tools

### One Locked Batch

Use `gdb_workflow_batch` when ordering matters and you want one structured transcript:

```json
{
  "session_id": 1,
  "steps": [
    {
      "tool": "gdb_breakpoint_manage",
      "label": "break main",
      "arguments": {
        "action": "create",
        "breakpoint": {
          "kind": "code",
          "location": "main"
        }
      }
    },
    {
      "tool": "gdb_execution_manage",
      "label": "run",
      "arguments": {
        "action": "run",
        "execution": {}
      }
    },
    {
      "tool": "gdb_context_query",
      "label": "stack",
      "arguments": {
        "action": "backtrace",
        "query": {}
      }
    }
  ]
}
```

### Repeat Until Failure

Use `gdb_run_until_failure` instead of hand-written rerun loops:

```json
{
  "startup": {
    "program": "examples/sample_program"
  },
  "setup_steps": [
    {
      "tool": "gdb_breakpoint_manage",
      "arguments": {
        "action": "create",
        "breakpoint": {
          "kind": "code",
          "location": "worker_thread"
        }
      }
    }
  ],
  "max_iterations": 25,
  "failure": {
    "stop_reasons": ["signal-received", "watchpoint-trigger"]
  },
  "capture": {
    "enabled": true,
    "output_dir": "/tmp/gdb-mcp-captures",
    "bundle_name_prefix": "sample-failure"
  }
}
```

## Troubleshooting

### Session Not Starting

- Verify GDB is installed: `which gdb`
- Check the executable path you passed to `gdb_session_start`
- Inspect `warnings`, `startup_output`, `env_output`, and `init_output`

### No Debug Symbols

- Rebuild with `-g`
- Check the `warnings` field on `gdb_session_start`
- Use `gdb_execute_command` only when you specifically need raw GDB output such as `info sources`

### Timeout Errors

- Increase `timeout_sec` on the structured tool you are calling
- If execution is still running, use `gdb_execution_manage(action="wait_for_stop")` or `gdb_execution_manage(action="interrupt")`
- For long-running launches, prefer `gdb_execution_manage(action="run", execution.wait.until="acknowledged")`

## Tips for Working with AI

1. Be explicit about the executable, core dump, or PID.
2. Break multi-step tasks into ordered requests when you care about the debugging narrative.
3. Ask for summaries after the assistant inspects threads, backtraces, or variables.
4. Mention whether you want source-level, assembly-level, or memory-level inspection.
5. Use init scripts and `working_dir` when reproducing environment-sensitive bugs.

## Next Steps

- Try the example prompts with your MCP client
- Compare the request and response details in [TOOLS.md](../TOOLS.md)
- Use [skills/debug-with-gdb-mcp/SKILL.md](../skills/debug-with-gdb-mcp/SKILL.md) for higher-discipline debugging workflows
