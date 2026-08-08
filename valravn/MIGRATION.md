# Valravn provider migration

Valravn now separates workflow knowledge from Burp transport.

## Retired path

Munin previously called a local REST wrapper which forwarded requests to a custom Java Burp extension on port 8111.

## Current path

Munin calls the compact `valravn_talons_*` gateway. Talons discovers and invokes `burp-mcp-ultimate` over Streamable HTTP MCP, and Ultimate uses the Montoya API inside Burp.

## Compatibility mapping

- Runtime health: use `valravn_talons_status`.
- Tool discovery: use `valravn_talons_tools`.
- Passive Burp state: use `valravn_talons_read` and provider resources such as `burp://proxy/history`.
- Provider execution: use `valravn_talons_call` with the current provider schema.
- External security-tool integrations: use the Valravn Arsenal gateway instead of adding them to the Burp transport layer.

## Skill migration rule

Legacy skills remain useful as operational playbooks. When an old skill names a tool from the retired implementation, preserve the intent and evidence requirements, then discover the equivalent provider operation through Talons. Do not restore the old REST endpoint merely to keep an obsolete call signature.

The imported legacy source tree remains as a reference corpus while those workflows are translated. It is not registered as Munin's active Burp backend.

## CI contract

The live integration workflow must prove the actual provider path against OWASP Juice Shop: build the pinned provider, start Burp unattended, negotiate MCP, invoke a real provider operation, route traffic through Burp Proxy, and observe the resulting state through the provider.
