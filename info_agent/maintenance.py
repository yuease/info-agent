"""Explicit, idempotent import of old file-only runs. Never automatic."""
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit
from info_agent.schemas import Article, Selection, Profile
from info_agent.ranking import validate_selection
from info_agent.preferences import SHANGHAI


def import_legacy(store, directory, profile, now, published=False):
    directory = directory.resolve()
    run_id = 'legacy-' + hashlib.sha256(str(directory).encode()).hexdigest()[:20]
    if store.db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
        return run_id
    old = json.loads((directory / 'run.json').read_text(encoding='utf-8-sig'))
    old_profile = Profile.model_validate(old['profile'])
    if old_profile.context != profile.context:
        raise ValueError('旧运行兴趣上下文与当前配置不一致')
    articles = [Article.model_validate(a).model_dump() for a in json.loads((directory / 'candidates.json').read_text(encoding='utf-8-sig'))]
    for a in articles:
        u = urlsplit(a['url'])
        if u.scheme not in ('http','https') or not u.hostname:
            raise ValueError('invalid legacy source URL')
    selection = Selection.model_validate(old.get('selection', {}))
    validate_selection(selection, articles, profile.max_picks)
    formal = published and not old.get('preview', False) and (directory / 'briefing.md').is_file()
    created = now
    try:
        created = datetime.strptime(directory.name[:15], '%Y%m%d-%H%M%S').replace(tzinfo=SHANGHAI)
    except ValueError:
        pass
    data = dict(profile=old_profile.model_dump(), imported_from=str(directory), created=created.isoformat(),
                selection=selection.model_dump(), legacy=True, metadata_missing=True)
    if formal:
        data['report'] = (directory / 'briefing.md').read_text(encoding='utf-8-sig')
    store.run(run_id, profile.context, created, 'completed' if formal else 'preview', data, articles,
              selection.model_dump()['picks'] if formal else None)
    store.export(run_id)
    return run_id
