import { useState, useEffect, useRef } from "react";
import type { ApiLog } from "../../types";

function formatJson(str: string | null): string {
  if (!str) return "(empty)";
  try {
    return JSON.stringify(JSON.parse(str), null, 2);
  } catch {
    return str;
  }
}

function formatHeaders(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
}

function truncateUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname + (u.search || "");
  } catch {
    return url;
  }
}

type StatusFilter = "all" | "2xx" | "4xx" | "5xx";

function buildCurl(log: ApiLog): string {
  let cmd = `curl -X ${log.method} '${log.url}'`;
  for (const [k, v] of Object.entries(log.req_headers || {})) {
    cmd += ` \\\n  -H '${k}: ${v}'`;
  }
  if (log.req_body) {
    const escaped = log.req_body.replace(/'/g, "'\\''");
    cmd += ` \\\n  -d '${escaped}'`;
  }
  return cmd;
}

function ApiLogTabs({ log }: { log: ApiLog }) {
  const [tab, setTab] = useState<"res-body" | "res-headers" | "req-body" | "req-headers">("res-body");
  const [copied, setCopied] = useState(false);

  const tabs = [
    { id: "res-body" as const, label: "Response Body" },
    { id: "res-headers" as const, label: "Response Headers" },
    { id: "req-body" as const, label: "Request Body" },
    { id: "req-headers" as const, label: "Request Headers" },
  ];

  const content = () => {
    switch (tab) {
      case "res-body":
        return formatJson(log.res_body);
      case "res-headers":
        return formatHeaders(log.res_headers);
      case "req-body":
        return formatJson(log.req_body);
      case "req-headers":
        return formatHeaders(log.req_headers);
    }
  };

  const copyContent = async () => {
    try {
      await navigator.clipboard.writeText(content());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const copyCurl = async () => {
    try {
      await navigator.clipboard.writeText(buildCurl(log));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  return (
    <div className="alog-detail-tabs">
      <div className="alog-detail-tab-bar">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`alog-detail-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div style={{ position: "relative" }}>
        <div style={{ position: "absolute", top: 6, right: 8, display: "flex", gap: 4, zIndex: 2 }}>
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, padding: "2px 8px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4 }} onClick={copyContent}>
            {copied ? "Copied!" : "Copy"}
          </button>
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, padding: "2px 8px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4 }} onClick={copyCurl} title="Copy as cURL command">
            cURL
          </button>
        </div>
        <pre className="alog-detail-content">{content()}</pre>
      </div>
    </div>
  );
}

export function ApiLogsPanel({ logs, isLive, stepLabel, disabled }: { logs: ApiLog[]; isLive: boolean; stepLabel?: string; disabled?: boolean }) {
  const [selected, setSelected] = useState<ApiLog | null>(null);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(logs.length);

  useEffect(() => {
    if (isLive && logs.length > prevCountRef.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
    prevCountRef.current = logs.length;
  }, [logs.length, isLive]);

  if (disabled) {
    return (
      <div className="alog-panel" style={{ opacity: 0.5 }}>
        <div className="alog-body" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
          <div style={{ textAlign: "center", color: "var(--muted)", padding: 32 }}>
            <div style={{ fontSize: 22, marginBottom: 10 }}>&#128683;</div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>API logging is disabled</div>
            <div style={{ fontSize: 11, lineHeight: 1.6 }}>Enable the <strong style={{ color: "var(--text)" }}>API Logging</strong> toggle before running to capture network traffic.</div>
          </div>
        </div>
      </div>
    );
  }

  const filtered = logs.filter((log) => {
    const matchText = log.url.toLowerCase().includes(filter.toLowerCase());
    const matchStatus =
      statusFilter === "all" ||
      (statusFilter === "2xx" && log.status_code >= 200 && log.status_code < 300) ||
      (statusFilter === "4xx" && log.status_code >= 400 && log.status_code < 500) ||
      (statusFilter === "5xx" && log.status_code >= 500);
    return matchText && matchStatus;
  });

  const statusFilters: StatusFilter[] = ["all", "2xx", "4xx", "5xx"];

  return (
    <div className="alog-panel">
      <div className="alog-toolbar">
        <input
          className="alog-search"
          placeholder="Filter by URL…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <div className="alog-status-filters">
          {statusFilters.map((s) => (
            <button
              key={s}
              className={`alog-status-btn ${statusFilter === s ? "active" : ""} sf-${s}`}
              onClick={() => setStatusFilter(s)}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>
        {isLive && <span className="alog-live-badge">● LIVE</span>}
        <span className="alog-count">{filtered.length} calls</span>
      </div>

      <div className="alog-body">
        <div className="alog-list" ref={listRef}>
          {filtered.length === 0 && (
            <div className="alog-empty">
              {isLive
                ? stepLabel ? `Waiting for network calls on ${stepLabel}…` : "Waiting for network calls…"
                : stepLabel ? `No API calls captured for ${stepLabel}` : "No API calls captured"}
            </div>
          )}
          {filtered.map((log) => (
            <div
              key={log.id}
              className={`alog-row ${selected?.id === log.id ? "selected" : ""}`}
              onClick={() => setSelected(log)}
            >
              <span className={`alog-method m-${log.method.toLowerCase()}`}>{log.method}</span>
              <span className={`alog-status s-${Math.floor(log.status_code / 100)}xx`}>
                {log.status_code}
              </span>
              <span className="alog-url" title={log.url}>
                {truncateUrl(log.url)}
              </span>
              <span className="alog-dur">{log.duration_ms}ms</span>
            </div>
          ))}
        </div>

        {selected && (
          <div className="alog-detail">
            <div className="alog-detail-url">
              <span className={`alog-method m-${selected.method.toLowerCase()}`}>
                {selected.method}
              </span>
              <span className="alog-detail-full-url">{selected.url}</span>
            </div>
            <div className="alog-detail-meta">
              <span className={`alog-status s-${Math.floor(selected.status_code / 100)}xx`}>
                {selected.status_code}
              </span>
              <span>{selected.duration_ms}ms</span>
              <span>{new Date(selected.timestamp).toLocaleTimeString()}</span>
            </div>
            <ApiLogTabs log={selected} />
          </div>
        )}
      </div>
    </div>
  );
}
