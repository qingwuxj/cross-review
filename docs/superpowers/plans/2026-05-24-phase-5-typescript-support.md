# Phase 5 TypeScript Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first TypeScript/JavaScript evidence support for ES import/export and downstream call-sites.

**Architecture:** Extend contract extraction with a narrow text/regex-based TypeScript scanner. Keep it deterministic and fixture-driven. Do not introduce heavy parser dependencies until benchmark evidence shows regex is insufficient.

**Tech Stack:** Python 3.11, pytest, regex, existing benchmark runner.

---

### Task 1: TypeScript Export Rename Fixture

**Files:**
- Create: `examples/regression_cases/ts_export_rename/src/billing/client.ts`
- Create: `examples/regression_cases/ts_export_rename/src/admin/panel.ts`
- Create: `examples/regression_cases/ts_export_rename/expected.json`
- Modify: `tests/test_benchmark.py`

- [ ] **Step 1: Add fixture**

Provider:

```typescript
export function chargeUser(userId: string, amount: number): boolean {
  return true;
}
```

Consumer:

```typescript
import { chargeUser } from "../billing/client";

export function triggerBillingOverride(userId: string) {
  return chargeUser(userId, 100);
}
```

Expected contract id:

```text
typescript:function:src/billing/client.ts:chargeUser
```

- [ ] **Step 2: Add failing benchmark assertion**

Update benchmark expectation count to include the TypeScript case.

- [ ] **Step 3: Implement TypeScript scanner**

Add TS support in `cross_review/contracts/contract_graph.py` or split into `cross_review/contracts/typescript.py`.

Support only:

- `export function name(...)`
- `import { name } from "..."`
- `name(...)` call-sites

- [ ] **Step 4: Verify**

```powershell
python -m pytest tests/test_benchmark.py tests/test_contract_graph.py -q
```

Expected: TypeScript export case passes.

### Task 2: Express Route Fixture

**Files:**
- Create: `examples/regression_cases/ts_express_route_break/src/api/orders.ts`
- Create: `examples/regression_cases/ts_express_route_break/src/client/dashboard.ts`
- Create: `examples/regression_cases/ts_express_route_break/expected.json`

- [ ] **Step 1: Add fixture**

Provider:

```typescript
router.get("/orders/:orderId", (req, res) => {
  res.json({ id: req.params.orderId });
});
```

Consumer:

```typescript
export async function loadOrder(orderId: string) {
  return fetch(`/orders/${orderId}`);
}
```

- [ ] **Step 2: Implement minimal Express route detection**

Detect:

- `router.get("...")`
- `app.get("...")`
- downstream `fetch("...")` and template literal path prefixes

- [ ] **Step 3: Verify**

```powershell
python -m pytest tests/test_benchmark.py -q
```

Expected: Express route case passes.

### Task 3: Full Verification

Run:

```powershell
python -m pytest tests/
python C:\Users\86193\.codex\skills\.system\skill-creator\scripts\quick_validate.py "C:\Users\86193\Desktop\cross review\cross-review-skill"
python -m cross_review.cli benchmark --cases examples/regression_cases
```

Expected:

- all tests pass
- skill is valid
- benchmark includes Python and TypeScript cases

---

## Non-Goals

- Do not implement full TypeScript AST.
- Do not support class methods, generics, overloads, or path alias resolution beyond relative imports.
- Do not add package manager or build step requirements.

## Self-Review

- Spec coverage: Adds the first JS/TS evidence layer requested by the design document.
- Placeholder scan: No placeholders remain.
- Type consistency: Uses existing contract graph and benchmark terminology.
