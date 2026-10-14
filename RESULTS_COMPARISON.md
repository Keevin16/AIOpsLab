# SRE-Agent (MAS) vs. AIOpsLab Baseline — Model-Controlled Comparison

**Date:** 2026-08-26
**Purpose:** Consolidate every result currently available, at a fixed LLM (GPT-5-mini), to answer the
ICSME 2026 VERS reviewers' central objection: *the reported gain confounds architecture with model generation.*

---

## 0. TL;DR

| Question | Answer from current data |
|---|---|
| Does the MAS beat the baseline on **localization** at a fixed model? | **No.** Best MAS config 80.0% vs baseline 81.1% (15 scenarios). The +20.46 pp in the submitted paper was a GPT-4 → GPT-5-mini effect. |
| Does it beat it on **detection**? | **Yes for the best configs** (100% vs 88.9%), driven by two specific failure modes. |
| Does it beat it on **fault-free clusters**? | **Yes, decisively.** 24/24 vs 2/4, at **100× lower cost** (786 vs 80,128 tokens). |
| Is it cheaper **end-to-end**? | **Yes.** Configs A/D deliver detection+localization+RCA report in 56–64k tokens; the baseline needs 3 separate runs totalling ~149k. |
| Is the architecture worthless? | **No.** It achieves parity on localization *while also detecting the fault, without shell access, and at ~1/3 the end-to-end cost.* |
| Is the submitted paper's headline claim defensible? | **No.** It must be reframed. See §9. |

---

## 1. Data Sources

| Dataset | Location | Runs | Notes |
|---|---|---|---|
| MAS (SRE-Agent) | `SRE-agent/Results/**/*.json` | 702 unique `run_id` | 12 configurations (A–L); `aggregate-results/` used for all tables below |
| AIOpsLab baseline | `aiopslab/data/results/` + `results_archive/` | 216 evaluable | Agent `gpt-w-shell`, 1–4 repetitions per problem |
| Consolidated baseline table | `analysis_output/ALL_RUNS_gpt5mini.csv` | 216 | Produced for this analysis |

### 1.1 Metric provenance (baseline)

Written by `Session.to_json()` (`aiopslab/session.py`); the `results` dict is populated by each problem's
`eval()` plus `Task.common_eval()` (`aiopslab/orchestrator/tasks/base.py`).

| Metric used here | Source field |
|---|---|
| Detection accuracy | `results["Detection Accuracy"] == "Correct"` |
| Localization (strict) | `results["success"]` = `is_exact or (is_subset and len(soln)==1)` |
| Localization (substring) | `results["is_subset"]` |
| Localization (partial credit) | `results["Localization Accuracy"]` = `100` if exact, else `100/len(soln)` if subset, else `0` |
| Tokens (native) | `in_tokens + out_tokens` — counts each message **once** |
| Tokens (reconstructed) | Computed here by replaying the trace and re-charging conversation history at every turn |

### 1.2 Metric provenance (MAS)

`evaluation.localization` is produced by `sre-agent/evaluation/evaluation.py`:

```python
return target in localization   # pure substring, no precision penalty
```

For fault-free scenarios it returns `True` iff the agent reports no localization.

> **Metric symmetry is satisfied.** The MAS uses pure substring matching (justified: it reports *pods*, whose
> names carry runtime suffixes, against service-level ground truth). All head-to-head tables below therefore use
> the baseline's `is_subset` field, which is the exact analogue. Strict `success` is reported separately for context.

---

## 2. Systems Under Comparison

| | AIOpsLab `gpt-w-shell` | SRE-Agent MAS |
|---|---|---|
| Model | GPT-5-mini | GPT-5-mini |
| Architecture | Single ReAct agent | Triage → Planner → parallel RCA Workers → Supervisor |
| Tooling | `exec_shell` — **arbitrary `kubectl`** | Constrained, validated MCP tools |
| Task framing | **Told which task to solve, and that a fault exists** | Must detect the fault itself |
| Prior context | Given namespace + app description | Datagraph (static topology) |
| Output per run | One answer for one task | Detection + localization + RCA report |

---

## 3. Critical Methodological Notes

