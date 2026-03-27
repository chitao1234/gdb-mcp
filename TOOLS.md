# GDB MCP Server - Tools Reference

This document provides detailed documentation for all available tools in the GDB MCP Server.

## Session Management

### `gdb_start_session`
Start a new GDB debugging session.

**Parameters:**
- `program` (optional): Path to executable to debug
- `args` (optional): Command-line arguments for the program. Accepts either:
  - list form: `["--mode", "fast"]`
  - shell-style string form: `"--mode fast"`
  Only valid for live program launches, not core-dump analysis.
- `core` (optional): Path to core dump file (uses --core flag for proper symbol resolution)
- `init_commands` (optional): List of GDB commands to run on startup after environment variables have been applied
- `env` (optional): Environment variables to set for the debugged program before any init command can run the inferior (dictionary of name-value pairs)
- `gdb_path` (optional): Path to GDB executable (default: "gdb")
- `working_dir` (optional): Working directory to use when starting GDB

**Returns:**
- `status`: "success" or "error"
- `message`: Status message
- `program` (optional): Program path if specified
- `core` (optional): Core dump path if specified
- `target_loaded`: Whether GDB finished startup with an executable or core file loaded
- `execution_state`: Inferior state after startup (`not_started`, `paused`, `running`, `exited`, or `unknown`)
- `stop_reason` (optional): Stop reason if startup left the inferior paused
- `exit_code` (optional): Exit code if startup completed with an exited inferior
- `startup_output` (optional): GDB's initial output when loading the program
- `warnings` (optional): Array of critical warnings detected, such as:
  - "No debugging symbols found - program was not compiled with -g"
  - "File is not an executable"
  - "Program file not found"
- `env_output` (optional): Output from setting environment variables if env was provided
- `init_output` (optional): Output from init_commands if provided

**Important:** Always check the `warnings` field! Missing debug symbols will prevent breakpoints from working and variable inspection from showing useful information.
Also check `target_loaded`: GDB itself can start successfully while the requested executable or core file still fails to load.
By default, sessions apply `set confirm off` during startup so CLI commands remain non-interactive for automation.

**Core Dump Debugging:**

When debugging core dumps with a sysroot, the order of operations matters for proper symbol resolution. Set `sysroot` and `solib-search-path` **AFTER** loading the core:

```json
{
  "program": "/path/to/executable",
  "core": "/path/to/core.dump",
  "init_commands": [
    "set sysroot /path/to/sysroot",
    "set solib-search-path /path/to/libs"
  ]
}
```

If using `core-file` in init_commands instead of the `core` parameter, ensure it comes before sysroot:
```python
[
    "core-file /path/to/core.dump",
    "set sysroot /path/to/sysroot",
    "set solib-search-path /path/to/libs"
]
```

`args` and `core` cannot be used together in the same startup request. Use `args` for live launches, or `core` for post-mortem analysis.

**Example with custom GDB path:**
```json
{
  "program": "/path/to/myprogram",
  "gdb_path": "/usr/local/bin/gdb-custom"
}
```

Use `gdb_path` when you need to use a specific GDB version or when GDB is not in your PATH.

**Example with environment variables:**
```json
{
  "program": "/path/to/myprogram",
  "env": {
    "LD_LIBRARY_PATH": "/custom/libs:/opt/libs",
    "DEBUG_MODE": "1",
    "LOG_LEVEL": "verbose"
  }
}
```

Environment variables are applied before any `init_commands` run. This is useful for:
- Setting library search paths (LD_LIBRARY_PATH, DYLD_LIBRARY_PATH)
- Configuring application behavior (DEBUG_MODE, LOG_LEVEL, etc.)
- Testing with different environment configurations

### `gdb_list_sessions`
List all currently registered debugger sessions.

**Parameters:**
- none

**Returns:**
- `status`: "success" or "error"
- `sessions`: Array of session summary objects
- `count`: Total number of active sessions

**Each session summary contains:**
- `session_id`: Session identifier
- `lifecycle_state`: Session lifecycle state (`created`, `starting`, `ready`, `failed`, `stopped`, or `closing`)
- `execution_state`: Inferior execution state (`not_started`, `running`, `paused`, `exited`, or `unknown`)
- `target_loaded`: Whether an executable or core is loaded
- `has_controller`: Whether the underlying GDB controller is still active
- `program`: Loaded executable path when known
- `core`: Loaded core path when known
- `working_dir`: Startup working directory when known
- `attached_pid`: Attached process PID when relevant
- `current_thread_id`: Last known selected thread
- `current_frame`: Last known selected frame
- `current_inferior_id`: Last known selected inferior ID
- `inferior_count`: Last known inferior inventory size
- `inferior_states`: Optional array of per-inferior state records (`inferior_id`, `is_current`, `execution_state`, `stop_reason`, `exit_code`)
- `stop_reason`: Last stop reason when known
- `exit_code`: Inferior exit code when known
- `follow_fork_mode`: Last configured follow-fork-mode (`parent` or `child`) when known
- `detach_on_fork`: Last configured detach-on-fork value when known
- `last_failure_message`: Failure detail when the session is in a failed state

