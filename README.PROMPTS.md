# Munin prompt design

## ROGUE is the default laboratory profile

> [!CAUTION]
> Munin's research prompts are designed in **ROGUE mode by default** to stress-test agentic planning, tool use, delegation, persistence, recovery and long-horizon execution inside an **isolated, explicitly authorised laboratory**.

In this project, **ROGUE** means an aggressive capability-evaluation profile. It does **not** grant permission to target third parties, exceed written scope, bypass runtime policy or ignore operator approval. Identity, capability policy, approval boundaries, durable audit events and hard execution controls remain authoritative regardless of how assertive a prompt is.

The profile exists because conservative prompts can hide failure modes that only appear when an agent is encouraged to explore, delegate, recover from errors and continue working autonomously. ROGUE prompts are therefore intended to expose limits and unsafe assumptions during controlled testing, not to remove operational safeguards.

Use these prompts only when all of the following are true:

- the environment is owned by, or explicitly authorised for, the operator;
- target boundaries are written and technically enforced;
- credentials and secrets are test-scoped;
- impactful actions remain governed by Munin's policy and approval layer;
- evidence, tool calls and state transitions are retained for review;
- an operator can interrupt, reject or cancel the run.

## Why the control prompts use Chinese

Much of Munin's internal control-language prompt surface is written in **Simplified Chinese**.

Chinese can encode operational constraints with fewer characters and less visual repetition than an equivalent English paragraph. More importantly for this project, several model families used during development are produced by Chinese labs and are trained and evaluated extensively on Chinese as well as English. Native-language instructions can therefore provide more natural planning vocabulary and, on some models, more stable instruction following during long agentic runs.

This is **not** a universal claim that Chinese always consumes fewer tokens or always produces better results. Tokenisation and task success are model-dependent. A 2026 SWE-bench study found that GLM-5 consumed fewer tokens with Chinese prompts, while other evaluated models did not; it also observed lower Chinese task success overall in its tested setup. Munin treats Chinese as an empirically selected control language for this prompt suite—not as a general cost-saving rule.

The practical rationale is therefore:

1. **Compact human-readable control text.** Dense constraints can be expressed with relatively little visual overhead.
2. **Native alignment for Chinese-developed models.** Chinese-heavy pretraining and evaluation can improve comprehension of nuanced native-language instructions.
3. **Reduced translation ambiguity.** Prompts can use the terminology and imperative style encountered during model development instead of translating every control concept through English.
4. **Empirical validation.** Munin keeps the language choice only where repeated agentic tests show acceptable tool use, persistence and recovery behaviour.

Language remains a deployment variable. Every provider/model/version combination must be tested using representative prompts and its actual tokenizer or API usage data.

## Internal validation matrix

The ROGUE profile and Chinese control-language variants have been exercised internally with:

- **Kimi K3**
- **DeepSeek V4 Pro**
- **DeepSeek V4 Flash 0731**
- **LongCat**
- **MiMo V2.5 Pro**
- **NVIDIA Nemotron Ultra**
- **OffX 9B**

This list records compatibility testing, not equivalent capability, safety or benchmark performance. Provider aliases and hosted versions can change. Revalidate the exact deployed model for structured tool calls, multi-step delegation, context retention, persistence recovery and scoped execution before relying on it operationally.

## References

- [Chinese Language Is Not More Efficient Than English in Vibe Coding: A Preliminary Study on Token Cost and Problem-Solving Rate](https://arxiv.org/abs/2604.14210) — empirical evidence that token and success effects are model-dependent rather than universally favourable to Chinese.
- [DeepSeek-V3 Technical Report](https://arxiv.org/abs/2412.19437) — documents predominantly English-and-Chinese pretraining data and evaluation across English, Chinese and multilingual benchmarks.
- [Chinese Tiny LLM](https://arxiv.org/abs/2404.04167) — demonstrates how Chinese-centric pretraining can materially improve Chinese-language task performance.
- [Kimi model documentation](https://platform.kimi.ai/docs/models) — official documentation for Kimi's agentic and coding model family.
- [DeepSeek API models and pricing](https://api-docs.deepseek.com/quick_start/pricing) — official V4 Pro and V4 Flash identifiers and current capabilities.
- [LongCat official repositories](https://github.com/meituan-longcat) — official LongCat models and technical-report implementations from Meituan.
- [MiMo model releases](https://mimo.mi.com/docs/zh-CN/updates/model) — official MiMo V2.5 Pro release and agent-capability notes.
- [NVIDIA Nemotron 3 Ultra](https://research.nvidia.com/labs/nemotron/Nemotron-3-Ultra/) — official model page and technical report for NVIDIA's agentic reasoning model.

## Operational interpretation

Prompt assertiveness is not authority. A ROGUE prompt may encourage the model to search harder, form subplans, create bounded specialists and recover from failures, but Munin's runtime remains responsible for deciding which capabilities exist, which arguments are permitted and where human approval is mandatory.

A successful model response is also not validation by itself. Keep the resulting diff, evidence, tool lifecycle, validation exit codes and operator decisions independently inspectable.