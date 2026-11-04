"""网页入口：接收请求，检查密码，再调用资料、对话和任务模块。"""
import asyncio
import base64
import io
import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
import db
import agent
from word_import import extract_docx

@asynccontextmanager
async def lifespan(app):
    # 启动时建表；已有的数据不会清空。
    db.init()
    db.execute("UPDATE runs SET status='interrupted' WHERE status='working'")
    yield

app = FastAPI(title='学习搭子', lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware('http')
async def protect(request: Request, call_next):
    # 密码和 API Key 只留在后端，不返回给网页。
    password = os.environ.get('APP_PASSWORD', '')
    if password:
        try:
            value = request.headers.get('authorization', '')
            scheme, payload = value.split(' ', 1)
            supplied = base64.b64decode(payload, validate=True).decode().split(':', 1)[1]
            valid = scheme.lower() == 'basic' and secrets.compare_digest(supplied.encode(), password.encode())
        except (ValueError, IndexError, UnicodeError):
            valid = False
        if not valid:
            return Response(status_code=401, headers={'WWW-Authenticate': 'Basic realm="Study Agent", charset="UTF-8"'})
    elif not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):
        return JSONResponse({'detail':'请先配置 APP_PASSWORD，当前只允许本机访问。'}, status_code=503)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != str(request.base_url).rstrip('/')):
            return JSONResponse({'detail':'拒绝跨站写入请求'}, status_code=403)
    token = None
    if request.url.path.startswith('/api/'):
        qq = request.headers.get('x-study-channel') == 'qq'
        raw = str(db.qq_session_id()) if qq else request.headers.get('x-study-session', '1')
        if not re.fullmatch(r'[1-9][0-9]{0,17}', raw):
            return JSONResponse({'detail': '会话编号无效'}, status_code=400)
        session = db.get_session(int(raw))
        if session is None:
            return JSONResponse({'detail': '会话不存在'}, status_code=404)
        token = db.workspace_scope.set((session['course_id'], session['id']))
    try:
        response = await call_next(request)
    finally:
        if token is not None:
            db.workspace_scope.reset(token)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response

class ChatInput(BaseModel):
    text: str = Field(min_length=1, max_length=14000)

class WorkspaceTitle(BaseModel):
    title: str = Field(min_length=1, max_length=60)

class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=200000)

class ProgressAction(BaseModel):
    action: str = Field(pattern='^(confirm|reject|delete)$')

class ReminderInput(BaseModel):
    title: str = Field(min_length=1,max_length=300)
    due: str

class ReminderAction(BaseModel):
    action: str = Field(pattern='^(done|cancelled)$')

class QuizAnswer(BaseModel):
    answer: str = Field(min_length=1,max_length=5000)

@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')

@app.post('/api/courses')
def create_course(body: WorkspaceTitle):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, '请输入课程名称')
    try:
        return {'session_id': db.create_course(title)}
    except sqlite3.IntegrityError:
        raise HTTPException(409, '课程名称已存在')


@app.post('/api/sessions')
def create_session(body: WorkspaceTitle):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, '请输入会话名称')
    try:
        sid = db.execute('INSERT INTO study_sessions(course_id,title) VALUES(?,?)', (db.course_id(), title))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '这个课程已有同名会话')
    return {'session_id': sid}


@app.post('/api/workspace/qq')
def use_session_in_qq():
    db.set_qq_session(db.session_id())
    return {'session_id': db.session_id()}


@app.get('/api/state')
def state():
    cid = db.course_id()
    sid = db.session_id()
    db.execute("UPDATE reminders SET status='due' WHERE course_id=? AND status='pending' AND due<=?", (cid, db.now()))
    documents = []
    for doc in db.rows('SELECT * FROM documents WHERE course_id=? ORDER BY id DESC', (cid,)):
        pages = json.loads(doc.pop('pages'))
        documents.append({**doc,'page_count':len(pages),'characters':sum(map(len,pages))})
    quizzes = db.rows('SELECT * FROM quizzes WHERE course_id=? ORDER BY id DESC LIMIT 100', (cid,))
    for quiz in quizzes:
        if quiz['answer'] is None:
            quiz.pop('expected')
        quiz['sources'] = [{'document_id':s['document_id'],'title':s['title'],'page':s['page'],'location_kind':s.get('location_kind','page')} for s in json.loads(quiz['sources'])]
    import lesson
    return {'configured':bool(os.environ.get('DEEPSEEK_API_KEY')), 'model':os.environ.get('DEEPSEEK_MODEL','deepseek-flash'),
            'documents':documents,'messages':db.rows('SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 100', (sid,))[::-1],
            'memories':db.rows('SELECT * FROM memories WHERE course_id=? ORDER BY id DESC LIMIT 100', (cid,)),
            'reminders':db.rows('SELECT * FROM reminders WHERE course_id=? ORDER BY due DESC LIMIT 100', (cid,)), 'quizzes':quizzes,
            'runs':db.rows('SELECT * FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 20', (sid,)), 'tasks': lesson.tasks_for_state(),
            'sessions': db.list_sessions(), 'session_id': sid, 'qq_session_id': db.qq_session_id(),
            'now':db.now()}

