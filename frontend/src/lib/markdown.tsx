import type { ReactNode } from "react";

// Minimal, safe Markdown renderer for the help panel (headings, paragraphs, lists,
// bold and inline code). Builds React elements; never injects HTML.

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    out.push(
      tok.startsWith("**") ? (
        <strong key={`${key}-${i++}`}>{tok.slice(2, -2)}</strong>
      ) : (
        <code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>
      ),
    );
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) {
      blocks.push(<p key={`p${blocks.length}`}>{inline(para.join(" "), `p${blocks.length}`)}</p>);
      para = [];
    }
    if (list.length) {
      const k = `l${blocks.length}`;
      blocks.push(
        <ul key={k}>
          {list.map((li, i) => (
            <li key={i}>{inline(li, `${k}-${i}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (line.startsWith("<!--")) continue;
    if (/^#{1,4} /.test(line)) {
      flush();
      const level = line.indexOf(" ");
      const text = line.slice(level + 1);
      const k = `h${blocks.length}`;
      blocks.push(level <= 2 ? <h2 key={k}>{text}</h2> : <h3 key={k}>{text}</h3>);
    } else if (/^[-*] /.test(line)) {
      if (para.length) flush();
      list.push(line.slice(2));
    } else if (!line.trim()) {
      flush();
    } else {
      if (list.length) flush();
      para.push(line.trim());
    }
  }
  flush();
  return <>{blocks}</>;
}

export function extractHelp(doc: string): string {
  const start = doc.indexOf("<!-- help:start -->");
  const end = doc.indexOf("<!-- help:end -->");
  return start >= 0 && end > start ? doc.slice(start + "<!-- help:start -->".length, end) : doc;
}
