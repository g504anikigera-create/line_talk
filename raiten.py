#!/usr/bin/env python3
"""静岡県のパチンコホール来店（タレント・演者）予定を DMMぱちタウン から集め、
今日から N 日先までをまとめて LINE Messaging API で送る。

環境変数:
  LINE_CHANNEL_ACCESS_TOKEN  チャネルアクセストークン（長期）
  LINE_TO                    送り先 ID（U… ユーザーID / C… グループID）。カンマ区切りで複数可
  DAYS_AHEAD                 何日先までまとめるか（既定 31）
  DRY_RUN                    "1" なら送信せずに本文を表示するだけ
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from bs4 import BeautifulSoup

JST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://p-town.dmm.com/schedules/shizuoka"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
WD = "月火水木金土日"

DATE_RE = re.compile(r"実施日\s*[:：]\s*(\d{4})/(\d{1,2})/(\d{1,2})(?:\s*([0-9]{1,2}:[0-9]{2})\s*[〜~～-]\s*([0-9]{1,2}:[0-9]{2}))?")
ADDR_RE = re.compile(r"^静岡県")
# 「○○来店」の○○を来店者として拾う（注意書きの「来店の際は」は除外）
RAITEN_RE = re.compile(r"([^\s「」『』【】※。、&＆!！☆★♪♫]{1,30}?)\s*(?:さん)?\s*来店(?!の際)")
PAREN_RE = re.compile(r"[（(]([^）)]{1,20})[）)]")


@dataclass(frozen=True)
class Visit:
    date: dt.date
    talent: str
    hall: str
    city: str
    time: str

    def key(self):
        return (self.date, self.talent, self.hall)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def city_of(addr: str) -> str:
    m = re.match(r"静岡県((?:[^市郡]+?郡)?[^市町村]+?[市町村])", addr)
    return m.group(1) if m else ""


def extract_talent(lines: list[str]) -> str | None:
    names: list[str] = []
    for ln in lines:
        head = ln.split("※")[0]
        for m in RAITEN_RE.finditer(head):
            n = m.group(1).strip("・:：")
            if n and n not in names:
                names.append(n)
        # 例: 妖-あやかし-【2店舗回り】（てつ）
        if "【" in ln or "回り" in ln:
            for m in PAREN_RE.finditer(ln):
                n = m.group(1).strip()
                if n and n not in names:
                    names.append(n)
    return "・".join(names) if names else None


def parse(html: str) -> list[Visit]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    # 「実施日:」を含む最小の要素を1件の掲載ブロックとみなす
    blocks = []
    for node in soup.find_all(string=re.compile("実施日")):
        el = node.parent
        while el is not None and el.name not in ("body", "html"):
            txt = el.get_text("\n", strip=True)
            if ADDR_RE.search(txt.replace("\n", " ")) or "静岡県" in txt:
                if txt.count("実施日") == 1:
                    blocks.append(el)
                break
            el = el.parent
    visits: list[Visit] = []
    seen_ids = set()
    for el in blocks:
        if id(el) in seen_ids:
            continue
        seen_ids.add(id(el))
        lines = [l for l in el.get_text("\n", strip=True).split("\n") if l and l != "PR"]
        text = " ".join(lines)
        m = DATE_RE.search(text)
        if not m:
            continue
        y, mo, d, t1, t2 = m.groups()
        date = dt.date(int(y), int(mo), int(d))
        addr_i = next((i for i, l in enumerate(lines) if ADDR_RE.match(l)), None)
        addr = lines[addr_i] if addr_i is not None else ""
        if addr_i is not None and addr_i > 0:
            hall = lines[addr_i - 1]
        else:
            hall = lines[0]
        body = [l for l in lines if not l.startswith("実施日") and l not in (hall, addr)]
        talent = extract_talent(body)
        if not talent:
            continue
        visits.append(Visit(date, talent, hall, city_of(addr), f"{t1}〜{t2}" if t1 else ""))
    return visits


def collect(today: dt.date, end: dt.date) -> list[Visit]:
    urls = []
    d = today.replace(day=1)
    while d <= end:
        urls.append(f"{BASE}/{MONTHS[d.month - 1]}_{d.year}")
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    urls.append(BASE)  # 月別ページが取れない場合の保険
    found: dict = {}
    ok_pages = 0
    for base in urls:
        last_count = -1
        for page in range(1, 31):
            url = base if page == 1 else f"{base}?page={page}"
            try:
                html = fetch(url)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                print(f"[warn] {url}: HTTP {e.code}", file=sys.stderr)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {url}: {e}", file=sys.stderr)
                break
            ok_pages += 1
            vs = parse(html)
            before = len(found)
            for v in vs:
                found.setdefault(v.key(), v)
            has_next = f"page={page + 1}" in html
            print(f"[info] {url}: {len(vs)}件（来店）", file=sys.stderr)
            time.sleep(1.0)
            if not has_next or (len(found) == before and last_count == 0):
                break
            last_count = len(found) - before
    if ok_pages == 0:
        raise SystemExit("来店情報ページを1ページも取得できませんでした。サイトの状態を確認してください。")
    return sorted((v for v in found.values() if today <= v.date <= end),
                  key=lambda v: (v.date, v.time, v.hall))


def build_messages(visits: list[Visit], today: dt.date, end: dt.date) -> list[str]:
    head = f"【静岡 来店予定】{today.month}/{today.day}〜{end.month}/{end.day}　全{len(visits)}件"
    if not visits:
        return [head + "\n\n期間内のタレント・演者の来店予定は見つかりませんでした。"]
    chunks, cur = [], head
    last = None
    for v in visits:
        part = ""
        if v.date != last:
            part += f"\n\n■{v.date.month}/{v.date.day}({WD[v.date.weekday()]})"
            last = v.date
        where = f"{v.hall}（{v.city}）" if v.city else v.hall
        part += f"\n・{v.talent}｜{where}" + (f" {v.time}" if v.time else "")
        if len(cur) + len(part) > 4800:
            chunks.append(cur)
            cur = "（つづき）" + part
        else:
            cur += part
    chunks.append(cur + "\n\n出典: DMMぱちタウン https://p-town.dmm.com/schedules/shizuoka")
    return chunks


def push(token: str, to: str, texts: list[str]) -> None:
    for i in range(0, len(texts), 5):  # 1リクエスト最大5吹き出し
        body = json.dumps({"to": to, "messages": [{"type": "text", "text": t} for t in texts[i:i + 5]]}).encode()
        req = urllib.request.Request("https://api.line.me/v2/bot/message/push", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"[info] LINE送信OK → {to[:6]}… ({r.status})", file=sys.stderr)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"LINE送信に失敗しました（{to[:6]}…）: HTTP {e.code} {e.read().decode(errors='replace')}")


def main() -> None:
    days = int(os.environ.get("DAYS_AHEAD", "31"))
    today = dt.datetime.now(JST).date()
    end = today + dt.timedelta(days=days)
    visits = collect(today, end)
    texts = build_messages(visits, today, end)
    if os.environ.get("DRY_RUN") == "1":
        print("\n\n----- (次の吹き出し) -----\n\n".join(texts))
        return
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    targets = [t.strip() for t in os.environ.get("LINE_TO", "").split(",") if t.strip()]
    if not token or not targets:
        raise SystemExit("LINE_CHANNEL_ACCESS_TOKEN と LINE_TO を設定してください。")
    for to in targets:
        push(token, to, texts)


if __name__ == "__main__":
    main()
