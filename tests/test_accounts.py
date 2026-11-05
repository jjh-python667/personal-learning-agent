"""双账号：邀请、隔离、额度和引导。模型调用都是模拟的。"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import test_core
import agent
import auth
import db
import limits
from fastapi.responses import JSONResponse


class AccountTests(unittest.TestCase):
    def setUp(self):
        test_core.StudyTests.setUp(self)

    def tearDown(self):
        test_core.StudyTests.tearDown(self)

    def as_admin(self):
        self.client.auth = ('tester', 'test-password-123456')

    def as_mate(self):
        self.client.auth = None

    def join(self, name, password='mate-pass-123', hours=48, max_uses=3):
        self.as_admin()
        created = self.client.post('/api/invites', json={'hours': hours, 'max_uses': max_uses})
        self.assertEqual(created.status_code, 200, created.text)
        self.as_mate()
        self.client.cookies.clear()
        redeemed = self.client.post('/api/auth/redeem', json={
            'code': created.json()['code'], 'username': name, 'password': password,
        })
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        return created.json()

    def test_admin_cookie_login_and_hash_link(self):
        self.as_mate()
        self.client.cookies.clear()
        denied = self.client.get('/api/state')
        self.assertEqual(denied.status_code, 401)
        logged = self.client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'test-password-123456',
        })
        self.assertEqual(logged.status_code, 200, logged.text)
        self.assertEqual(self.client.get('/api/state').json()['user']['role'], 'admin')
        self.as_admin()
        created = self.client.post('/api/invites', json={'hours': 24, 'max_uses': 2})
        body = created.json()
        self.assertIn('/invite#', body['link'])
        self.assertNotIn('/invite/' + body['code'], body['link'])
        self.assertEqual(self.client.get('/invite').status_code, 200)
        self.assertIn('同学怎么试用', self.client.get('/').text)

    def test_two_classmates_are_isolated_and_guessing_ids_fails(self):
        self.as_admin()
        secret = self.client.post('/api/documents', json={'title': '管理员私有', 'content': '只有管理员的句子'})
        doc_id = secret.json()['id']
        reminder = self.client.post('/api/reminders', json={
            'title': '管理员提醒', 'due': '2035-01-01T20:00:00+08:00',
        })
        self.assertEqual(reminder.status_code, 200)
        memory = asyncio.run(agent.tool('propose_progress', {'content': '管理员进度'}))
        qid = db.execute(
            'INSERT INTO quizzes(question,expected,sources,created,course_id,user_id) VALUES(?,?,?,?,?,?)',
            ('私密题', '答案', '[]', db.now(), 1, 1))
        task = __import__('lesson').start_task(doc_id)
        db.execute(
            'INSERT INTO messages(role,content,created,session_id,user_id) VALUES(?,?,?,?,?)',
            ('user', '管理员悄悄话', db.now(), 1, 1))
        db.execute(
            'INSERT INTO runs(request,status,trace,created,session_id,user_id) VALUES(?,?,?,?,?,?)',
            ('管理员的提问', 'completed', '[]', db.now(), 1, 1))
        db.add_document('网络', ['TCP 管理员独有'])
        db.cached_sources('TCP')
        self.join('ana')
        self.as_mate()
        self.assertEqual(self.client.get(f'/api/documents/{doc_id}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/documents/{doc_id}').status_code, 404)
        self.assertEqual(self.client.post(f'/api/memories/{memory["id"]}', json={'action': 'confirm'}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/reminders/{reminder.json()["id"]}', json={'action': 'done'}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/quizzes/{qid}/answer', json={'answer': '猜'}).status_code, 400)
        self.assertEqual(self.client.post(f'/api/tasks/{task["id"]}/pause').status_code, 400)
        self.assertEqual(self.client.get('/api/state', headers={'X-Study-Session': '1'}).status_code, 404)
        state = self.client.get('/api/state').json()
        self.assertEqual(state['documents'], [])
        self.assertEqual(state['messages'], [])
        self.assertEqual(state['runs'], [])
        self.assertEqual(state['reminders'], [])
        self.assertEqual(state['quizzes'], [])
        self.assertNotIn('管理员进度', [item['content'] for item in state['memories']])
        self.client.cookies.clear()
        self.join('bao')
        self.as_mate()
        self.assertEqual(self.client.get('/api/state').json()['documents'], [])
        uid = db.rows("SELECT id FROM users WHERE username='bao'")[0]['id']
        user_token = db.user_scope.set(uid)
        try:
            sid = db.default_session_id()
            session = db.get_session(sid)
            scope = db.workspace_scope.set((session['course_id'], sid))
            try:
                hits, cached = db.cached_sources('TCP')
                self.assertEqual(hits, [])
                self.assertFalse(cached)
            finally:
                db.workspace_scope.reset(scope)
        finally:
            db.user_scope.reset(user_token)
        self.as_admin()
        self.assertEqual(self.client.get(f'/api/documents/{doc_id}').status_code, 200)
        self.assertEqual(db.rows('SELECT user_id FROM courses WHERE id=1')[0]['user_id'], 1)
        self.assertEqual(db.rows("SELECT role FROM users WHERE id=1")[0]['role'], 'admin')

    def test_revoke_and_expiry_log_the_classmate_out(self):
        created = self.join('leaver')
        self.as_mate()
        self.assertEqual(self.client.get('/api/state').status_code, 200)
        self.as_admin()
        self.assertEqual(self.client.post(f'/api/invites/{created["id"]}/revoke').status_code, 200)
        self.as_mate()
        revoked = self.client.get('/api/state')
        self.assertEqual(revoked.status_code, 401)
        self.assertIn('邀请', revoked.json()['detail'])
        self.client.cookies.clear()
        again = self.join('stayer')
        self.as_mate()
        db.execute("UPDATE invites SET expires_at=? WHERE id=?", ('2020-01-01T00:00:00+08:00', again['id']))
        expired = self.client.get('/api/state')
        self.assertEqual(expired.status_code, 401)
        self.assertIn('邀请', expired.json()['detail'])
        relogin = self.client.post('/api/auth/login', json={'username': 'stayer', 'password': 'mate-pass-123'})
        self.assertEqual(relogin.status_code, 401)

    def test_logout_login_and_member_cannot_touch_qq_or_invites(self):
        self.join('qqmate')
        self.as_mate()
        self.assertEqual(self.client.post('/api/workspace/qq').status_code, 403)
        self.assertEqual(self.client.post('/api/invites', json={'hours': 1, 'max_uses': 1}).status_code, 403)
        qq = self.client.post('/api/chat', headers={'X-Study-Channel': 'qq'}, json={'text': '列出会话'})
        self.assertEqual(qq.status_code, 403)
        self.as_admin()
        qq_session = self.client.get('/api/state').json()['qq_session_id']
        self.as_mate()
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        logged = self.client.post('/api/auth/login', json={'username': 'qqmate', 'password': 'mate-pass-123'})
        self.assertEqual(logged.status_code, 200, logged.text)
        self.assertEqual(self.client.get('/api/state').status_code, 200)
        self.client.auth = ('tester', 'wrong-password')
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.as_admin()
        self.assertEqual(self.client.get('/api/state').json()['qq_session_id'], qq_session)

    def test_quota_stops_the_model_but_listing_still_works(self):
        os.environ['AI_DAILY_QUOTA'] = '0'
        limits.reset_runtime()
        model = AsyncMock(side_effect=AssertionError('不应调用模型'))
        with patch.object(agent, 'completion', model):
            blocked = self.client.post('/api/chat', json={'text': '你好'})
            listed = self.client.post('/api/chat', json={'text': '请列出我的资料'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('用完', blocked.json()['detail'])
        model.assert_not_called()
        self.assertEqual(listed.status_code, 200)
        self.assertIn('没有资料', listed.json()['reply'])

    def test_frequency_and_concurrency_stop_extra_model_calls(self):
        os.environ['AI_MIN_INTERVAL_SECONDS'] = '60'
        limits.reset_runtime()
        model = AsyncMock(return_value={'role': 'assistant', 'content': '好的[[下一条]]继续'})
        with patch.object(agent, 'completion', model):
            first = self.client.post('/api/chat', json={'text': '你好'})
            second = self.client.post('/api/chat', json={'text': '再问一次'})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 429)
        self.assertIn('太快', second.json()['detail'])
        self.assertEqual(model.await_count, 1)
        os.environ['AI_MIN_INTERVAL_SECONDS'] = '0'
        os.environ['AI_MAX_CONCURRENCY'] = '1'
        limits.reset_runtime()
        limits._inflight_global = 1
        blocked_model = AsyncMock(side_effect=AssertionError('不应调用模型'))
        with patch.object(agent, 'completion', blocked_model):
            crowded = self.client.post('/api/chat', json={'text': '还有人吗'})
        self.assertEqual(crowded.status_code, 429)
        self.assertIn('同时', crowded.json()['detail'])
        blocked_model.assert_not_called()

        async def overlap():
            async def hold():
                async with limits.turn(1):
                    await asyncio.sleep(0.05)
                    return 'held'

            async def late():
                await asyncio.sleep(0.01)
                async with limits.turn(1):
                    return 'late'

            return await asyncio.gather(hold(), late(), return_exceptions=True)

        limits.reset_runtime()
        os.environ['AI_USER_CONCURRENCY'] = '1'
        held, late = asyncio.run(overlap())
        self.assertEqual(held, 'held')
        self.assertIsInstance(late, limits.LimitError)

    def test_restart_keeps_each_users_rows(self):
        self.join('keepmate')
        self.as_mate()
        doc_id = self.client.post('/api/documents', json={'title': '还在', 'content': '重启后还在'}).json()['id']
        db.init()
        self.assertEqual(self.client.get(f'/api/documents/{doc_id}').json()['title'], '还在')
        self.as_admin()
        self.assertEqual(self.client.get(f'/api/documents/{doc_id}').status_code, 404)

    def test_sample_guide_file_limit_and_usage_log(self):
        self.join('samplemate')
        self.as_mate()
        self.assertTrue(self.client.get('/api/state').json()['onboarding']['needs_choice'])
        chosen = self.client.post('/api/onboarding', json={'choice': 'sample'})
        self.assertEqual(chosen.status_code, 200, chosen.text)
        state = self.client.get('/api/state').json()
        self.assertGreaterEqual(len(state['documents']), 2)
        self.assertTrue(state['onboarding']['failure_tips'])
        model = AsyncMock(return_value={'role': 'assistant', 'content': '循环就是重复做。[[下一条]]再看 while。'})
        with self.assertLogs('study.usage', level='INFO') as captured:
            with patch.object(agent, 'completion', model):
                reply = self.client.post('/api/chat', json={'text': '示例里的循环是什么？'})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.client.get('/api/state').json()['onboarding']['step'], 'learn')
        logged = '\n'.join(captured.output)
        self.assertIn('study_ok', logged)
        self.assertIn('latency_ms', logged)
        self.assertNotIn('示例里的循环是什么', logged)
        self.assertNotIn('mate-pass-123', logged)
        self.assertNotIn('test-password-123456', logged)
        self.as_admin()
        self.assertEqual(self.client.get('/api/state').json()['documents'], [])
        os.environ['MAX_UPLOAD_BYTES'] = '20'
        upload = self.client.post('/api/upload', files={'file': ('note.txt', '一二三四五六七八九十'.encode(), 'text/plain')})
        self.assertEqual(upload.status_code, 413)
        self.assertIn('太大', upload.json()['detail'])
        stored = db.rows("SELECT password_hash FROM users WHERE username='samplemate'")[0]['password_hash']
        self.assertNotIn('mate-pass-123', stored)

    def test_secure_cookie_follows_https_proxy_header(self):
        response = JSONResponse({})

        class Request:
            headers = {'x-forwarded-proto': 'https'}
            url = type('URL', (), {'scheme': 'http'})()

        auth.attach_cookie(response, Request(), 'token')
        self.assertIn('secure', response.headers['set-cookie'].lower())


if __name__ == '__main__':
    unittest.main()