@app.post('/api/chat')
async def chat(body: ChatInput, request: Request):
    if request.headers.get('x-study-channel') == 'qq':
        text = body.text.strip()
        if text in ('列出会话', '查看会话'):
            lines = [f"#{s['id']} · {s['course_title']} / {s['title']}"
                     for s in db.list_sessions()]
            reply = f"当前会话 #{db.session_id()}。发「切换会话 编号」可切换。\n" + '\n'.join(lines)
            return {'reply': reply, 'replies': agent.split_reply(reply), 'trace': []}
        match = re.fullmatch(r'切换会话\s*#?\s*([1-9][0-9]{0,17})', text)
        if match:
            sid = int(match.group(1))
            session = next((s for s in db.list_sessions() if s['id'] == sid), None)
            if session is None:
                reply = '会话不存在。发「列出会话」查看可用编号。'
            else:
                db.set_qq_session(sid)
                reply = f"已切换到 {session['course_title']} / {session['title']}。接下来可以提问或继续学习。"
            return {'reply': reply, 'replies': agent.split_reply(reply), 'trace': []}
    try:
        return await agent.chat(body.text)
    except agent.ModelError as exc:
        raise HTTPException(502,str(exc))
    except Exception:
        raise HTTPException(500,'本次任务异常停止。已经完成的保存操作仍有效，请查看执行记录后再试。')

_chat_stream_jobs = set()


@app.post('/api/chat/stream')
async def chat_stream(body: ChatInput):
    events = asyncio.Queue()

    async def run():
        sent = 0

        def publish(event):
            nonlocal sent
            if event['type'] == 'message':
                sent += 1
            events.put_nowait(event)

        try:
            result = await agent.chat(body.text, emit=publish)
            for part in result['replies'][sent:]:
                if sent:
                    await asyncio.sleep(0.65)
                publish({'type': 'message', 'text': part})
            events.put_nowait({'type': 'done', **result})
        except agent.ModelError as exc:
            events.put_nowait({'type': 'error', 'text': str(exc)})
        except Exception:
            events.put_nowait({'type': 'error', 'text': '处理已停止，请查看执行记录。已保存的步骤仍有效。'})

    job = asyncio.create_task(run())
    # 客户端断线时保留已开始的请求，避免保存操作只执行一半。
    _chat_stream_jobs.add(job)
    job.add_done_callback(_chat_stream_jobs.discard)

    async def stream():
        yield db.encode({'type': 'progress', 'text': '等待处理'}) + '\n'
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=15)
            except asyncio.TimeoutError:
                yield '\n'
                continue
            yield db.encode(event) + '\n'
            if event['type'] in ('done', 'error'):
                break

    return StreamingResponse(stream(), media_type='application/x-ndjson')


@app.post('/api/documents')
def add_text(body: DocumentInput):
    return {'id':db.add_document(body.title,[body.content])}

