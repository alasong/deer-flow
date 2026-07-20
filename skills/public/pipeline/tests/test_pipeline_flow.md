# Pipeline Skill Test Scenarios

## Test 1: dev-flow pipeline completes all 3 stages

**Setup:** User requests: "Create a weather CLI app using dev-flow"

**Expected behavior:**
1. Agent selects the `dev-flow` template
2. Agent explains the 3 stages to the user (design -> code -> review)
3. Agent sets a `/goal` with the overall pipeline objective
4. **Stage 1 (Design):** Agent calls `task_tool` with `subagent_type="general-purpose"` and a design prompt. Returns a design document result.
5. Agent presents the design artifact to the user and asks: "Continue to Code stage? [Y/n]"
6. User approves. **Stage 2 (Code):** Agent calls `task_tool` with the same `subagent_type` and embeds the design document in the prompt. Returns implementation.
7. Agent presents the code artifact and asks: "Continue to Review stage? [Y/n]"
8. User approves. **Stage 3 (Review):** Agent calls `task_tool` embedding the code artifact. Returns review findings.
9. Agent clears the `/goal` and presents a final summary.

**Pass criteria:** All 3 stages execute in sequence. Each stage receives the previous stage's artifact as context. `/goal` is set before stage 1 and cleared after stage 3.

---

## Test 2: dev-flow-quick skips review stage

**Setup:** User requests: "Build a simple TODO API using dev-flow-quick"

**Expected behavior:**
1. Agent selects the `dev-flow-quick` template
2. Agent explains the 2 stages (design -> code, no review)
3. **Stage 1 (Design):** Runs normally, produces design artifact
4. After user approval, **Stage 2 (Code):** Runs with design document context
5. After code completion, pipeline ends **without** asking about a review stage
6. `/goal` is cleared. Final summary includes design + code artifacts only.

**Pass criteria:** Exactly 2 task_tool calls. No review stage is prompted or executed. Pipeline completes cleanly without review.

---

## Test 3: Pipeline with human approval gate

**Setup:** User requests: "Create a CLI tool using dev-flow"

**Expected behavior:**
1. After Stage 1 (Design), the agent presents the output and asks: "Continue to Code stage? [Y/n]"
2. The agent **waits** for user input and does not proceed automatically
3. User types "Y" — agent proceeds to Stage 2
4. After Stage 2 (Code), agent asks: "Continue to Review stage? [Y/n]"
5. User types "N" — agent does **not** proceed. Agent asks how the user would like to proceed (e.g., revise, stop)
6. If user asks to stop, agent clears `/goal` and summarizes completed stages

**Pass criteria:** Agent pauses at each stage boundary. Agent does not auto-advance without user approval. Agent respects both approval and rejection. Goal is cleared on pipeline termination.

---

## Test 4: Pipeline stage revision on rejection

**Setup:** User requests: "Build a login system using dev-flow"

**Expected behavior:**
1. Stage 1 (Design) completes. User rejects: "The design needs more detail about token refresh"
2. Agent revises by calling `task_tool` again with the original design prompt plus extra instruction: "Add a section on token refresh: ..."
3. Stage 1 runs a second time. New design is presented.
4. User approves. Agent proceeds to Stage 2 (Code).
5. Stage 2 (Code) completes. User rejects: "The implementation should use async/await"
6. Agent revises the code: calls `task_tool` with the code prompt plus "Make all handlers async/await"
7. Code runs a second time. User approves. Pipeline continues.

**Pass criteria:** Agent performs up to 3 revisions per stage. Revisions re-run only the rejected stage (not the whole pipeline). Context from previous stages is preserved. If user rejects 3 times on the same stage, agent offers alternative approaches and does not retry a 4th time.