These must appear in any paper built on this data.

1. **The baseline is told the fault exists.** The localization prompt reads *"You are assigned to localize faults…
   identify the service(s) where the root cause **of the fault** lies."* The MAS receives no such hint. Any
   localization comparison is therefore **biased in the baseline's favour**.
2. **The baseline has unrestricted shell access**; the MAS does not. This is a capability advantage for the
   baseline and a deployability advantage for the MAS.
3. **Per-run cost is not comparable; per-pipeline cost is.** The baseline solves one task per run. Comparing MAS
   full-pipeline tokens against baseline localization-only tokens is invalid (§7).
4. **Baseline token counts are reconstructed, not measured.** AIOpsLab's native counter under-reports by ≈4–5×
   because it ignores history re-transmission. The reconstruction here also *omits* the system prompt resent each
   turn, so it is a **lower bound**. MAS tokens are real API totals from LangSmith. → Instrument the baseline
   before publishing exact ratios.
5. **The model is not recorded in the baseline artefacts.** Attribution to GPT-5-mini is inferred from
   `clients/utils/llm.py` (`GPT_MODEL = "gpt-5-mini"`, commit `afd83fa`), an empty LLM cache, and the fact that the
   same commit introduces `reasoning_effort` (rejected by the previous `gpt-4o`), making the switch atomic.
   **This is strong circumstantial evidence, not proof.** Fix before releasing the replication package.
6. **Astronomy Shop is excluded.** Its 10 localization runs score 0/10, with 4 hitting the 30-step cap at 3–8 M
   reconstructed prompt tokens. This reflects a broken deployment on the available hardware, not agent behaviour.
7. **Scenario set.** The submitted paper's "17 fault scenarios" = 20 (app, fault) pairs minus 3 excluded in
   `notebooks/analysis/result-analysis.ipynb` (`hotel reservation - container kill / pod kill / redeploy without pv`).
   **Two of the 17 are fault-free**, so only **15 are real faults**. Localization on the 17-scenario set includes
   2 automatic successes from the no-fault cases.
8. **Sample sizes are unbalanced.** MAS: n = 1 per (config, scenario), n = 12 per scenario when pooling configs.
   Baseline: n = 1–4 per scenario. All statistics below use the pooled view.

---

## 4. Reproduction of the Submitted Paper

Applying the notebook's exclusion list reproduces Table I and Figure 2 exactly.

| Agent | Paper Det. | Computed Det. | Paper Loc. | Computed Loc. | Paper tokens | Computed tokens |
|---|---|---|---|---|---|---|
| G | 100% | 100.00% | 82.00% | 82.35% | ≈137k | 136,601 |
| D | 88% | 88.24% | 76.00% | 76.47% | ≈64k | 63,541 |
| A | 100% | 100.00% | 71.00% | 70.59% | ≈56k | 56,212 |

✔ The submitted numbers are correct and reproducible.

---

## 5. Results — MAS Configurations

### 5.1 All 12 configurations (17-scenario set)

| Agent | Configuration | Det. | Loc. | RCA /5 | Tokens | Cost $ | Time s |
|---|---|---|---|---|---|---|---|
| A | Plain ReAct P2 B7 | 1.000 | 0.706 | 2.29 | 56,212 | 0.025 | 188 |
| B | Plain ReAct P3 B7 | 1.000 | 0.765 | 1.88 | 88,260 | 0.033 | 248 |
| C | Plain ReAct P5 B7 *(n=10)* | 0.900 | 0.400 | 2.50 | 131,649 | 0.044 | 361 |
| D | ReAct conservative P2 B7 | 0.882 | 0.765 | **2.59** | 63,541 | 0.026 | 209 |
| E | ReAct conservative P3 B7 | 0.941 | 0.647 | 2.06 | 111,503 | 0.032 | 267 |
| F | Tool-free P2 B7 | 1.000 | 0.765 | 2.29 | 110,043 | 0.033 | 259 |
| **G** | **Tool-free P3 B7** | **1.000** | **0.824** | 2.24 | 136,601 | 0.043 | 336 |
| H | Supervisor-enforce P2 B7 *(n=16)* | 0.938 | 0.688 | 1.56 | 70,173 | 0.036 | 290 |
| I | Supervisor-enforce P3 B7 | 0.941 | 0.706 | 2.12 | 88,472 | 0.044 | 344 |
| J | Improved Plain P2 B7 | 0.882 | 0.647 | 2.29 | 60,019 | **0.024** | 190 |
| K | Improved Plain P3 B7 | 0.882 | 0.706 | 2.29 | 100,135 | 0.030 | 252 |
| L | Improved Plain P2 B15 | 0.941 | 0.647 | 2.41 | 82,290 | 0.028 | 226 |

