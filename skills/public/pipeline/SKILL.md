---
name: pipeline
description: Multi-stage development pipeline skill. Use this when the user asks for a "pipeline", "workflow", "multi-stage", "dev-flow", or wants to "design and implement" a feature with structured stages. Provides stage orchestration, artifact passing, and human approval gates.
---

# Pipeline Skill

## Overview

Pipeline workflows orchestrate multi-stage development flows. Each stage delegates a focused task to a subagent via `task_tool`, and the output (artifact) flows into the next stage as context. Human approval gates ensure quality before progression.

**When to use this skill:**
- User asks for a "pipeline", "workflow", "multi-stage", or "dev-flow"
- User wants to "design and implement" a feature with clear phases
- User describes a multi-step process (survey -> design -> code)
- User wants structured handoffs between stages

## Pipeline Templates

### 1. `dev-flow` — 3 stages: design -> code -> review
| Stage | task_tool prompt | subagent_type |
|-------|-----------------|---------------|
| 1. Design | Produce a design document covering architecture, key decisions, and component breakdown for the feature described below. | general-purpose |
| 2. Code | Implement the feature based on the design document below. Write production-ready code with tests. | general-purpose |
| 3. Review | Review the implementation below for correctness, style, edge cases, and test coverage. List issues and suggestions. | general-purpose |

### 2. `dev-flow-quick` — 2 stages: design -> code (no review)
Same as dev-flow but omits stage 3 (review). Use for rapid prototyping or small changes.

### 3. `survey-design-code` — 3 stages: survey -> design -> code
| Stage | task_tool prompt | subagent_type |
|-------|-----------------|---------------|
| 1. Survey | Research the topic below thoroughly. Summarize current state, key approaches, trade-offs, and recommendations. | general-purpose |
| 2. Design | Based on the survey below, produce a design document covering architecture, key decisions, and component breakdown. | general-purpose |
| 3. Code | Implement the feature based on the design document below. Write production-ready code with tests. | general-purpose |

## How to Run a Pipeline

### Step-by-step

1. **Choose template** — Match user request to dev-flow, dev-flow-quick, or survey-design-code
2. **Explain stages** — Tell the user which stages will run and what each produces
3. **Set a /goal** — Use `/goal set "<pipeline objective>"` to track the overall objective
4. **Run each stage sequentially:**
   - Call `task_tool` with the appropriate prompt and `subagent_type="general-purpose"`
   - Include the previous stage's result in the prompt context (e.g., "Below is the design document produced in the previous stage: ...")
   - Present the result to the user
   - Ask: "Continue to **{next stage}**? [Y/n]"
5. **On completion** — Present the final artifact summary, then use `/goal clear` to mark the goal satisfied

### Human Approval Gate Rules

- After each stage, present the output and ask the user for approval before proceeding
- If user rejects: revise the stage output based on feedback and re-run that stage
- **Max 3 revisions per stage** — after 3 rejections, offer the user alternative approaches
- If user approves: proceed to the next stage with the approved artifact

### Artifact Management

- Store each stage's output in thread memory by noting the result as a key document
- Pass artifacts forward by embedding them in the next stage's `task_tool` prompt
- Final summary references all stages: design doc, implementation notes, review findings

### Error Handling

| Error | Action |
|-------|--------|
| task_tool returns failed status | Present the error to the user, offer to retry or abort |
| Subagent times out | Suggest splitting the stage into smaller tasks |
| User rejects 3 times | Offer alternative approaches or ask user to refine requirements |
| Pipeline interrupted mid-flow | Resume from last completed stage when user returns |

## Example Flow

```
User: "Create a calculator app using dev-flow"
Agent: "Let's use `dev-flow` (design -> code -> review). Setting goal..."
/goal set "Create a calculator app with basic arithmetic operations"

--- Stage 1: Design ---
task_tool(description="Design calculator", prompt="Design...", subagent_type="general-purpose")
-> [design doc]
"Continue to Code stage? [Y/n]"
User: "Y"

--- Stage 2: Code (receives design doc) ---
task_tool(description="Implement calculator", prompt="Implement... Design doc:\n{design}", ...)
-> [implementation]
"Continue to Review stage? [Y/n]"
User: "Y"

--- Stage 3: Review (receives implementation) ---
task_tool(description="Review calculator", prompt="Review... Implementation:\n{code}", ...)
-> [review findings]
/goal clear
"Pipeline complete. Summary: ..."
```