### `gdb_execute_command`
Execute a GDB command. Supports both CLI and MI commands.

**Parameters:**
- `command`: GDB command to execute (CLI or MI format)
- `timeout_sec`: Timeout in seconds (default: 30)

**NOTE:** For calling functions in the target process, prefer using the dedicated
`gdb_call_function` tool instead of the 'call' command, as it provides better
structured output and can be separately permissioned.

**Automatically handles two types of commands:**

1. **CLI Commands** (traditional GDB commands):
   - Examples: `info breakpoints`, `list`, `print x`, `run`, `backtrace`
   - Output is formatted as readable text
   - These are the commands you'd type in interactive GDB

2. **MI Commands** (Machine Interface commands, start with `-`):
   - Examples: `-break-list`, `-exec-run`, `-data-evaluate-expression`
   - Return structured data
   - More precise but less human-readable

**Common CLI commands:**
- `info breakpoints` - List all breakpoints
- `info threads` - List all threads
- `run` - Start the program
- `print variable` - Print a variable's value
- `backtrace` - Show call stack
- `list` - Show source code
- `disassemble` - Show assembly code

### `gdb_run`
Run the currently loaded target in a structured way.

**Parameters:**
- `args` (optional): Override inferior arguments for this run. Accepts either:
  - list form: `["--mode", "fast"]`
  - shell-style string form: `"--mode fast"`
- `timeout_sec`: Timeout in seconds (default: 30)

**Use this when:**
- The target is loaded but has not started yet
- You want a structured alternative to raw `run` text
- You want to override inferior argv without going through raw commands

### `gdb_attach_process`
Attach GDB to a running process by PID.

**WARNING:** This is a privileged operation and should be separately permissioned
when possible.

**Parameters:**
- `pid`: PID of the process to attach to
- `timeout_sec`: Timeout in seconds (default: 30)

**Returns:**
- Standard command execution payload

**Typical result:**
- The process becomes paused and inspectable after attach succeeds

### `gdb_list_inferiors`
List inferiors currently known to this GDB session.

**Parameters:**
- none beyond `session_id`

**Returns:**
- `inferiors`: Array of inferiors with fields such as `inferior_id`, `is_current`, `description`, and optional `executable`
- `count`: Number of inferiors
- `current_inferior_id`: Active inferior ID when known

### `gdb_select_inferior`
Select the active inferior by ID.

**Parameters:**
- `inferior_id`: Inferior ID from `gdb_list_inferiors`

### `gdb_set_follow_fork_mode`
Set fork-follow behavior for multi-process debugging.

**Parameters:**
- `mode`: `"parent"` or `"child"`

### `gdb_set_detach_on_fork`
Configure whether GDB detaches from the non-followed side after fork.

**Parameters:**
- `enabled`: `true` or `false`

### `gdb_batch`
Execute a structured sequence of session-scoped tools under one session workflow lock.

**Parameters:**
- `steps`: Ordered list of steps. Each step can be:
  - full object form: `{"tool":"gdb_get_status","arguments":{},"label":"optional"}`
  - shorthand form: `"gdb_get_status"`
- `fail_fast`: Stop after first failed step (default: `true`)
- `capture_stop_events`: Include per-step stop events when available (default: `true`)

### `gdb_capture_bundle`
Write a structured forensic bundle to disk.

**Parameters:**
- `output_dir` (optional): Parent directory for bundle output
- `bundle_name` (optional): Deterministic bundle directory name
- `expressions` (optional): Expressions to evaluate into bundle
- `memory_ranges` (optional): Explicit memory ranges, each entry either:
  - object form: `{"address":"&value","count":16,"offset":0,"name":"label"}`
  - shorthand form: `"&value:16"` or `"&value:16@4"`
- `max_frames`: Frames per backtrace (default: `100`)
- `include_threads`, `include_backtraces`, `include_frame`, `include_variables`, `include_registers`, `include_transcript`, `include_stop_history`

### `gdb_run_until_failure`
Run fresh sessions repeatedly until a failure predicate matches.

