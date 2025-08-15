# AI Market Newsletter — README

## 1) Prereqs
- Python 3.10+
- `pip install feedparser pyyaml python-dateutil beautifulsoup4 requests markdown`
- (Optional) X API access (paid) if you want tweets. Add your **Bearer Token** in `config.yaml` and flip `x_api.enabled: true`.

## 2) Configure sources
Edit `config.yaml` and list the **RSS feeds** you want. Good free, reliable picks:
- Reuters: https://www.reuters.com/finance/rss
- CNBC Markets: https://www.cnbc.com/id/100003114/device/rss/rss.html
- Investing.com Economic Indicators: https://www.investing.com/rss/news_25.rss
- MarketWatch Top Stories: https://www.marketwatch.com/feeds/topstories
- BBC Business: https://www.bbc.co.uk/news/business/rss.xml

> Many sites publish dedicated **Earnings**, **Technology**, **Commodities**, or **Crypto** RSS feeds—add those too.

## 3) Run locally
```bash
python3 newsletter.py
