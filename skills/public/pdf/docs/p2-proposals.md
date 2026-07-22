# P2 Optimization Proposals — PDF Skill

## P2-1: Incremental Execution (DAG Input Hash Skipping)

### Approach

Add a Make-style input fingerprinting system to the pipeline runner. Before a
node starts, compute a hash of its "inputs". After successful completion, store
that hash. On subsequent `pipeline_tick` rounds, if a completed node's input
hash still matches the stored hash, the engine skips the node (marks it done
without re-execution). If the hash changed, the node and all downstream nodes
are reset to `pending`.

**What constitutes "input" per node type:**

| Node type | Input hash = |
|-----------|-------------|
| `engine_exec` | `sha256(command + sorted(args) + content_of_all_dep_artifacts)` |
| `llm_spawn` | `sha256(context_bundle_output + content_of_all_dep_artifacts + spawn_role)` |
| `llm_converge` / `llm_merge` | `sha256(content_of_all_dep_artifacts + target_artifact_pattern)` |
| `repair_gate` | `sha256(content_of_review_artifact + current_repair_decisions)` |
| `manual_checkpoint` | Never skip (always requires human judgment) |

**Storage:** A new key `state.input_hashes` mapping `{stage.ref: hash_string}`
for each completed node. This is already pre-figured by the existing (unused)
`mark_fingerprint()` and `_quick_hash()` in `dag.py` -- repurpose those.

**Integration point in runner.py:**
- In `node_complete()` (runner.py line 106): after the node is marked done,
  compute the input hash and store it in `state.input_hashes[stage.ref]`.
- In `_compute_ready_nodes()` (dag.py line 140): before returning a node as
  "ready", check if it is already `done` AND its stored input hash matches
  the current computed hash. If matched, skip it (mark as done directly).
- On rollback (`hsm event` with `reset`): must clear `input_hashes` entries
  for all reset nodes so they don't keep stale fingerprints.

**Cache invalidation:** Any node that re-executes (due to retry or rollback)
automatically overwrites its stored hash on completion. No manual cleanup
needed.

### Files Affected

- **`tools/engine/dag.py`** (lines 317-324): Repurpose `mark_fingerprint()`
  to compute real input hashes (read dep artifact file content). Add
  `_compute_node_input_hash(state, stage, ref, blueprint)` and
  `_check_can_skip(state, stage, ref, blueprint)`.
- **`tools/engine/runner.py`** (lines 106-140): In `node_complete()`, call
  `_dag.mark_fingerprint()` with the computed hash after successful marking.
  In `pipeline_tick()` / `_compute_ready_nodes()`, call `_check_can_skip()`
  to skip unchanged nodes.
- **`tools/engine/rollback.py`**: When `rollback_to_stage()` resets nodes,
  also clear their `state.input_hashes[stage.ref]` entries.
- **`docs/pdf_state.schema.json`**: Add optional `input_hashes` field
  (type: object, patternProperties: `{stage.ref: {type: string}}`).

### Complexity

**High** -- the core logic is small, but there are several edge cases:

1. **Diamond dependencies:** If two upstream nodes both change, the downstream
   node sees a different merged artifact hash and re-executes correctly, but
   the order of re-execution matters.
2. **Rollback consistency:** After a rollback resets upstream nodes, the
   downstream node's stored hash is now stale. Must clear it.
3. **First-run vs second-run:** The hash comparison only helps on the second
   execution of the same pipeline (or a resumed session). On the first run,
   every node executes once by definition.

### Risk

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Hash mismatch due to non-deterministic artifacts (timestamps in output) | Low | Ignore `mtime`/timestamp fields; hash only content via `stable_hash()` |
| Stale hashes survive rollback | Low | Clear `input_hashes` for all reset nodes in rollback path |
| Hash computation reads large files (e.g. 50K context bundle) every tick | Medium | Cache file hash results within a single `pipeline_tick` call; use file mtime as quick pre-check |

### Recommendation

**Do it** -- but only after the P0 sharding work is done, because sharding changes
the artifact structure. The incremental benefit:
- On re-resume after a crash: skip all previously completed nodes that
  haven't changed inputs. Saves minutes, especially on large plans.
- During rollback-and-retry (e.g. `design_flaw` resets P1-P3): P0 nodes
  retain their hashes and can be skipped if they didn't change. Saves
  ~30s per retry loop.
- Multi-session workflows: re-run only the changed branch.

The benefit scales with pipeline length and rollback frequency. For short
(5-minute) pipelines the overhead may exceed savings, but PDF pipelines are
typically 20+ minutes, making this worthwhile.

---

## P2-2: Model Tiering (task_type-aware Model Selection)

### Approach

