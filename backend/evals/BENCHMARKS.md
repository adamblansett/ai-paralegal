# AI Paralegal Model Evaluation Scorecard

Generated using `python -m evals.runner --probe-live --markdown` on `evals/dataset.json` (Dataset `v1.0.0`).

## Environment Probe Scorecard (Offline Baseline + Live Endpoint Discovery)

| Target | Status | Composite | Clause Recall | Grounding | Hallucination Catch | Avg Latency | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `mock:mock-legal-deterministic-v1` | completed | 100.0% | 100.0% | 100.0% | 100.0% | 0.8 ms | Est. \$0.0000 |
| `ollama:llama-3.1-8b-instruct` | skipped | — | — | — | — | — | Local Ollama server unreachable (`http://localhost:11434`) |
| `gemini:gemini-2.5-flash` | skipped | — | — | — | — | — | `GEMINI_API_KEY` not configured |

## Configured Per-Task Routing Defaults (`DEFAULT_TASK_MODELS`)

`ModelRouter.resolve_task_model()` automatically selects per-task defaults when no explicit `model` override is passed (and supports overriding via `LLM_TASK_ROUTING_JSON`):

| Workflow Task (`task_kind`) | Local Ollama Default | Cloud Gemini Default |
| --- | --- | --- |
| `contract_review` | `qwen-2.5-7b-instruct` | `gemini-2.5-flash` |
| `discovery_synthesis` | `mistral-nemo-12b` | `gemini-2.5-pro` |
| `drafting` | `llama-3.1-8b-instruct` | `gemini-2.5-flash` |
| `qa_research` | `qwen-2.5-7b-instruct` | `gemini-2.5-flash` |

## Running Live Head-to-Head Model Comparisons

```bash
cd backend

# Offline deterministic CI baseline:
python -m evals.runner --provider mock --markdown

# Auto-probe local Ollama + Gemini alongside mock baseline:
python -m evals.runner --probe-live --markdown

# Compare specific Ollama & Gemini models explicitly:
python -m evals.runner --compare "mock:default,ollama:llama-3.1-8b-instruct,ollama:qwen-2.5-7b-instruct,ollama:mistral-nemo-12b,gemini:gemini-2.5-flash" --markdown --output scorecard.json

# Include attorney-approved regression feedback exported from a matter:
python -m evals.runner --provider mock --merge-feedback matter_eval_feedback.json --markdown
```
