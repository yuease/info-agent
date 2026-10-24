"""RSS parsing without extra requests."""
import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timezone
from html import unescape
from urllib.parse import urlsplit, urlunsplit, parse_qs, quote
from urllib.request import build_opener, ProxyHandler, Request
from info_agent.schemas import Article


def description_fields(raw):
    lines = re.split(r'<br\s*/?>|[\r\n]+', raw, flags=re.I)
    duration, warnings = None, []
    # Only anchored template header rows are metadata. Ordinary prose is preserved.
    while lines:
        line = lines[0].strip()
        if not line:
            lines.pop(0)
            continue
        if re.fullmatch(r'Length:\s*.*', line, re.I):
            value = line.split(':', 1)[1].strip()
            if re.fullmatch(r'\d+:\d{2}(?::\d{2})?', value):
                parts = list(map(int, value.split(':')))
                if parts[-1] < 60 and (len(parts) == 2 or parts[-2] < 60):
                    duration = sum(x * 60 ** i for i, x in enumerate(reversed(parts))) or None
            if duration is None:
                warnings.append('invalid Length')
        elif not re.fullmatch(r'(?:AuthorID:\s*\d+|(?:Play|Favorite|Danmaku|Comment):\s*[\d,.万亿kKmM]+(?:\s+(?:Play|Favorite|Danmaku|Comment):\s*[\d,.万亿kKmM]+)*)', line, re.I):
            break
        lines.pop(0)
    body = '<br>'.join(lines)
    body = re.sub(r'<iframe\b[^>]*>.*?</iframe>', '', body, flags=re.S | re.I)
    body = re.sub(r'(?:<br\s*/?>)Match By:\s*[a-z, ]+(?=<|$)', '', body, flags=re.I)
    return duration, clean_text(body)[:1200], warnings


def clean_text(value: str) -> str:
    """只保留文本摘要，不把网页 HTML 或脚本传给模型。"""
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def parse_feed(data: bytes) -> list[Article]:
    """拒绝错误页和异常协议；同一 B 站视频归一编号。"""
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("不接受带 DTD 的 XML")
    root = ET.fromstring(data)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("返回内容不是 RSS，可能是服务错误页")
    articles = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        parts = urlsplit(link)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            continue
        path = parts.path.rstrip("/")
        canonical = urlunsplit(("https", parts.netloc.lower(), quote(path, safe="/:@!$&'()*+,;=-._~%"), "", ""))
        identity = canonical
        if parts.hostname in {"bilibili.com", "www.bilibili.com"}:
            match = re.fullmatch(r"/video/((?:av\d+)|(?:BV\w+))", path)
            if match:
                identity = "bilibili:" + match[1]
        title = clean_text(item.findtext("title") or "")
        if not title:
            continue
        raw = item.findtext("description") or ""
        duration, excerpt, warnings = description_fields(raw)
        aliases = [identity]
        for src in re.findall(r'<iframe[^>]+src=[\"\']([^\"\']+)', raw, re.I):
            player = urlsplit(unescape(unescape(src)))
            if player.hostname == 'player.bilibili.com':
                query = parse_qs(player.query)
                for key, prefix, pattern in [('aid', 'av', r'\d+'), ('bvid', '', r'BV\w+')]:
                    val = query.get(key, [''])[0]
                    if re.fullmatch(pattern, val):
                        aliases.append('bilibili:' + prefix + val)
        published_at = None
        try:
            dt = parsedate_to_datetime(item.findtext('pubDate') or '')
            if dt.tzinfo is None:
                raise ValueError('timezone missing')
            published_at = dt.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            warnings.append('unknown publication time')
        articles.append(Article(
            id=hashlib.sha256(identity.encode()).hexdigest()[:20], title=title,
            url=canonical,
            author=clean_text(item.findtext("author") or item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "未知"),
            published=item.findtext("pubDate") or "未知",
            excerpt=excerpt, duration_seconds=duration, published_at=published_at,
            tags=list(dict.fromkeys(clean_text(t.text or '').casefold() for t in item.findall('category') if clean_text(t.text or '')))[:20],
            source_ids=list(dict.fromkeys(aliases)), parse_warnings=warnings,
        ))
    return articles


def fetch_feed(url: str) -> list[Article]:
    # 已配置本机转发，避免系统代理把 localhost 请求转到远端。
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url, headers={"User-Agent": "PersonalBriefing/0.1"}), timeout=40) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("RSS 超过 2 MB 上限")
    return parse_feed(data)


