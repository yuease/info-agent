"""Dynamic RSS metadata only. Images are display assets and never model inputs."""
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit,parse_qs
from info_agent.schemas import Article
from info_agent.rss import clean_text,description_fields
from info_agent.covers import extract_images,extract_cover
from info_agent.errors import DiagnosedError


class FeedBatch(list):
    def __init__(self,items,*,raw_ids=(),oldest=None):
        super().__init__(items)
        self.raw_ids=list(raw_ids)
        self.raw_count=len(self.raw_ids)
        self.cursor=self.raw_ids[-1] if self.raw_ids else None
        self.oldest=oldest
        self.newest=oldest


def timestamp(value):
    try:
        dt=parsedate_to_datetime(value or '')
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError,TypeError,OverflowError):return None


def dynamic_id(value):
    if not isinstance(value,str):return None
    try:
        u=urlsplit(value)
        if u.hostname in ('t.bilibili.com','www.bilibili.com'):
            m=re.fullmatch(r'/(?:opus/|dynamic/)?(\d+)',u.path.rstrip('/'))
            if m:return m[1]
    except ValueError:pass
    return value if re.fullmatch(r'\d{1,30}',value) else None


def explicit_uid(item,raw,*,channel=None,allow_channel=False):
    # Never use the requested/feed account UID as an item's author in following feeds.
    for node in item.iter():
        name=node.tag.rsplit('}',1)[-1].lower()
        if name in ('author_uid','authoruid','mid','uid'):
            value=(node.text or '').strip()
            if re.fullmatch(r'\d{1,30}',value):return value
        if name=='author':
            value=node.get('uid','')
            if re.fullmatch(r'\d{1,30}',value):return value
            for child in node:
                if child.tag.rsplit('}',1)[-1].lower()=='uri':
                    m=re.fullmatch(r'https?://space\.bilibili\.com/(\d+)/?',child.text or '')
                    if m:return m[1]
    m=re.match(r'\s*(?:Length:[^<]*<br\s*/?>\s*)?AuthorID:\s*(\d+)\b',raw,re.I)
    if m:return m[1]
    creator=clean_text(item.findtext('author') or item.findtext('{http://purl.org/dc/elements/1.1/}creator') or '')
    if allow_channel and channel is not None and creator:
        # A single-author channel explicitly identifies its creator; following channel titles don't match.
        title=channel.findtext('title') or ''
        link=channel.findtext('link') or ''
        m=re.fullmatch(r'https?://space\.bilibili\.com/(\d+)/(?:dynamic|video)/?',link)
        if m and title in (creator+' 的 bilibili 动态',creator+' 的 bilibili 投稿',creator+' 的 bilibili 视频',creator+' 的 bilibili 空间'):
            return m[1]
    return None


def video_reference(item,raw):
    # Only explicit primary-video template fields; a casual BV mention is not identity evidence.
    values=[]
    for node in item:
        name=node.tag.rsplit('}',1)[-1].lower()
        if name=='enclosure':
            u=urlsplit(node.get('url',''))
            if u.hostname in ('www.bilibili.com','player.bilibili.com'):
                q=parse_qs(u.query)
                if re.fullmatch(r'BV[A-Za-z0-9]+',q.get('bvid',[''])[0]):values.append(q['bvid'][0])
                elif re.fullmatch(r'\d+',q.get('aid',[''])[0]):values.append('av'+q['aid'][0])
    match=re.search(r'视频地址[：:]\s*<a\b[^>]*href=[\"\']https?://www\.bilibili\.com/video/((?:BV[A-Za-z0-9]+)|(?:av\d+))',raw,re.I)
    if match:values.append(match[1])
    link=item.findtext('link') or ''
    m=re.fullmatch(r'https?://www\.bilibili\.com/video/((?:BV[A-Za-z0-9]+)|(?:av\d+))/?',link)
    if m:values.append(m[1])
    return list(dict.fromkeys(values))


