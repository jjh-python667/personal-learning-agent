import json
import unittest
from unittest.mock import AsyncMock, patch
import test_core
import db
import agent

class ListingTests(unittest.TestCase):
    setUp=test_core.StudyTests.setUp
    tearDown=test_core.StudyTests.tearDown

    def test_list_uses_live_database_without_model(self):
        db.add_document('Day1.pdf',['第一页','第二页'])
        db.add_document('笔记.docx',['正文'],'section')
        db.execute('INSERT INTO messages(role,content,created) VALUES(?,?,?)',('assistant','资料列表是空的。',db.now()))
        with patch('agent.completion',new=AsyncMock(side_effect=AssertionError('不应调用模型'))):
            result=self.client.post('/api/chat',json={'text':'请调用 list_documents 工具，列出我的资料。'})
        self.assertEqual(result.status_code,200)
        self.assertIn('Day1.pdf（2页）',result.json()['reply'])
        self.assertIn('笔记.docx（1片段）',result.json()['reply'])
        self.assertEqual(json.loads(db.rows('SELECT trace FROM runs')[0]['trace']),[{'tool':'list_documents','ok':True}])
        db.execute('DELETE FROM documents')
        result=self.client.post('/api/chat',json={'text':'请列出我的资料。'})
        self.assertIn('没有资料',result.json()['reply'])

    def test_compound_requests_continue_to_model_with_current_inventory(self):
        db.add_document('当前资料.pdf',['TCP'])
        mock=AsyncMock(return_value={'role':'assistant','content':'继续处理'})
        with patch('agent.completion',new=mock):
            result=self.client.post('/api/chat',json={'text':'请列出我的资料，再读取第一页。'})
        self.assertEqual(result.status_code,200)
        context=mock.call_args.args[0][0]['content']
        self.assertIn('当前资料.pdf',context)
        self.assertFalse(agent.is_document_list_request('不要列出我的资料'))
