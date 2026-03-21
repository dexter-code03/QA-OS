import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type DebugEvent = {
  id: number;
  name: string;
  category: "find" | "action" | "assert" | "error" | "capture" | "nav";
  ts: number;
  dur?: number;
  [key: string]: unknown;
};

const CAT_COLOR: Record<string, string> = {
  find: "#378ADD",
  action: "#00e5a0",
  assert: "#a78bfa",
  error: "#ff3b5c",
  capture: "#7F77DD",
  nav: "#ffb020",
};

export function DebugEventView({ runId, projectId }: { runId: number; projectId?: number }) {
  const [events, setEvents] = useState<DebugEvent[]>([]);
  const [selected, setSelected] = useState<DebugEvent | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [paused, setPaused] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);
  const pausedRef = useRef(false);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const wsRef = { current: null as WebSocket | null };
    (async () => {
      const token = await api.bootstrapAuth();
      if (cancelled) return;
      const url = `ws://${location.host}/ws/runs/${runId}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
      const ws = new WebSocket(url);
      if (cancelled) {
        ws.close();
        return;
      }
      wsRef.current = ws;
      ws.onmessage = (msg) => {
        const ev = JSON.parse(msg.data);
        if (ev.type !== "debug" || pausedRef.current) return;
        const dbgEv: DebugEvent = {
          id: ++idRef.current,
          ...ev.payload,
        };
        setEvents((prev) => [...prev.slice(-500), dbgEv]);
        requestAnimationFrame(() => {
          if (listRef.current) {
            listRef.current.scrollTop = listRef.current.scrollHeight;
          }
        });
      };
    })();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [runId]);

  const startTs = events[0]?.ts ?? 0;
  const filtered = filter === "all" ? events : events.filter((e) => e.category === filter);
  const cats = ["all", "find", "action", "assert", "error", "capture"];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "260px 1fr",
        height: "100%",
        minHeight: 320,
        border: "1px solid var(--border)",
        borderRadius: 10,
        overflow: "hidden",
        background: "var(--bg2)",
        fontFamily: "var(--mono)",
      }}
    >
      {/* LEFT — event stream */}
      <div style={{ borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
        <div
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 600 }}>Debug Events</span>
          <span style={{ fontSize: 10, color: paused ? "var(--warn)" : "var(--accent)" }}>
            {paused ? "Paused" : "● Live"}
          </span>
        </div>

        {/* Category filters */}
        <div
          style={{
            padding: "6px 10px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            gap: 4,
            flexWrap: "wrap",
          }}
        >
          {cats.map((cat) => (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              style={{
                fontSize: 10,
                padding: "2px 8px",
                borderRadius: 20,
                border: `1px solid ${filter === cat ? CAT_COLOR[cat] || "var(--accent)" : "var(--border2)"}`,
                background: filter === cat ? `${CAT_COLOR[cat] || "#00e5a0"}22` : "transparent",
                color: filter === cat ? CAT_COLOR[cat] || "var(--accent)" : "var(--muted)",
                cursor: "pointer",
              }}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Event list */}
        <div ref={listRef} style={{ flex: 1, overflowY: "auto" }}>
          {filtered.map((ev) => {
            const offset = ev.ts - startTs;
            const isSel = selected?.id === ev.id;
            return (
              <div
                key={ev.id}
                onClick={() => setSelected(ev)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 12px",
                  borderBottom: "1px solid var(--border)",
                  cursor: "pointer",
                  background: isSel ? "var(--bg3)" : "transparent",
                  borderLeft: isSel ? "2px solid var(--accent)" : "2px solid transparent",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: CAT_COLOR[ev.category] || "var(--muted)",
                  }}
                />
                <span
                  style={{
                    fontSize: 11,
                    flex: 1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: ev.category === "error" ? "var(--danger)" : "var(--text)",
                  }}
                >
                  {ev.name}
                </span>
                {ev.dur != null && (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color:
                        ev.dur > 1000 ? "var(--danger)" : ev.dur > 500 ? "var(--warn)" : "var(--accent)",
                    }}
                  >
                    {ev.dur}ms
                  </span>
                )}
                <span style={{ fontSize: 10, color: "var(--muted)" }}>+{offset}ms</span>
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div style={{ padding: 20, fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
              {events.length === 0 ? "Waiting for events..." : "No events match filter"}
            </div>
          )}
        </div>

        <div
          style={{
            padding: "6px 12px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <button
            onClick={() => setPaused((p) => !p)}
            style={{
              fontSize: 10,
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border2)",
              background: "transparent",
              color: "var(--muted)",
              cursor: "pointer",
            }}
          >
            {paused ? "Resume" : "Pause"}
          </button>
          <button
            onClick={() => setEvents([])}
            style={{
              fontSize: 10,
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border2)",
              background: "transparent",
              color: "var(--muted)",
              cursor: "pointer",
            }}
          >
            Clear
          </button>
          <span style={{ fontSize: 10, color: "var(--muted)" }}>{events.length} events</span>
        </div>
      </div>

      {/* RIGHT — parameter inspector */}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {selected ? (
          <>
            <div
              style={{
                padding: "10px 16px",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  borderRadius: 20,
                  fontWeight: 600,
                  background: `${CAT_COLOR[selected.category] || "#888"}22`,
                  color: CAT_COLOR[selected.category] || "var(--muted)",
                }}
              >
                {selected.category}
              </span>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{selected.name}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
                +{selected.ts - startTs}ms from run start
              </span>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "14px 16px" }}>
              <div
                style={{
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: ".8px",
                  color: "var(--muted)",
                  marginBottom: 8,
                }}
              >
                Parameters
              </div>
              {Object.entries(selected)
                .filter(([k]) => !["id", "name", "category", "ts"].includes(k))
                .map(([k, v]) => (
                  <div
                    key={k}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr",
                      gap: 8,
                      padding: "5px 0",
                      borderBottom: "1px solid var(--border)",
                      fontSize: 12,
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>{k}</span>
                    <span
                      style={{
                        fontFamily: "var(--mono)",
                        wordBreak: "break-all",
                        color:
                          v === true
                            ? "var(--accent)"
                            : v === false
                              ? "var(--danger)"
                              : k === "dur" && typeof v === "number"
                                ? v > 1000
                                  ? "var(--danger)"
                                  : v > 500
                                    ? "var(--warn)"
                                    : "var(--accent)"
                                : "var(--text)",
                      }}
                    >
                      {JSON.stringify(v)}
                    </span>
                  </div>
                ))}
            </div>
          </>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flex: 1,
              color: "var(--muted)",
              fontSize: 12,
            }}
          >
            Select an event to inspect its parameters
          </div>
        )}
      </div>
    </div>
  );
}
