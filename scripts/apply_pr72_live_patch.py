#!/usr/bin/env python3
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def gh_expr(body: str) -> str:
    return "$" + "{{ " + body + " }}"


live_path = Path(".github/workflows/live-session.yml")
live = live_path.read_text(encoding="utf-8")

live = replace_once(
    live,
    "image: bkimminich/juice-shop:v20.0.0",
    "image: bkimminich/juice-shop:v20.1.1",
    "Juice Shop version",
)

tavily = f"      TAVILY_API_KEY:       {gh_expr('secrets.TAVILY_API_KEY')}\n"
intel_lines = [
    tavily.rstrip("\n"),
    "      # Valravn intelligence providers. Empty secrets leave only that provider unavailable.",
    f"      NVD_API_KEY:          {gh_expr('secrets.NVD_API_KEY')}",
    f"      OTX_API_KEY:          {gh_expr('secrets.OTX_API_KEY')}",
    f"      ABUSECH_AUTH_KEY:     {gh_expr('secrets.ABUSECH_AUTH_KEY')}",
    f"      ABUSEIPDB_API_KEY:    {gh_expr('secrets.ABUSEIPDB_API_KEY')}",
    f"      VT_API_KEY:           {gh_expr('secrets.VT_API_KEY')}",
    f"      SHODAN_API_KEY:       {gh_expr('secrets.SHODAN_API_KEY')}",
    f"      CENSYS_API_ID:        {gh_expr('secrets.CENSYS_API_ID')}",
    f"      CENSYS_API_SECRET:    {gh_expr('secrets.CENSYS_API_SECRET')}",
    f"      ZOOMEYE_API_KEY:      {gh_expr('secrets.ZOOMEYE_API_KEY')}",
    f"      LEAKIX_API_KEY:       {gh_expr('secrets.LEAKIX_API_KEY')}",
    f"      NETLAS_API_KEY:       {gh_expr('secrets.NETLAS_API_KEY')}",
    f"      URLSCAN_API_KEY:      {gh_expr('secrets.URLSCAN_API_KEY')}",
    f"      CLOUDFLARE_RADAR_TOKEN: {gh_expr('secrets.CLOUDFLARE_RADAR_TOKEN')}",
    f"      FULLHUNT_API_KEY:     {gh_expr('secrets.FULLHUNT_API_KEY')}",
    f"      GOOGLE_SAFE_BROWSING_API_KEY: {gh_expr('secrets.GOOGLE_SAFE_BROWSING_API_KEY')}",
    f"      CLOUDFLARE_ACCOUNT_ID: {gh_expr('secrets.CLOUDFLARE_ACCOUNT_ID')}",
    f"      CLOUDFLARE_URL_SCANNER_TOKEN: {gh_expr('secrets.CLOUDFLARE_URL_SCANNER_TOKEN')}",
    f"      CLOAKBROWSER_LICENSE_KEY: {gh_expr('secrets.CLOAKBROWSER_LICENSE_KEY')}",
    f"      GOOGLE_TRANSLATE_API_KEY: {gh_expr('secrets.GOOGLE_TRANSLATE_API_KEY')}",
    f"      GITHUB_TOKEN:         {gh_expr('github.token')}",
    f"      VALRAVN_USAGE_MODE:   {gh_expr("vars.VALRAVN_USAGE_MODE || 'personal'")}",
    f"      VALRAVN_URLSCAN_SUBMIT_ENABLED: {gh_expr("vars.VALRAVN_URLSCAN_SUBMIT_ENABLED || 'false'")}",
    f"      VALRAVN_CLOUDFLARE_URL_SCAN_ENABLED: {gh_expr("vars.VALRAVN_CLOUDFLARE_URL_SCAN_ENABLED || 'false'")}",
    f"      VALRAVN_FULLHUNT_ENABLED: {gh_expr("vars.VALRAVN_FULLHUNT_ENABLED || 'false'")}",
    f"      VALRAVN_SAFE_BROWSING_ENABLED: {gh_expr("vars.VALRAVN_SAFE_BROWSING_ENABLED || 'false'")}",
    f"      VALRAVN_BROWSER_ENABLED: {gh_expr("vars.VALRAVN_BROWSER_ENABLED || 'false'")}",
]
live = replace_once(live, tavily, "\n".join(intel_lines) + "\n", "Valravn provider env")