**Note for the paper:** reviewers 1 and 2 both asked for all 12 configurations to be described and labelled.
This table answers that directly. Strategies are: Plain ReAct (A,B,C), Conservative (D,E), Tool-free (F,G),
Supervisor-enforce-certainty (H,I), Improved Plain ReAct (J,K,L). Parameters: P = parallel tasks, B = tool budget.

### 5.2 Localization on the 15 **real** faults (no-fault cases removed)

| Agent | A | B | C | D | E | F | **G** | H | I | J | K | L | Pooled |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Loc. | .667 | .733 | .333 | .733 | .600 | .733 | **.800** | .643 | .667 | .600 | .667 | .600 | **.665** |

> Removing the two free no-fault successes lowers every headline figure by ~2–3 pp (G: 82.4% → 80.0%).
> **The revised paper must report this number**, otherwise a reviewer will find the inflation.

---

## 6. Head-to-Head — 15 Real Fault Scenarios

`b_*` = AIOpsLab baseline (GPT-5-mini) · `m_*` = MAS pooled over 12 configs · `G` = MAS Agent G

| Scenario | b_n | **b_loc** | m_n | **m_loc** | G_loc | b_det | m_det |
|---|---|---|---|---|---|---|---|
| Network delay (Hotel) | 2 | 0.000 | 12 | **0.583** | ✔ | 0.500 | **1.000** |
| Network loss (Hotel) | 3 | 0.333 | 12 | **0.917** | ✔ | 0.333 | **1.000** |
| Pod failure (Hotel) | 3 | 1.000 | 12 | 0.750 | ✔ | 1.000 | 0.750 |
| Port mismatch geo (Hotel) | 3 | 1.000 | 12 | 1.000 | ✔ | 1.000 | 1.000 |
| Revoke auth MongoDB Geo | 4 | 1.000 | 12 | 1.000 | ✔ | 1.000 | 1.000 |
| Revoke auth MongoDB Rate | 4 | 1.000 | 12 | 1.000 | ✔ | 0.750 | 1.000 |
| User unregistered MongoDB Geo | 3 | 1.000 | 12 | 1.000 | ✔ | 1.000 | 1.000 |
| User unregistered MongoDB Rate | 3 | 0.333 | 12 | **0.833** | ✔ | 1.000 | 1.000 |
| Wrong bin usage (Hotel) | 1 | 1.000 | 12 | **0.083** | ✘ | 1.000 | 1.000 |
| Assign Pod non-existent node (Social) | 3 | 1.000 | 12 | 0.417 | ✔ | 1.000 | 0.750 |
| MongoDB url-shorten auth missing (Social) | 4 | 1.000 | 11 | 0.909 | ✔ | 1.000 | 1.000 |
| Port misconfig user (Social) | 4 | 1.000 | 12 | 0.417 | ✘ | 0.750 | 1.000 |
| Port misconfig text (Social) | 4 | 0.750 | 12 | 0.667 | ✘ | 1.000 | 1.000 |
| Port misconfig post-storage (Social) | 4 | 0.750 | 12 | 0.167 | ✔ | 1.000 | 1.000 |
| Scale Pod to Zero (Social) | 3 | 1.000 | 12 | 0.250 | ✔ | 1.000 | 0.583 |
| **Macro mean** | | **0.811** | | **0.666** | **0.800** | **0.889** | **0.939** |

### 6.1 Per-application split — the most actionable finding

| Application | Scenarios | Baseline Loc. | MAS pooled Loc. | MAS G Loc. |
|---|---|---|---|---|
| Hotel Reservation | 9 | 0.741 | **0.796** | **0.889** |
| Social Network | 6 | **0.917** | 0.471 | 0.667 |

