import json
import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_core
import agent
import db
import lesson


def reply(text):
    return {'role': 'assistant', 'content': text}


def quiz(question):
    return reply(json.dumps({'question': question, 'expected': '2'}))


class ContinuousChatTests(unittest.TestCase):
    setUp = test_core.StudyTests.setUp
    tearDown = test_core.StudyTests.tearDown

    def test_one_request_yields_three_distinct_messages(self):
        answer = '先回答。[[下一条]]再补一个例子。[[下一条]]最后给个练习。'
        with patch('agent.completion', new=AsyncMock(return_value=reply(answer))):
            response = self.client.post('/api/chat/stream', json={'text': '讲讲变量'})
        events = [json.loads(line) for line in response.text.splitlines() if line]
        parts = [event['text'] for event in events if event['type'] == 'message']
        self.assertEqual(parts, ['先回答。', '再补一个例子。', '最后给个练习。'])
        self.assertEqual(parts, events[-1]['replies'])
        saved = db.rows("SELECT content FROM messages WHERE role='assistant' ORDER BY id")
        self.assertEqual([row['content'] for row in saved], parts)

    def test_qq_replies_separately_with_unique_sequence(self):
        # CI does not require the optional QQ SDK or a real QQ account.
        spec = importlib.util.spec_from_file_location(
            'qq_bridge_for_test', Path(agent.__file__).with_name('qq_bridge.py'))
        bridge = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'botpy': SimpleNamespace(Client=object)}):
            spec.loader.exec_module(bridge)
        response = SimpleNamespace(status_code=200, json=lambda: {
            'reply': '甲\n\n乙\n\n丙', 'replies': ['甲', '乙', '丙']})
        client = AsyncMock()
        client.__aenter__.return_value.post.return_value = response
        message = SimpleNamespace(id='msg-123', author=SimpleNamespace(user_openid='owner'),
                                  content='讲讲变量', attachments=[], reply=AsyncMock())
        with patch.dict(os.environ, {'QQ_OWNER_OPENID': 'owner'}), \
             patch.object(bridge.httpx, 'AsyncClient', return_value=client), \
             patch.object(bridge.asyncio, 'sleep', new=AsyncMock()):
            asyncio.run(bridge.Client().on_c2c_message_create(message))
            asyncio.run(bridge.Client().on_c2c_message_create(message))
        self.assertEqual([call.kwargs for call in message.reply.await_args_list], [
            {'content': part, 'msg_type': 0, 'msg_seq': i}
            for i, part in enumerate(['甲', '乙', '丙'], 1)])

    def start(self):
        doc = db.add_document('网络讲义', ['内容 1', '内容 2', '内容 3', '内容 4'])
        with patch('agent.completion', new=AsyncMock(side_effect=[reply('前两页讲解'), quiz('第一道题')])):
            response = self.client.post('/api/chat', json={'text': f'带我学完编号{doc}的资料，每次两页，开启连学'})
        self.assertEqual(response.status_code, 200)
        task = lesson.list_resumable_tasks()[0]
        self.assertEqual(task['covered_through'], 2)
        self.assertTrue(lesson.continues_after_answer(task))
        return task

    def test_followup_keeps_quiz_and_uses_history(self):
        task = self.start()
        model = AsyncMock(return_value=reply('这里是给你的提示'))
        with patch('agent.completion', new=model):
            response = self.client.post('/api/chat', json={'text': '为什么？给个例子'})
        self.assertIn('这里是给你的提示', response.json()['reply'])
        messages, tools = model.call_args.args
        self.assertTrue(any('前两页讲解' in msg['content'] for msg in messages))
        self.assertNotIn('answer_quiz', [tool['function']['name'] for tool in tools])
        self.assertEqual(db.get_task(task['id'])['active_quiz_id'], task['active_quiz_id'])
        self.assertIsNone(db.rows('SELECT answer FROM quizzes WHERE id=?', (task['active_quiz_id'],))[0]['answer'])

    def test_stream_grades_then_starts_next_chunk(self):
        task = self.start()
        steps = [reply('{"correct":true,"explanation":"答对了"}'), reply('后两页讲解'), quiz('第二道题')]
        with patch('agent.completion', new=AsyncMock(side_effect=steps)):
            response = self.client.post('/api/chat/stream', json={'text': '答：2'})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines() if line]
        feedback = [e for e in events if e['type'] == 'message']
        self.assertIn('答对了', feedback[0]['text'])
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIn('第二道题', events[-1]['reply'])
        updated = db.get_task(task['id'])
        self.assertEqual(updated['covered_through'], 4)
        self.assertEqual(updated['status'], 'waiting_answer')
        self.assertNotEqual(updated['active_quiz_id'], task['active_quiz_id'])
        self.assertEqual(db.rows('SELECT answer FROM quizzes WHERE id=?', (task['active_quiz_id'],))[0]['answer'], '2')

    def test_next_chunk_failure_preserves_answer_and_cursor(self):
        task = self.start()
        steps = [reply('{"correct":true,"explanation":"已批改"}'), agent.ModelError('临时失败')]
        with patch('agent.completion', new=AsyncMock(side_effect=steps)):
            response = self.client.post('/api/chat', json={'text': '2'})
        self.assertIn('下一段暂未推进', response.json()['reply'])
        self.assertEqual(db.get_task(task['id'])['covered_through'], 2)
        self.assertEqual(db.get_task(task['id'])['status'], 'active')
        self.assertEqual(db.rows('SELECT answer FROM quizzes WHERE id=?', (task['active_quiz_id'],))[0]['answer'], '2')
