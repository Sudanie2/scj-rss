#!/usr/bin/env python3
"""日本学術会議「提言・報告等」の新着を RSS 2.0 (rss.xml) に変換する。

取得元（優先順）
  1. 提言・報告等の検索結果ページ（年を指定すると最大50件がHTMLで返る）
  2. トップページの「最新情報 > 提言・報告等」タブ（直近4件）…1が失敗したときの予備

標準ライブラリだけで動くので、pip install は不要。
"""
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

BASE = "https://www.scj.go.jp"
TOP_URL = BASE + "/index.html"
SEARCH_URL = BASE + "/ja/info/search/index.html"
OUTPUT = "rss.xml"
MAX_ITEMS = 50
UA = "Mozilla/5.0 (compatible; scj-rss-bot/1.0; +https://github.com/)"
JST = timezone(timedelta(hours=9))


def fetch(url: str, retries: int = 3, wait: int = 10) -> str:
    """URL を取得して文字列で返す。失敗したら数回リトライ。"""
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            print(f"fetch failed ({i + 1}/{retries}): {url} -> {e}", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def clean(text: str) -> str:
    """タグを除去し、空白をそろえて、HTMLエンティティを戻す。"""
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_search(page: str) -> list[dict]:
    """検索結果ページ（c-article-item）から記事を抜き出す。"""
    items = []
    pattern = re.compile(
        r'<a\s+class="c-article-item"\s+href="([^"]+)"\s*>(.*?)</a>', re.S
    )
    for href, body in pattern.findall(page):
        date = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', body)
        title = re.search(
            r'class="c-article-item__link"\s*>\s*<span>(.*?)</span>', body, re.S
        )
        tag = re.search(r'<span class="c-tag c-tag--fill">(.*?)</span>', body, re.S)
        if not (date and title):
            continue
        items.append(
            {
                "date": date.group(1),
                "category": clean(tag.group(1)) if tag else "",
                "title": clean(title.group(1)),
                "link": urllib.parse.urljoin(BASE, html.unescape(href)),
            }
        )
    return items


def parse_top(page: str) -> list[dict]:
    """トップページの提言・報告等タブ（c-info-item）から記事を抜き出す。"""
    items = []
    pattern = re.compile(r'<div class="c-info-item">(.*?)</div>\s*</div>\s*</li>', re.S)
    for body in pattern.findall(page):
        date = re.search(r"<time[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</time>", body)
        tag = re.search(r'class="c-info-item__tag"\s*>(.*?)</span>', body, re.S)
        link = re.search(
            r'<a\s+href="([^"]+)"\s+class="c-info-item__title"[^>]*>(.*?)</a>',
            body,
            re.S,
        )
        if not (date and link):
            continue
        title = clean(link.group(2))
        # 「…を公表しました。」の外側かぎ括弧を外して検索結果側の表記に近づける
        title = re.sub(r"を公表しました。?$", "", title).strip()
        if title.startswith("「") and title.endswith("」"):
            title = title[1:-1]
        items.append(
            {
                "date": date.group(1),
                "category": clean(tag.group(1)) if tag else "",
                "title": title,
                "link": urllib.parse.urljoin(BASE, html.unescape(link.group(1))),
            }
        )
    return items


def collect() -> list[dict]:
    year = datetime.now(JST).year
    params = {
        "query": "", "title": "", "type": "", "issuer1": "", "issuer2": "",
        "date-filter-type": "year-month", "period": "", "year": str(year),
        "month": "", "limit": "50", "sort": "date_desc",
    }
    items: list[dict] = []
    try:
        page = fetch(SEARCH_URL + "?" + urllib.parse.urlencode(params))
        items = parse_search(page)
        print(f"search page: {len(items)} items")
    except Exception as e:  # 予備ルートに切り替える
        print(f"search route failed: {e}", file=sys.stderr)

    if not items:
        page = fetch(TOP_URL)
        items = parse_top(page)
        print(f"top page (fallback): {len(items)} items")
    return items


def build_rss(items: list[dict]) -> str:
    # 重複を除き、新しい順（同日ならページ上の並びを維持）に並べる
    seen, uniq = set(), []
    for it in items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        uniq.append(it)
    uniq.sort(key=lambda x: x["date"], reverse=True)  # sort は安定ソート
    uniq = uniq[:MAX_ITEMS]

    newest = datetime.strptime(uniq[0]["date"], "%Y-%m-%d").replace(tzinfo=JST)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        "<title>日本学術会議 提言・報告等</title>",
        f"<link>{BASE}/ja/info/index.html</link>",
        "<description>日本学術会議の提言・報告等の新着情報（非公式の自動生成フィード）</description>",
        "<language>ja</language>",
        # 実行のたびに変わらないよう、最新記事の日付を使う（無駄なコミットを防ぐ）
        f"<lastBuildDate>{format_datetime(newest)}</lastBuildDate>",
    ]
    for it in uniq:
        d = datetime.strptime(it["date"], "%Y-%m-%d").replace(tzinfo=JST)
        title = f"【{it['category']}】{it['title']}" if it["category"] else it["title"]
        out.append(
            "<item>"
            f"<title>{html.escape(title)}</title>"
            f"<link>{html.escape(it['link'])}</link>"
            f"<pubDate>{format_datetime(d)}</pubDate>"
            f"<guid isPermaLink=\"true\">{html.escape(it['link'])}</guid>"
            + (f"<category>{html.escape(it['category'])}</category>" if it["category"] else "")
            + "</item>"
        )
    out.append("</channel></rss>")
    return "\n".join(out) + "\n"


def main() -> int:
    items = collect()
    if not items:
        # 0件のときは rss.xml を上書きせず失敗扱いにする（サイト改修の検知用）
        print("ERROR: 記事を1件も取得できませんでした。ページ構造が変わった可能性があります。", file=sys.stderr)
        return 1
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(build_rss(items))
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
