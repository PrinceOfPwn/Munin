# LLM Providers and Chinese-First Runtime

Munin accepts any endpoint implementing OpenAI-style `/v1/chat/completions`
through `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Its operating contract
is provider-independent: internal coordination is Simplified Chinese, code and
machine artifacts are English, and operator delivery follows
`MUNIN_OPERATOR_LANGUAGE` or the latest operator message.

```bash
LLM_BASE_URL=https://<provider>/v1
LLM_API_KEY=<provider-key>
LLM_MODEL=<provider-model-id>
MUNIN_OPERATOR_LANGUAGE=auto
```

`auto` is recommended. Set `es`, `en`, `pt-BR`, or `zh-CN` when a deployment
must always use one operator language.

## Supported Chinese model families

### GLM

GLM is a first-class profile. Any model id containing `glm` receives the same
Chinese operating protocol, including GLM-5/5.x and GLM-4.x endpoints.

```bash
LLM_BASE_URL=https://api.z.ai/api/paas/v4
LLM_API_KEY=<z-ai-key>
LLM_MODEL=glm-5
```

Z.AI documents OpenAI-style `tool_calls` and interleaved thinking across tool
turns. Munin keeps model-native thinking private and exposes only decision
summaries, tool progress, evidence, and outcomes.

- [Z.AI Function Calling](https://docs.z.ai/guides/capabilities/function-calling)
- [Z.AI Thinking Mode](https://docs.z.ai/guides/capabilities/thinking-mode)
- [GLM-5](https://github.com/zai-org/GLM-5)

### MiMo

The supported open model is **MiMo-V2-Flash**, not the obsolete MiMo 7B
example. Provider aliases such as `mimo-v2.5` also match the MiMo profile.

```bash
LLM_BASE_URL=https://<mimo-provider>/v1
LLM_API_KEY=<provider-key>
LLM_MODEL=mimo-v2-flash
```

For self-hosted MiMo-V2-Flash, Xiaomi recommends SGLang with the `mimo` tool
parser and a lower temperature for agentic tool use. If a provider returns
`reasoning_content`, its adapter must preserve that field across multi-turn
tool calls; it must never be displayed as operator-visible reasoning.

- [MiMo-V2-Flash official repository](https://github.com/XiaomiMiMo/MiMo-V2-Flash)

### Qwen

Qwen3 and compatible Qwen endpoints are supported. The provider must expose
tool calls as OpenAI-compatible structured calls; Munin does not ask the model
to print textual ReAct/XML tool tags.

```bash
LLM_BASE_URL=https://<qwen-provider>/v1
LLM_API_KEY=<provider-key>
LLM_MODEL=qwen3-32b
```

- [Qwen Function Calling](https://qwen.readthedocs.io/en/stable/framework/function_call.html)

The OFFX Qwen3.5 planner can also be served through vLLM/Ollama:

```bash
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=dummy
LLM_MODEL=offx-qwen35-9b-dora-planner
```

### DeepSeek

Use a DeepSeek chat model whose endpoint supports function calling. Do not
select a reasoning-only variant that the provider documents as incompatible
with tools.

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=<deepseek-key>
LLM_MODEL=deepseek-chat
```

- [DeepSeek API updates and function-calling support](https://api-docs.deepseek.com/updates)

### Kimi, Yi, and other compatible Chinese models

Model ids containing `kimi`, `moonshot`, or `yi` receive a named profile; all
other endpoints use the generic `OpenAI-compatible` profile. The behavioral
contract remains identical. Provider-specific chat templates and tool parsers
must be configured at the serving layer, not imitated in the system prompt.

## Why the prompt does not request visible chain-of-thought

Munin uses concise Chinese for operational instructions and inter-agent
handoffs, but it does not request or expose private reasoning. This avoids
provider-specific reasoning formats and gives the GUI/Discord a stable,
auditable surface:

- objective and scope;
- action/tool selected;
- evidence and source identifiers;
- risk or blocker;
- next step.

See [Prompt Architecture](prompt-architecture.md) for the exact contracts and
few-shot design.

## Sampling and timeout guidance

- Coordinator/subagent tool use: start around `temperature=0.2-0.3`.
- Code generation in `tool_forge`: `temperature=0.1`.
- `LLM_TIMEOUT_FLOOR` defaults to `40s`; `LLM_TIMEOUT_CEILING` defaults to
  `240s`.
- Preserve the complete assistant tool-call message and every matching tool
  result in multi-turn history.
- Keep exactly one composed system message per request; Munin already does so.
