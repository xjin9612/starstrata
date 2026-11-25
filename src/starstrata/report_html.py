"""把 reports.render() 產生的週報、月報 Markdown 轉成 dashboard 顯示用的 HTML。

報告格式由 render() 固定產生，只轉換它會用到的語法，不引入 Markdown 套件：
- 標題 `#`（不輸出，由頁面顯示）、`##`、`###`，層級各下移一級。
- 段落、`- ` 清單、`> ` 引言、表格（對齊列的 `---:` 靠右）。
- 行內：反斜線跳脫、行內 code、`[owner/name](https://github.com/owner/name)` 連結。

所有文字一律跳脫。只有網址與文字都是同一個 GitHub repo 的連結會轉成站內詳細頁連結，
其他連結只留文字，因為解讀內容源自 README，不能在頁面產生外部連結。
"""

from __future__ import annotations

import html
import re

from starstrata.reports import GAINERS_HEADINGS

_REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_LINK = re.compile(r"\[([^\]\\]+)\]\((https://[^)\s]+)\)")
_ALIGN = re.compile(r"\s*:?-{3,}:?\s*")
_BLOCK_START = re.compile(r"(#{1,3} |\||- |> )")


def _inline(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(html.escape(text[i + 1]))
            i += 2
            continue
        if c == "`":
            end = text.find("`", i + 1)
            if end > i + 1:
                out.append(f"<code>{html.escape(text[i + 1 : end])}</code>")
                i = end + 1
                continue
        if c == "[" and (link := _LINK.match(text, i)):
            label, url = link.group(1), link.group(2)
            repo = _REPO_URL.fullmatch(url)
            if repo and f"{repo.group(1)}/{repo.group(2)}" == label:
                owner, name = html.escape(repo.group(1)), html.escape(repo.group(2))
                out.append(
                    f'<a href="#/repo/{owner}/{name}"><span class="own">{owner}/</span>{name}</a>'
                )
            else:
                out.append(html.escape(label))
            i = link.end()
            continue
        out.append(html.escape(c))
        i += 1
    return "".join(out)


def _cells(row: str) -> list[str]:
    """以沒有被反斜線跳脫的 | 切開表格列。"""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells: list[str] = []
    current = ""
    i = 0
    while i < len(row):
        if row[i] == "\\" and i + 1 < len(row):
            current += row[i : i + 2]
            i += 2
            continue
        if row[i] == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += row[i]
        i += 1
    cells.append(current.strip())
    return cells


def _table(rows: list[list[str]]) -> str:
    head, body = rows[0], rows[2:]
    aligns = rows[1] if len(rows) > 1 else []
    right = (
        [a.strip().endswith(":") for a in aligns]
        if all(_ALIGN.fullmatch(a) for a in aligns)
        else []
    )

    def cls(k: int) -> str:
        return ' class="num"' if k < len(right) and right[k] else ""

    th = "".join(f'<th scope="col"{cls(k)}>{_inline(h)}</th>' for k, h in enumerate(head))
    trs = "".join(
        "<tr>" + "".join(f"<td{cls(k)}>{_inline(c)}</td>" for k, c in enumerate(r)) + "</tr>"
        for r in body
    )
    table = f'<table class="rp-data"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'
    return f'<div class="rp-table">{table}</div>'


def to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("# "):
            i += 1
        elif line.startswith("### "):
            out.append(f"<h4>{_inline(line[4:])}</h4>")
            i += 1
        elif line.startswith("## "):
            out.append(f"<h3>{_inline(line[3:])}</h3>")
            i += 1
        elif line.startswith("|"):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append(_table(rows))
        elif line.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
        elif line.startswith("> "):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(_inline(lines[i][2:]))
                i += 1
            out.append(f"<blockquote>{'<br>'.join(quote)}</blockquote>")
        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() and not _BLOCK_START.match(lines[i]):
                para.append(_inline(lines[i]))
                i += 1
            out.append(f"<p>{' '.join(para)}</p>")
    return "\n".join(out)


def title(md: str) -> str | None:
    """報告的 `#` 標題；沒有時回傳 None。"""
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def top_repos(md: str, n: int = 3) -> list[str]:
    """「Star 增加最多」段落表格的前 n 個 repo；找不到段落時回傳空清單。"""
    headings = "|".join(re.escape(h) for h in GAINERS_HEADINGS)
    section = re.search(rf"^## (?:{headings})\s*$(.*?)(?:^## |\Z)", md, re.S | re.M)
    if not section:
        return []
    return re.findall(
        r"\[([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\]\(https://github\.com/", section.group(1)
    )[:n]
