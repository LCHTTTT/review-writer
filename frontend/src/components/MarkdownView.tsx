import type { ReactNode } from "react";
import { useUiText } from "../i18n/useUiText";

const subscript = new Map(Object.entries({
  "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
  "6": "₆", "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋",
  "=": "₌", "(": "₍", ")": "₎", a: "ₐ", e: "ₑ", h: "ₕ", i: "ᵢ",
  j: "ⱼ", k: "ₖ", l: "ₗ", m: "ₘ", n: "ₙ", o: "ₒ", p: "ₚ",
  r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ", v: "ᵥ", x: "ₓ",
}));
const superscript = new Map(Object.entries({
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
  "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻",
  "=": "⁼", "(": "⁽", ")": "⁾", i: "ⁱ", n: "ⁿ",
}));
const texSymbols: Record<string, string> = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", theta: "θ",
  lambda: "λ", mu: "μ", nu: "ν", pi: "π", rho: "ρ", sigma: "σ", tau: "τ",
  phi: "φ", chi: "χ", psi: "ψ", omega: "ω", cdot: "·", times: "×", pm: "±",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", neq: "≠", prime: "′", circ: "°",
};

function translateScript(value: string, table: Map<string, string>) {
  return value.replace(/\s+/g, "").split("").map((character) => table.get(character) || character).join("");
}

export function readableInlineMath(raw: string) {
  let value = raw;
  for (let pass = 0; pass < 8; pass += 1) {
    const next = value.replace(/\\(?:mathrm|mathsf|mathbf|mathit|text|operatorname|pmb|boldsymbol)\s*\{([^{}]*)\}/g, "$1");
    if (next === value) break;
    value = next;
  }
  value = value.replace(/\\([A-Za-z]+)/g, (_match, command: string) => texSymbols[command] || command);
  for (const [marker, table] of [["^", superscript], ["_", subscript]] as const) {
    const grouped = marker === "^" ? /\s*\^\s*\{([^{}]+)\}/g : /\s*_\s*\{([^{}]+)\}/g;
    const single = marker === "^" ? /\s*\^\s*([0-9+\-=()])/g : /\s*_\s*([0-9+\-=()])/g;
    value = value.replace(grouped, (_match, content: string) => translateScript(content, table));
    value = value.replace(single, (_match, content: string) => translateScript(content, table));
  }
  return value
    .replace(/[{}]/g, "")
    .replace(/\s*([·×])\s*/g, "$1")
    .replace(/(?<!\w)-(?=\d)/g, "−")
    .replace(/\(-\)/g, "(−)")
    .replace(/([−+]?\d+(?:\.\d+)?)\s*°\s*([CFK])\b/g, "$1 °$2")
    .replace(/\s+/g, " ")
    .trim();
}

/** Read balanced labels/destinations; chemistry names frequently contain []. */
function markdownLinkAt(source: string, start: number) {
  const image = source[start] === "!";
  const labelStart = start + (image ? 2 : 1);
  const close = (from: number, open: string, end: string) => {
    let depth = 1;
    for (let index = from; index < source.length; index += 1) {
      if (source[index] === "\\") { index += 1; continue; }
      if (source[index] === "\n") return -1;
      if (source[index] === open) depth += 1;
      if (source[index] === end && --depth === 0) return index;
    }
    return -1;
  };
  const labelEnd = close(labelStart, "[", "]");
  if (labelEnd < 0 || source[labelEnd + 1] !== "(") return null;
  const urlEnd = close(labelEnd + 2, "(", ")");
  if (urlEnd < 0) return null;
  const unescape = (value: string) => value.replace(/\\([\[\]()\\])/g, "$1");
  return { image, label: unescape(source.slice(labelStart, labelEnd)),
    url: unescape(source.slice(labelEnd + 2, urlEnd)).trim(), end: urlEnd + 1 };
}

