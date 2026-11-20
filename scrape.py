#!/usr/bin/env python3
"""都内クラブのスケジュールを集めて data/events.json / data/events.js に書き出す。

標準ライブラリのみ。1日1回の実行を想定（各サイトへは1リクエストごとに間隔を空ける）。
    python3 scrape.py            # 全会場
    python3 scrape.py zero       # 会場を指定（asia / circus / zero）
"""
import datetime as dt
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UA = "Mozilla/5.0 (compatible; club-calendar/1.0; personal schedule viewer)"
DELAY = 0.5          # リクエスト間隔（秒）
KEEP_PAST_DAYS = 35  # 終了したイベントを残す日数
ZERO_MONTHS_AHEAD = 3
JST = ZoneInfo("Asia/Tokyo")
TODAY = dt.datetime.now(JST).date()

VENUES = {
    "asia": "club asia",
    "circus": "CIRCUS Tokyo",
    "zero": "ZERO TOKYO",
}


# ---------------------------------------------------------------- helpers

def fetch(url):
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@~")  # 日本語や記号入りのスラッグ対策
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
            time.sleep(DELAY)
            return body
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"fetch failed: {url}: {last}")


def to_text(s):
    s = re.sub(r"(?s)<(script|style|noscript)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?s)<!--.*?-->", "", s)
    s = re.sub(r"<(br|/p|/div|/li|/h\d|/dd|/dt)[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("\xa0", " ").replace("　", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def strip_prose(text):
    """公開ページに載せるので、紹介文（文章）と注意書きは落とし、出演者・料金などの事実情報の行だけ残す。"""
    keep = []
    for ln in (text or "").split("\n"):
        t = ln.strip()
        if not t or "。" in t or len(t) > 120 or t.startswith(("※", "*", "＊")):
            continue
        if sum(1 for ch in t if "ぁ" <= ch <= "ゖ") >= 6:
            continue
        keep.append(t)
    return "\n".join(keep)[:3000]


def dedupe(seq):
    seen, out = set(), []
    for x in seq:
        k = unicodedata.normalize("NFKC", x).lower()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ---------------------------------------------------------------- lineup / genre heuristics

SKIP_START = re.compile(
    r"^(open|start|close|adv|door|doors|ticket|tickets|u-?2\d|under ?\d+|over ?\d+|price|entrance|fee|info|"
    r"date|venue|place|time|with flyer|w/f|early|general|vip|presented by|supported by|"
    r"学割|前売|当日|料金|時間|会場|日時|チケット|主催|協力|協賛|問い?合わせ)\b",
    re.I,
)
SKIP_ANY = re.compile(r"[¥￥]|yen\b|円|https?://|※|〒|drink|ドリンク|入場|再入場|\bID\b|\d{1,2}:\d{2}|\d{4}[./]\d{1,2}[./]\d{1,2}|^\*|sold ?out|venue|price", re.I)
HEADER = re.compile(
    r"^(.*\b(floor|room|stage|lounge|hall|bar|area|edition)\d*\b.*|b?\d+f\b.*|line ?-?up|dj'?s?|live|live ?set|guest'?s?|"
    r"(guest|host|special|resident|exodus|release) ?(sound|dj|live|act|guest)'?s?|sound ?system|"
    r"special guest'?s?|act|artist'?s?|resident'?s?|host(ed by)?|mc|出演|出演者|ゲスト|"
    r"vj'?s?|food|shop|deco|decoration|pa|sound|lighting|laser|photo|photographer|visual|flyer|design|"
    r"staff|.*dancer'?s?|pop ?up|other|.*\bdj'?s)$",
    re.I,
)
NON_PERFORMER = re.compile(
    r"^(vj'?s?|food|shop|deco|decoration|pa|sound|lighting|laser|photo|photographer|visual|flyer|design|staff|pop ?up|"
    r".*dancer'?s?|tickets?|sound ?system)$", re.I
)
FILLER = re.compile(r"^(and more|& more|more|\.+ ?and more.*|tba|t\.b\.a\.?|coming soon|secret guest)$", re.I)
STOP = re.compile(r"^[—―ー_=\-]{4,}|attention|注意事項|notice", re.I)
DECOR = "=-–—―・•●○■□◆◇▼▽▶︎►➤★☆✷✸✦✧✩✪❖◎※#【】[]《》〈〉<>『』「」:：|｜ \t"


def strip_decor(line):
    """行頭の絵文字・記号と、行末の飾りを落とす（$uicideboy のような名前の $ は残す）。"""
    i = 0
    while i < len(line) and not (line[i].isalnum() or line[i] in "$(（"):
        i += 1
    return line[i:].rstrip(DECOR)


def is_genre_word(name):
    """フロア欄にジャンル名が並んでいる会場があるので、出演者から除く。"""
    n = name.strip()
    if any(re.fullmatch(pat, n, re.I) for _, pat in GENRES):
        return True
    return bool(re.fullmatch(
        r"(all ?mix|open format|latin( hits)?|reggaeton.*|bachata|salsa|merengue|dembow|brazilian funk|"
        r"(international|global)( top)? hits|j-?pop|k-?pop|pops?|oldies|trap|rave|top ?40|.* hits)", n, re.I))


def parse_lineup(text, need_header=False):
    """フリーテキストから出演者らしき行を拾う。完璧ではない前提（照合は本文全体でも行う）。"""
    names = []
    started = not need_header
    skipping = False
    for raw in text.split("\n"):
        line = unicodedata.normalize("NFKC", raw).replace("’", "'").strip()  # 𝐆𝐔𝐄𝐒𝐓 のような装飾英字を通常の字に
        if not line:
            continue
        if STOP.search(line):
            if started and names:
                break
            continue
        bare = strip_decor(line)
        if re.match(r"tickets?\b", bare, re.I):  # TICKET: の後ろはプレイガイド名が並ぶ
            skipping = True
            continue
        if SKIP_START.match(bare) or SKIP_ANY.search(line) or line.startswith("@"):
            continue
        # 「DJ: xxx」「VJ:xxx」のように役割と名前が同じ行にある形
        role = re.match(r"^([A-Za-z][A-Za-z' ]{1,24}?)\s*[:：]\s*(\S.*)$", bare)
        if role and HEADER.match(role.group(1).strip()):
            started = True
            skipping = False
            if NON_PERFORMER.match(role.group(1).strip()):
                continue  # その行だけ飛ばす
            line = bare = role.group(2)
        core = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", bare).strip(DECOR)
        is_header = bool(HEADER.match(core)) or line.rstrip().endswith((":", "：")) or re.match(r"^[-–—【\[《■▼◆].*[-–—】\]》]$", line)
        if is_header:
            started = True
            skipping = bool(NON_PERFORMER.match(core))
            continue
        if not started or skipping:
            continue
        if "。" in line or "！" in line or "!" in line or len(line) > 90:
            continue
        if sum(1 for ch in line if "\u3041" <= ch <= "\u3096") >= 6:  # ひらがなが多い行は紹介文
            continue
        for part in re.split(r"\s+/\s+|\s*／\s*|\s+\|\s+|\s*、\s*", bare):
            name = strip_decor(re.sub(r"\s*@[\w.]+", "", part).strip())
            name = re.sub(r"(?i)\s+from\s+\S+$", "", name)
            while True:
                stripped = re.sub(r"\s*[(（\[][^)）\]]*[)）\]]\s*$", "", name).strip()
                if stripped == name:
                    break
                name = stripped
            if not name or len(name) > 45 or FILLER.match(name) or HEADER.match(name):
                continue
            if re.fullmatch(r"[\d\W_]+", name) or is_genre_word(name):
                continue
            names.append(name)
    return dedupe(names)[:80]


GENRES = [
    ("Hard Trance", r"hard ?trance"),
    ("Psytrance", r"psy(chedelic)?[ -]?trance|サイケ"),
    ("Trance", r"trance|トランス"),
    ("Hard Techno", r"hard ?techno"),
    ("Techno", r"techno|テクノ"),
    ("Tech House", r"tech ?house"),
    ("Deep House", r"deep ?house"),
    ("House", r"house|ハウス"),
    ("Drum & Bass", r"drum ?(&|and|n|'n'|’n’) ?bass|dnb|d&b|ドラムンベース"),
    ("Dubstep", r"dubstep|ダブステップ"),
    ("UK Garage", r"uk ?garage|ukg|2 ?step"),
    ("Bass", r"bass( music)?|ベースミュージック"),
    ("Jungle", r"jungle"),
    ("Footwork", r"footwork|juke"),
    ("Hip Hop", r"hip[ -]?hop|ヒップホップ|rap"),
    ("R&B", r"r ?& ?b|rnb"),
    ("Disco", r"disco|ディスコ"),
    ("Ambient", r"ambient|アンビエント"),
    ("Electro", r"electro"),
    ("Hardcore", r"hardcore|gabber|ハードコア"),
    ("Hyperpop", r"hyper ?pop"),
    ("Experimental", r"experimental|leftfield|noise"),
    ("Reggae / Dancehall", r"reggae|dancehall|レゲエ"),
    ("Afro / Amapiano", r"afro(beats?)?|amapiano"),
    ("Jersey Club", r"jersey ?club"),
    ("K-POP", r"k-?pop"),
    ("Anison", r"anison|アニソン"),
    ("EDM", r"edm"),
]


def norm_genre(g):
    """サイト側のジャンル表記を揃える（完全一致のときだけ正規名に寄せる）。"""
    g = unicodedata.normalize("NFKC", g).strip(" /")
    for canon, pat in GENRES:
        if re.fullmatch(pat, g, re.I):
            return canon
    return g.title() if g.isupper() or g.islower() else g


def scan_genres(text):
    """サイトがジャンルを持たない会場向けに、本文からキーワードで拾う。"""
    t = unicodedata.normalize("NFKC", text)
    found = []
    for canon, pat in GENRES:
        if re.search(r"(?<![A-Za-z])(" + pat + r")(?![A-Za-z])", t, re.I):
            found.append(canon)
    # 上位ジャンルの重複を落とす（Hard Trance があれば Trance は足さない等）
    for specific, general in (("Hard Trance", "Trance"), ("Psytrance", "Trance"), ("Hard Techno", "Techno"),
                              ("Tech House", "House"), ("Deep House", "House"), ("Drum & Bass", "Bass")):
        if specific in found and general in found:
            rest = re.sub(GENRES[[c for c, _ in GENRES].index(specific)][1], " ", t, flags=re.I)
            gp = GENRES[[c for c, _ in GENRES].index(general)][1]
            if not re.search(r"(?<![A-Za-z])(" + gp + r")(?![A-Za-z])", rest, re.I):
                found.remove(general)
    return found


# ---------------------------------------------------------------- club asia (Framer)

def scrape_asia():
    base = "https://clubasia.jp"
    page = fetch(base + "/events")
    listed = {}
    for m in re.finditer(r'href="\./events/([^"#?]+)"', page):
        slug = m.group(1)
        tail = page[m.end(): m.end() + 8000]
        nxt = tail.find('href="./events/')
        if nxt > 0:
            tail = tail[:nxt]
        t = re.search(r'<time datetime="(\d{4}-\d\d-\d\d)', tail)
        if t and slug not in listed:
            listed[slug] = t.group(1)

    def seg(src, name):
        key = f'data-framer-name="{name}"'
        i = src.find(key)
        while i >= 0:
            j = src.find('data-framer-name="', i + len(key))
            chunk = src[i: j if j > 0 else i + 20000]
            chunk = chunk[: chunk.rfind("<")] if j > 0 else chunk  # 次の要素の開きタグの断片を落とす
            txt = to_text("<x " + chunk)
            if txt:
                return txt
            i = src.find(key, i + len(key))
        return ""

    def genre_slot(src, name):
        i = src.find(f'data-framer-name="{name}"')
        if i < 0:
            return ""
        end = i + 20
        for m in re.finditer(r'data-framer-name="([^"]*)"', src[i + 20: i + 12000]):
            end = i + 20 + m.end()
            if m.group(1) != "ジャンル":
                end = i + 20 + m.start()
                break
        else:
            end = i + 12000
        t = re.search(r'class="framer-text"[^>]*>([^<]+)<', src[i:end])
        return html.unescape(t.group(1)).strip() if t else ""

    events = []
    for slug, date in listed.items():
        url = f"{base}/events/{slug}"
        ev = {"id": f"asia:{slug}", "venue": "asia", "date": date, "url": url,
              "title": slug, "open": "", "genres": [], "lineup": [], "text": ""}
        try:
            d = fetch(url)
            title = seg(d, "タイトル").split("\n")[0].strip()
            if title:
                ev["title"] = unicodedata.normalize("NFKC", title).strip("『』「」 ")
            body = seg(d, "本文")
            ev["text"] = body[:4000]
            ev["lineup"] = parse_lineup(body, need_header=True)
            genres = [genre_slot(d, "ジャンル1"), genre_slot(d, "ジャンル2")]
            ev["genres"] = dedupe([norm_genre(g) for g in genres if g])
            times = re.findall(r"\d{1,2}:\d{2}", seg(d, "open start"))
            if times:
                ev["open"] = times[0]
        except Exception as e:  # 詳細が取れなくても一覧の情報だけで載せる
            print(f"  asia detail failed {slug}: {e}", file=sys.stderr)
        events.append(ev)
    return events


# ---------------------------------------------------------------- CIRCUS Tokyo (WP Event Manager)

def scrape_circus():
    base = "https://circus-tokyo.jp"
    links = []
    for path in ("/schedule/", "/"):
        try:
            page = fetch(base + path)
        except Exception as e:
            print(f"  circus list failed {path}: {e}", file=sys.stderr)
            continue
        links += re.findall(r'href="(https://circus-tokyo\.jp/event/[^"#?]+)"', page)
    links = list(dict.fromkeys(links))
    if not links:
        raise RuntimeError("no event links found")

    events = []
    for url in links:
        try:
            d = fetch(url)
        except Exception as e:
            print(f"  circus detail failed {url}: {e}", file=sys.stderr)
            continue
        data = None
        for block in re.findall(r'(?s)<script type="application/ld\+json"[^>]*>(.*?)</script>', d):
            try:
                j = json.loads(block)
            except ValueError:
                continue
            if isinstance(j, dict) and j.get("@type") == "Event":
                data = j
                break
        if not data or not data.get("startDate"):
            continue
        start = str(data["startDate"])
        end = str(data.get("endDate") or "")
        body = to_text(str(data.get("description") or ""))
        title = html.unescape(str(data.get("name") or "")).strip()
        slug = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
        ev = {"id": f"circus:{slug}", "venue": "circus", "date": start[:10], "url": url,
              "title": title or slug, "open": start[11:16], "genres": scan_genres(title + "\n" + body),
              "lineup": parse_lineup(body), "text": body[:4000]}
        m = re.search(r"OPEN\s*[:：]?\s*(\d{1,2}:\d{2})", body, re.I)
        if m:
            ev["open"] = m.group(1)
        # 深夜イベントは終了日が翌日になるだけなので、2日以上またぐ場合だけ end_date を持つ
        if end[:10] > start[:10]:
            try:
                span = (dt.date.fromisoformat(end[:10]) - dt.date.fromisoformat(start[:10])).days
                if span >= 2:
                    ev["end_date"] = end[:10]
            except ValueError:
                pass
        events.append(ev)
    return events


# ---------------------------------------------------------------- ZERO TOKYO (WordPress)

def scrape_zero():
    base = "https://zerotokyo.jp"
    listed = {}
    y, mth = TODAY.year, TODAY.month
    for _ in range(ZERO_MONTHS_AHEAD + 1):
        try:
            page = fetch(f"{base}/event/{y}/{mth:02d}")
        except Exception as e:
            print(f"  zero month {y}/{mth:02d}: {e}", file=sys.stderr)
            page = ""
        for chunk in page.split('<li class="c-event__item')[1:]:
            href = re.search(r'href="(https://zerotokyo\.jp/event/[^"/]+/)"', chunk)
            mm = re.search(r'c-event-time-month">\s*(\d{1,2})', chunk)
            dd = re.search(r'c-event-time-date">\s*(\d{1,2})', chunk)
            title = re.search(r'(?s)c-event__title">(.*?)</h3>', chunk)
            if not (href and mm and dd):
                continue
            if int(mm.group(1)) != mth:  # 別の月の「Upcoming」欄などは無視
                continue
            cats = re.findall(r'category__item">([^<]+)<', chunk)
            listed.setdefault(href.group(1), {
                "date": f"{y}-{mth:02d}-{int(dd.group(1)):02d}",
                "title": to_text(title.group(1)) if title else "",
                "genres": dedupe([norm_genre(html.unescape(c)) for c in cats
                                  if not re.fullmatch(r"\s*(tba|t\.b\.a\.?|-)\s*", c, re.I)]),
            })
        mth += 1
        if mth > 12:
            y, mth = y + 1, 1
    if not listed:
        raise RuntimeError("no events found in monthly pages")

    events = []
    for url, info in listed.items():
        slug = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
        ev = {"id": f"zero:{slug}", "venue": "zero", "date": info["date"], "url": url,
              "title": info["title"] or slug, "open": "", "genres": info["genres"], "lineup": [], "text": ""}
        try:
            d = fetch(url)
            m = re.search(r"(?s)<dt>\s*OPEN\s*</dt>\s*<dd[^>]*>(.*?)</dd>", d)
            if m:
                ev["open"] = to_text(m.group(1)).replace("\n", " ")
            m = re.search(r"(?s)<dt>\s*PRICE\s*</dt>\s*<dd[^>]*>(.*?)</dd>", d)
            if m:
                ev["price"] = to_text(m.group(1)).replace("\n", " / ")
            floors = re.findall(
                r'(?s)p-post-artist-floor__title">(.*?)</h4>\s*<div class="p-post-artist-floor__text">(.*?)</div>', d)
            lineup, parts = [], []
            for name, body in floors:
                name, body = to_text(name), to_text(body)
                parts.append(f"[{name}]\n{body}")
                if not re.fullmatch(r"other|etc\.?", name, re.I):
                    lineup += parse_lineup(body)
            full = to_text(d)
            a = re.search(r"\d{4}\.\d{1,2}\.\d{1,2}", full)
            b = full.find("\nOPEN\n")
            desc = full[a.end(): b].strip() if a and b > a.end() else ""
            if desc.startswith(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")):
                desc = desc[3:].strip()
            ev["lineup"] = dedupe(lineup)[:80]
            ev["text"] = ("\n".join(parts) + ("\n\n" + desc if desc else ""))[:4000]
        except Exception as e:
            print(f"  zero detail failed {slug}: {e}", file=sys.stderr)
        events.append(ev)
    return events


SCRAPERS = {"asia": scrape_asia, "circus": scrape_circus, "zero": scrape_zero}


# ---------------------------------------------------------------- merge & write

def main():
    targets = [a for a in sys.argv[1:] if a in SCRAPERS] or list(SCRAPERS)
    if "--no-fetch" in sys.argv:  # 取得せず、保存済みデータの整形と書き出しだけやり直す
        targets = []
    out_path = DATA / "events.json"
    prev = {"events": [], "sources": {}}
    first_run = not out_path.exists()
    if not first_run:
        prev = json.loads(out_path.read_text(encoding="utf-8"))
    prev_by_id = {e["id"]: e for e in prev.get("events", [])}
    sources = dict(prev.get("sources", {}))
    now = dt.datetime.now(JST).isoformat(timespec="minutes")

    merged = {}
    for venue in SCRAPERS:
        old = [e for e in prev.get("events", []) if e.get("venue") == venue]
        if venue not in targets:
            merged.update({e["id"]: e for e in old})
            continue
        print(f"[{venue}] scraping…", file=sys.stderr)
        try:
            fresh = SCRAPERS[venue]()
            if not fresh:
                raise RuntimeError("0 events")
            sources[venue] = {"ok": True, "count": len(fresh), "checked": now}
        except Exception as e:
            # 失敗した会場は前回のデータを残し、画面に警告を出せるようにする
            print(f"[{venue}] FAILED: {e}", file=sys.stderr)
            sources[venue] = {"ok": False, "error": str(e)[:200], "checked": now,
                              "count": len(old)}
            merged.update({e["id"]: e for e in old})
            continue
        # 一覧から消えた過去イベントも保持期間内は残す
        merged.update({e["id"]: e for e in old})
        for e in fresh:
            e["venue_name"] = VENUES[venue]
            old_e = prev_by_id.get(e["id"])
            # 初回実行は全件が「新着」になってしまうので first_seen を付けない
            e["first_seen"] = old_e.get("first_seen") if old_e else (None if first_run else TODAY.isoformat())
            merged[e["id"]] = e
        print(f"[{venue}] {len(fresh)} events", file=sys.stderr)

    manual_path = DATA / "manual.json"
    if manual_path.exists():
        for e in json.loads(manual_path.read_text(encoding="utf-8")):
            old_e = prev_by_id.get(e["id"])
            e.setdefault("first_seen", old_e.get("first_seen") if old_e else None)
            merged[e["id"]] = e

    cutoff = (TODAY - dt.timedelta(days=KEEP_PAST_DAYS)).isoformat()
    events = [e for e in merged.values() if (e.get("end_date") or e["date"]) >= cutoff]
    for e in events:
        e["text"] = strip_prose(e.get("text", ""))
    events.sort(key=lambda e: (e["date"], e["venue"], e["title"]))

    out = {"updated": now if targets else prev.get("updated", now), "sources": sources, "events": events}
    DATA.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    js = json.dumps(out, ensure_ascii=False).replace("</", "<\\/")
    (DATA / "events.js").write_text("window.CLUB_DATA = " + js + ";\n", encoding="utf-8")
    print(f"wrote {len(events)} events", file=sys.stderr)
    if any(not s.get("ok") for v, s in sources.items() if v in targets):
        sys.exit(1)


if __name__ == "__main__":
    main()
