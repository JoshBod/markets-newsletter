import os, re, sys, json, time, math, smtplib, ssl, hashlib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml
import feedparser
from dateutil import tz
from bs4 import BeautifulSoup
import requests

# Optional: markdown rendering
try:
    import markdown as md
except Exception:
    md = None

# --------------------
# Utilities
# --------------------

def now_tz(tzname):
    return datetime.now(tz.gettz(tzname))


def clean_text(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    soup = BeautifulSoup(html_or_text, "html.parser")
    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def source_class(url: str) -> str:
    # Very rough mapping to weight source reliability
    if any(d in url for d in ["reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com"]):
        return "wire"
    if any(d in url for d in ["cnbc.com", "bbc.co.uk", "marketwatch.com", "investing.com", "yahoo.com"]):
        return "mainstream"
    return "blog"


def score_item(title: str, summary: str, url: str, weights: dict) -> float:
    text = f"{title} {summary}".lower()
    s = 0.0
    srcw = weights.get("sources", {}).get(source_class(url), 1.0)
    s += srcw
    for bucket, kws in weights.get("keywords", {}).items():
        for kw in kws:
            if kw.lower() in text:
                s += 1.0
    # Tiny boost if % numbers appear (magnitude hints)
    if re.search(r"\b\d{1,2}\.?\d?%\b", text):
        s += 0.5
    return s


def summarize(text: str, max_bullets: int = 3) -> str:
    # Heuristic fallback summarizer (no external API required).
    # Grabs first sentences containing numbers/action verbs; trims to ~800 chars.
    text = text[:1200]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    picks = []
    for s in sentences:
        if len(picks) >= max_bullets:
            break
        if any(x in s.lower() for x in ["%", "billion", "million", "guidance", "beats", "misses", "raises", "cuts", "sec", "ecb", "boe", "fed", "cpi", "nfp", "merger", "acquisition", "downgrade", "upgrade"]):
            picks.append(s.strip())
    if not picks:
        picks = sentences[:max_bullets]
    return "\n".join(f"- {p.strip()}" for p in picks if p.strip())


def to_markdown(title: str, items: list, tzname: str, tweets: list = None) -> str:
    dt = now_tz(tzname)
    header = f"# {title} — {dt.strftime('%A, %d %B %Y %H:%M %Z')}\n\n"
    md = [header]

    # Top Movers
    top = [it for it in items if it.get("score", 0) >= it.get("min_top_score", 2.0)]
    if top:
        md.append("## Top movers\n")
        for it in sorted(top, key=lambda x: x["score"], reverse=True)[:12]:
            md.append(f"**{it['title']}**  ")
            md.append(f"{it['bullets']}\n")
            md.append(f"[Read]({it['link']}) — _Score: {it['score']:.1f}_\n\n")

    # Sections by bucket
    buckets = {
        "Macro / Policy": ["macro", "policy"],
        "Earnings & Guidance": ["earnings"],
        "Analysts & Ratings": ["analyst"],
        "M&A / Activism": ["mna"],
        "Energy / Commodities": ["energy"],
        "Crypto": ["crypto"],
        "Other": ["other"],
    }

    def bucket_for(itext):
        text = itext.lower()
        for key in ["macro","earnings","analyst","mna","energy","crypto"]:
            for kw in CONFIG['weights']['keywords'].get(key, []):
                if kw.lower() in text:
                    return key
        return "other"

    grouped = {k: [] for k in buckets.keys()}
    for it in items:
        bkey = bucket_for(it["title"] + " " + it.get("summary",""))
        # Map internal key to display section
        for disp, keys in buckets.items():
            if bkey in keys:
                grouped[disp].append(it)
                break

    for section, arr in grouped.items():
        if not arr:
            continue
        md.append(f"## {section}\n")
        for it in arr[:CONFIG.get('max_items_per_section', 12)]:
            md.append(f"**{it['title']}**  ")
            md.append(f"{it['bullets']}\n")
            md.append(f"[Read]({it['link']}) — _Score: {it['score']:.1f}_\n\n")

    if tweets:
        md.append("## Notable tweets\n")
        for tw in tweets:
            line = f"- **@{tw['handle']}**: {tw['text']} ([link]({tw['url']}))\n"
            md.append(line)

    return "\n".join(md)


def send_email(subject: str, html: str, plain: str, cfg: dict):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = cfg['from_name'] + f" <{cfg['username']}>"
    msg['To'] = ", ".join(cfg['to'])
    part1 = MIMEText(plain, 'plain')
    part2 = MIMEText(html, 'html')
    msg.attach(part1)
    msg.attach(part2)

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port']) as server:
        server.starttls(context=context)
        server.login(cfg['username'], cfg['password'])
        server.sendmail(cfg['username'], cfg['to'], msg.as_string())

# --------------------
# Fetchers
# --------------------

def fetch_rss(feed_url: str):
    fp = feedparser.parse(feed_url)
    items = []
    for e in fp.entries:
        title = clean_text(getattr(e, 'title', ''))
        link = getattr(e, 'link', '')
        summary = clean_text(getattr(e, 'summary', ''))
        # time
        published = None
        for attr in ['published_parsed','updated_parsed']:
            if hasattr(e, attr) and getattr(e, attr):
                published = datetime.fromtimestamp(time.mktime(getattr(e, attr)))
                break
        items.append({"title": title, "link": link, "summary": summary, "published": published})
    return items


def fetch_tweets(handles: list, bearer: str, max_per: int = 5):
    # Requires X API v2; you must supply a paid bearer token.
    headers = {"Authorization": f"Bearer {bearer}"}
    tweets = []
    # Resolve usernames to user IDs
    for handle in handles:
        u = requests.get(f"https://api.x.com/2/users/by/username/{handle}", headers=headers)
        if u.status_code != 200:
            continue
        uid = u.json().get('data', {}).get('id')
        if not uid:
            continue
        r = requests.get(f"https://api.x.com/2/users/{uid}/tweets?max_results={min(max_per*2, 100)}&tweet.fields=created_at,public_metrics", headers=headers)
        if r.status_code != 200:
            continue
        for t in r.json().get('data', [])[:max_per]:
            text = clean_text(t.get('text',''))
            tid = t.get('id')
            url = f"https://x.com/{handle}/status/{tid}"
            tweets.append({"handle": handle, "text": text, "url": url})
    return tweets

# --------------------
# Main
# --------------------

with open('config.yaml','r') as f:
    CONFIG = yaml.safe_load(f)

def main():
    tzname = CONFIG['output'].get('timezone', 'Europe/London')
    start = now_tz(tzname)
    lookback = start - timedelta(hours=CONFIG.get('lookback_hours', 24))

    all_items = []
    seen = set()

    for feed in CONFIG.get('feeds', []):
        try:
            for it in fetch_rss(feed):
                if it['published'] and it['published'] < lookback:
                    continue
                key = it['link'] or hashlib.md5((it['title']+it.get('summary','')).encode()).hexdigest()
                if key in seen:
                    continue
                seen.add(key)
                s = score_item(it['title'], it.get('summary',''), it['link'], CONFIG['weights'])
                bullets = summarize((it.get('summary') or it['title']))
                all_items.append({
                    **it,
                    "score": s,
                    "bullets": bullets,
                    "min_top_score": CONFIG.get('min_top_score', 2.0),
                })
        except Exception as e:
            print(f"[warn] feed failed: {feed} — {e}")

    all_items.sort(key=lambda x: x['score'], reverse=True)

    tweets = []
    if CONFIG.get('x_api', {}).get('enabled'):
        try:
            tweets = fetch_tweets(CONFIG['x_api']['handles'], CONFIG['x_api']['bearer_token'], CONFIG['x_api'].get('max_tweets_per_handle', 5))
        except Exception as e:
            print(f"[warn] X API fetch failed: {e}")

    title = "Daily Market Brief"
    md_text = to_markdown(title, all_items, tzname, tweets)

    outdir = CONFIG['output']['directory']
    os.makedirs(outdir, exist_ok=True)
    date_tag = start.strftime('%Y-%m-%d')
    base = f"{CONFIG['output'].get('filename_prefix','newsletter')}_{date_tag}"

    md_path = None
    if CONFIG['output'].get('include_markdown', True):
        md_path = os.path.join(outdir, base + '.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_text)

    html_text = None
    if CONFIG['output'].get('include_html', True):
        if md:
            html = md.markdown(md_text, extensions=['extra', 'toc'])
            # Simple styling
            html_text = f"""
            <html><head><meta charset='utf-8'>
            <style>
              body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
              h1, h2 {{ margin-top: 1.6rem; }}
              a {{ text-decoration: none; }}
              code {{ background:#f3f3f3; padding:2px 4px; border-radius:4px; }}
              .meta {{ color:#666; font-size: 0.9rem; }}
            </style></head><body>{html}</body></html>
            """
        else:
            # Fallback: wrap markdown as pre
            html_text = f"<html><body><pre>{md_text}</pre></body></html>"
        html_path = os.path.join(outdir, base + '.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_text)

    if CONFIG.get('email', {}).get('enabled') and html_text:
        subj = f"{title} — {start.strftime('%d %b %Y')}"
        send_email(subj, html_text, md_text, CONFIG['email'])
        print("[ok] Email sent")

    print("[ok] Built:", md_path or '(md disabled)', 'and', html_path if CONFIG['output'].get('include_html', True) else '(html disabled)')

if __name__ == '__main__':
    main()
