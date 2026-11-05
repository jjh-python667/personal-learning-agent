"""网页账号和邀请码。QQ 不走这里，仍然只认管理员的 APP_PASSWORD。"""
import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from contextvars import ContextVar

import db

COOKIE = 'study_auth'
PBKDF2_ROUNDS = 120_000
current_user = ContextVar('current_user', default=None)
_dummy_hash = None


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ROUNDS)
    return f'pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}'


def verify_password(password, stored):
    try:
        kind, rounds, salt_hex, digest_hex = stored.split('$', 3)
        if kind != 'pbkdf2':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _dummy():
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password('not-a-real-password')
    return _dummy_hash


def ensure_admin():
    """保证有一个管理员。网页密码就是环境变量 APP_PASSWORD。"""
    password = os.environ.get('APP_PASSWORD', '')
    with db.connection() as con:
        row = con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            # 没配密码时先放一个随机哈希，本机仍可直接打开；配上密码后下次启动会更新。
            digest = hash_password(password or secrets.token_urlsafe(24))
            con.execute(
                'INSERT INTO users(id, username, password_hash, role, created) VALUES(1,?,?,?,?)',
                ('admin', digest, 'admin', db.now()))
        elif password:
            current_hash = con.execute('SELECT password_hash FROM users WHERE id=?', (row['id'],)).fetchone()['password_hash']
            if not verify_password(password, current_hash):
                con.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(password), row['id']))
                con.execute('DELETE FROM auth_sessions WHERE user_id=?', (row['id'],))


def current():
    user = current_user.get()
    if user is None:
        raise RuntimeError('当前请求没有登录用户')
    return user


def _public_user(row):
    return {
        'id': row['id'], 'username': row['username'], 'role': row['role'],
        'invite_id': row['invite_id'], 'guide_choice': row['guide_choice'],
        'guide_step': row['guide_step'],
    }


def _admin_row(con):
    return con.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()


def identify(request):
    """认出当前请求是谁。失败时返回 (None, {detail, status, clear_cookie})。"""
    password = os.environ.get('APP_PASSWORD', '')
    header = request.headers.get('authorization', '')
    local = bool(request.client and request.client.host in ('127.0.0.1', '::1', 'testclient'))
    if not password:
        if local:
            with db.connection() as con:
                admin = _admin_row(con)
            if admin is None:
                return None, {'detail': '请先配置 APP_PASSWORD，当前只允许本机访问。', 'status': 503}
            return _public_user(admin), None
        return None, {'detail': '请先配置 APP_PASSWORD，当前只允许本机访问。', 'status': 503}
    if header:
        user = _admin_from_basic(header, password)
        if user is None:
            # 带了密码头但不对时，不再看 Cookie，避免把错误密码当成同学登录。
            return None, {'detail': '请先登录。', 'status': 401}
        return user, None
    token = request.cookies.get(COOKIE, '')
    if not token:
        return None, {'detail': '请先登录。', 'status': 401}
    return _user_from_token(token)


def _admin_from_basic(header, password):
    try:
        scheme, payload = header.split(' ', 1)
        if scheme.lower() != 'basic':
            return None
        import base64
        supplied = base64.b64decode(payload, validate=True).decode().split(':', 1)[1]
        if not secrets.compare_digest(supplied.encode(), password.encode()):
            return None
    except (ValueError, IndexError, UnicodeError):
        return None
    with db.connection() as con:
        admin = _admin_row(con)
    return _public_user(admin) if admin else None


def _user_from_token(token):
    with db.connection() as con:
        row = con.execute(
            '''SELECT u.*, s.expires AS session_expires FROM auth_sessions s
               JOIN users u ON u.id=s.user_id WHERE s.token=?''', (token,)).fetchone()
        if row is None or row['session_expires'] <= db.now():
            con.execute('DELETE FROM auth_sessions WHERE token=?', (token,))
            return None, {'detail': '登录已过期，请重新登录。', 'status': 401, 'clear_cookie': True}
        if row['role'] != 'admin':
            invite = con.execute('SELECT * FROM invites WHERE id=?', (row['invite_id'],)).fetchone()
            if invite is None or invite['revoked'] or invite['expires_at'] <= db.now():
                con.execute('DELETE FROM auth_sessions WHERE user_id=?', (row['id'],))
                return None, {
                    'detail': '邀请已经过期或被收回，请向管理员再要一个新链接，然后重新登录。',
                    'status': 401, 'clear_cookie': True,
                }
        return _public_user(row), None


