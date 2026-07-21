# Living Agent System

The Living Agent system provides a background task execution framework — agents
register their capabilities, clients submit tasks, and a background worker
dispatches and executes them. It is **independent** of the Lead Agent graph
runtime: tasks run via a pluggable executor callback, not LangGraph.

## Module Map

| Module | Path | Role |
|--------|------|------|
| Agent model | `deerflow/agents/model.py` | `Agent` dataclass (id, capabilities, status, access_level) |
| AgentRegistry | `deerflow/agents/registry.py` | Agent CRUD + capability prefix routing |
| Task model | `deerflow/tasks/model.py` | `Task` dataclass + pure lifecycle transitions |
| TaskStore | `deerflow/tasks/store.py` | Thread-safe task queue + file persistence |
| Classifier | `deerflow/tasks/classifier.py` | Keyword-based task → skill/channel mapping |
| Orchestrator | `deerflow/tasks/orchestrator.py` | Execution plan generation with gate steps |
| HumanGate | `deerflow/tasks/gate.py` | Gate model + approve/reject lifecycle |
| HumanGateStore | `deerflow/tasks/gate_store.py` | Gate CRUD + file persistence |
| TaskCheckpointer | `deerflow/runtime/checkpointer/task_checkpointer.py` | Checkpoint save/restore for task state |
| AgentWorker | `deerflow/runtime/agent_worker.py` | Background poll loop + dispatch + gate resume |
| LivingAgentService | `app/gateway/living_agent.py` | Wires worker into Gateway lifespan |
| AgentTasks Router | `app/gateway/routers/agent_tasks.py` | REST API (agents, tasks, gates) |

## Lifecycle

A task flows through the system as follows:

```
Client → POST /api/agents/tasks → TaskStore (pending)
                                          ↓
AgentWorker._poll() → find_pending()      ↓
                                          ↓
AgentRegistry.find_by_capability() → claim → classify / orchestrate
                                          ↓
                               Executor(task, skill, channel)
                                          ↓
                                 complete() or fail()
```

## Agent Model

`Agent` has four lifecycle statuses: `idle`, `active`, `paused`, `disabled`.
Each agent declares a list of `capabilities` for routing. Task capabilities
are matched against agent capabilities via **prefix match**: an agent whose
capability starts with the task's requirement can handle it (e.g. agent has
`"dev.code"`, task requests `"dev"` → match). This allows hierarchical
categorization: an agent specialized in `"dev.code"` can handle general
`"dev"` tasks but not `"dev.ops"` tasks.

Capability routing is implemented in `agent_matches_capability()`:

```python
def agent_matches_capability(agent: Agent, capability: str) -> bool:
    return any(cap.startswith(capability) for cap in agent.capabilities)
```

## Task Lifecycle

Tasks progress through: `pending → claimed → executing → completed | failed`
Cancellation is allowed from `pending` or `claimed`.

Transitions are pure functions (`transition_claim`, `transition_execute`,
`transition_complete`, `transition_fail`, `transition_cancel`) — they raise
`ValueError` on invalid state transitions. `TaskStore` wraps each transition
with thread-safe locking and optional file persistence.

## AgentWorker

The `AgentWorker` is an async background loop:

- **Poll cycle**: Every `poll_interval` seconds, calls `_poll()` which:
  1. Fetches up to 5 pending tasks via `find_pending()`
  2. Matches each to an idle agent via `find_by_capability()`
  3. Dispatches via `_dispatch_simple()` (classify + execute) or
     `_dispatch_with_plan()` (orchestrator plan with optional gate steps)
  4. Checks for gate-resumable tasks via `_check_gate_resumes()`

- **Dispatch paths**:
  - `_dispatch_simple`: Uses `classify_task()` for skill/channel, executes once
  - `_dispatch_with_plan`: Uses `SkillOrchestrator` to build an `ExecutionPlan`
    with multiple steps + optional `HumanGate` checkpoints

- **Executor callback**: Set via `set_executor(callable)`. The callable receives
  `(task, skill, channel)` and returns `dict[str, Any]` with `status` (must be
  `"completed"` or `"ok"` for success). `LivingAgentService.start()` sets a
  default executor that logs and returns a completed result.

## Human-in-the-loop Gates

Gates pause execution between steps for human approval:

1. `SkillOrchestrator.plan(description, catalog, human_review=True)` inserts
   `PlanStepKind.gate` steps into the execution plan
2. `AgentWorker._execute_steps()` encounters a gate step → creates a
   `HumanGate` record in `HumanGateStore` → pauses (saves remaining steps
   to checkpointer as `gate_paused` phase)
3. External consumers approve/reject via
   `POST /api/agents/gates/{id}/approve` or `.../reject`
4. Next poll cycle: `_check_gate_resumes()` detects resolved gates →
   `_resume_after_gate()` loads checkpoint and continues remaining steps
5. Rejection fails the task; multiple gates are supported (each creates a
   new `gate_paused` checkpoint)

## REST API

All endpoints are mounted at `/api/agents`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/agents/agents` | GET/POST | List/create agents |
| `/api/agents/agents/{id}` | GET/DELETE | Get/delete an agent |
| `/api/agents/tasks` | GET/POST | List/submit tasks |
| `/api/agents/tasks/{id}` | GET | Get task status |
| `/api/agents/tasks/{id}/claim` | POST | Claim a task |
| `/api/agents/tasks/{id}/cancel` | POST | Cancel a task |
| `/api/agents/gates` | GET | List gates (filterable) |
| `/api/agents/gates/{id}` | GET | Get gate details |
| `/api/agents/gates/{id}/approve` | POST | Approve a gate |
| `/api/agents/gates/{id}/reject` | POST | Reject a gate |

## Gateway Wiring

`LivingAgentService` wires everything into the Gateway lifespan:

```python
# In app.py lifespan:
living_agent = LivingAgentService(poll_interval=5.0)
await living_agent.start()
app.state.agent_registry = living_agent.registry
app.state.task_store = living_agent.task_store
app.state.checkpointer = living_agent.checkpointer
app.state.gate_store = living_agent.gate_store
```

`LivingAgentService` owns the shared stores (`AgentRegistry`, `TaskStore`,
`HumanGateStore`, `TaskCheckpointer`) and manages the worker's start/stop
lifecycle. The default checkpointer backend is a simple in-memory dict store
(rather than LangGraph's `MemorySaver`, whose `put()` signature is
incompatible with `TaskCheckpointer`'s call protocol).

## Testing

All tests are in `backend/tests/`:

| File | Tests | Scope |
|------|-------|-------|
| `test_agent_worker.py` | 24 | Worker poll/dispatch/gates/resume |
| `test_agent_tasks_router.py` | 29 | REST API endpoints |
| `test_living_agent.py` | 5 | Service lifecycle + default executor |
| `test_app.py` | 2 | Router registration + integration |
