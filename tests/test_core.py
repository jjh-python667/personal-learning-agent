import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from pypdf import PdfWriter
import db
import agent
import limits
from main import app

class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{
            'STUDY_DB':str(Path(self.tmp.name)/'db.sqlite'),
            'APP_PASSWORD':'test-password-123456',
            'DEEPSEEK_API_KEY':'',
            'AI_DAILY_QUOTA':'500',
            'AI_MIN_INTERVAL_SECONDS':'0',
            'AI_MAX_CONCURRENCY':'8',
            'AI_USER_CONCURRENCY':'4',
            'MAX_UPLOAD_BYTES':str(12*1024*1024),
        })
        self.env.start()
        limits.reset_runtime()
        self.client=TestClient(app)
        self.client.__enter__()
        self.client.auth=('test','test-password-123456')
    def tearDown(self):
        self.client.__exit__(None,None,None)
        limits.reset_runtime()
        self.env.stop()
        self.tmp.cleanup()
    def test_auth_and_origin(self):
        self.assertEqual(self.client.get('/api/state',auth=('test','wrong')).status_code,401)
        self.assertEqual(self.client.post('/api/documents',json={'title':'x','content':'y'},headers={'origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/').status_code,200)
    def test_document_retrieval_and_deletion(self):
        r=self.client.post('/api/documents',json={'title':'网络笔记','content':'TCP使用三次握手建立连接。序号帮助双方确认状态。'})
        did=r.json()['id']
        hits=db.sources('TCP三次握手')
        self.assertEqual(hits[0]['document_id'],did)
        self.assertEqual(hits[0]['page'],1)
        self.assertEqual(self.client.get('/api/documents/'+str(did)).status_code,200)
        self.client.delete('/api/documents/'+str(did))
        self.assertEqual(db.sources('TCP'),[])
    def test_pdf_scan_and_invalid_upload(self):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        data=io.BytesIO();writer.write(data)
        self.assertEqual(self.client.post('/api/upload',files={'file':('scan.pdf',data.getvalue(),'application/pdf')}).status_code,400)
        self.assertEqual(self.client.post('/api/upload',files={'file':('bad.pdf',b'not PDF','application/pdf')}).status_code,400)
        self.assertEqual(self.client.post('/api/upload',files={'file':('note.txt','进程与线程的学习笔记'.encode(),'text/plain')}).status_code,200)
    def test_pdf_text_preserves_page_numbers(self):
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter()
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        for text in ('CPU schedules processes.','TCP uses a three way handshake.'):
            page=writer.add_blank_page(width=300,height=300)
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 20 250 Td ('+text+') Tj ET').encode())
            page[NameObject('/Contents')]=writer._add_object(stream)
        data=io.BytesIO();writer.write(data)
        result=self.client.post('/api/upload',files={'file':('notes.pdf',data.getvalue(),'application/pdf')})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['pages'],2)
        self.assertEqual(db.sources('handshake')[0]['page'],2)
    def test_progress_requires_human_confirmation(self):
        r=asyncio.run(agent.tool('propose_progress',{'content':'完成网络第三章'}))
        self.assertEqual(db.rows('SELECT status FROM memories')[0]['status'],'pending')
        with self.assertRaises(ValueError):
            asyncio.run(agent.tool('confirm_progress',{'id':r['id']}))
        self.client.post('/api/memories/'+str(r['id']),json={'action':'confirm'})
        self.assertEqual(db.rows('SELECT status FROM memories')[0]['status'],'confirmed')
    def test_reminder_due_cancel_and_persistence(self):
        r=self.client.post('/api/reminders',json={'title':'复习','due':'2035-01-01T20:00:00+08:00'})
        self.assertEqual(r.status_code,200)
        rid=r.json()['id']
        db.execute('UPDATE reminders SET due=? WHERE id=?',('2020-01-01T00:00:00+08:00',rid))
        self.assertEqual(self.client.get('/api/state').json()['reminders'][0]['status'],'due')
        db.init()
        self.assertEqual(db.rows('SELECT status FROM reminders')[0]['status'],'due')
        self.client.post('/api/reminders/'+str(rid),json={'action':'done'})
        self.assertEqual(db.rows('SELECT status FROM reminders')[0]['status'],'done')
        self.assertEqual(self.client.post('/api/reminders',json={'title':'x','due':'yesterday'}).status_code,400)
    def test_quiz_answers_hidden_and_wrong_saved(self):
        db.add_document('TCP',['TCP使用三次握手建立连接。'])
        generate={'role':'assistant','content':json.dumps({'question':'TCP用什么建立连接？','expected':'三次握手'})}
        with patch('agent.completion',new=AsyncMock(return_value=generate)):
            q=asyncio.run(agent.tool('start_quiz',{'topic':'TCP'}))
        data=self.client.get('/api/state').json()['quizzes'][0]
        self.assertNotIn('expected',data)
        grade={'role':'assistant','content':json.dumps({'correct':False,'explanation':'TCP需要三次握手，不是一次。'})}
        with patch('agent.completion',new=AsyncMock(return_value=grade)):
            r=self.client.post('/api/quizzes/'+str(q['id'])+'/answer',json={'answer':'一次握手'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(db.rows('SELECT correct FROM quizzes')[0]['correct'],0)
        self.assertEqual(self.client.post('/api/quizzes/'+str(q['id'])+'/answer',json={'answer':'三次'}).status_code,400)
    def test_real_tool_loop_and_failure_record(self):
        sequence=[{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'save_document','arguments':json.dumps({'title':'笔记','content':'操作系统管理资源。'})}}]}, {'role':'assistant','content':'资料已保存。'}]
        with patch('agent.completion',new=AsyncMock(side_effect=sequence)):
            result=self.client.post('/api/chat',json={'text':'保存这段笔记：操作系统管理资源。'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(db.rows('SELECT * FROM documents')),1)
        self.assertEqual(result.json()['trace'][0]['tool'],'save_document')
        failed=self.client.post('/api/chat',json={'text':'你好'})
        self.assertEqual(failed.status_code,502)
        self.assertEqual(db.rows('SELECT status FROM runs ORDER BY id DESC LIMIT 1')[0]['status'],'failed')

if __name__=='__main__':
    unittest.main()
