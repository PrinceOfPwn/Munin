// tags: [utility-library, c-a-t-e-g-o-r-y--t-a-b-s, t-o-o-l--c-a-t-e-g-o-r-i-e-s]
export interface ToolCategoryDef {
  key: string;
  label: string;
  color: string;
  tools: string[];
}

export const TOOL_CATEGORIES: ToolCategoryDef[] = [
  {
    key: "LDAP",
    label: "LDAP",
    color: "#38bdf8",
    tools: [
      "ldap_who_am_i",
      "get_current_user_info",
      "get_user_groups",
      "ldap_search",
      "find_kerberoastable_users",
      "find_asrep_roastable_users",
      "find_domain_admins",
      "dump_domain_structure",
    ],
  },
  {
    key: "Forge",
    label: "Forge",
    color: "#7c3aed",
    tools: [
      "tool_forge",
      "graph_forge",
      "list_generated_tools",
      "list_generated_graphs",
      "describe_generated_tool",
      "describe_generated_graph",
      "run_generated_tool",
      "deactivate_generated_tool",
      "drop_generated_graph",
    ],
  },
  {
    key: "Memory",
    label: "Memory",
    color: "#f59e0b",
    tools: [
      "memory_remember",
      "memory_recall",
      "memory_list",
      "episodic_query",
      "soul_list",
      "soul_read",
      "soul_propose_edit",
    ],
  },
  {
    key: "Agents",
    label: "Agents",
    color: "#10b981",
    tools: [
      "munin_wake",
      "munin_wake_claim",
      "munin_wake_list",
      "list_agent_presence",
      "post_agent_message",
      "fetch_agent_messages",
      "ack_agent_message",
      "upsert_agent_presence",
    ],
  },
  {
    key: "Recon",
    label: "Recon",
    color: "#f43f5e",
    tools: [
      "nmap_scan",
      "nmap_advanced_scan",
      "nuclei_scan",
      "feroxbuster_scan",
      "ffuf_scan",
      "httpx_probe",
      "katana_crawl",
      "smbmap_scan",
      "netexec_scan",
      "hydra_attack",
      "sqlmap_scan",
      "web_evidence_screenshotter",
    ],
  },
  {
    key: "Intel",
    label: "Intel",
    color: "#6b7280",
    tools: [
      "cve_lookup",
      "cve_search",
      "cve_enrich",
      "exploit_search",
      "package_vuln_lookup",
      "tavily_search",
      "hugin_search",
      "hugin_refresh",
      "vpn_status",
      "health_check",
      "shared_state_overview",
    ],
  },
];

export function categorize(toolName: string): ToolCategoryDef {
  if (toolName.startsWith("gen__")) {
    return TOOL_CATEGORIES.find((c) => c.key === "Forge")!;
  }
  for (const cat of TOOL_CATEGORIES) {
    if (cat.tools.includes(toolName)) return cat;
  }
  // Heuristic fallbacks
  if (/ldap|user|group|domain|kerbero|asrep/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "LDAP")!;
  if (/forge|graph|generated/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "Forge")!;
  if (/memory|episodic|soul|recall|remember/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "Memory")!;
  if (/wake|agent|presence|message/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "Agents")!;
  if (/nmap|nuclei|ferox|ffuf|httpx|katana|smb|netexec|hydra|sqlmap|screenshot/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "Recon")!;
  if (/cve|exploit|vuln|tavily|hugin|vpn|health|state/i.test(toolName))
    return TOOL_CATEGORIES.find((c) => c.key === "Intel")!;
  // Default to violet (Munin's color)
  return { key: "Other", label: "Other", color: "#7c3aed", tools: [] };
}

export const CATEGORY_TABS = ["All", ...TOOL_CATEGORIES.map((c) => c.label)];
