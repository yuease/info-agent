"""Bounded RSS-only acquisition. No keywords are inferred or added."""
from urllib.parse import quote


def subscriptions(profile):
    s = profile.source_plan
    groups = []
    if s.keyword_search.enabled:
        groups.append([dict(kind='keyword_search',key=k,label='关键词 · '+k,
                            path='/bilibili/vsearch/'+quote(k,safe='')+'/pubdate') for k in profile.keywords])
    uploads,dynamics=[],[]
    for u in s.ups:
        if not u.enabled:continue
        if u.scope in ('uploads','both'):
            uploads.append(dict(kind='up_uploads',key=u.uid,label='UP 投稿 · '+u.uid,path='/bilibili/user/video/'+u.uid))
        if u.scope in ('dynamics','both'):
            dynamics.append(dict(kind='up_dynamics',key=u.uid,label='UP 动态 · '+u.uid,
                path='/bilibili/user/dynamic/'+u.uid+'/directLink=0&hideGoods=0'))
    groups += [uploads,dynamics]
    if s.following.enabled:
        u=s.following.uid
        kind='following_videos' if s.following.scope=='videos' else 'following_dynamics'
        path='/bilibili/followings/video/'+u if s.following.scope=='videos' else '/bilibili/followings/dynamic/'+u+'/embed=0'
        groups.append([dict(kind=kind,key=u,label=('关注视频动态 · ' if kind=='following_videos' else '关注综合动态 · ')+u,path=path)])
    result=[]
    while any(groups):
        for group in groups:
            if group:result.append(group.pop(0))
    return result


def merge_article(old, new):
    # Personal briefing/display projection. This deliberately is not a lossless
    # evidence store: Agent retrieval retains allowed pool contributions separately.
    for field in ('source_ids','matched_keywords','tags','parse_warnings'):
        old[field] = list(dict.fromkeys(old.get(field,[]) + new.get(field,[])))
    old['tags'] = old['tags'][:20]
    refs = {(s['kind'],s['key']):s for s in old.get('sources',[]) + new.get('sources',[])}
    old['sources'] = list(refs.values())
    for field in ('cover_url','duration_seconds','published_at','fetched_at'):
        if old.get(field) is None and new.get(field) is not None:
            old[field] = new[field]
    for field in ('author','published','title'):
        if old.get(field) in (None,'','未知') and new.get(field) not in (None,'','未知'):
            old[field] = new[field]
    if len(new.get('body_text') or '')>len(old.get('body_text') or ''):
        old['body_text']=new['body_text'];old['body_truncated']=new.get('body_truncated',False)
    if len(new.get('excerpt','')) > len(old.get('excerpt','')):
        old['excerpt'] = new['excerpt']
    for image in new.get('images',[]):
        if image not in old.setdefault('images',[]):old['images'].append(image)
    old['images']=old.get('images',[])[:12]
    contexts=old.get('dynamic_contexts',[])+new.get('dynamic_contexts',[])
    old['dynamic_contexts']=list({c['id']:c for c in contexts if c.get('id')}.values())[:12]
    if old.get('is_dynamic') and not new.get('is_dynamic',False):
        for field in ('author','author_uid','published','published_at','url','content_type','text_available'):
            if new.get(field) is not None:old[field]=new[field]
        old['is_dynamic']=False
    old['filter_hits']=list({(h['id'],h['version'],h['reason']):h for h in old.get('filter_hits',[])+new.get('filter_hits',[])}.values())
    return old


def prepare(groups, profile, blocked):
    found = {}
    counts = dict(history_filtered=0,duration_filtered=0,duplicates=0)
    for ref, group in groups:
        for item in group:
            a = dict(item)
            # Retain the legacy prepare([(keyword, group)]) Python interface.
            source = dict(kind='keyword_search',key=ref,label='关键词 · '+ref) if isinstance(ref,str) else ref
            a['sources'] = list({(s['kind'],s['key']):s for s in list(a.get('sources',[]))+[source]}.values())
            if source['kind'] == 'keyword_search':
                a['matched_keywords'] = list(dict.fromkeys(a.get('matched_keywords',[]) + [source['key']]))
            if a['id'] in found:
                counts['duplicates'] += 1
                merge_article(found[a['id']],a)
            else:
                found[a['id']] = a
    eligible = set()
    for identity, a in found.items():
        if identity in blocked:
            counts['history_filtered'] += 1
        elif a.get('content_type','video') == 'video' and profile.max_duration_seconds is not None and (a['duration_seconds'] is None or a['duration_seconds'] > profile.max_duration_seconds):
            counts['duration_filtered'] += 1
        else:
            eligible.add(identity)
    # First share the budget among source kinds, then among subscriptions of that kind.
    by_kind = {}
    for ref, group in groups:
        kind = 'keyword_search' if isinstance(ref,str) else ref['kind']
        queue = [found[a['id']] for a in group if a['id'] in eligible]
        queue.sort(key=lambda a:a.get('published_at') or '',reverse=True)
        by_kind.setdefault(kind,[]).append(queue)
    chosen,used = [],set()
    while by_kind and len(chosen) < profile.max_candidates:
        for kind in list(by_kind):
            queues = by_kind[kind]
            while queues:
                q = queues.pop(0)
                while q and q[0]['id'] in used:
                    q.pop(0)
                if not q:
                    continue
                a = q.pop(0)
                if q:
                    queues.append(q)
                chosen.append(a)
                used.add(a['id'])
                break
            if not queues:
                by_kind.pop(kind,None)
            if len(chosen) >= profile.max_candidates:
                break
    return chosen,list(found.values()),counts
