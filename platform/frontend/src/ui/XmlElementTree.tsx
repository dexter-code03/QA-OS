import React, { useMemo, useState } from "react";

type XmlNode = {
  tag: string;
  attrs: Record<string, string>;
  children: XmlNode[];
};

function parseXmlToTree(xml: string): XmlNode | null {
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(xml, "text/xml");
    const err = doc.querySelector("parsererror");
    if (err) return null;
    const root = doc.documentElement;
    if (!root) return null;

    function walk(el: Element): XmlNode {
      const attrs: Record<string, string> = {};
      for (let i = 0; i < el.attributes.length; i++) {
        const a = el.attributes[i];
        attrs[a.name] = a.value;
      }
      const children: XmlNode[] = [];
      for (let i = 0; i < el.children.length; i++) {
        children.push(walk(el.children[i]));
      }
      return { tag: el.tagName, attrs, children };
    }
    return walk(root);
  } catch {
    return null;
  }
}

function getLocators(node: XmlNode): { strategy: string; value: string }[] {
  const locators: { strategy: string; value: string }[] = [];
  const rid = node.attrs["resource-id"];
  const contentDesc = node.attrs["content-desc"] ?? node.attrs["contentDescription"];
  const text = node.attrs["text"];
  const name = node.attrs["name"] ?? node.attrs["label"];

  if (rid && rid.trim()) {
    const idVal = rid.includes(":id/") ? rid.split(":id/")[1] || rid : rid;
    locators.push({ strategy: "id", value: idVal });
  }
  const seen = new Set<string>();
  for (const v of [contentDesc, name, text].filter(Boolean)) {
    const val = (v || "").trim();
    if (val && !seen.has(val)) {
      seen.add(val);
      locators.push({ strategy: "accessibilityId", value: val });
    }
  }
  return locators;
}

function getShortClass(tag: string, attrs: Record<string, string>): string {
  const cls = attrs["class"] ?? tag;
  const short = cls.split(".").pop() || cls;
  return short || tag;
}

/** Simplify raw page source XML for AI: compact tree with class, resource-id, content-desc, text, bounds. */
export function simplifyXmlForAI(xml: string): string {
  const tree = parseXmlToTree(xml);
  if (!tree) return xml;

  const KEY_ATTRS = ["resource-id", "content-desc", "contentDescription", "text", "name", "label", "bounds", "clickable"];
  function formatNode(node: XmlNode, indent: number): string {
    const shortClass = getShortClass(node.tag, node.attrs);
    const parts: string[] = [shortClass];
    for (const k of KEY_ATTRS) {
      const v = node.attrs[k];
      if (v && v.trim()) {
        const val = k === "resource-id" && v.includes(":id/") ? v.split(":id/")[1] || v : v;
        parts.push(`${k}="${val}"`);
      }
    }
    const attrsStr = parts.length > 1 ? " " + parts.slice(1).join(" ") : "";
    const prefix = "  ".repeat(indent);
    if (node.children.length === 0) {
      return `${prefix}<${parts[0]}${attrsStr}/>`;
    }
    const childLines = node.children.map((c) => formatNode(c, indent + 1)).join("\n");
    return `${prefix}<${parts[0]}${attrsStr}>\n${childLines}\n${prefix}</${parts[0]}>`;
  }
  return formatNode(tree, 0);
}