def content_type(item,raw,video):
    marker=' '.join((item.get('type',''),item.findtext('type') or '',item.findtext('dynamic_type') or '',
                     ' '.join(c.text or '' for c in item.findall('category') if c.get('domain')=='dynamic_type'))).upper().strip()
    mapping={'DYNAMIC_TYPE_AV':'video','8':'video','VIDEO':'video','DYNAMIC_TYPE_DRAW':'image','2':'image','IMAGE':'image',
        'DYNAMIC_TYPE_WORD':'text','4':'text','TEXT':'text','DYNAMIC_TYPE_ARTICLE':'article','64':'article','ARTICLE':'article',
        'DYNAMIC_TYPE_MUSIC':'audio','AUDIO':'audio','DYNAMIC_TYPE_LIVE_RCMD':'live','LIVE':'live'}
    if marker in ('DYNAMIC_TYPE_FORWARD','1','FORWARD') or '//转发自:' in clean_text(raw):
        # Determine the forwarded content, not the outer forward wrapper.
        marker=(item.findtext('original_type') or '').upper()
    if video:return 'video'
    if marker in mapping:return mapping[marker]
    if re.search(r'图文地址[：:]\s*<a[^>]*href=[\"\']https?://www\.bilibili\.com/opus/\d+',raw):return 'image'
    if re.search(r'专栏地址[：:]\s*<a[^>]*href=[\"\']https?://www\.bilibili\.com/read/cv\d+',raw):return 'article'
    # Pictures alone cannot establish the type of an unknown upstream card.
    return 'unknown'


def parse_dynamic_feed(data,*,allow_channel_author=False):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise DiagnosedError('SOURCE_PARSE')
    root=ET.fromstring(data);channel=root.find('channel')
    if root.tag!='rss' or channel is None:raise DiagnosedError('SOURCE_NOT_RSS')
    result,raw_ids,times=[],[],[]
    for item in channel.findall('item'):
        did=dynamic_id(item.findtext('guid')) or dynamic_id(item.findtext('link'))
        raw_ids.append(did)
        dt=timestamp(item.findtext('pubDate'))
        if dt:times.append(dt)
        if not did:continue
        raw=item.findtext('description') or item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or ''
        title=clean_text(item.findtext('title') or '')
        images=extract_images(raw);videos=video_reference(item,raw)
        ctype=content_type(item,raw,videos)
        duration,body,warnings=description_fields(raw,max_chars=None)
        # Strip exact RSSHub navigation labels, preserving commentary and ordinary text.
        body=re.sub(r'(?:视频|图文|专栏)地址[：:]\s*https?://\S+','',body).strip()
        title=title or body[:80] or ('图片动态' if images else '动态（正文缺失）')
        author=clean_text(item.findtext('author') or item.findtext('{http://purl.org/dc/elements/1.1/}creator') or '未知')
        author_uid=explicit_uid(item,raw,channel=channel,allow_channel=allow_channel_author)
        original_uid=(item.findtext('original_author_uid') or '').strip()
        if not re.fullmatch(r'\d{1,30}',original_uid):original_uid=None
        repost=body.split('//转发自:',1)[0].strip() if '//转发自:' in body else ''
        if repost=='转发动态':repost=''
        aliases=['bilibili:'+v for v in videos]
        identity=aliases[0] if aliases else 'bilibili:dynamic:'+did
        url='https://www.bilibili.com/video/'+videos[0] if videos else 'https://t.bilibili.com/'+did
        text_available=bool(body.strip() and body not in ('转发动态','动态','图片动态'))
        if not dt:warnings.append('dynamic publication time missing or invalid; first-seen fallback')
        a=Article(id=hashlib.sha256(identity.encode()).hexdigest()[:20],title=title,url=url,author=author,
            author_uid=author_uid,original_author_uid=original_uid,published=item.findtext('pubDate') or '未知',
            published_at=dt.isoformat() if dt else None,body_text=body[:20000],body_truncated=len(body)>20000,excerpt=body[:1200],duration_seconds=duration if ctype=='video' else None,
            tags=list(dict.fromkeys(clean_text(c.text or '').casefold() for c in item.findall('category') if c.get('domain')!='dynamic_type'))[:20],
            source_ids=aliases,is_dynamic=True,dynamic_id=did,content_type=ctype,images=images,
            cover_url=extract_cover(raw,item) if ctype=='video' else None,parse_warnings=warnings,
            repost_text=repost,text_available=text_available,
            dynamic_contexts=[dict(id=did,url='https://t.bilibili.com/'+did,author=author,author_uid=author_uid,
                original_author_uid=original_uid,text=repost or body[:1200],published_at=dt.isoformat() if dt else None)])
        result.append(a)
    batch=FeedBatch(result,raw_ids=raw_ids,oldest=min(times) if times else None)
    batch.newest=max(times) if times and len(times)==len(raw_ids) else None
    return batch