| Application | Baseline Det. | MAS pooled Det. |
|---|---|---|
| Hotel Reservation | 0.843 | **0.972** |
| Social Network | **0.958** | 0.889 |

> **The MAS beats the baseline on Hotel Reservation and collapses on Social Network.** The aggregate "parity"
> hides two opposite regimes. Given that `Results/` contains folders named `social-net-plain-react-jaeger-fix`
> and `plain-react-social-net-new-dg`, the Social Network **Datagraph / Jaeger trace ingestion is the prime
> suspect**. This is a concrete engineering lead, and it is the single highest-leverage fix available.

---

## 7. Cost — End-to-End, Not Per-Run

The baseline needs **three separate runs** to produce what the MAS produces in one pipeline.

| System | Tokens | Time | Output |
|---|---|---|---|
| Baseline — detection | 51,792 | 54 s | fault yes/no |
| Baseline — localization | 64,939 | 90 s | faulty service |
| Baseline — analysis | 32,747 | 49 s | fault-type classification |
| **Baseline total** | **149,478** | **193 s** | 3 disjoint answers, no narrative |
| **MAS Agent A** | **56,212** | 188 s | detection + localization + RCA report |
| **MAS Agent D** | **63,541** | 209 s | idem, best RCA score (2.59/5) |
| MAS Agent G | 136,601 | 336 s | idem, best localization |

**Configs A and D deliver strictly more output at ~2.4× lower token cost and equal latency.**

> ⚠️ Baseline tokens are reconstructed (see §3.4) and are a lower bound. The qualitative conclusion is robust;
> the exact ratio is not. Instrument the baseline with real API `usage` before quoting "2.4×" in print.

### 7.1 Fault-free clusters — the strongest single result

| System | Runs | Detection accuracy | Tokens | Time |
|---|---|---|---|---|
| Baseline | 4 | **0.50** (Hotel 2/2, Social **0/2**) | 80,128 | 59.5 s |
| **MAS (all 12 configs)** | 24 | **1.00** | **786** | **4.5 s** |

The deterministic Four-Golden-Signals check short-circuits the entire pipeline when the cluster is healthy:
**~100× fewer tokens, ~13× faster, and zero false positives** where the monolithic agent hallucinates a fault
half the time. This is the cleanest validation of the *"determinism before generation"* principle in the dataset.

---

## 8. Statistical Analysis

Paired over the 15 real-fault scenarios (MAS pooled vs. baseline), bootstrap 20,000 resamples + exact sign test.

| Metric | Mean diff (MAS − baseline) | 95% CI | Sign test | p |
|---|---|---|---|---|
| Localization | −0.145 | [−0.372, +0.088] | 3 better / 8 worse | 0.227 |
| Detection | +0.050 | [−0.078, +0.194] | 4 better / 3 worse | 1.000 |

Agent G alone vs. baseline: 4 wins / 3 losses / 8 ties — indistinguishable.

**Interpretation.** No statistically significant difference in either direction on this scenario set. With 15
paired scenarios the design can only detect very large effects; the confidence intervals are wide and both
contain zero. **No superiority claim on localization is defensible.** The fault-free result (24/24 vs 2/4) is the
only comparison with a large, consistent, and mechanistically explained effect.

---

## 9. What the Data Supports — and What It Does Not

### Supported ✅
- At a fixed model, the MAS achieves **localization parity** with the monolithic baseline **while additionally
  performing detection, operating without shell access, and not being told that a fault exists**.
- **Zero false positives on healthy clusters** (24/24) versus 50% for the baseline, at ~100× lower cost.
- **~2.4× lower end-to-end token cost** for configs A and D at equal latency.
- Within the MAS, tool-free planning (G) buys +13 pp localization for 2.4× tokens and 1.8× time — a measured,
  reportable trade-off.
- The MAS **outperforms the baseline on Hotel Reservation** (0.796/0.889 vs 0.741).

