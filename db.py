"""数据库模块：保存资料、聊天和任务，用完连接后及时关闭。"""
import json
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
workspace_scope = ContextVar('workspace_scope', default=(1, 1))


def course_id():
    return workspace_scope.get()[0]


def session_id():
    return workspace_scope.get()[1]

def now():
    return datetime.now(TZ).isoformat(timespec='seconds')

@contextmanager
def connection():
    path = Path(os.environ.get('STUDY_DB', str(ROOT / 'data' / 'study.sqlite3')))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    # 正常完成就提交；中途出错就回滚，避免只保存一半。
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init():
    with connection() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, title TEXT NOT NULL, pages TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, content TEXT, status TEXT DEFAULT 'pending', created TEXT);
        CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, title TEXT, due TEXT, status TEXT DEFAULT 'pending', created TEXT);
        CREATE TABLE IF NOT EXISTS quizzes(id INTEGER PRIMARY KEY, question TEXT, expected TEXT, sources TEXT, answer TEXT, correct INTEGER, explanation TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, request TEXT, status TEXT, trace TEXT DEFAULT '[]', created TEXT);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, state TEXT, reply TEXT);
        CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, title TEXT NOT NULL COLLATE NOCASE UNIQUE);
        CREATE TABLE IF NOT EXISTS study_sessions(
          id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES courses(id),
          title TEXT NOT NULL COLLATE NOCASE, UNIQUE(course_id,title)
        );
        CREATE TABLE IF NOT EXISTS workspace_settings(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT OR IGNORE INTO courses(id,title) VALUES(1,'默认课程');
        INSERT OR IGNORE INTO study_sessions(id,course_id,title) VALUES(1,1,'默认会话');
        INSERT OR IGNORE INTO workspace_settings(key,value) VALUES('qq_session',1);
        CREATE TABLE IF NOT EXISTS document_revision(
          id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO document_revision(id,revision) VALUES(1,0);
        CREATE TABLE IF NOT EXISTS document_search_cache(
          id INTEGER PRIMARY KEY, query TEXT NOT NULL UNIQUE, result TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS invalidate_search_on_insert AFTER INSERT ON documents
        BEGIN
          UPDATE document_revision SET revision=revision+1 WHERE id=1;
          DELETE FROM document_search_cache;
        END;
        CREATE TRIGGER IF NOT EXISTS invalidate_search_on_update AFTER UPDATE ON documents
        BEGIN
          UPDATE document_revision SET revision=revision+1 WHERE id=1;
          DELETE FROM document_search_cache;
        END;
        CREATE TRIGGER IF NOT EXISTS invalidate_search_on_delete AFTER DELETE ON documents
        BEGIN
          UPDATE document_revision SET revision=revision+1 WHERE id=1;
          DELETE FROM document_search_cache;
        END;
        CREATE TABLE IF NOT EXISTS learning_tasks(
          id INTEGER PRIMARY KEY,
          title TEXT,
          document_id INTEGER NOT NULL,
          pages_per_turn INTEGER NOT NULL DEFAULT 2,
          quiz_each_chunk INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL,
          cursor_page INTEGER NOT NULL DEFAULT 1,
          covered_through INTEGER NOT NULL DEFAULT 0,
          active_quiz_id INTEGER,
          goal_json TEXT,
          created TEXT,
          updated TEXT
        );
        CREATE TABLE IF NOT EXISTS task_steps(
          id INTEGER PRIMARY KEY,
          task_id INTEGER NOT NULL,
          step_index INTEGER NOT NULL,
          kind TEXT NOT NULL,
          status TEXT NOT NULL,
          idempotency_key TEXT UNIQUE,
          request_json TEXT,
          evidence_json TEXT,
          created TEXT
        );
        ''')
        con.execute('BEGIN IMMEDIATE')
        if 'location_kind' not in {r['name'] for r in con.execute('PRAGMA table_info(documents)')}:
            con.execute("ALTER TABLE documents ADD COLUMN location_kind TEXT NOT NULL DEFAULT 'page'")
        for tables, column in (
            (('documents', 'quizzes', 'learning_tasks', 'memories', 'reminders'), 'course_id'),
            (('messages', 'runs'), 'session_id'),
        ):
            for table in tables:
                if column not in {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1')
                con.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column},id)')


def create_course(title):
    with connection() as con:
        cid = con.execute('INSERT INTO courses(title) VALUES(?)', (title,)).lastrowid
        sid = con.execute('INSERT INTO study_sessions(course_id,title) VALUES(?,?)', (cid,'默认会话')).lastrowid
    return sid


def get_session(sid):
    found = rows('SELECT * FROM study_sessions WHERE id=?', (sid,))
    return found[0] if found else None


def list_sessions():
    return rows('SELECT s.*, c.title AS course_title FROM study_sessions s '
                'JOIN courses c ON c.id=s.course_id ORDER BY c.id,s.id')


def qq_session_id():
    return rows("SELECT value FROM workspace_settings WHERE key='qq_session'")[0]['value']


def set_qq_session(sid):
    if get_session(sid) is None:
        raise ValueError('会话不存在')
    execute("UPDATE workspace_settings SET value=? WHERE key='qq_session'", (sid,))

def rows(sql, args=()):
    with connection() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]

def execute(sql, args=()):
    with connection() as con:
        return con.execute(sql, args).lastrowid

def encode(value):
    return json.dumps(value, ensure_ascii=False)

def add_document(title, pages, location_kind='page'):
    return execute('INSERT INTO documents(title,pages,created,location_kind,course_id) VALUES(?,?,?,?,?)',
                   (title, encode(pages), now(), location_kind, course_id()))

def sources(query):
    return cached_sources(query)[0]


def cached_sources(query):
    """复用同一资料版本的检索结果，最多保留最近写入的 128 条。"""
    normalized = query.strip().lower()
    key = encode([course_id(), normalized])
    with connection() as con:
        con.execute('BEGIN')
        cached = con.execute('SELECT result FROM document_search_cache WHERE query=?', (key,)).fetchone()
        if cached is not None:
            return json.loads(cached['result']), True
        revision = con.execute('SELECT revision FROM document_revision WHERE id=1').fetchone()['revision']
        documents = con.execute('SELECT * FROM documents WHERE course_id=? ORDER BY id DESC', (course_id(),)).fetchall()

    result = _rank_sources(normalized, documents)
    # 扫描资料期间若有上传或删除，不把旧结果写回新资料库。
    with connection() as con:
        inserted = con.execute(
            'INSERT OR IGNORE INTO document_search_cache(query,result) '
            'SELECT ?,? FROM document_revision WHERE id=1 AND revision=?',
            (key, encode(result), revision),
        )
        if inserted.rowcount:
            con.execute('DELETE FROM document_search_cache WHERE id IN ('
                        'SELECT id FROM document_search_cache ORDER BY id DESC LIMIT -1 OFFSET 128)')
    return result, False


def _rank_sources(query, documents):
    """用关键词找资料；中文还会拆成相邻的两个字来匹配。不是向量检索。"""
    import re
    terms = set(re.findall(r'[a-z0-9_]{2,}|[\u4e00-\u9fff]', query.lower()))
    for word in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.update(word[i:i+2] for i in range(len(word)-1))
    hits = []
    for doc in documents:
        for page, content in enumerate(json.loads(doc['pages']), 1):
            for offset in range(0, len(content), 1000):
                chunk = content[offset:offset+1200]
                score = sum(min(chunk.lower().count(t), 3) * (2 if len(t)>1 else .2) for t in terms)
                if score:
                    hits.append({'document_id': doc['id'], 'title': doc['title'], 'page': page, 'location_kind':doc['location_kind'], 'text': chunk, 'score': score})
    return sorted(hits, key=lambda h: h['score'], reverse=True)[:5]

# --- learning task helpers ---

def create_task(title, document_id, pages_per_turn=2, quiz_each_chunk=1, goal_json=None, status='active', cursor_page=1, covered_through=0):
    ts = now()
    return execute(
        'INSERT INTO learning_tasks(title,document_id,pages_per_turn,quiz_each_chunk,status,cursor_page,covered_through,active_quiz_id,goal_json,created,updated,course_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (title, document_id, pages_per_turn, int(bool(quiz_each_chunk)), status, cursor_page, covered_through, None, goal_json, ts, ts, course_id())
    )

def get_task(task_id):
    found = rows('SELECT * FROM learning_tasks WHERE id=? AND course_id=?', (task_id, course_id()))
    return found[0] if found else None

def list_tasks(statuses=None, limit=40):
    if statuses:
        placeholders = ','.join('?' * len(statuses))
        return rows(f'SELECT * FROM learning_tasks WHERE course_id=? AND status IN ({placeholders}) ORDER BY updated DESC LIMIT ?', (course_id(), *statuses, limit))
    return rows('SELECT * FROM learning_tasks WHERE course_id=? ORDER BY updated DESC LIMIT ?', (course_id(), limit))

def update_task(task_id, **fields):
    if not fields:
        return
    fields = dict(fields)
    fields['updated'] = now()
    cols = ', '.join(f'{k}=?' for k in fields)
    execute(f'UPDATE learning_tasks SET {cols} WHERE id=? AND course_id=?', (*fields.values(), task_id, course_id()))

def next_step_index(task_id):
    found = rows('SELECT COALESCE(MAX(step_index),0) AS m FROM task_steps WHERE task_id=?', (task_id,))
    return (found[0]['m'] if found else 0) + 1

def add_step(task_id, kind, status, idempotency_key=None, request=None, evidence=None, step_index=None):
    idx = step_index if step_index is not None else next_step_index(task_id)
    return execute(
        'INSERT INTO task_steps(task_id,step_index,kind,status,idempotency_key,request_json,evidence_json,created) VALUES(?,?,?,?,?,?,?,?)',
        (task_id, idx, kind, status, idempotency_key, encode(request) if request is not None else None, encode(evidence) if evidence is not None else None, now())
    )

def get_step_by_key(idempotency_key):
    found = rows('SELECT * FROM task_steps WHERE idempotency_key=?', (idempotency_key,))
    return found[0] if found else None

def update_step(step_id, **fields):
    if not fields:
        return
    payload = {}
    for k, v in fields.items():
        if k in ('request', 'evidence'):
            payload[k + '_json'] = encode(v)
        else:
            payload[k] = v
    cols = ', '.join(f'{k}=?' for k in payload)
    execute(f'UPDATE task_steps SET {cols} WHERE id=?', (*payload.values(), step_id))

def list_steps(task_id, limit=100):
    return rows('SELECT * FROM task_steps WHERE task_id=? ORDER BY step_index ASC LIMIT ?', (task_id, limit))
