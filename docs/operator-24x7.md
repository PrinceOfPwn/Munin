# 24x7 operator runtime and lab fixtures

## Discord bridge

Set `MUNIN_DISCORD_TOKEN`, `MUNIN_DISCORD_CHANNEL_ID`, and the numeric
`MUNIN_DISCORD_ALLOWED_USER_IDS` allowlist in `.env`. Inbound commands are
ignored unless they begin with `munin`, for example `munin, show capabilities`.
The bot only listens in the configured channel (or its threads) and sends back
the final operator-safe response; it never sends raw credentials or hidden
model reasoning.

Run a restart-resilient MCP + Discord runtime with:

```bash
docker compose --profile runtime up -d --build munin
docker compose logs -f munin
```

The service uses `restart: unless-stopped`. It is an operator interface, not an
authorization bypass: active tools still use the existing scope and OPSEC gates.

### 24/7 via GitHub Actions (Cloud)

While GitHub-hosted runners have a maximum execution limit of 6 hours per job, you can simulate a 24/7 Discord operation entirely in the cloud without local infrastructure by leveraging **Turso** for durable persistence and scheduled workflows.

1. **Configure Secrets**: Set `MUNIN_DISCORD_TOKEN`, `MUNIN_DISCORD_CHANNEL_ID`, `MUNIN_DISCORD_ALLOWED_USER_IDS`, and your `MUNIN_DB_URL` (Turso) in your repository secrets.
2. **Cron Scheduler**: Create a GitHub Actions workflow (e.g., `.github/workflows/discord-24x7.yml`) that runs on a schedule (e.g., `cron: '0 */5 * * *'`) to restart the agent just before the 6-hour limit expires.
3. **Stateless Reconnection**: Because the Discord connection is stateless and all memory (semantic, episodic, procedural tools) is safely stored in Turso, the bot will briefly disconnect and instantly reconnect when the new runner spins up, resuming its operations exactly where it left off.

## Local LDAP + Apache fixture

The Docker lab contains a richer AKATSUKI directory and `WEB01`, represented by
the `svc_webdeploy` service account and Web Operations groups. Apache is pinned
to 2.4.49 solely as a localhost-only training fingerprint; it is never bound to
the LAN/WAN.

```bash
docker compose up -d openldap phpldapadmin apache-lab
nmap -sV --script http-server-header -p 8081 127.0.0.1
```

The expected result identifies the local Apache service. Treat this as a safe
version-detection exercise; do not point the command at targets outside your
authorized lab.

If OpenLDAP was already created before this version of the seed data, rebuild
only the disposable lab volumes so its bootstrap LDIF files are applied:

```bash
docker compose down -v
docker compose up -d openldap phpldapadmin apache-lab
```

That command deletes the local OpenLDAP lab directory, not Turso or Git data.

## Resetting Turso state

Use **Actions → Reset Munin Turso State → Run workflow** and type the exact
confirmation `WIPE_MUNIN_TURSO`. The workflow deletes operational rows
(memory, episodes, procedural tools, graphs, queues, messages, and intel) but
keeps the database schema and Git history intact.