**Parameters:**
- `startup`: Session startup settings for each iteration
- `setup_steps` (optional): Same step forms as `gdb_batch.steps`
- `run_args` (optional): List form or shell-style string form, same as `gdb_run.args`
- `run_timeout_sec`: Timeout for each run attempt
- `max_iterations`: Maximum attempts
- `failure`: Matching predicates (stop reasons, exit codes, regex, etc.)
- `capture`: Bundle options for the matching iteration, including:
  - `bundle_name_prefix` for iteration-suffixed naming
  - `bundle_name` for an exact fixed bundle name (mutually exclusive with prefix)
  - `memory_ranges` in object or shorthand string form

### `gdb_call_function`
Call a function in the target process.

**WARNING:** This is a privileged operation that executes code in the debugged program. Use with caution as it may have side effects.

**Parameters:**
- `function_call`: Function call expression (e.g., `printf("hello\n")` or `my_func(arg1, arg2)`)
- `timeout_sec`: Timeout in seconds (default: 30)

**Returns:**
- `status`: "success" or "error"
- `function_call`: The function call expression that was executed
- `result`: The return value or output from the function call

**Use this for:**
- Calling standard library functions: `printf("debug: x=%d\n", x)`, `strlen(str)`
- Calling program functions: `my_cleanup_func()`, `reset_state()`
- Inspecting complex data structures via helper functions

**Examples:**
```json
{"function_call": "printf(\"value: %d\\n\", x)"}
{"function_call": "strlen(buffer)"}
{"function_call": "validate_state()"}
```

**Note:** This dedicated tool enables MCP clients to implement separate permission controls for function calling, which executes code in the target process with the target's privileges.

### `gdb_get_status`
Get the current status of the GDB session.

**Returns:**
- `is_running`: Whether the GDB session is still alive and usable
- `target_loaded`: Whether GDB successfully loaded an executable or core file
- `has_controller`: Whether the session still has an active GDB controller
- `execution_state`: Inferior state (`not_started`, `running`, `paused`, `exited`, or `unknown`)
- `stop_reason`: Stop reason when the inferior is paused or has exited
- `exit_code`: Exit code when the inferior exited and GDB reported one
- `current_inferior_id`: Selected inferior ID when known
- `inferior_count`: Known inferior count when available
- `inferior_states`: Optional array of per-inferior state records (`inferior_id`, `is_current`, `execution_state`, `stop_reason`, `exit_code`)
- `follow_fork_mode`: Current follow-fork-mode when known
- `detach_on_fork`: Current detach-on-fork setting when known

**Notes:**
- If the GDB process has exited unexpectedly, `is_running` becomes `false`
  and `has_controller` becomes `false`
- If startup succeeded but the requested executable or core file did not load,
  `target_loaded` remains `false`
- `execution_state`, `stop_reason`, and `exit_code` reflect the currently selected
  inferior when multiple inferiors exist; use `inferior_states` for full visibility

### `gdb_stop_session`
Stop the current GDB session.

## Thread Inspection

### `gdb_get_threads`
Get information about all threads in the debugged process.

**Returns:**
- List of threads with IDs and states
- Current thread ID
- Thread count

### `gdb_get_backtrace`
Get stack backtrace for a thread.

**Parameters:**
- `thread_id` (optional): Thread ID as an integer or numeric string (`None` for current thread)
- `max_frames`: Maximum number of frames to retrieve (default: 100)

**Notes:**
- `max_frames` is a true upper bound on returned frame count
- Supplying `thread_id` does not change the selected thread after the call

## Breakpoints and Execution Control

### `gdb_set_breakpoint`
Set a breakpoint at a location.

**Parameters:**
- `location`: Function name, file:line, or *address
- `condition` (optional): Conditional expression
- `temporary`: Whether breakpoint is temporary (default: false)

**Examples:**
- `location: "main"` - Break at main function
- `location: "foo.c:42"` - Break at line 42 of foo.c
- `location: "/tmp/my project/foo.c:42"` - Break at a source path containing spaces
- `location: "*0x12345678"` - Break at memory address
- `condition: "x > 10"` - Only break when x > 10

### `gdb_set_watchpoint`
Set a watchpoint on an expression.

**Parameters:**
- `expression`: Expression to watch
- `access`: Access mode
  - `"write"`: break on writes
  - `"read"`: break on reads
  - `"access"`: break on read or write

### `gdb_delete_watchpoint`
Delete a watchpoint by number.

### `gdb_set_catchpoint`
Set a catchpoint for debugger events.

**Parameters:**
- `kind`: Event kind (`fork`, `vfork`, `exec`, `signal`, `syscall`, `throw`, `catch`, etc.)
- `argument` (optional): Kind-specific argument (for example syscall name)
- `temporary` (optional): Use `tcatch` semantics

### `gdb_list_breakpoints`
List all breakpoints with structured data.

**Returns:**
- `status`: "success" or "error"
- `breakpoints`: Array of breakpoint objects
- `count`: Total number of breakpoints