function inlineContent(text: string): ReactNode[] {
  const source = text
    .replace(/\$(\([RS]\)-\(-\))\$\s+-/g, (_match, math: string) => `$${math}$-`)
    .replace(/\$\s+([,;:])/g, (_match, punctuation: string) => `$${punctuation}`);
  const parts: ReactNode[] = [];
  const pattern = /(!?\[)|\$((?:\\.|[^$\n])+)\$|`([^`]+)`|\*\*([^*]+)\*\*/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > cursor) parts.push(source.slice(cursor, match.index));
    if (match[1] !== undefined) {
      const link = markdownLinkAt(source, match.index);
      if (!link) { parts.push(match[0]); cursor = pattern.lastIndex; continue; }
      pattern.lastIndex = link.end;
      const literal = source.slice(match.index, link.end);
      if (link.image) {
        const src = link.url.startsWith("/api/v1/artifacts/") ? link.url : "";
        parts.push(src ? <img key={`${match.index}-img`} alt={link.label} src={src} loading="lazy" /> : literal);
      } else {
        const href = /^(?:https?:\/\/|\/api\/v1\/)/i.test(link.url) ? link.url : "";
        parts.push(href ? <a key={`${match.index}-link`} href={href} target="_blank" rel="noreferrer">{link.label}</a> : literal);
      }
    } else if (match[2] !== undefined) {
      parts.push(<span className="inline-math" key={`${match.index}-math`} title={match[2]}>{readableInlineMath(match[2])}</span>);
    } else if (match[3] !== undefined) {
      parts.push(<code key={`${match.index}-code`}>{match[3]}</code>);
    } else {
      parts.push(<strong key={`${match.index}-strong`}>{match[4]}</strong>);
    }
    cursor = pattern.lastIndex;
  }
  if (cursor < source.length) parts.push(source.slice(cursor));
  // Editable manuscripts identify citations/figure references in text nodes.
  // Failed link candidates must not split ordinary prose into separate nodes.
  const merged: ReactNode[] = [];
  for (const part of parts) {
    const last = merged.length - 1;
    if (typeof part === "string" && typeof merged[last] === "string") merged[last] += part;
    else merged.push(part);
  }
  return merged;
}

export function MarkdownView({ content, empty }: { content?: string | null; empty?: string }) {
  const { text } = useUiText();
  const emptyMessage = empty || text("暂无内容。", "No content yet.");
  const source = String(content || "").replace(/<!--[\s\S]*?-->/g, "").trim();
  if (!source) return <div className="empty-state">{emptyMessage}</div>;
  const blocks = source.split(/\n\s*\n/);
  return (
    <article className="markdown-view">
      {blocks.map((block, index) => {
        const value = block.trim();
        const tableRows = value.split("\n").map((line) => line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|")));
        if (tableRows.length >= 2 && tableRows[0].length > 1
          && tableRows[1].length === tableRows[0].length
          && tableRows[1].every((cell) => /^:?-{3,}:?$/.test(cell))
          && tableRows.slice(2).every((row) => row.length === tableRows[0].length)) {
          return <div className="markdown-table-scroll" key={index} tabIndex={0}>
            <table>
              <thead><tr>{tableRows[0].map((cell, column) => <th scope="col" key={column}>{inlineContent(cell)}</th>)}</tr></thead>
              <tbody>{tableRows.slice(2).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => <td key={column}>{inlineContent(cell)}</td>)}</tr>)}</tbody>
            </table>
          </div>;
        }
        const heading = /^(#{1,3})\s+(.+)$/.exec(value);
        if (heading) {
          const children = inlineContent(heading[2]);
          if (heading[1].length === 1) return <h1 key={index}>{children}</h1>;
          if (heading[1].length === 2) return <h2 key={index}>{children}</h2>;
          return <h3 key={index}>{children}</h3>;
        }
        if (value.split("\n").every((line) => /^\s*[-*]\s+/.test(line))) {
          return <ul key={index}>{value.split("\n").map((line, itemIndex) => <li key={itemIndex}>{inlineContent(line.replace(/^\s*[-*]\s+/, ""))}</li>)}</ul>;
        }
        if (value.split("\n").every((line) => /^\s*\d+[.)]\s+/.test(line))) {
          return <ol key={index}>{value.split("\n").map((line, itemIndex) => <li key={itemIndex}>{inlineContent(line.replace(/^\s*\d+[.)]\s+/, ""))}</li>)}</ol>;
        }
        return <p key={index}>{value.split("\n").map((line, lineIndex) => <span key={lineIndex}>{lineIndex ? <br /> : null}{inlineContent(line)}</span>)}</p>;
      })}
    </article>
  );
}
