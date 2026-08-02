# About the bundled Soul profile

> [!IMPORTANT]
> The prompts in this directory are **not the recommended default personality or operating doctrine for Munin**.
>
> They are a deliberately opinionated characterization created for **CTF solving, lab exercises and controlled adversarial simulations**. The war-raven identity, campaign language, Chinese internal shorthand, aggressive momentum and related doctrine are part of that specific experimental profile.

## Intended use

Use this profile only when all of the following are true:

- the environment is a CTF, training lab, local fixture or otherwise explicitly authorised simulation;
- the operator understands the behaviour encouraged by the profile;
- scope, credentials and acceptable actions are defined outside the prompt;
- runtime policy, approval gates and legal authorisation remain authoritative.

## Not intended as a default

A normal Munin deployment should provide a neutral, task-appropriate Soul or no custom characterization at all. Production, enterprise, defensive and general research deployments should not inherit the CTF persona by default.

The contents of `identity.md`, `principles.md`, `goals.md`, `skills.md` and `valravn.md` are examples of a specialised profile. They are not a universal security policy, permission grant, product guarantee or substitute for operator authorisation.

## Safety and authority

Prompt text cannot grant legal or operational authority. Statements inside this profile such as campaign doctrine, aggressive language or descriptions of scope must always remain subordinate to:

1. explicit written authorisation;
2. runtime policy and approval controls;
3. configured target boundaries;
4. applicable law and provider terms;
5. operator review.

## Customising Soul

Use the governed Soul workflow rather than silently editing runtime identity:

- `soul_list` lists the available files;
- `soul_read` inspects the active profile;
- `soul_propose_edit` creates a human-reviewable proposal;
- approved changes can then be committed and versioned.

For a general-purpose deployment, replace this profile with a neutral role definition tailored to the real operational context.