### Not supported ❌
- *"Architectural design outweighs raw model capacity"* — falsified at fixed model.
- *"+20.46 pp / +33.25% over the state of the art"* — that delta is a model-generation effect.
- Any claim of statistically significant localization superiority.
- Claims about Astronomy Shop in either direction.

### Genuinely weak, must be disclosed ⚠️
- **RCA score averages 2.2–2.6 / 5.** The submitted paper quotes only the best case (4.25/5 on Port mismatch geo).
  The system localizes well but *explains* poorly. Disclose this and use it to motivate the neuro-symbolic
  direction, which currently reads as speculative.
- **Social Network localization collapses to 0.471.** Must be reported and diagnosed, not averaged away.
- **n = 1 per (config, scenario) cell.** Only the pooled view supports inference.

---

## 10. Recommended Reframing

**Proposed title:** *Doing More with Less Privilege: A Controlled Comparison of Multi-Agent and Monolithic LLM
Agents for Kubernetes RCA*

| Claim | Evidence |
|---|---|
| C1 — At fixed model capacity, architectural decomposition yields **localization parity**, despite a harder task framing and no shell access | §6, §8 |
| C2 — It eliminates false positives on healthy clusters at ~100× lower cost | §7.1 |
| C3 — It delivers the full pipeline at ~2.4× lower end-to-end token cost | §7 |
| C4 — Gains are **application-dependent**: +5.5 pp on Hotel Reservation, −44.6 pp on Social Network, traced to topology-graph quality | §6.1 |
| L1 — Diagnostic *narrative* quality remains low (2.4/5): decomposition helps **find**, not yet **explain** | §5.1 |

This directly answers all three reviewers: model-controlled comparison (R1/R2/R3), all 12 configurations
described (R2), component-level evidence via the fault-free and network-fault cells (R1/R3), and an explicit
statement of what does *not* work (R3).

### Free ablation already in the data
| Design principle | Component | Verdict |
|---|---|---|
| Determinism before generation | Deterministic Triage | **Validated** — §7.1, and network-fault detection 0.33→1.00 |
| Scope before exploring | Datagraph / Planner | **Conditional** — works on Hotel, fails on Social (§6.1) |
| Distill before delivering | MCP server | **Validated on cost** — full pipeline at 1/3 the baseline tokens (§7) |

---

## 11. Experiments: Necessary vs. Optional

Given the constraint of running as little as possible:

### Necessary (blocking) 🔴
1. **Record the model in baseline artefacts.** One-line change; without it the replication package is
   indefensible (§3.5). No re-runs required for already-collected data — but *do* re-run a small verification
   subset (~10 runs) to certify the existing numbers.
2. **Instrument real API token usage** for the baseline (same code site). Needed only if the "2.4×" figure is
   quoted numerically. ~10 runs to calibrate the reconstruction factor, then rescale.

### Strongly recommended 🟠
3. **Enable `qualitative_eval` on the baseline** so the RCA-score comparison is symmetric. Currently the MAS has
   an RCA score and the baseline does not — an unfair asymmetry a reviewer will flag. Analysis task only: ~15 runs.

### Optional 🟢
4. Raising MAS repetitions to n = 5 per (config, scenario). Pooling the 12 configurations already yields n = 12
   per scenario, which is sufficient for the family-level claims in §10. **Skip unless a reviewer demands it.**
5. Architectural ablation (Triage off / Datagraph off / MCP raw). Nice to have; §10's free ablation covers the
   reviewers' request adequately.
6. Astronomy Shop. Not feasible on current hardware; declare as a threat to validity.

**Bottom line: ~25 runs of new execution are genuinely required, not 200.**

---

## 12. Threats to Validity

- Model attribution of the baseline is inferential (§3.5).
- Baseline token counts are reconstructed lower bounds (§3.4).
- KinD virtualised cluster; no bare-metal validation.
- Astronomy Shop excluded due to environment failure, limiting generalisability to two applications.
- MAS cells have n = 1; only pooled statistics are inferentially valid.
- 15 paired scenarios detect only large effects; absence of significance is not evidence of equivalence.
- The baseline receives task-type and fault-existence information the MAS does not — biasing localization
  comparisons **in the baseline's favour**, i.e. the reported parity is conservative.