mcp = f"      MUNIN_MCP_AUTH_TOKEN: {gh_expr('secrets.MUNIN_MCP_AUTH_TOKEN')}\n"
mcp_plus = mcp + (
    "      # Talons points at the real Ultimate provider booted before Munin starts.\n"
    '      VALRAVN_TALON_ULTIMATE_URL: "http://127.0.0.1:9444/mcp"\n'
    '      BURP_MCP_HOST: "127.0.0.1"\n'
    '      BURP_MCP_PORT: "9444"\n'
    '      BURP_MCP_TOKEN: "valravn-live-local-only"\n'
    '      BURP_PROXY_HOST: "127.0.0.1"\n'
    '      BURP_PROXY_PORT: "8080"\n'
)
live = replace_once(live, mcp, mcp_plus, "Talons runtime env")

live = replace_once(
    live,
    "              ca-certificates curl git pipx ldap-utils nodejs npm \\\n              build-essential libldap2-dev libsasl2-dev libssl-dev golang-go \\\n",
    "              ca-certificates curl git pipx ldap-utils nodejs npm \\\n              default-jdk xvfb xauth \\\n              build-essential libldap2-dev libsasl2-dev libssl-dev golang-go \\\n",
    "Java/Xvfb dependencies",
)

start_server = "      - name: Start unified Munin server\n"
bootstrap = (
    "      - name: Bootstrap Valravn Talons + Burp Ultimate\n"
    "        # Finish the real mesh first so Discord presence observes live status.\n"
    "        run: bash scripts/valravn_live_bootstrap.sh\n\n"
    + start_server
)
live = replace_once(live, start_server, bootstrap, "Valravn live bootstrap step")

live = replace_once(
    live,
    "              'find_asrep_roastable_users',\n",
    "              'find_asrep_roastable_users', 'valravn_talons_status',\n",
    "Talons required MCP tool",
)

talons_smoke_anchor = "          print('OK — nmap_scan executed through MCP against the authorized Juice Shop box')\n          PY\n"
talons_smoke = """          print('OK — nmap_scan executed through MCP against the authorized Juice Shop box')

          talons_message = rpc(
              5,
              'tools/call',
              {
                  'name': 'valravn_talons_status',
                  'arguments': {
                      'refresh': True,
                      'run_id': f'ci-talons-{os.environ.get("GITHUB_RUN_ID", "manual")}',
                  },
              },
              timeout=90,
          )
          talons_rendered = json.dumps(talons_message, ensure_ascii=False)
          if (
              talons_message.get('error')
              or talons_message.get('result', {}).get('isError')
              or 'valravn-ultimate' not in talons_rendered
              or '\"reachable\": true' not in talons_rendered
          ):
              print(f'::error::Valravn Talons MCP smoke failed: {talons_rendered}', file=sys.stderr)
              sys.exit(1)
          print('OK — Munin sees the live Valravn Ultimate provider through Talons')
          PY
"""
live = replace_once(live, talons_smoke_anchor, talons_smoke, "Talons MCP live smoke")

live = replace_once(
    live,
    "          | **Vulnerable web box** | \\`http://juiceshop:3000\\` |\n",
    "          | **Vulnerable web box** | \\`http://juiceshop:3000\\` |\n"
    "          | **Valravn Talons** | \\`http://127.0.0.1:9444/mcp\\` → pinned Burp MCP Ultimate |\n"
    "          | **Burp Proxy** | \\`127.0.0.1:8080\\` → authorized Juice Shop lab |\n",
    "Live Session summary",
)