**Each breakpoint object contains:**
- `number`: Breakpoint number (string)
- `type`: "breakpoint", "watchpoint", etc.
- `enabled`: "y" or "n"
- `addr`: Memory address (e.g., "0x0000000000401234")
- `func`: Function name (if available)
- `file`: Source file name (if available)
- `fullname`: Full path to source file (if available)
- `line`: Line number (if available)
- `times`: Number of times this breakpoint has been hit (string)
- `original-location`: Original location string used to set the breakpoint

**Example output:**
```json
{
  "status": "success",
  "breakpoints": [
    {
      "number": "1",
      "type": "breakpoint",
      "enabled": "y",
      "addr": "0x0000000000016cd5",
      "func": "HeapColorStrategy::operator()",
      "file": "color_strategy.hpp",
      "fullname": "/home/user/project/src/color_strategy.hpp",
      "line": "119",
      "times": "3",
      "original-location": "color_strategy.hpp:119"
    }
  ],
  "count": 1
}
```

**Use this to:**
- Verify breakpoints were set at correct locations
- Check which breakpoints have been hit (times > 0)
- Find breakpoint numbers for deletion
- Confirm file paths resolved correctly

### `gdb_continue`
Continue execution until next breakpoint.

If no stop event occurs before timeout, this tool can still return success with the inferior in `running` state.

**Recommended flow:**
- use `gdb_continue` to resume execution
- use `gdb_wait_for_stop` to block for a stop event
- use `gdb_interrupt` if you need to force a pause

### `gdb_wait_for_stop`
Wait for the inferior to stop without polling loops.

**Parameters:**
- `timeout_sec`: Maximum wait time
- `stop_reasons` (optional): Restrict what counts as a match

**Returns:**
- `matched`: Whether observed stop matched optional reason filter
- `timed_out`: Whether wait timed out without a matching stop
- `execution_state`, `stop_reason`, and `last_stop_event`

### `gdb_step`
Step into next instruction (enters functions).

**IMPORTANT:** Only works when program is PAUSED at a specific location.

### `gdb_next`
Step over to next line (doesn't enter functions).

**IMPORTANT:** Only works when program is PAUSED at a specific location.

### `gdb_interrupt`
Interrupt (pause) a running program.

**Use when:**
- Program is running and hasn't hit a breakpoint
- You want to pause execution to inspect state
- Program appears stuck and you want to see where it is
- Commands are timing out because program is running

**After interrupting:** You can use `gdb_get_backtrace`, `gdb_get_variables`, etc.

## Data Inspection

### `gdb_evaluate_expression`
Evaluate a C/C++ expression in the current context.

**Parameters:**
- `expression`: Expression to evaluate
- `thread_id` (optional): Thread ID override (integer or numeric string)
- `frame` (optional): Frame override (integer or numeric string)

**Examples:**
- `"x"` - Get value of variable x
- `"*ptr"` - Dereference pointer
- `"array[5]"` - Access array element
- `"obj->field"` - Access struct field

### `gdb_get_variables`
Get local variables for a stack frame.

**Parameters:**
- `thread_id` (optional): Thread ID (integer or numeric string)
- `frame`: Frame number (integer or numeric string, 0 is current, default: 0)

**Notes:**
- This call inspects the requested thread/frame and then restores the prior
  selection
- Use `gdb_select_thread` or `gdb_select_frame` when you want to change the
  debugger context for later commands

### `gdb_get_registers`
Get CPU register values for the current frame.

**Parameters:**
- `thread_id` (optional): Thread ID override (integer or numeric string)
- `frame` (optional): Frame override (integer or numeric string)
- `register_numbers` (optional): Explicit register numbers to query (integers or numeric strings)
- `register_names` (optional): Explicit register names to query (resolved to numbers)
- `include_vector_registers` (optional): When `false`, omit vector/SIMD-style registers when names are available (default: `true`)
- `max_registers` (optional): Upper bound on returned register records
- `value_format` (optional): Value rendering mode (`"hex"` or `"natural"`, default: `"hex"`)

**Notes:**
- Like `gdb_evaluate_expression`, this can inspect a specific thread/frame
  without changing the selected debugger context permanently
- `register_numbers` and `register_names` can be combined; duplicates are removed
- `max_registers` is applied after any vector-register filtering

### `gdb_read_memory`
Read raw target memory bytes from an address expression.

**Parameters:**
- `address`: Address expression
- `count`: Number of addressable units to read
- `offset` (optional): Offset relative to `address`

**Returns:**
- `blocks`: Readable memory blocks (with gaps represented as separate blocks)
- `captured_bytes`: Total successfully captured bytes
