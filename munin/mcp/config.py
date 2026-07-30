from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class Settings:
    # --- Workspace & OFFX-original ---
    workspace_root: Path
    default_timeout: int
    max_output_chars: int
    expected_egress_ip: str
    forbidden_egress_ip: str
    route_probe_ip: str
    job_workers: int
    github_token: str
    nvd_api_key: str

    # --- LLM (OpenAI-compatible) ---
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_floor: int = 40
    llm_timeout_ceiling: int = 240
    llm_retry_attempts: int = 5
    llm_retry_base_delay: float = 5.0
    llm_retry_max_delay: float = 60.0
    operator_language: str = "auto"

    # --- Passive intel providers ---
    tavily_api_key: str = ""
    hugin_url: str = "https://raw.githubusercontent.com/PrinceOfPwn/Hugin/main/hugin/graph.json"
    hugin_ttl_seconds: int = 900

    # --- LDAP ---
    ldap_uri: str = "ldap://localhost:389"
    ldap_base_dn: str = "dc=akatsuki,dc=com"
    ldap_bind_dn: str = ""
    ldap_password: str = ""

    # --- OPSEC policy ---
    #   always      → preflight + postflight in EVERY tool (OFFX legacy behavior)
    #   active_only → preflight + postflight only when the tool is level=='active'
    #   off         → skip preflight (debug only; logs a warning at startup)
    preflight_policy: str = "active_only"

    # --- MCP transport ---
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8890
    mcp_auth_token: str = ""

    # --- Munin paths ---
    munin_soul_path: Path = field(default_factory=lambda: Path("./soul"))
    munin_data_path: Path = field(default_factory=lambda: Path("./data"))

    # --- Persistence backend ---
    # Empty → local sqlite file at munin_data_path/shared_state.sqlite (default).
    # ``libsql://<host>`` + ``MUNIN_DB_AUTH_TOKEN`` → Turso embedded replica.
    # ``file:/abs/path`` → explicit local file path.
    db_url: str = ""
    db_auth_token: str = ""
    # Root secret used to encrypt BYOK provider keys at rest in Turso. It is
    # deliberately environment-only and is never returned by an MCP tool.
    byok_master_key: str = ""

    # --- LangGraph server (PR-11) ---
    #   MUNIN_LANGGRAPH_URL: empty string means LangGraph server not configured
    munin_langgraph_url: str = ""
    munin_langgraph_port: int = 8123
    munin_checkpoint_db: str = "data/langgraph_checkpoints.sqlite"

    # --- Parallel workers (PR-12) ---
    #   Advisory only — not a hard cap; replaces old MUNIN_MAX_PARALLEL_TOOLS
    munin_suggested_workers: int = 4

    @property
    def runs_root(self) -> Path:
        return self.workspace_root / "runs"

    @property
    def reports_root(self) -> Path:
        return self.workspace_root / "reports"

    @property
    def evidence_root(self) -> Path:
        return self.workspace_root / "evidence"

    @property
    def knowledge_sync_root(self) -> Path:
        return self.workspace_root / "knowledge_sync"

    @property
    def shared_state_db(self) -> Path:
        return self.munin_data_path / "shared_state.sqlite"

    @property
    def generated_tools_dir(self) -> Path:
        return self.workspace_root / "munin" / "generated"

    @property
    def generated_graphs_dir(self) -> Path:
        return self.generated_tools_dir / "graphs"

    def ensure_workspace(self) -> None:
        for path in (
            self.workspace_root,
            self.runs_root,
            self.reports_root,
            self.evidence_root,
            self.knowledge_sync_root,
            self.workspace_root / "intel",
            self.workspace_root / "prompts",
            self.workspace_root / "specs",
            self.workspace_root / "templates",
            self.munin_data_path,
            self.munin_soul_path,
            self.generated_tools_dir,
            self.generated_graphs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _resolve_root() -> Path:
    env_root = os.environ.get("OFFX_WORKSPACE_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    # `.../munin/munin/mcp/config.py` → workspace root is the project root two levels up
    return Path(__file__).resolve().parents[2]


def _resolve_path(env: str, default: Path) -> Path:
    raw = os.environ.get(env, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


def get_settings() -> Settings:
    workspace = _resolve_root()
    settings = Settings(
        workspace_root=workspace,
        default_timeout=int(os.environ.get("OFFX_TIMEOUT", "300")),
        max_output_chars=int(os.environ.get("OFFX_MAX_OUTPUT_CHARS", "32000")),
        expected_egress_ip=os.environ.get("OFFX_EXPECTED_EGRESS_IP", ""),
        forbidden_egress_ip=os.environ.get("OFFX_FORBIDDEN_EGRESS_IP", ""),
        route_probe_ip=os.environ.get("OFFX_ROUTE_PROBE_IP", "1.1.1.1"),
        job_workers=int(os.environ.get("OFFX_JOB_WORKERS", "5")),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        nvd_api_key=os.environ.get("NVD_API_KEY", ""),
        # LLM
        llm_base_url=os.environ.get("LLM_BASE_URL", "").strip(),
        llm_api_key=os.environ.get("LLM_API_KEY", "").strip(),
        llm_model=os.environ.get("LLM_MODEL", "").strip(),
        llm_timeout_floor=int(os.environ.get("LLM_TIMEOUT_FLOOR", "40")),
        llm_timeout_ceiling=int(os.environ.get("LLM_TIMEOUT_CEILING", "240")),
        llm_retry_attempts=max(1, int(os.environ.get("LLM_RETRY_ATTEMPTS", "5"))),
        llm_retry_base_delay=max(0.0, float(os.environ.get("LLM_RETRY_BASE_DELAY", "5"))),
        llm_retry_max_delay=max(0.0, float(os.environ.get("LLM_RETRY_MAX_DELAY", "60"))),
        operator_language=os.environ.get("MUNIN_OPERATOR_LANGUAGE", "auto").strip() or "auto",
        # Intel providers
        tavily_api_key=os.environ.get("TAVILY_API_KEY", "").strip(),
        hugin_url=os.environ.get(
            "HUGIN_URL",
            "https://raw.githubusercontent.com/PrinceOfPwn/Hugin/main/hugin/graph.json",
        ).strip(),
        hugin_ttl_seconds=int(os.environ.get("HUGIN_TTL_SECONDS", "900")),
        # LDAP
        ldap_uri=os.environ.get("LDAP_URI", "ldap://localhost:389").strip(),
        ldap_base_dn=os.environ.get("LDAP_BASE_DN", "dc=akatsuki,dc=com").strip(),
        ldap_bind_dn=os.environ.get("LDAP_BIND_DN", "").strip(),
        ldap_password=os.environ.get("LDAP_PASSWORD", ""),
        # Policy
        preflight_policy=os.environ.get("PREFLIGHT_POLICY", "active_only").strip().lower(),
        # MCP
        mcp_host=os.environ.get("MUNIN_MCP_HOST", "127.0.0.1").strip(),
        mcp_port=int(os.environ.get("MUNIN_MCP_PORT", "8890")),
        # .strip() protects against a common footgun: `.env` files often leave a
        # trailing newline on the last line. Without strip, `hmac.compare_digest`
        # rejects every valid Bearer request because "abc\n" != "abc".
        mcp_auth_token=os.environ.get("MUNIN_MCP_AUTH_TOKEN", "").strip(),
        # Munin paths
        munin_soul_path=_resolve_path("MUNIN_SOUL_PATH", workspace / "soul"),
        munin_data_path=_resolve_path("MUNIN_DATA_PATH", workspace / "data"),
        # Persistence — empty falls back to local file
        db_url=os.environ.get("MUNIN_DB_URL", "").strip(),
        db_auth_token=os.environ.get("MUNIN_DB_AUTH_TOKEN", "").strip(),
        byok_master_key=os.environ.get("MUNIN_BYOK_MASTER_KEY", ""),
        # LangGraph server (PR-11)
        munin_langgraph_url=os.environ.get("MUNIN_LANGGRAPH_URL", "").strip(),
        munin_langgraph_port=int(os.environ.get("MUNIN_LANGGRAPH_PORT", "8123")),
        munin_checkpoint_db=os.environ.get(
            "MUNIN_CHECKPOINT_DB", "data/langgraph_checkpoints.sqlite"
        ).strip(),
        # Parallel workers (PR-12)
        munin_suggested_workers=int(os.environ.get("MUNIN_SUGGESTED_WORKERS", "4")),
    )
    settings.ensure_workspace()
    return settings


def safe_slug(parts: Iterable[str]) -> str:
    raw = "-".join([p.strip().lower() for p in parts if p and p.strip()])
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_") or "item"


# Redact for `repr(settings)` — never spill secrets into logs.
def _redact_db_url(raw_url: str) -> str:
    if not raw_url or "://" not in raw_url:
        return raw_url
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        sanitized_query = [
            (
                key,
                "***REDACTED***"
                if any(marker in key.lower() for marker in ("token", "password", "secret", "api_key", "apikey"))
                else value,
            )
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                urlencode(sanitized_query),
                "",
            )
        )
    except (TypeError, ValueError):
        return "***REDACTED_DB_URL***"


def redact_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        llm_api_key="***REDACTED***" if settings.llm_api_key else "",
        tavily_api_key="***REDACTED***" if settings.tavily_api_key else "",
        github_token="***REDACTED***" if settings.github_token else "",
        nvd_api_key="***REDACTED***" if settings.nvd_api_key else "",
        ldap_password="***REDACTED***" if settings.ldap_password else "",
        mcp_auth_token="***REDACTED***" if settings.mcp_auth_token else "",
        db_url=_redact_db_url(settings.db_url),
        db_auth_token="***REDACTED***" if settings.db_auth_token else "",
        byok_master_key="***REDACTED***" if settings.byok_master_key else "",
    )