juice_health = (
    '              if ! curl -fsS "$MUNIN_LAB_WEB_URL/" >/dev/null; then\n'
    '                echo "::warning::Juice Shop health check failed"\n'
    "              fi\n"
)
mesh_health = juice_health + (
    '              if [ -f "${BURP_HOME:-}/burp.pid" ] && ! kill -0 "$(cat "${BURP_HOME}/burp.pid")" 2>/dev/null; then\n'
    '                echo "::warning::Burp MCP Ultimate process is not alive"\n'
    "              fi\n"
    "              if ! (exec 3<>/dev/tcp/127.0.0.1/9444) 2>/dev/null; then\n"
    '                echo "::warning::Valravn Talons Ultimate endpoint is unreachable"\n'
    "              fi\n"
)
live = replace_once(live, juice_health, mesh_health, "Valravn keepalive health")

gui_log = (
    '          echo "── /tmp/munin-gui.log (tail 100) ──"\n'
    '          tail -100 /tmp/munin-gui.log 2>/dev/null || echo "(no GUI log)"\n'
)
gui_plus = gui_log + (
    '          echo "── Valravn Burp Ultimate log (tail 150) ──"\n'
    '          tail -150 "${BURP_HOME:-/tmp/valravn-burp}/burp.log" 2>/dev/null || echo "(no Burp log)"\n'
)
live = replace_once(live, gui_log, gui_plus, "Burp runtime logs")

stop_anchor = '        run: |\n          GUI_PID=$(cat /tmp/munin-gui.pid 2>/dev/null || echo "")\n'
stop_plus = (
    "        run: |\n"
    '          BURP_PID=$(cat "${BURP_HOME:-/tmp/valravn-burp}/burp.pid" 2>/dev/null || echo "")\n'
    '          if [ -n "$BURP_PID" ] && kill -0 "$BURP_PID" 2>/dev/null; then\n'
    '              pkill -TERM -P "$BURP_PID" 2>/dev/null || true\n'
    '              kill -TERM "$BURP_PID" 2>/dev/null || true\n'
    "          fi\n"
    '          GUI_PID=$(cat /tmp/munin-gui.pid 2>/dev/null || echo "")\n'
)
live = replace_once(live, stop_anchor, stop_plus, "Burp clean shutdown")

live_path.write_text(live, encoding="utf-8")

launcher_path = Path("valravn/scripts/start-burp-headless.sh")
launcher = launcher_path.read_text(encoding="utf-8")
launcher = replace_once(
    launcher,
    "require_cmd java\nrequire_cmd curl\nrequire_cmd python\nrequire_cmd sha256sum\n",
    "require_cmd java\nrequire_cmd curl\nrequire_cmd sha256sum\n\n"
    "if command -v python >/dev/null 2>&1; then\n"
    "  PYTHON_BIN=python\n"
    "elif command -v python3 >/dev/null 2>&1; then\n"
    "  PYTHON_BIN=python3\n"
    "else\n"
    '  echo "required command not found: python/python3" >&2\n'
    "  exit 2\n"
    "fi\n",
    "Python command fallback",
)
launcher = replace_once(
    launcher,
    'BURP_JAR="$BURP_JAR" python - <<\'PY\'\n',
    'BURP_JAR="$BURP_JAR" "$PYTHON_BIN" - <<\'PY\'\n',
    "JAR validation Python",
)
launcher = replace_once(
    launcher,
    'BURP_ULTIMATE_JAR="$BURP_ULTIMATE_JAR" BURP_USER_CONFIG="$BURP_USER_CONFIG" python - <<\'PY\'\n',
    'BURP_ULTIMATE_JAR="$BURP_ULTIMATE_JAR" BURP_USER_CONFIG="$BURP_USER_CONFIG" "$PYTHON_BIN" - <<\'PY\'\n',
    "Burp config Python",
)
launcher = replace_once(
    launcher,
    'BURP_LOG="$BURP_LOG" \\\npython - <<\'PY\'\n',
    'BURP_LOG="$BURP_LOG" \\\n"$PYTHON_BIN" - <<\'PY\'\n',
    "MCP probe Python",
)
launcher_path.write_text(launcher, encoding="utf-8")