def attach_cookie(response, request, token):
    forwarded = (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
    secure = request.url.scheme == 'https' or forwarded == 'https'
    response.set_cookie(
        COOKIE, token, max_age=14 * 24 * 3600, httponly=True, samesite='lax',
        secure=secure, path='/')


def public_base(request):
    base = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    return base or str(request.base_url).rstrip('/')


def _new_token(con, user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(db.TZ) + timedelta(days=14)).isoformat(timespec='seconds')
    con.execute(
        'INSERT INTO auth_sessions(token,user_id,created,expires) VALUES(?,?,?,?)',
        (token, user_id, db.now(), expires))
    return token


def login(username, password):
    name = username.strip()
    with db.connection() as con:
        row = con.execute('SELECT * FROM users WHERE username=?', (name,)).fetchone()
        stored = row['password_hash'] if row else _dummy()
        if not verify_password(password, stored) or row is None:
            return None
        if row['role'] != 'admin':
            invite = con.execute('SELECT * FROM invites WHERE id=?', (row['invite_id'],)).fetchone()
            if invite is None or invite['revoked'] or invite['expires_at'] <= db.now():
                return 'expired'
        token = _new_token(con, row['id'])
        username = row['username']
        role = row['role']
    return {'token': token, 'username': username, 'role': role}


def logout(token):
    if token:
        db.execute('DELETE FROM auth_sessions WHERE token=?', (token,))


def create_invite(hours, max_uses, request):
    expires = (datetime.now(db.TZ) + timedelta(hours=hours)).isoformat(timespec='seconds')
    admin_id = current()['id']
    code = None
    invite_id = None
    for _ in range(3):
        candidate = secrets.token_urlsafe(9)
        try:
            invite_id = db.execute(
                'INSERT INTO invites(code,created_by,expires_at,max_uses,created) VALUES(?,?,?,?,?)',
                (candidate, admin_id, expires, max_uses, db.now()))
            code = candidate
            break
        except sqlite3.IntegrityError:
            continue
    if code is None:
        raise RuntimeError('邀请码没有生成成功')
    return {
        'id': invite_id, 'code': code,
        'link': f'{public_base(request)}/invite#{code}',
        'expires_at': expires, 'max_uses': max_uses, 'use_count': 0, 'revoked': 0,
    }


def list_invites():
    admin_id = current()['id']
    found = db.rows(
        'SELECT id,code,expires_at,max_uses,use_count,revoked,created FROM invites '
        'WHERE created_by=? ORDER BY id DESC', (admin_id,))
    base = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    for item in found:
        # 列表里只在配置了对外地址时拼链接；否则前端用自己的网址加 #邀请码。
        item['link'] = f'{base}/invite#{item["code"]}' if base else f'/invite#{item["code"]}'
        item['revoked'] = bool(item['revoked'])
    return found


def revoke_invite(invite_id):
    admin_id = current()['id']
    with db.connection() as con:
        updated = con.execute(
            'UPDATE invites SET revoked=1 WHERE id=? AND created_by=?', (invite_id, admin_id))
        if updated.rowcount != 1:
            return False
    # 不在这里删登录状态。下一次请求会看到邀请已收回，再清掉登录并提示同学。
    return True


def redeem(code, username, password):
    name = username.strip()
    if name.casefold() == 'admin' or not re.fullmatch(r'[0-9A-Za-z_\u4e00-\u9fff]{2,20}', name):
        raise ValueError('用户名用 2 到 20 位的中文、字母或数字，不能叫 admin。')
    if not isinstance(password, str) or not 8 <= len(password) <= 72:
        raise ValueError('密码至少 8 位，最长 72 位。')
    now = db.now()
    with db.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        invite = con.execute('SELECT * FROM invites WHERE code=?', (code.strip(),)).fetchone()
        if invite is None or invite['revoked'] or invite['expires_at'] <= now or invite['use_count'] >= invite['max_uses']:
            raise ValueError('这个邀请不能用了：可能过期、次数用完，或已被收回。请向管理员再要一个。')
        taken = con.execute(
            'UPDATE invites SET use_count=use_count+1 WHERE id=? AND revoked=0 AND use_count<max_uses AND expires_at>?',
            (invite['id'], now))
        if taken.rowcount != 1:
            raise ValueError('这个邀请不能用了：可能过期、次数用完，或已被收回。请向管理员再要一个。')
        try:
            user_id = con.execute(
                'INSERT INTO users(username,password_hash,role,invite_id,created) VALUES(?,?,?,?,?)',
                (name, hash_password(password), 'member', invite['id'], now)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('这个用户名已经有人用了，请换一个。')
        course_id = con.execute(
            'INSERT INTO courses(title,user_id) VALUES(?,?)', ('我的课程', user_id)).lastrowid
        session_id = con.execute(
            'INSERT INTO study_sessions(course_id,title,user_id) VALUES(?,?,?)',
            (course_id, '第一次学习', user_id)).lastrowid
        token = _new_token(con, user_id)
    return {'token': token, 'username': name, 'role': 'member', 'session_id': session_id}
