#!/usr/bin/env python3
"""静岡・愛知・神奈川のパチンコホールに来店するセクシー女優の予定を P-WORLD から集め、
今日から N 日先までをまとめて LINE Messaging API で送る。

セクシー女優かどうかの判定（どれか1つに当てはまれば対象）:
  1. 掲載の題名に「セクシー女優」「AV女優」などの肩書きがある
  2. actresses.txt に名前が書いてある（1行1名。自分で追加・削除できる）
  3. 日本語版ウィキペディアで、その名前の記事が「AV女優」のカテゴリに入っている

環境変数:
  LINE_CHANNEL_ACCESS_TOKEN  チャネルアクセストークン（長期）
  LINE_TO                    送り先 ID（U… ユーザーID / C… グループID）。カンマ区切りで複数可
  PERIOD_DAYS                何日先までまとめるか（既定 31 ≒ 1ヶ月）
  PREFS                      対象エリア（既定 "shizuoka,aichi,kanagawa"）
  DRY_RUN                    "1" なら送信せずに本文を表示するだけ
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

JST = dt.timezone(dt.timedelta(hours=9))
PWORLD = "https://www.p-world.co.jp/hall/interviews/prefs/{pref}"
PREF_NAMES = {"shizuoka": "静岡", "aichi": "愛知", "kanagawa": "神奈川"}
PREF_KANJI = {"shizuoka": "静岡県", "aichi": "愛知県", "kanagawa": "神奈川県"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
WIKI_UA = "raiten-line-notifier/1.0 (personal use; GitHub Actions)"
WD = "月火水木金土日"
HERE = Path(__file__).resolve().parent

TITLE_KEYWORDS = ("セクシー女優", "AV女優", "セクシーアイドル", "セクシータレント", "FANZA")
WIKI_CATEGORY_KEYS = ("AV女優",)

PW_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})\([月火水木金土日祝・]+\)\s*(?:(\d{1,2}:\d{2})\s*[〜~～-]\s*(\d{1,2}:\d{2})?)?")
SAN_RE = re.compile(r"[【「『(（\[]([^】」』)）\]]{1,20})[】」』)）\]]\s*(?:さん|ちゃん|くん|君)"
                    r"|([^\s☆★【】!！・、&＆♪「」『』()（）\[\]。][^\s☆★【】!！・、&＆♪「」『』()（）\[\]]{0,19}?)(?:さん|ちゃん|くん)")
RAITEN_RE = re.compile(r"([^\s「」『』【】※。、&＆!！☆★♪♫]{1,30}?)\s*(?:さん)?\s*(?:実践)?来店(?!の際)")
NOISE_RE = re.compile(r"^(\d+|更新|https?://\S+|\d+(分|時間|日)前|周辺)$")
STRIP_WORDS = ("セクシー女優", "AV女優", "セクシーアイドル", "セクシータレント", "人気", "元", "超人気")


@dataclass(frozen=True)
class Visit:
    pref: str
    date: dt.date
    talent: str
    title: str
    hall: str
    city: str
    time: str

    def key(self):
        return (self.pref, self.date, self.talent, self.hall)


def fetch(url: str, ua: str = UA) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t)
    t = "".join(c for c in t if unicodedata.category(c) != "So" and c not in "️‍")
    return re.sub(r"\s+", " ", t).strip()


def city_of(addr: str) -> str:
    m = re.match(r"(?:静岡県|愛知県|神奈川県)((?:[^市郡]+?郡)?[^市町村]+?[市町村])", addr)
    return m.group(1) if m else ""


def talent_names(title: str) -> list[str]:
    title = norm(title)
    names: list[str] = []
    for m in SAN_RE.finditer(title):
        n = (m.group(1) or m.group(2) or "").replace(" ", "").strip("☆★♪!・")
        if "。" in n[:-1]:
            n = n[:-1].rsplit("。", 1)[-1] + n[-1]
        for w in STRIP_WORDS:
            if n.startswith(w) and len(n) > len(w):
                n = n[len(w):]
        suf = re.search(r"(ちゃん|くん|君)$", m.group(0))
        if n and suf:
            n += suf.group(1)
        if n and n not in ("さん", "ちゃん") and n not in names:
            names.append(n)
    if not names:
        for m in RAITEN_RE.finditer(title):
            n = m.group(1).strip("・:：")
            for w in STRIP_WORDS:
                if n.startswith(w) and len(n) > len(w):
                    n = n[len(w):]
            if n and n not in ("さん", "ちゃん") and n not in names:
                names.append(n)
    return names


def parse_pworld(html: str, pref: str, today: dt.date) -> list[Visit]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [l.strip() for l in soup.get_text("\n").split("\n")]
    lines = [l for l in lines if l]
    prefix = PREF_KANJI[pref]
    visits: list[Visit] = []
    hall, city = "", ""
    for i, ln in enumerate(lines):
        if ln.startswith(prefix):
            j = i - 1
            while j >= 0 and NOISE_RE.match(lines[j]):
                j -= 1
            hall = norm(lines[j]) if j >= 0 else ""
            city = city_of(ln)
            continue
        if ln != "来店" or not hall:
            continue
        window = lines[i + 1:i + 6]
        title = window[0] if window else ""
        date_line = next((w for w in window if PW_DATE_RE.match(w)), None)
        if not title or not date_line:
            continue
        m = PW_DATE_RE.match(date_line)
        mo, d, t1, t2 = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        year = today.year + 1 if mo < today.month - 6 else (today.year - 1 if mo > today.month + 6 else today.year)
        try:
            date = dt.date(year, mo, d)
        except ValueError:
            continue
        tm = f"{t1}〜{t2}" if t1 and t2 else (f"{t1}〜" if t1 else "")
        names = talent_names(title)
        if not names:
            continue
        for n in names:  # 複数人の来店は1人ずつ判定する
            visits.append(Visit(pref, date, n, norm(title), hall, city, tm))
    return visits


def collect(prefs: list[str], today: dt.date, end: dt.date) -> list[Visit]:
    found: dict = {}
    ok_pages = 0
    for pref in prefs:
        base = PWORLD.format(pref=pref)
        for page in range(1, 41):
            url = base if page == 1 else f"{base}?page={page}"
            try:
                html = fetch(url)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {url}: {e}", file=sys.stderr)
                break
            ok_pages += 1
            vs = parse_pworld(html, pref, today)
            for v in vs:
                found.setdefault(v.key(), v)
            print(f"[info] {url}: 来店 {len(vs)}名", file=sys.stderr)
            time.sleep(1.0)
            if f"page={page + 1}" not in html:
                break
    if ok_pages == 0:
        raise SystemExit("来店情報ページを1ページも取得できませんでした。サイトの状態を確認してください。")
    return [v for v in found.values() if today <= v.date <= end]


# ---------- セクシー女優の判定 ----------
def load_list() -> set[str]:
    p = HERE / "actresses.txt"
    if not p.exists():
        return set()
    names = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(norm(line).replace(" ", ""))
    return names


_wiki_cache: dict[str, bool] = {}


def wiki_is_actress(name: str) -> bool:
    if name in _wiki_cache:
        return _wiki_cache[name]
    result = False
    if len(name) >= 2 and not re.fullmatch(r"[ぁ-んー]{1,3}", name):
        q = urllib.parse.urlencode({"action": "query", "titles": name, "prop": "categories",
                                    "cllimit": "max", "redirects": 1, "format": "json"})
        try:
            data = json.loads(fetch(f"https://ja.wikipedia.org/w/api.php?{q}", WIKI_UA))
            for page in data.get("query", {}).get("pages", {}).values():
                cats = [c.get("title", "") for c in page.get("categories", [])]
                if any(k in c for c in cats for k in WIKI_CATEGORY_KEYS):
                    result = True
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Wikipedia確認に失敗（{name}）: {e}", file=sys.stderr)
        time.sleep(0.3)
    _wiki_cache[name] = result
    return result


def is_sexy_actress(v: Visit, listed: set[str]) -> str | None:
    """対象なら判定理由を返す。"""
    if any(k in v.title for k in TITLE_KEYWORDS):
        return "題名"
    if v.talent.replace(" ", "") in listed:
        return "リスト"
    if wiki_is_actress(v.talent):
        return "Wikipedia"
    return None


# ---------- メッセージ ----------
def build_messages(visits: list[Visit], prefs: list[str], today: dt.date, end: dt.date) -> list[str]:
    head = f"【セクシー女優 来店予定】{today.month}/{today.day}〜{end.month}/{end.day}　全{len(visits)}件"
    if not visits:
        return [head + "\n\n期間内の来店予定は見つかりませんでした。"]
    chunks, cur = [], head
    for pref in prefs:
        vs = sorted((v for v in visits if v.pref == pref), key=lambda v: (v.date, v.time, v.hall))
        part = f"\n\n◆{PREF_NAMES[pref]}（{len(vs)}件）"
        if not vs:
            part += "\nなし"
        last = None
        for v in vs:
            if v.date != last:
                part += f"\n■{v.date.month}/{v.date.day}({WD[v.date.weekday()]})"
                last = v.date
            where = f"{v.hall}（{v.city}）" if v.city else v.hall
            part += f"\n・{v.talent}｜{where}" + (f" {v.time}" if v.time else "")
        for seg in re.split(r"(?=\n■)", part):
            if len(cur) + len(seg) > 4800:
                chunks.append(cur)
                cur = "（つづき）" + seg
            else:
                cur += seg
    chunks.append(cur + "\n\n出典: P-WORLD 取材・来店情報")
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
    days = int(os.environ.get("PERIOD_DAYS", "31"))
    prefs = [p.strip() for p in os.environ.get("PREFS", "shizuoka,aichi,kanagawa").split(",") if p.strip() in PREF_NAMES]
    today = dt.datetime.now(JST).date()
    end = today + dt.timedelta(days=days)

    all_visits = collect(prefs, today, end)
    listed = load_list()
    picked, others = [], set()
    for v in all_visits:
        why = is_sexy_actress(v, listed)
        if why:
            picked.append(v)
            print(f"[対象] {v.talent}（{why}）", file=sys.stderr)
        else:
            others.add(v.talent)
    print("[対象外の来店者] " + "、".join(sorted(others)), file=sys.stderr)
    print("  ↑セクシー女優なのに漏れている人がいたら actresses.txt に名前を追加してください", file=sys.stderr)

    texts = build_messages(picked, prefs, today, end)
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
