# Fork Inferior Status Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix stale inferior status metadata after fork-driven inferior changes and correctly attribute stop events when GDB omits `thread-group`.

**Architecture:** Keep the fix inside the session runtime/command runner boundary. Extend notify processing so fork-related topology changes are fully observed, then reconcile inferior inventory/current selection from authoritative GDB data when the async stream indicates topology churn. For stop attribution, resolve the inferior from the stop payload using both `thread-group` and thread data before mutating runtime state or recording the structured stop event.

**Tech Stack:** Python 3.10+, session runtime under `src/gdb_mcp/session/`, pytest, ruff, mypy, uv.

---

### Task 1: Reproduce The Two Runtime Bugs

**Files:**
- Modify: `tests/session/test_execution_api.py`
- Test/Verify: `uv run --extra dev pytest -q tests/session/test_execution_api.py -k 'fork_inventory or missing_thread_group'`

**Testing approach:** `TDD`
Reason: Both bugs are observable session-state regressions with a narrow automated seam in the session API tests.

- [ ] **Step 1: Add a failing test for fork-related inferior reconciliation**

```python
def test_continue_execution_refreshes_inferior_inventory_after_fork(...):
    ...
```

- [ ] **Step 2: Add a failing test for stopped-event attribution without `thread-group`**

```python
def test_continue_execution_attributes_stop_without_thread_group(...):
    ...
```

- [ ] **Step 3: Verify the focused tests fail for the expected reasons**

Run: `uv run --extra dev pytest -q tests/session/test_execution_api.py -k 'fork_inventory or missing_thread_group'`
Expected: both tests fail because runtime selection/inventory stays stale and the stop event is attributed to the wrong inferior.

- [ ] **Step 4: Commit**

```bash
git add tests/session/test_execution_api.py docs/superpowers/plans/2026-03-31-fork-inferior-status-fix.md
git commit -m "test: reproduce fork inferior status bugs"
```

### Task 2: Reconcile Inferiors And Attribute Stops Correctly

**Files:**
- Modify: `src/gdb_mcp/session/command_runner.py`
- Modify: `src/gdb_mcp/session/runtime.py`
- Test/Verify: `uv run --extra dev pytest -q tests/session/test_execution_api.py -k 'fork_inventory or missing_thread_group'`

**Testing approach:** `TDD`
Reason: The runtime mutation path should be implemented only after the reproducer tests are in place and failing.

- [ ] **Step 1: Implement authoritative inferior reconciliation after fork-related topology changes**

```python
# Extend command-runner notify processing and refresh runtime inventory/current selection
# from authoritative GDB inferior data when async topology changes were observed.
```

- [ ] **Step 2: Implement stop-inferior resolution that can fall back from `thread-group` to thread-based data**

```python
# Resolve the stopped inferior before mutating runtime state or building StopEvent.
```

- [ ] **Step 3: Verify the focused tests now pass**

Run: `uv run --extra dev pytest -q tests/session/test_execution_api.py -k 'fork_inventory or missing_thread_group'`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/gdb_mcp/session/command_runner.py src/gdb_mcp/session/runtime.py tests/session/test_execution_api.py
git commit -m "fix: refresh inferiors after fork stops"
```

### Task 3: Full Verification And Local Integration

**Files:**
- Modify: none expected unless verification exposes gaps
- Test/Verify: `uv run --extra dev ruff check src tests`
- Test/Verify: `uv run --extra dev mypy src`
- Test/Verify: `uv run --extra dev pytest -q`
- Test/Verify: `git diff --check`

**Testing approach:** `existing tests + targeted verification`
Reason: The change is small but touches central runtime coordination, so repo-standard verification is warranted before merging back locally.

- [ ] **Step 1: Run repository verification**

```bash
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
uv run --extra dev pytest -q
git diff --check
```

- [ ] **Step 2: Merge back into local `master`**

```bash
git checkout master
git merge --ff-only fork-inferior-status-fix
```
