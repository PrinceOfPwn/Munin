"use client";

import { useEffect } from "react";
import { Activity, Cpu, Hammer, Bell, History } from "lucide-react";
import StatusDot from "./StatusDot";
import { useMuninStore } from "@/store/muninStore";
import { relativeTime } from "@/lib/format";

export default function RightSidebar() {
  const live = useMuninStore((s) => s.live);
  const refreshLive = useMuninStore((s) => s.refreshLive);

  useEffect(() => {
    const id = window.setInterval(() => {
      refreshLive();
    }, 15000);
    return () => window.clearInterval(id);
  }, [refreshLive]);

  return (
    <div className="w-full h-full flex flex-col overflow-y-auto">
      <Section
        icon={<Activity size={14} />}
        title="MCP Status"
      >
        <div className="flex items-center gap-2">
          <StatusDot
            status={live.mcpConnected ? "ok" : "error"}
            pulse={live.mcpConnected}
          />
          <span
            className={
              live.mcpConnected ? "text-success text-sm" : "text-rose text-sm"
            }
          >
            {live.mcpConnected ? "Connected" : "Unreachable"}
          </span>
        </div>
        <div className="text-xs text-muted mt-1 font-mono">
          {live.mcpConnected ? `${live.toolCount} tools` : "—"}
        </div>
        {live.lastError && !live.mcpConnected && (
          <div className="text-[11px] text-rose/80 mt-2 break-all font-mono">
            {live.lastError}
          </div>
        )}
        <div className="text-[10px] text-muted mt-2">
          updated {relativeTime(live.lastUpdated)}
        </div>
      </Section>

      <Section icon={<Cpu size={14} />} title="Agent Presence">
        {live.presence.length === 0 ? (
          <div className="text-xs text-muted">No agents reporting.</div>
        ) : (
          <ul className="space-y-1.5">
            {live.presence.slice(0, 8).map((p, i) => {
              const status = String(p.status || "UNKNOWN").toUpperCase();
              const dot =
                /RUNNING|ACTIVE/i.test(status)
                  ? "ok"
                  : /IDLE/i.test(status)
                  ? "idle"
                  : "unknown";
              return (
                <li
                  key={i}
                  className="flex items-center justify-between text-xs"
                >
                  <span className="font-mono text-body truncate">
                    {p.agent || p.name || "—"}
                  </span>
                  <span className="flex items-center gap-1.5 text-muted">
                    <StatusDot status={dot} size={6} />
                    {status}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section icon={<Hammer size={14} />} title="Forged Tools">
        <div className="text-2xl font-mono text-accent">
          {live.forgedToolCount}
        </div>
        <div className="text-[11px] text-muted">generated via tool_forge</div>
      </Section>

      <Section icon={<Bell size={14} />} title="Wake Queue">
        <div className="text-2xl font-mono text-success">
          {live.wakePendingCount}
        </div>
        <div className="text-[11px] text-muted">pending wake calls</div>
      </Section>

      <Section icon={<History size={14} />} title="Last Episodic">
        {live.lastEpisodic ? (
          <div className="space-y-1">
            {live.lastEpisodic.timestamp && (
              <div className="text-[11px] text-muted font-mono">
                {relativeTime(live.lastEpisodic.timestamp)}
              </div>
            )}
            {live.lastEpisodic.agent && (
              <div className="text-xs text-ice font-mono">
                {live.lastEpisodic.agent}
              </div>
            )}
            {live.lastEpisodic.action && (
              <div className="text-xs text-body">
                {live.lastEpisodic.action}
              </div>
            )}
            {live.lastEpisodic.summary && (
              <div className="text-[11px] text-muted">
                {live.lastEpisodic.summary}
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-muted">No episodic events recorded.</div>
        )}
      </Section>

      <div className="mt-auto p-3 border-t border-border">
        <button
          onClick={() => refreshLive()}
          className="w-full text-xs font-mono uppercase tracking-wider text-muted hover:text-accent py-1.5 border border-border rounded transition-colors"
        >
          Refresh now
        </button>
      </div>
    </div>
  );
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="p-3 border-b border-border">
      <div className="flex items-center gap-1.5 mb-2 text-muted">
        {icon}
        <h3 className="text-[10px] uppercase tracking-widest font-mono">
          {title}
        </h3>
      </div>
      {children}
    </section>
  );
}
