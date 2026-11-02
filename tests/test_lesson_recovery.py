"""Cross-channel grading, numeric replies, and retry regression tests."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
import test_core
import db
import agent
import lesson

GRADE = {'role':'assistant','content':json.dumps({'correct':True,'explanation':'正确'})}

class RecoveryTests(unittest.TestCase):
    setUp = test_core.StudyTests.setUp
    tearDown = test_core.StudyTests.tearDown

    def waiting_task(self, total=4):
        did = db.add_document('复习.pdf',['课程内容']*total)
        task = lesson.start_task(did)
        qid = db.execute('INSERT INTO quizzes(question,expected,sources,created) VALUES(?,?,?,?)',('一加一？','2','[]',db.now()))
        db.update_task(task['id'],status='waiting_answer',covered_through=2,cursor_page=3,active_quiz_id=qid)
        return task['id'],qid

    def test_web_answer_then_resume_advances_next_chunk(self):
        tid,qid = self.waiting_task()
        sequence = [GRADE,{'role':'assistant','content':'下一块讲解'},
                    {'role':'assistant','content':json.dumps({'question':'下一题','expected':'答案'})}]
        with patch('agent.completion',new=AsyncMock(side_effect=sequence)) as mock:
            response = self.client.post(f'/api/quizzes/{qid}/answer',json={'answer':'2'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(db.get_task(tid)['status'],'active')
            self.assertIsNone(db.get_task(tid)['active_quiz_id'])
            resumed = self.client.post(f'/api/tasks/{tid}/resume')
        self.assertEqual(resumed.status_code,200,resumed.text)
        self.assertTrue(resumed.json()['advanced'])
        self.assertEqual(db.get_task(tid)['covered_through'],4)
        self.assertEqual(mock.await_count,3)

    def test_numeric_chat_answer_is_graded(self):
        tid,qid = self.waiting_task()
        with patch('agent.completion',new=AsyncMock(return_value=GRADE)) as mock:
            response = self.client.post('/api/chat',json={'text':'2'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(db.rows('SELECT answer FROM quizzes WHERE id=?',(qid,))[0]['answer'],'2')
        self.assertEqual(db.get_task(tid)['status'],'active')
        self.assertEqual(mock.await_count,1)

    def test_explicit_task_choice_still_works(self):
        t1,q1=self.waiting_task()
        t2,q2=self.waiting_task()
        with patch('agent.completion',new=AsyncMock(side_effect=AssertionError('不需要模型'))):
            choices=self.client.post('/api/chat',json={'text':'继续'})
            self.assertIn('多个可继续',choices.json()['reply'])
            chosen=self.client.post('/api/chat',json={'text':str(t2)})
        self.assertIn(f'任务 #{t2}',chosen.json()['reply'])
        self.assertEqual(db.rows('SELECT answer FROM quizzes WHERE id=?',(q2,))[0]['answer'],None)

    def test_repeated_grade_failure_then_success_reuses_one_step(self):
        tid,qid=self.waiting_task()
        with patch('agent.completion',new=AsyncMock(side_effect=[agent.ModelError('故障1'),agent.ModelError('故障2'),GRADE])):
            for _ in range(2):
                with self.assertRaises(lesson.LessonError):
                    asyncio.run(lesson.handle_answer_for_task(tid,'2'))
                self.assertEqual(db.get_task(tid)['status'],'waiting_answer')
                self.assertEqual(db.get_task(tid)['covered_through'],2)
                self.assertIsNone(db.rows('SELECT answer FROM quizzes WHERE id=?',(qid,))[0]['answer'])
            result=asyncio.run(lesson.handle_answer_for_task(tid,'2'))
        self.assertTrue(result['ok'])
        steps=db.rows("SELECT * FROM task_steps WHERE task_id=? AND kind='grade'",(tid,))
        self.assertEqual(len(steps),1)
        self.assertEqual(steps[0]['status'],'succeeded')
        self.assertEqual(db.get_task(tid)['status'],'active')

    def test_skip_clears_task_and_keeps_pause(self):
        tid,qid=self.waiting_task()
        asyncio.run(lesson.pause_task(tid))
        response=self.client.post(f'/api/quizzes/{qid}/skip')
        self.assertEqual(response.status_code,200)
        self.assertEqual(db.get_task(tid)['status'],'paused')
        self.assertIsNone(db.get_task(tid)['active_quiz_id'])
        self.client.post(f'/api/quizzes/{qid}/skip')
        self.assertEqual(len(db.rows("SELECT id FROM task_steps WHERE kind='skip'")),1)

    def test_final_web_answer_completes_without_duplicate_grading(self):
        tid,qid=self.waiting_task(total=2)
        with patch('agent.completion',new=AsyncMock(return_value=GRADE)) as mock:
            self.assertEqual(self.client.post(f'/api/quizzes/{qid}/answer',json={'answer':'2'}).status_code,200)
            self.assertEqual(self.client.post(f'/api/quizzes/{qid}/answer',json={'answer':'2'}).status_code,400)
        self.assertEqual(mock.await_count,1)
        self.assertEqual(db.get_task(tid)['status'],'completed')

    def test_task_write_failure_rolls_back_quiz_answer(self):
        tid,qid=self.waiting_task()
        with patch('agent.completion',new=AsyncMock(return_value=GRADE)), patch('lesson.resolve_task_quiz',side_effect=RuntimeError('模拟写入失败')):
            with self.assertRaises(RuntimeError):
                asyncio.run(agent.tool('answer_quiz',{'quiz_id':qid,'answer':'2'}))
        self.assertIsNone(db.rows('SELECT answer FROM quizzes WHERE id=?',(qid,))[0]['answer'])
        self.assertEqual(db.get_task(tid)['status'],'waiting_answer')
