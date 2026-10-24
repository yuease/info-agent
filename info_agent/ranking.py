"""Shortlist validation and bounded ranking, independent of model transport."""
import math
from info_agent.preferences import features, duration_feature


def exploration_slots(profile):
    return math.floor(profile.max_picks * profile.exploration_ratio + .5) if profile.max_picks >= 5 else 0


def shortlist_limit(profile, count):
    return min(count, 2 * profile.max_picks + exploration_slots(profile))


def validate_selection(selection, articles, limit):
    ids = {a['id'] for a in articles}
    picks = [p.id for p in selection.picks]
    if len(picks) > limit or len(set(picks)) != len(picks) or not set(picks) <= ids:
        raise ValueError('模型选择了重复、未知或过量条目')


def bounded_order(picks, articles, weights, profile):
    base = sorted(picks, key=lambda p: (-p['base_score'], p['id']))
    by_id = {a['id']: a for a in articles}
    result = []
    for i, p in enumerate(base):
        fs = features(by_id[p['id']], profile.learning)
        duration = duration_feature(by_id[p['id']], profile.learning)
        adjustment = sum(weights.get(f, 0) for f in fs + ([duration] if duration else [])) if profile.learning.enabled else 0
        cap = profile.learning.max_score_adjustment
        adjustment = max(-cap, min(cap, adjustment))
        result.append(dict(p, adjustment=adjustment, final_score=p['base_score'] + adjustment, base_position=i + 1))
    # Disjoint adjacent swaps cannot move any item more than one position.
    i = 0
    while i + 1 < len(result) and profile.learning.max_rank_movement:
        if result[i + 1]['final_score'] > result[i]['final_score']:
            result[i], result[i + 1] = result[i + 1], result[i]
            i += 2
        else:
            i += 1
    for i, p in enumerate(result, 1):
        p['adjusted_position'] = i
    return result


def finalize(selection, articles, weights, profile):
    picks = [p.model_dump() for p in selection.picks]
    regular = bounded_order([p for p in picks if p['eligible_regular']], articles, weights, profile)
    by_id = {a['id']: a for a in articles}
    # Reserve exploration from the same shortlist, using base score then diversity.
    explore = []
    pool = [p for p in picks if p['eligible_explore']]
    reference = [by_id[p['id']] for p in regular[:profile.max_picks - exploration_slots(profile)]]
    while pool and len(explore) < exploration_slots(profile):
        def key(p):
            a = by_id[p['id']]
            tags = {t for r in reference for t in r['tags']}
            return (p['base_score'], len(set(a['tags']) - tags), a['author'] not in {r['author'] for r in reference}, p['id'])
        chosen = max(pool, key=key)
        pool.remove(chosen)
        reference.append(by_id[chosen['id']])
        explore.append(dict(chosen, lane='explore', adjustment=0, final_score=chosen['base_score']))
    used = {p['id'] for p in explore}
    normal = [dict(p, lane='regular') for p in regular if p['id'] not in used][:profile.max_picks - len(explore)]
    return normal + explore