Introduce a `task_type`-sensitive model selection path in
`cmd_config_get_model()`. Currently, all non-upgraded roles default to `sonnet`
(see `spawn-config.yaml` `model_tier.default`). The proposal adds an
intermediate tier between `haiku` and `sonnet`, and tightens the `sonnet` →
`opus` escalation trigger:

**Tier mapping (new):**

| task_type | Default model | Opus trigger |
|-----------|--------------|--------------|
| `analysis` / `research` | `haiku` | Only when `fact_check: true` in context |
| `design` / `architecture` | `sonnet` (unchanged) | M >= 3 for correctness dimension |
| `implementation` / `doer` | `sonnet` (unchanged) | M >= 5 (current behavior) |
| `review` / `check` | `haiku` (was `sonnet`) | M >= 3 for correctness+security (current) |

**The big savings:** Currently every `llm_spawn` node spawns sub-agents at
`sonnet`. Analysis sub-agents don't need `sonnet` for pattern recognition and
summarization -- `haiku` is sufficient. Only fact-checking and critical
reasoning (where a hallucinated claim propagates into the final output) need
`sonnet`/`opus`.

**Implementation mechanism:**

1. Add a `task_type_map` section to `spawn-config.yaml`:
   ```yaml
   model_tier:
     task_type_overrides:
       analysis: haiku
       research:  haiku
       design:    sonnet
       review:    sonnet
       fact_check: opus
   ```

2. Modify `cmd_config_get_model()` in `pdf_engine_channel.py`
   (around line 67-94) to accept an optional `--task-type` parameter.
   The resolution order becomes:
   ```
   CLI --model-tier > CLI --task-type > domain model_tier_overrides
   > domain_model_defaults > spawn-config task_type_overrides
   > model_tier.default
   ```

3. In the blueprint topology, add a `task_type` field to relevant nodes
   (e.g., `P0_analysis` gets `task_type: analysis` instead of relying
   on the fixed spawn-config default).

4. Update `SKILL.md` section "通道选择" (around line 290-330) to include
   `task_type` in the `channel select` JSON payload, so the engine stores
   it in state for downstream `config get-model` calls.

5. The escalation logic in `model_upgrade` (spawn-config.yaml lines 58-67)
   stays but gains a `fact_check_only` flag:
   ```yaml
   opus_escalation:
     fact_check_only: true  # only opus when M>=2 AND task_type=fact_check
   ```

### Files Affected

- **`docs/spawn-config.yaml`**: Add `model_tier.task_type_overrides` section.
  Add `opus_escalation.fact_check_only` field. Optionally demote the
  `model_tier.default` from sonnet to haiku for `check.p1` and `plan.analysis`.
- **`tools/pdf_engine_channel.py`** (`_get_domain_model_override()` line 40,
  `cmd_config_get_model()` around line 67-94): Add `--task-type` parameter.
  Insert `task_type_overrides` lookup before falling through to defaults.
- **`docs/topology/blueprint.full.yaml`**: Add `task_type: analysis` to
  `P0_analysis` node. Add `task_type: fact_check` to review nodes that
  must do factual verification. (Only for `full` channel; lite/standard
  can stay flatter.)
- **`docs/topology/blueprint.std.yaml`** and **`blueprint.lite.yaml`**:
  Same `task_type` annotations if they exist.
- **`SKILL.md`** (section "通道选择", around line 290-330): Add `task_type`
  to the `channel select` JSON. Add note that `config get-model` now
  respects `--task-type`.

### Complexity

**Low** -- it's a config change plus <50 lines of engine code. No new
state structures, no new validation logic.

### Risk

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| haiku analysis misses nuance, then downstream design wastes a round fixing it | Medium | Put `haiku` only on analysis sub-agents (P0_analysis). Design/doer agents stay `sonnet`. If a cycle's analysis turns out wrong, the next retry round escalates. |
| `fact_check` flag not set, review stays `haiku`, bugs slip through | Low | The default for review is still `sonnet` unless `task_type` explicitly says `analysis`. Only analysis/research reviews are affected. |
| Static overrides are fragile (no runtime adaptation) | Low | This is fine for P2. If runtime adaptation is needed later, the Cycle-History learning system already provides a framework. |

### Recommendation

**Do it. First.** This is low complexity, zero architectural risk, and
yields immediate cost savings. The 30-50% reduction estimate is reasonable:

- Analysis sub-agents (P0_analysis, MADE explorers): ~25% of total LLM cost
  in a full-channel run. Dropping from sonnet to haiku saves ~60% on those
  calls → ~15% total savings.
- Check sub-agents (C1_check): ~20% of total cost. Dropping from sonnet to
  haiku (with opus only for fact-check flagged items) saves ~50% on those
  calls → ~10% total savings.
- Total: ~25% minimum, up to ~50% if the project heavily uses analysis-heavy
  flows.

Implementation time: ~1 hour for config changes + ~1 hour for engine
modification + testing.
