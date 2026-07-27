# LLM Providers & Model Configuration

Munin interacts with any OpenAI-compatible `/v1/chat/completions` endpoint via `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Below are canonical provider configurations and recommended models.

---

## 🎯 Fine-Tuned Model Recommendation for Exercise Planning

For maximum efficiency and high-precision tool selection during offensive security exercise planning and ReAct orchestration, we invite operators to utilize our specialized fine-tuned model:

- **Model / Notebook**: [OFFX-Qwen3.5-9B Track A (DoRA Planner)](https://www.kaggle.com/code/emilianoperalta/offx-qwen35-9b-track-a-dora-planner-w10-20260701)
- **Specialization**: Fine-tuned via DoRA (Weight-Decomposed Low-Rank Adaptation) specifically for structured threat assessment, LDAP enumeration strategies, and high-precision ReAct tool calling.
- **Deployment**: Serve via vLLM or Ollama on an OpenAI-compatible endpoint.

---

## ⚡ Supported LLM Providers

### NVIDIA NIM

```bash
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_API_KEY=nvapi-...
LLM_MODEL=meta/llama-3.3-70b-instruct
```

Recommended NIM models:
- `meta/llama-3.3-70b-instruct`
- `nvidia/llama-3.3-nemotron-super-49b-v1`
- `qwen/qwen2.5-coder-32b-instruct` (optimized for `tool_forge`)

### OpenAI

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

### Groq

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
```

### Self-Hosted vLLM (Recommended for Custom Models)

```bash
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=dummy
LLM_MODEL=offx-qwen35-9b-dora-planner
```

### Local Ollama

```bash
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:70b
```

---

## ⏱️ Timeout Controls

- `LLM_TIMEOUT_FLOOR` (default `40s`) and `LLM_TIMEOUT_CEILING` (default `240s`) bound the adaptive timeout.
- The 40s floor ensures complex model iterations (such as `tool_forge` code synthesis) complete reliably.
