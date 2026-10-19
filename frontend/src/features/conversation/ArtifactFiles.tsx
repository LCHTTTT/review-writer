import { useUiText } from "../../i18n/useUiText";
import type { ResultCard } from "./types";

function fileBadge(format: string): string {
  const map: Record<string, string> = { json: "DOC", md: "MD", pdf: "PDF", docx: "DOCX", png: "IMG", jpg: "IMG", jpeg: "IMG", webp: "IMG" };
  return map[format] || (format || "FILE").toUpperCase().slice(0, 5);
}

const readableIndexes = new Set(["planning/selected_outline.json", "blueprint/section_blueprint.json",
  "sections/section_drafts.json", "matrix/literature_matrix.json", "figures/manifest.json", "final/front_matter.json"]);

export function ArtifactFiles({ ids, artifacts, onOpen }: { ids: string[]; artifacts: ResultCard[]; onOpen?: (card: ResultCard) => void }) {
  const { text } = useUiText();
  // Only user documents: JSON indexes open as readable presentations, never
  // raw downloads. Unknown IDs and internal diagnostic records are not files.
  const readable = [...new Set(ids)].map(id => artifacts.find(a => a.id === id)).filter((card): card is ResultCard =>
    !!card && (card.format !== "json" || (readableIndexes.has(card.logical_name) && !!onOpen)));
  if (!readable.length) return null;
  return <div className="conversation-artifacts">
    {readable.map(card => <div className="conversation-file-card" key={card.id}>
          <span className="conversation-file-icon">{fileBadge(card.format)}</span>
          <span className="conversation-file-meta">
            <strong>{card.label}</strong>
            <small>{card.current ? text("已保存版本", "Saved version") : text("历史版本", "History")}</small>
          </span>
          {onOpen ? <button type="button" className="button button-quiet" aria-label={text(`查看 ${card.label}`, `View ${card.label}`)} onClick={() => onOpen(card)}>{text("查看", "View")}</button> : null}
          {card.format !== "json" ? <a className="button button-secondary" href={card.url}
            download={`${card.label}.${card.format}`} aria-label={text(`下载 ${card.label}`, `Download ${card.label}`)}>
            {text("下载", "Download")}</a> : null}
        </div>)}
  </div>;
}
