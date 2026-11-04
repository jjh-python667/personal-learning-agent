import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_core
import agent
import db
import lesson


def scope(sid):
    return {'X-Study-Session': str(sid)}


class WorkspaceTests(unittest.TestCase):
    setUp = test_core.StudyTests.setUp
    tearDown = test_core.StudyTests.tearDown

    def course(self, title):
        result = self.client.post('/api/courses', json={'title': title})
        self.assertEqual(result.status_code, 200)
        return result.json()['session_id']

    def test_course_documents_search_cache_and_tasks_are_separate(self):
        python = self.course('Python')
        net_doc = db.add_document('网络讲义', ['TCP 连接'])
        net_task = lesson.start_task(net_doc)
        self.assertEqual(db.cached_sources('TCP')[0][0]['document_id'], net_doc)
        upload = self.client.post('/api/documents', headers=scope(python),
                                  json={'title': 'Python 笔记', 'content': 'TCP socket 示例'})
        py_doc = upload.json()['id']
        state = self.client.get('/api/state', headers=scope(python)).json()
        self.assertEqual([d['id'] for d in state['documents']], [py_doc])
        self.assertEqual(state['tasks'], [])
        self.assertEqual(self.client.get(f'/api/documents/{net_doc}', headers=scope(python)).status_code, 404)
        self.client.delete(f'/api/documents/{net_doc}', headers=scope(python))
        self.assertEqual(self.client.get(f'/api/documents/{net_doc}').status_code, 200)
        self.assertEqual(self.client.post(f'/api/tasks/{net_task["id"]}/pause', headers=scope(python)).status_code, 400)
        workspace = db.get_session(python)
        token = db.workspace_scope.set((workspace['course_id'], python))
        try:
            self.assertEqual(db.cached_sources('TCP')[0][0]['document_id'], py_doc)
        finally:
            db.workspace_scope.reset(token)
        self.assertEqual(db.cached_sources('TCP')[0][0]['document_id'], net_doc)

    def test_stream_and_qq_share_selected_session_without_mixing_history(self):
        course = self.course('计算机网络')
        other = self.client.post('/api/sessions', headers=scope(course),
                                 json={'title': '考前复习'}).json()['session_id']
        model = AsyncMock(return_value={'role': 'assistant', 'content': '解释连接。[[下一条]]继续看例子。'})
        with patch.object(agent, 'completion', new=model):
            stream = self.client.post('/api/chat/stream', headers=scope(course),
                                      json={'text': '我在学 TCP 窗口'})
            self.assertEqual(json.loads(stream.text.strip().splitlines()[-1])['type'], 'done')
            self.assertEqual(self.client.get('/api/state', headers=scope(other)).json()['messages'], [])
            qq = {'X-Study-Channel': 'qq'}
            listed = self.client.post('/api/chat', headers=qq, json={'text': '列出会话'})
            self.assertIn('考前复习', listed.json()['reply'])
            selected = self.client.post('/api/chat', headers=qq, json={'text': f'切换会话 {course}'})
            self.assertIn('已切换', selected.json()['reply'])
            self.client.post('/api/chat', headers=qq, json={'text': '再举个例子'})
        self.assertTrue(any('我在学 TCP 窗口' in m['content'] for m in model.call_args.args[0]))
        state = self.client.get('/api/state', headers=scope(course)).json()
        self.assertIn('再举个例子', [m['content'] for m in state['messages']])
        self.assertEqual(self.client.get('/api/state').json()['messages'], [])
        self.assertEqual(self.client.get('/api/state', headers=scope(other)).json()['messages'], [])

    def test_old_tables_gain_default_scope_without_losing_rows(self):
        database = Path(self.tmp.name) / 'legacy.sqlite'
        with sqlite3.connect(database) as con:
            con.executescript("""
                CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                    pages TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT, created TEXT);
                INSERT INTO documents VALUES(1,'旧笔记','["TCP"]','');
                INSERT INTO messages VALUES(1,'user','旧对话','');
            """)
        with patch.dict('os.environ', {'STUDY_DB': str(database)}):
            db.init()
            db.init()
            self.assertEqual(db.rows('SELECT course_id,location_kind FROM documents'),
                             [{'course_id': 1, 'location_kind': 'page'}])
            self.assertEqual(db.rows('SELECT session_id,content FROM messages'),
                             [{'session_id': 1, 'content': '旧对话'}])

    def test_answer_advances_only_current_course(self):
        python = self.course('Python')
        network = self.course('网络')
        stages = [
            {'role': 'assistant', 'content': '本节讲解'},
            {'role': 'assistant', 'content': '{"question":"答题","expected":"2"}'},
        ]
        for sid in (python, network):
            doc_id = self.client.post('/api/documents', headers=scope(sid),
                                      json={'title': '课程资料', 'content': '内容'}).json()['id']
            with patch.object(agent, 'completion', new=AsyncMock(side_effect=stages)):
                self.client.post('/api/chat', headers=scope(sid),
                                 json={'text': f'带我学完编号{doc_id}的资料'})
        with patch.object(agent, 'completion', new=AsyncMock(return_value={
                'role': 'assistant', 'content': '{"correct":true,"explanation":"正确"}'})):
            response = self.client.post('/api/chat', headers=scope(python), json={'text': '答：2'})
        self.assertIn('任务 #', response.json()['reply'])
        py = self.client.get('/api/state', headers=scope(python)).json()
        net = self.client.get('/api/state', headers=scope(network)).json()
        self.assertEqual(py['tasks'][0]['status'], 'completed')
        self.assertEqual(net['tasks'][0]['status'], 'waiting_answer')
        self.assertIsNone(net['quizzes'][0]['answer'])
