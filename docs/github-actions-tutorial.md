# GitHub Actions live-session guide

The live-session workflow is for a temporary, authorised Munin environment. It
can provision a disposable runner, start the unified server, verify selected
integration points and expose only the deliberately configured access path.

## What the workflow provides

- a clean runner with the declared dependency set;
- a repeatable startup path for `munin serve`;
- health and MCP checks suitable for the workflow contract;
- optional persistent/durable-store validation when configured; and
- uploaded diagnostics and artifacts when the run ends.

It is not a replacement for target authorisation, a production secret manager
or a full real-environment acceptance test.

## Repository secrets

Configure only the secrets needed for the session, such as the LLM endpoint and
key, `MUNIN_MASTER_KEY`, MCP token, durable-store credentials and any protected
tunnel credentials. Never place secrets in workflow YAML, issues, pull requests
or session logs. Rotate a credential if it may have appeared in any of them.

## Start a session

1. Open the repository's Actions tab and select the live-session workflow.
2. Choose the intended branch and provide only the workflow inputs documented
   by that run.
3. Watch startup logs until health and the expected integration checks succeed.
4. Connect only through the configured protected endpoint and use the supplied
   authenticated web or MCP path.

## Operate and end safely

Use the same operating rules as a local session: keep targets authorised,
review the live capability registry, watch the durable timeline and resolve
human requests deliberately. A temporary runner is not automatically a safe
network boundary.

When the task ends, close the external access path, cancel the workflow if it
is still running, and download the relevant diagnostics/artifacts through the
normal repository retention policy. Do not treat a runner teardown as proof
that any remote data or credentials have been cleaned up; rotate or revoke as
the deployment requires.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Server health fails | Dependency install, environment contract and server logs. |
| MCP check fails | `/mcp/` path, bearer token and matching FastMCP dependency range. |
| UI opens but no progress appears | API base URL, authenticated session and durable replay stream. |
| Run cannot recover | Persistent hot/checkpoint storage and durable archive configuration. |
| Provider calls fail | Secret availability, endpoint/model compatibility and provider logs. |

## What the workflow proves

A passing live-session workflow proves the declared runner can build the
application and satisfy the selected health, MCP and persistence checks. It does
not prove every provider, target, external tunnel or production deployment
behaves identically. Keep production acceptance tests separate and explicit.