function XmlTreeNode({
  node,
  depth,
  path,
  expanded,
  onToggle,
  onCopy,
  onNodeClick,
}: {
  node: XmlNode;
  depth: number;
  path: string;
  expanded: Set<string>;
  onToggle: (p: string) => void;
  onCopy: (s: string, v: string) => void;
  onNodeClick?: (selector: { using: string; value: string }) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expanded.has(path);
  const shortClass = getShortClass(node.tag, node.attrs);
  const locators = getLocators(node);
  const rid = node.attrs["resource-id"];
  const contentDesc = node.attrs["content-desc"] ?? node.attrs["contentDescription"];
  const text = node.attrs["text"];
  const bounds = node.attrs["bounds"];
  const hasIdentifiers = !!(rid || contentDesc || text);

  return (
    <div style={{ marginLeft: depth * 12, fontSize: 11, fontFamily: "var(--mono)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 6,
          padding: "2px 0",
          borderLeft: hasIdentifiers ? "2px solid rgba(99,102,241,.5)" : "2px solid transparent",
          paddingLeft: 4,
          cursor: onNodeClick && hasIdentifiers ? "pointer" : "default",
        }}
        onClick={() => {
          if (onNodeClick && hasIdentifiers && locators.length > 0) {
            const best = locators[0];
            onNodeClick({ using: best.strategy === "id" ? "id" : "accessibilityId", value: best.value });
          }
        }}
      >
        <span
          onClick={(e) => { e.stopPropagation(); hasChildren && onToggle(path); }}
          style={{
            cursor: hasChildren ? "pointer" : "default",
            color: "var(--muted)",
            minWidth: 14,
            userSelect: "none",
          }}
        >
          {hasChildren ? (isExpanded ? "▼" : "▶") : "·"}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span style={{ color: "#7dd3fc" }}>{shortClass}</span>
          {rid && (
            <span style={{ color: "#a78bfa", marginLeft: 6 }}>
              id=<span style={{ color: "#fbbf24" }}>{rid.includes(":id/") ? rid.split(":id/")[1] : rid}</span>
            </span>
          )}
          {contentDesc && (
            <span style={{ color: "#34d399", marginLeft: 6 }}>
              content-desc=&quot;<span style={{ color: "#fbbf24" }}>{contentDesc}</span>&quot;
            </span>
          )}
          {text && !contentDesc && (
            <span style={{ color: "#34d399", marginLeft: 6 }}>
              text=&quot;<span style={{ color: "#fbbf24" }}>{text}</span>&quot;
            </span>
          )}
          {bounds && (
            <span style={{ color: "var(--muted)", marginLeft: 6, fontSize: 10 }}>{bounds}</span>
          )}
          {locators.length > 0 && (
            <span style={{ marginLeft: 8, display: "inline-flex", gap: 4, flexWrap: "wrap" }} onClick={e => e.stopPropagation()}>
              {locators.map((l, i) => (
                <button
                  key={i}
                  className="btn-ghost btn-sm"
                  style={{ fontSize: 9, padding: "1px 5px", height: 18 }}
                  onClick={() => onCopy(l.strategy, l.value)}
                  title={`Copy ${l.strategy}=${l.value}`}
                >
                  {l.strategy}: {l.value.length > 20 ? l.value.slice(0, 17) + "…" : l.value}
                </button>
              ))}
            </span>
          )}
        </div>
      </div>
      {hasChildren && isExpanded &&
        node.children.map((c, i) => (
          <XmlTreeNode
            key={i}
            node={c}
            depth={depth + 1}
            path={`${path}/${i}`}
            expanded={expanded}
            onToggle={onToggle}
            onCopy={onCopy}
            onNodeClick={onNodeClick}
          />
        ))}
    </div>
  );
}

export function XmlElementTree({
  xml,
  onCopy,
  onNodeClick,
}: {
  xml: string;
  onCopy?: (msg: string) => void;
  onNodeClick?: (selector: { using: string; value: string }) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["0"]));
  const [viewMode, setViewMode] = useState<"tree" | "raw">("tree");
  const [filterActionable, setFilterActionable] = useState(false);

  const tree = useMemo(() => parseXmlToTree(xml), [xml]);

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const handleCopy = (strategy: string, value: string) => {
    const s = `${strategy}=${value}`;
    navigator.clipboard.writeText(value);
    onCopy?.(`Copied ${strategy}: ${value}`);
  };

  const filterNodes = (node: XmlNode): XmlNode | null => {
    const rid = node.attrs["resource-id"];
    const contentDesc = node.attrs["content-desc"] ?? node.attrs["contentDescription"];
    const text = node.attrs["text"];
    const hasId = !!(rid || contentDesc || text);
    const filteredChildren = node.children.map(filterNodes).filter((c): c is XmlNode => c !== null);
    if (filterActionable && !hasId && filteredChildren.length === 0) return null;
    return { ...node, children: filteredChildren };
  };

  const filteredTree = useMemo(() => (tree && filterActionable ? filterNodes(tree) : tree), [tree, filterActionable]);

  const collectAllPaths = (node: XmlNode, prefix: string): string[] => {
    const paths = [prefix];
    node.children.forEach((c, i) => paths.push(...collectAllPaths(c, `${prefix}/${i}`)));
    return paths;
  };
  const allPaths = useMemo(() => (filteredTree ? collectAllPaths(filteredTree, "0") : []), [filteredTree]);
  const expandAll = () => setExpanded(new Set(allPaths));
  const collapseAll = () => setExpanded(new Set(["0"]));

  const copyXml = () => {
    const toCopy = simplifyXmlForAI(xml);
    navigator.clipboard.writeText(toCopy);
    onCopy?.("Copied XML to clipboard");
  };

  if (!xml) return null;

  if (viewMode === "raw") {
    return (
      <div className="xml-panel">
        <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
          <button className="btn-ghost btn-sm" onClick={() => setViewMode("tree")} style={{ fontSize: 10 }}>
            Switch to Element Tree
          </button>
          <button className="btn-ghost btn-sm" onClick={() => { navigator.clipboard.writeText(xml); onCopy?.("Copied raw XML to clipboard"); }} style={{ fontSize: 10 }}>
            📋 Copy XML
          </button>
        </div>
        <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 10, color: "var(--muted)" }}>
          {xml.slice(0, 8000)}
        </pre>
      </div>
    );
  }

  if (!filteredTree) {
    return (
      <div className="xml-panel">
        <div style={{ color: "var(--muted)", fontSize: 11 }}>Invalid or empty XML</div>
        <button className="btn-ghost btn-sm" style={{ marginTop: 8, fontSize: 10 }} onClick={() => setViewMode("raw")}>
          View raw XML
        </button>
      </div>
    );
  }

  return (
    <div className="xml-panel xml-tree-panel">
      <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn-ghost btn-sm" onClick={() => setViewMode("raw")} style={{ fontSize: 10 }}>
          Raw XML
        </button>
        <button className="btn-ghost btn-sm" onClick={expandAll} style={{ fontSize: 10 }} title="Expand all nodes">
          Extended tree
        </button>
        <button className="btn-ghost btn-sm" onClick={collapseAll} style={{ fontSize: 10 }} title="Collapse to root">
          Collapse
        </button>
        <button className="btn-ghost btn-sm" onClick={copyXml} style={{ fontSize: 10 }}>
          📋 Copy XML
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--muted)", cursor: "pointer" }}>
          <input type="checkbox" checked={filterActionable} onChange={(e) => setFilterActionable(e.target.checked)} />
          Show only elements with id/desc/text
        </label>
      </div>
      <div style={{ maxHeight: 320, overflow: "auto" }}>
        <XmlTreeNode node={filteredTree} depth={0} path="0" expanded={expanded} onToggle={toggle} onCopy={handleCopy} onNodeClick={onNodeClick} />
      </div>
    </div>
  );
}
