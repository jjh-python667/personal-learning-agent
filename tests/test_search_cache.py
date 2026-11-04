import unittest
from unittest.mock import AsyncMock, patch

import test_core
import agent
import db


class SearchCacheTests(unittest.TestCase):
    setUp = test_core.StudyTests.setUp
    tearDown = test_core.StudyTests.tearDown

    def test_repeat_search_reuses_persisted_result(self):
        db.add_document('网络', ['TCP 三次握手'])
        initial, hit = db.cached_sources('TCP')
        self.assertFalse(hit)
        with patch('db._rank_sources', side_effect=AssertionError('应复用缓存')):
            repeated, hit = db.cached_sources(' tcp ')
        self.assertTrue(hit)
        self.assertEqual(repeated, initial)

    def test_upload_edit_and_delete_invalidate_even_empty_results(self):
        self.assertEqual(db.cached_sources('TCP'), ([], False))
        self.assertEqual(db.cached_sources('TCP'), ([], True))
        doc_id = self.client.post('/api/documents', json={'title': '网络', 'content': 'TCP'}).json()['id']
        result, hit = db.cached_sources('TCP')
        self.assertFalse(hit)
        self.assertEqual(result[0]['document_id'], doc_id)
        db.execute('UPDATE documents SET title=? WHERE id=?', ('网络笔记', doc_id))
        self.assertEqual(db.cached_sources('TCP')[0][0]['title'], '网络笔记')
        self.client.delete(f'/api/documents/{doc_id}')
        self.assertEqual(db.cached_sources('TCP'), ([], False))

    def test_change_during_search_cannot_repopulate_old_cache(self):
        doc_id = db.add_document('网络', ['TCP'])
        rank = db._rank_sources

        def delete_during_search(query, documents):
            db.execute('DELETE FROM documents WHERE id=?', (doc_id,))
            return rank(query, documents)

        with patch('db._rank_sources', side_effect=delete_during_search):
            db.cached_sources('TCP')
        self.assertEqual(db.cached_sources('TCP'), ([], False))

    def test_chat_trace_records_hit_without_changing_tool_result(self):
        db.add_document('网络', ['TCP'])
        call = {'role': 'assistant', 'content': '', 'tool_calls': [{
            'id': 'search1', 'type': 'function',
            'function': {'name': 'search_documents', 'arguments': '{"query":"TCP"}'},
        }]}
        reply = {'role': 'assistant', 'content': '找到网络资料。'}
        with patch.object(agent, 'completion', new=AsyncMock(side_effect=[call, reply, call, reply])):
            results = [self.client.post('/api/chat', json={'text': '查 TCP'}).json() for _ in range(2)]
        self.assertEqual([r['trace'][0]['cache'] for r in results], ['miss', 'hit'])
