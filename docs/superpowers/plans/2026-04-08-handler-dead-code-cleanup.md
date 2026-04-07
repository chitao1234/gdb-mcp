# MCP Handler Dead Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Remove unreachable compatibility-era handler code from `src/gdb_mcp/mcp/handlers.py` without changing the public MCP behavior.

**Architecture:** Keep the current typed v2 dispatch structure intact and delete only helper functions and imports that no longer participate in `dispatch_tool_call()` or `SESSION_TOOL_SPECS`. Treat this as cleanup-only work: no schema changes, no public tool changes, and no behavioral rewrites.

**Tech Stack:** Python 3.10+, Pydantic v2, pytest, ruff, mypy.

---

### Task 1: Prune Unreachable MCP Handler Helpers

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Verify: `tests/mcp/test_handlers.py`
- Verify: `tests/mcp/test_runtime.py`
- Verify: `tests/mcp/test_app.py`

**Testing approach:** `existing tests + targeted verification`
Reason: This slice is structural cleanup only. The runtime behavior is already covered by MCP handler, runtime, and app tests; the goal is to preserve that behavior while deleting code that is no longer reachable.

- [ ] **Step 1: Capture the current dead-code candidates and live dispatch surface**

```bash
rg -n "def _handle_(run|add_inferior|remove_inferior|list_inferiors|select_inferior|set_follow_fork_mode|set_detach_on_fork|get_status|get_threads|select_thread|get_backtrace|select_frame|get_frame_info|set_breakpoint|set_watchpoint|delete_watchpoint|set_catchpoint|list_breakpoints|delete_breakpoint|enable_breakpoint|disable_breakpoint|continue|wait_for_stop|step|next|finish|interrupt|evaluate_expression|read_memory|disassemble|get_variables|get_source_context|get_registers)" src/gdb_mcp/mcp/handlers.py
sed -n '1404,1453p' src/gdb_mcp/mcp/handlers.py
```

Expected: the helper definitions exist, but the live v2 dispatch surface is limited to `dispatch_tool_call()` branches plus `SESSION_TOOL_SPECS`.

- [ ] **Step 2: Remove unreachable helpers and the imports that only support them**

```python
# src/gdb_mcp/mcp/handlers.py

# Delete the private helpers that no longer participate in v2 dispatch:
# - _handle_run
# - _handle_add_inferior / _handle_remove_inferior / _handle_list_inferiors / _handle_select_inferior
# - _handle_set_follow_fork_mode / _handle_set_detach_on_fork
# - _handle_get_status / _handle_get_threads / _handle_select_thread / _handle_get_backtrace
# - _handle_select_frame / _handle_get_frame_info
# - _handle_set_breakpoint / _handle_set_watchpoint / _handle_delete_watchpoint
# - _handle_set_catchpoint / _handle_list_breakpoints / _handle_delete_breakpoint
# - _handle_enable_breakpoint / _handle_disable_breakpoint
# - _handle_continue / _handle_wait_for_stop / _handle_step / _handle_next / _handle_finish / _handle_interrupt
# - _handle_evaluate_expression / _handle_read_memory / _handle_disassemble
# - _handle_get_variables / _handle_get_source_context / _handle_get_registers
# - _normalize_int_argument
# - _invalid_session_result

# Also delete schema imports that become unused after the helper block is removed.
```

- [ ] **Step 3: Run focused MCP verification after the cleanup**

Run: `uv run pytest -q tests/mcp/test_handlers.py tests/mcp/test_runtime.py tests/mcp/test_app.py`
Expected: PASS

- [ ] **Step 4: Run repository verification for the cleanup slice**

Run: `uv run ruff check src tests && uv run mypy src && uv run pytest -q && git diff --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-04-08-handler-dead-code-cleanup.md src/gdb_mcp/mcp/handlers.py
git commit -m "refactor: prune dead MCP handler helpers"
```
