# Persistent conversations

Munin's operator chat is a durable workspace, not a sequence of unrelated MCP
calls. Every natural-language turn belongs to a conversation id and is stored
in Turso together with the assistant response, observable tool timeline, rolling
context summary, and generated text/code files.

## Required backend

Conversation tools and munin_chat require a remote Turso URL:

    export MUNIN_DB_URL='libsql://your-database.turso.io'
    export MUNIN_DB_AUTH_TOKEN='...'

There is deliberately no SQLite fallback and no GitHub Actions artifact copy for
conversation state. If Turso is missing, the server returns turso_required
instead of presenting a thread that disappears with the runner.

## MCP contract

- conversation_list: recent non-archived threads.
- conversation_get(conversation_id): transcript and generated files.
- conversation_create(conversation_id, title): idempotently creates a thread.
- munin_chat(message, conversation_id, mode): appends a user turn, injects the
  relevant history into the ReAct agent, and persists the final answer.

The GUI creates a conv_<uuid> id for a new thread and reuses it for every
follow-up. On reload it fetches the conversation list and restores the selected
transcript from Turso.

## Context policy

The agent receives the latest 16 user/assistant turns (up to 28,000 characters)
plus a deterministic rolling summary of older turns. The full transcript remains
available in Turso and in the GUI; only the LLM working set is bounded.

## Downloadable files

When an assistant response contains a fenced Markdown, Python, JSON, YAML, SQL,
or shell block, Munin stores it as a conversation_artifact in Turso. The GUI
shows it under the response with Copy and Download controls. Download creates a
browser-local Blob; no file is written to the server filesystem or to an Actions
artifact.
