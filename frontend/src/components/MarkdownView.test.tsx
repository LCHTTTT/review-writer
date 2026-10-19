import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkdownView, readableInlineMath } from "./MarkdownView";

describe("MarkdownView chemistry notation", () => {
  afterEach(() => cleanup());

  it("renders common MinerU inline chemistry as readable text", () => {
    render(
      <MarkdownView content={String.raw`$Pd_{2}(dba)_{3}\cdot CHCl_{3}$ , $(S)-(-)$ -MeO-MOP, $-78^{\circ}C$`} />,
    );

    expect(screen.getByText(/Pd₂\(dba\)₃·CHCl₃/)).toBeInTheDocument();
    expect(document.body).toHaveTextContent("(S)-(−)-MeO-MOP");
    expect(document.body).toHaveTextContent("−78 °C");
    expect(document.body).not.toHaveTextContent("Pd_{2}");
  });

  it("keeps unsupported commands visible instead of throwing", () => {
    expect(readableInlineMath(String.raw`\unknown{CH}_{3}`)).toBe("unknownCH₃");
  });

  it("renders comparison tables with formulas, citations, and escaped cell pipes", () => {
    render(<MarkdownView content={String.raw`Table 1. Systems.

| System | Conditions | Source |
| --- | --- | --- |
| A \| B | ZnI₂, **25 °C** | [1, 2] |

<!-- comparison_table: private evidence metadata -->`} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(3);
    expect(screen.getByRole("cell", { name: "A | B" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "ZnI₂, 25 °C" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("private evidence");
  });

  it("keeps unsafe links inert inside table cells", () => {
    render(<MarkdownView content={"| System | Link |\n| --- | --- |\n| A | [click](javascript:alert) |"} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders the Human manuscript image with bracketed chemical names", () => {
    const label = "Scheme 5 reaction and oxidation aforded biologically active ¹,³·-dihydro-[2, 1]benzoxaphosphole 1-oxide derivatives 11 in moderate to good yields";
    const src = "/api/v1/artifacts/6f9f0df2-1375-45e8-b822-ab8e8164201a/content";
    render(<MarkdownView content={`![${label}](${src})\n*Figure 2. ${label}. Adapted from Ref. 11.*`} />);
    expect(screen.getByRole("img", { name: label })).toHaveAttribute("src", src);
    expect(document.body).not.toHaveTextContent(`![${label}]`);
    expect(document.body).toHaveTextContent("Adapted from Ref. 11.");
  });

  it("supports nested and escaped brackets without swallowing adjacent content", () => {
    render(<MarkdownView content={String.raw`![Complex [Pd[Cl]₂]](/api/v1/artifacts/a/content) and ![B \[2, 1\]](/api/v1/artifacts/b/content), [reference [1]](https://example.org/a(b)) [11] **Result** $H_{2}O$`} />);
    expect(screen.getByRole("img", { name: "Complex [Pd[Cl]₂]" })).toHaveAttribute("src", "/api/v1/artifacts/a/content");
    expect(screen.getByRole("img", { name: "B [2, 1]" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "reference [1]" })).toHaveAttribute("href", "https://example.org/a(b)");
    expect(document.body).toHaveTextContent("[11]");
    expect(screen.getByText("Result").tagName).toBe("STRONG");
    expect(screen.getByText("H₂O")).toBeInTheDocument();
  });

  it("keeps incomplete syntax and untrusted destinations as text", () => {
    render(<MarkdownView content={"![broken [label]](unfinished\n\n![remote [2]](https://example.org/image.png) [unsafe [2]](javascript:alert(1))\n\n![empty](/api/v1/artifacts/a/content)"} />);
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent("![broken [label]](unfinished");
    expect(document.body).toHaveTextContent("![remote [2]](https://example.org/image.png)");
  });

  it("preserves the ordinary empty state and an image with empty alt text", () => {
    const { rerender, container } = render(<MarkdownView content="" empty="Empty manuscript" />);
    expect(screen.getByText("Empty manuscript")).toBeInTheDocument();
    rerender(<MarkdownView content="![](/api/v1/artifacts/a/content)" />);
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
  });
});
