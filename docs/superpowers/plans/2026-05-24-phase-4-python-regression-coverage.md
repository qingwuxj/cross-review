# Phase 4 Python Regression Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand regression coverage beyond Python import/call-site breaks to route, event, and SQL boundary cases.

**Architecture:** Add benchmark fixtures first, then extend existing deterministic scanners only where the fixture exposes missing evidence. Keep this phase Python-focused and avoid TypeScript/JavaScript work.

**Tech Stack:** Python 3.11, pytest, stdlib AST/regex, existing benchmark runner.

---

### Task 1: FastAPI Route Parameter Break Case

**Files:**
- Create: `examples/regression_cases/python_route_param_break/src/api/orders.py`
- Create: `examples/regression_cases/python_route_param_break/src/client/dashboard.py`
- Create: `examples/regression_cases/python_route_param_break/expected.json`
- Modify: `tests/test_benchmark.py`
- Modify if needed: `cross_review/contracts/contract_graph.py`

- [ ] **Step 1: Add fixture**

Create a provider route file:

```python
class Router:
    def get(self, path):
        def wrap(fn):
            return fn
        return wrap

router = Router()

@router.get("/orders/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}
```

Create a downstream client file:

```python
def load_order(order_id):
    return http_get(f"/orders/{order_id}")
```

Expected JSON should include an edge from `api` to `client`, changed file `src/api/orders.py`, and expected route contract id:

```json
"python:route:src/api/orders.py:/orders/{order_id}"
```

- [ ] **Step 2: Add failing benchmark assertion**

Update `tests/test_benchmark.py` to expect 2 total cases and 2 passed cases.

- [ ] **Step 3: Run test and confirm failure**

```powershell
python -m pytest tests/test_benchmark.py -q
```

Expected: route case fails until route contracts and route consumer evidence are implemented or benchmark expectations are adjusted to existing capabilities.

- [ ] **Step 4: Implement minimal route contract evidence**

Extend contract graph extraction so route decorators become `ContractSurfaceModel(kind="route")`, changed route files become `ChangedContractModel`, and downstream files containing the route literal or compatible path prefix create call-site evidence.

- [ ] **Step 5: Verify**

```powershell
python -m pytest tests/test_contract_graph.py tests/test_benchmark.py -q
```

Expected: route benchmark passes.

### Task 2: Event Payload Rename Case

**Files:**
- Create: `examples/regression_cases/python_event_payload_rename/src/order/events.py`
- Create: `examples/regression_cases/python_event_payload_rename/src/notification/listener.py`
- Create: `examples/regression_cases/python_event_payload_rename/expected.json`
- Modify if needed: `cross_review/contracts/contract_graph.py`
- Modify: `tests/test_benchmark.py`

- [ ] **Step 1: Add fixture**

Provider:

```python
def publish_paid(bus, order):
    bus.publish("OrderPaid", {"order_id": order.id, "total_cents": order.total})
```

Consumer:

```python
def register(bus):
    bus.subscribe("OrderPaid", handle_order_paid)

def handle_order_paid(event):
    return event["amount_cents"]
```

Expected JSON should include edge `order -> notification`, event contract id, and a call-site/listener evidence prefix.

- [ ] **Step 2: Add failing benchmark assertion**

Expect 3 total cases and 3 passed cases after implementation.

- [ ] **Step 3: Implement minimal event contract evidence**

Use existing event publisher/listener scanner output where possible. Add contract graph surfaces for emitted events and call-site evidence for listened events.

- [ ] **Step 4: Verify**

```powershell
python -m pytest tests/test_contract_graph.py tests/test_benchmark.py -q
```

Expected: event benchmark passes.

### Task 3: SQL NOT NULL Migration Case

**Files:**
- Create: `examples/regression_cases/python_sql_not_null_migration/src/db/migrations/2026_add_plan.sql`
- Create: `examples/regression_cases/python_sql_not_null_migration/src/billing/subscription.py`
- Create: `examples/regression_cases/python_sql_not_null_migration/expected.json`
- Modify if needed: `cross_review/contracts/contract_graph.py`
- Modify: `tests/test_benchmark.py`

- [ ] **Step 1: Add fixture**

Migration:

```sql
ALTER TABLE subscriptions ADD COLUMN plan_tier TEXT NOT NULL;
```

Writer:

```python
def create_subscription(db, user_id):
    db.execute("INSERT INTO subscriptions (user_id) VALUES (?)", [user_id])
```

Expected JSON should include edge `db -> billing`, a DB contract id for `subscriptions.plan_tier`, and a writer evidence prefix.

- [ ] **Step 2: Add failing benchmark assertion**

Expect 4 total cases and 4 passed cases after implementation.

- [ ] **Step 3: Implement minimal SQL contract evidence**

Detect `ALTER TABLE ... ADD COLUMN ... NOT NULL` in changed `.sql` files and Python SQL strings that write to the same table.

- [ ] **Step 4: Verify**

```powershell
python -m pytest tests/test_contract_graph.py tests/test_benchmark.py -q
```

Expected: SQL benchmark passes.

### Task 4: Full Verification

Run:

```powershell
python -m pytest tests/
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"
python -m cross_review.cli benchmark --cases examples/regression_cases
```

Expected:

- all tests pass
- skill is valid
- benchmark reports `4/4 cases passed`

---

## Non-Goals

- Do not add TypeScript/JavaScript support in this phase.
- Do not implement full OpenAPI, SQL parser, or event schema inference.
- Do not make report evidence strict yet.

## Self-Review

- Spec coverage: Expands Python regression coverage for route, event, and SQL boundaries.
- Placeholder scan: No placeholders remain.
- Type consistency: Uses existing benchmark runner and contract graph terms.
