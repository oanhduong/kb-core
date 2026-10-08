// Markdown (GFM) + biến "[n]" thành chip trích dẫn bấm được.
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Children, Fragment, isValidElement, memo, type ReactNode } from "react";
import type { Citation } from "@/lib/types";

const CITE_RE = /\[(\d{1,2})\]/g;

function withChips(children: ReactNode, onCite: (n: number) => void, valid: Set<number> | null): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") {
      const parts: ReactNode[] = [];
      let last = 0;
      let m: RegExpExecArray | null;
      CITE_RE.lastIndex = 0;
      while ((m = CITE_RE.exec(child))) {
        const n = Number(m[1]);
        if (valid && !valid.has(n)) continue;
        parts.push(child.slice(last, m.index));
        parts.push(
          <button key={`${m.index}-${n}`} type="button" className="cite-chip" title={`[${n}]`} onClick={(e) => { e.stopPropagation(); onCite(n); }}>
            {n}
          </button>,
        );
        last = m.index + m[0].length;
      }
      if (!parts.length) return child;
      parts.push(child.slice(last));
      return <Fragment>{parts}</Fragment>;
    }
    if (isValidElement<{ children?: ReactNode }>(child) && child.props.children && child.type !== "code") {
      return { ...child, props: { ...child.props, children: withChips(child.props.children, onCite, valid) } };
    }
    return child;
  });
}

export const Markdown = memo(function Markdown({ text, citations, onCite, className, streaming }: { text: string; citations?: Citation[]; onCite?: (c: Citation | null, n: number) => void; className?: string; streaming?: boolean }) {
  const valid = citations ? new Set(citations.map((c) => c.n)) : null;
  const handle = (n: number) => onCite?.(citations?.find((c) => c.n === n) || null, n);
  const wrap = (node: ReactNode) => (onCite ? withChips(node, handle, valid) : node);
  return (
    <div className={`prose-kb ${className || ""} ${streaming ? "caret" : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p>{wrap(children)}</p>,
          li: ({ children }) => <li>{wrap(children)}</li>,
          td: ({ children }) => <td>{wrap(children)}</td>,
          th: ({ children }) => <th>{wrap(children)}</th>,
          h1: ({ children }) => <h1>{wrap(children)}</h1>,
          h2: ({ children }) => <h2>{wrap(children)}</h2>,
          h3: ({ children }) => <h3>{wrap(children)}</h3>,
          h4: ({ children }) => <h4>{wrap(children)}</h4>,
          a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
        }}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
