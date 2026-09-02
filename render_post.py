#!/usr/bin/env python3
"""Render a Markdown post into a standalone styled HTML page for jac-76.github.io.

Zero dependencies (stdlib only). Handles the subset of Markdown these posts use:
YAML front matter, ATX headings, fenced code blocks, blockquotes, GFM tables,
ordered/unordered lists (incl. a fenced block nested in a list item), thematic
breaks, and inline code / bold / italic / links.

    ./render_post.py ../Work/blog/foo.md blog/foo.html
"""
import html
import re
import sys
from pathlib import Path

PALETTE_CSS = """
  :root{--bg:#0f1115;--card:#171a21;--border:#262b36;--text:#e6e9ef;
    --muted:#9aa3b2;--accent:#4fd1c5;--mono:"JetBrains Mono","Fira Code",ui-monospace,monospace}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);
    color:var(--text);line-height:1.65;max-width:760px;margin:0 auto;padding:3rem 1.5rem 6rem}
  a{color:var(--accent)}
  h1{font-size:2rem;line-height:1.25;margin:.5rem 0 1rem}
  h2{font-size:1.3rem;margin:2.5rem 0 .75rem;color:var(--accent);font-family:var(--mono)}
  h3{font-size:1.05rem;margin:1.75rem 0 .5rem}
  p,ul,ol{margin:0 0 1rem}
  ul,ol{padding-left:1.4rem}
  li{margin:.3rem 0}
  blockquote{border-left:3px solid var(--accent);padding:.5rem 0 .5rem 1rem;margin:0 0 1.25rem;
    color:var(--muted)}
  blockquote p:last-child{margin-bottom:0}
  code{font-family:var(--mono);font-size:.87em;background:var(--card);border:1px solid var(--border);
    border-radius:5px;padding:.1rem .35rem}
  pre{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:1rem;
    overflow-x:auto;margin:0 0 1.25rem;font-size:.82rem;line-height:1.5}
  pre code{background:none;border:0;padding:0;font-size:1em}
  table{border-collapse:collapse;width:100%;margin:0 0 1.25rem;font-size:.9rem;display:block;overflow-x:auto}
  th,td{border:1px solid var(--border);padding:.45rem .7rem;text-align:left;vertical-align:top}
  th{background:var(--card);font-family:var(--mono);font-size:.82rem}
  hr{border:0;border-top:1px solid var(--border);margin:2.5rem 0}
  .backlink{font-family:var(--mono);font-size:.85rem;margin-bottom:2rem;display:inline-block}
  .meta{color:var(--muted);font-size:.9rem;margin:-.5rem 0 2rem}
"""

LINK_RE = re.compile(r"\[([^\]]+)\]\s*\(\s*(https?://[^)\s]+)\s*\)")


def inline(text):
    # code spans first, on escaped text, so ** / * / links don't touch code
    out, last = [], 0
    for m in re.finditer(r"`[^`]+`", text):
        out.append(html.escape(text[last:m.start()]))
        out.append(f"<code>{html.escape(m.group(0)[1:-1])}</code>")
        last = m.end()
    out.append(html.escape(text[last:]))
    s = "".join(out)
    s = LINK_RE.sub(r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", s)
    return s


def split_front_matter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm = text[4:end]
            body = text[end + 5:]
            meta = {}
            for line in fm.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"')
            return meta, body
    return {}, text


def render(md):
    meta, body = split_front_matter(md)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)  # drop HTML comments
    lines = body.split("\n")
    out = []
    i, n = 0, len(lines)
    para = []

    def close_para():
        if para:
            out.append(f"<p>{inline(' '.join(para).strip())}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code
        if stripped.startswith("```"):
            close_para()
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue

        # blank
        if not stripped:
            close_para()
            i += 1
            continue

        # thematic break
        if stripped == "---":
            close_para()
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"(#{1,4})\s+(.*)", stripped)
        if m:
            close_para()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            close_para()
            q = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].lstrip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(q).strip())}</p></blockquote>")
            continue

        # table
        if "|" in stripped and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            close_para()
            def cells(row):
                row = row.strip().strip("|")
                return [c.strip() for c in row.split("|")]
            head = cells(lines[i]); i += 2
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            while i < n and "|" in lines[i]:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(lines[i])) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        # lists (ordered / unordered), with nested fenced blocks + continuation lines
        lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", line)
        if lm:
            close_para()
            ordered = bool(re.match(r"\d+\.", lm.group(2)))
            tag = "ol" if ordered else "ul"
            marker_re = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)")
            out.append(f"<{tag}>")
            while i < n:
                lm = marker_re.match(lines[i])
                if not lm:
                    if lines[i].strip() == "":
                        # skip blank(s); keep the list open if what follows is
                        # another marker or indented continuation content
                        j = i + 1
                        while j < n and lines[j].strip() == "":
                            j += 1
                        if j < n and (marker_re.match(lines[j])
                                      or lines[j].startswith(("   ", "\t"))):
                            i = j
                            continue
                    break
                item = [lm.group(3)]
                extra = []
                i += 1
                while i < n:
                    cur = lines[i]
                    if re.match(r"^(\s*)([-*]|\d+\.)\s+", cur):
                        break
                    if cur.strip() == "":
                        j = i + 1
                        if j < n and (lines[j].startswith("   ") or lines[j].startswith("\t")) and lines[j].strip():
                            i += 1
                            continue
                        break
                    body_line = cur.strip()
                    if body_line.startswith("```"):
                        i += 1
                        code = []
                        while i < n and not lines[i].strip().startswith("```"):
                            code.append(lines[i].strip())
                            i += 1
                        i += 1
                        extra.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                    else:
                        item.append(body_line)
                        i += 1
                text = inline(" ".join(item).strip())
                out.append(f"<li>{text}{''.join(extra)}</li>")
            out.append(f"</{tag}>")
            continue

        # default: paragraph text
        para.append(stripped)
        i += 1

    close_para()
    return meta, "\n".join(out)


def page(meta, body_html):
    title = meta.get("title", "Post")
    desc = meta.get("description", "")
    tags = meta.get("tags", "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — jac-76</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚡</text></svg>">
<style>{PALETTE_CSS}</style>
</head>
<body>
<a class="backlink" href="/">← jac-76</a>
{body_html}
<hr>
<p class="meta">Written by jac-76 · <a href="https://github.com/jac-76">github.com/jac-76</a></p>
</body>
</html>
"""


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    meta, body_html = render(src.read_text())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(page(meta, body_html))
    print(f"wrote {dst} ({len(dst.read_text())} bytes)")
