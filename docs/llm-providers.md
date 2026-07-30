# LLM providers

Munin talks to any OpenAI-compatible `/v1/chat/completions` endpoint via `LLM_BASE_URL`
+ `LLM_API_KEY` + `LLM_MODEL`. Below the canonical configurations.

## NVIDIA NIM

```bash
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_API_KEY=nvapi-...
LLM_MODEL=meta/llama-3.3-70b-instruct
```

Other useful NIM models:
- `nvidia/llama-3.3-nemotron-super-49b-v1`
- `meta/llama-3.3-70b-instruct`
- `qwen/qwen2.5-coder-32b-instruct` (excellent for `tool_forge`)

## OpenAI

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

## Groq

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
```

## vLLM (self-hosted)

```bash
LLM_BASE_URL=https://your-vllm-host:8000/v1
LLM_API_KEY=dummy
LLM_MODEL=your/model-name
```

Or on loopback (allowed by SSRF check):

```bash
LLM_BASE_URL=http://localhost:8000/v1
```

## Ollama (local, dev-only)

```bash
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:70b
```

## Timeout

`LLM_TIMEOUT_FLOOR` (default 40s) and `LLM_TIMEOUT_CEILING` (default 240s) bound the
adaptive timeout. Never below 40s — a decision made because NIM's 70B+ models often
exceed 30s on tool-forge iterations.