@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    data = await file.read(12*1024*1024+1)
    await file.close()
    if len(data)>12*1024*1024:
        raise HTTPException(413,'单个文件最多12MB。')
    filename = Path((file.filename or '资料').replace('\\','/')).name[:120]
    location_kind = 'page'
    try:
        if filename.lower().endswith('.pdf'):
            def extract():
                reader = PdfReader(io.BytesIO(data))
                if reader.is_encrypted:
                    raise ValueError('暂不支持加密PDF，请先解密。')
                if len(reader.pages)>120:
                    raise ValueError('PDF最多120页，请拆分后上传。')
                pages = [p.extract_text() or '' for p in reader.pages]
                if sum(map(len,pages))>300000:
                    raise ValueError('提取文字超过30万字，请拆分。')
                return pages
            pages = await asyncio.to_thread(extract)
        elif filename.lower().endswith('.docx'):
            pages = await asyncio.to_thread(extract_docx, data)
            location_kind = 'section'
        elif filename.lower().endswith('.doc'):
            raise ValueError('旧版.doc请先用Word或WPS另存为.docx，再上传。')
        elif filename.lower().endswith(('.txt','.md')):
            text = data.decode('utf-8-sig')
            if len(text)>200000:
                raise ValueError('文字超过20万字，请拆分。')
            pages = [text]
        else:
            raise ValueError('支持PDF、Word(.docx)、UTF-8 TXT和Markdown文件。')
        if not any(p.strip() for p in pages):
            raise ValueError('未提取到文字，扫描件暂不支持OCR。')
    except Exception as exc:
        raise HTTPException(400,str(exc)[:250])
    return {'id':db.add_document(filename,pages,location_kind),'pages':len(pages),'location_kind':location_kind,'empty_pages':sum(not p.strip() for p in pages)}

@app.get('/api/documents/{doc_id}')
def read_document(doc_id:int):
    found = db.rows('SELECT * FROM documents WHERE id=? AND course_id=?',(doc_id,db.course_id()))
    if not found:
        raise HTTPException(404,'资料不存在')
    return {**found[0],'pages':json.loads(found[0]['pages'])}

@app.delete('/api/documents/{doc_id}')
def delete_document(doc_id:int):
    db.execute('DELETE FROM documents WHERE id=? AND course_id=?',(doc_id,db.course_id()))
    return {'ok':True}

@app.post('/api/memories/{mid}')
def progress(mid:int, body:ProgressAction):
    if body.action=='delete':
        db.execute('DELETE FROM memories WHERE id=? AND course_id=?',(mid,db.course_id()))
    else:
        db.execute("UPDATE memories SET status=? WHERE id=? AND course_id=? AND status='pending'", ('confirmed' if body.action=='confirm' else 'rejected',mid,db.course_id()))
    return {'ok':True}

@app.post('/api/reminders')
def remind(body:ReminderInput):
    try:
        return agent.add_reminder(body.title,body.due)
    except ValueError as exc:
        raise HTTPException(400,str(exc))

@app.post('/api/reminders/{rid}')
def update_reminder(rid:int,body:ReminderAction):
    db.execute("UPDATE reminders SET status=? WHERE id=? AND course_id=? AND status IN ('pending','due')",(body.action,rid,db.course_id()))
    return {'ok':True}

@app.post('/api/quizzes/{qid}/answer')
async def answer_quiz(qid:int,body:QuizAnswer):
    async with agent.LOCK:
        try:
            return await agent.tool('answer_quiz',{'quiz_id':qid,'answer':body.answer})
        except agent.ModelError as exc:
            raise HTTPException(502,str(exc))
        except (ValueError,KeyError) as exc:
            raise HTTPException(400,str(exc))

@app.post('/api/quizzes/{qid}/skip')
async def skip_quiz(qid:int):
    async with agent.LOCK:
        import lesson
        with db.connection() as con:
            updated = con.execute("UPDATE quizzes SET answer='（跳过）',explanation='已跳过，未计入错题' WHERE id=? AND course_id=? AND answer IS NULL",(qid,db.course_id()))
            if updated.rowcount:
                lesson.resolve_task_quiz(con, qid, skipped=True)
    return {'ok':True}


@app.post('/api/tasks/{task_id}/pause')
async def pause_task(task_id: int):
    import lesson
    async with agent.LOCK:
        try:
            task = await lesson.pause_task(task_id)
            return {'ok': True, 'task': task}
        except lesson.LessonError as exc:
            raise HTTPException(400, str(exc))

@app.post('/api/tasks/{task_id}/resume')
async def resume_task(task_id: int):
    """Resume: set active (or keep waiting); if active with no pending quiz, advance one chunk."""
    import lesson
    async with agent.LOCK:
        try:
            result = await lesson.resume_task(task_id, advance=True)
            reply = result.get('message')
            return {
                'ok': True,
                'task': db.get_task(task_id),
                'advanced': bool(result.get('advanced')),
                'reply': reply,
                'status': (result.get('advanced') or {}).get('status') or result['task']['status'],
            }
        except lesson.LessonError as exc:
            raise HTTPException(400, str(exc))
        except agent.ModelError as exc:
            raise HTTPException(502, str(exc))

app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
