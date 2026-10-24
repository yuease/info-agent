import pytest
from datetime import datetime, timezone

from info_agent.storage import Store
from info_agent.workflow import Article, Choice, Selection, build_briefing, parse_feed, validate_selection


def test_feed_rejects_error_page():
    with pytest.raises(ValueError):
        parse_feed(b'<html><body>Error</body></html>')


def test_feed_sanitizes_text_and_deduplicates_video_identity():
    items = parse_feed(b'''<rss><channel>
    <item><title>A</title><link>http://www.bilibili.com/video/av123?x=1</link><description>&lt;script&gt;evil&lt;/script&gt;useful</description></item>
    <item><title>B</title><link>https://www.bilibili.com/video/av123/</link></item>
    <item><title>C</title><link>javascript:bad</link></item>
    </channel></rss>''')
    assert len(items) == 2
    assert items[0].id == items[1].id
    assert items[0].excerpt == 'useful'


def test_unknown_and_duplicate_model_choices_fail_closed():
    choice = Choice(id='unknown', summary='summary', reason='reason', action='read')
    with pytest.raises(ValueError):
        validate_selection(Selection(picks=[choice]), [{'id': 'real'}], 5)
    with pytest.raises(ValueError):
        validate_selection(Selection(picks=[choice, choice]), [{'id': 'unknown'}], 5)


def test_graph_preserves_partial_failure_and_original_source(tmp_path):
    article = Article(id='real', title='Original', url='https://example.org/a')
    def fetch(url):
        if 'broken' in url:
            raise ConnectionError('private detail must not appear')
        return [article, article]
    class Model:
        def with_structured_output(self, schema):
            return self
        def invoke(self, prompt):
            return Selection(picks=[Choice(id='real', summary='summary', reason='reason', action='read')])
    profile = {'rsshub_url': 'http://localhost:1200', 'keywords': ['good', 'broken'], 'interests': 'learn', 'max_picks': 5}
    result = build_briefing(profile, model=Model(), fetcher=fetch, store=Store(tmp_path / 'test.db'), clock=lambda: datetime(2026,10,24,tzinfo=timezone.utc)).invoke({})
    assert len(result['articles']) == 1
    assert len(result['failures']) == 1
    assert 'https://example.org/a' in result['report']
    assert 'private detail' not in result['report']


def test_all_sources_failed_does_not_call_model(tmp_path):
    def fail(url):
        raise ConnectionError()
    profile = {'rsshub_url': 'http://localhost:1200', 'keywords': ['a'], 'interests': '', 'max_picks': 5}
    with pytest.raises(RuntimeError):
        build_briefing(profile, model=object(), fetcher=fail, store=Store(tmp_path / 'test.db'), clock=lambda: datetime(2026,10,24,tzinfo=timezone.utc)).invoke({})
