"""智能请求的次数、频率和并发限制。日志不写提问内容、密码或密钥。"""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

import db

log = logging.getLogger('study.usage')
# 计数本身不需要等待网络，用线程锁即可，避免绑死在某一个事件循环上。
_guard = threading.Lock()
_inflight_user = {}
_inflight_global = 0
_last_at = {}
_depth = ContextVar('ai_turn_depth', default=0)


class LimitError(Exception):
    """给同学看的白话限制，不是程序内部错误。"""


def _int_env(name, default):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def daily_quota():
    return max(0, _int_env('AI_DAILY_QUOTA', 40))


def min_interval():
    raw = os.environ.get('AI_MIN_INTERVAL_SECONDS', '').strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def max_upload_bytes():
    return max(1, _int_env('MAX_UPLOAD_BYTES', 12 * 1024 * 1024))


def max_concurrency():
    return max(1, _int_env('AI_MAX_CONCURRENCY', 2))


def user_concurrency():
    return max(1, _int_env('AI_USER_CONCURRENCY', 1))


def used_today(user_id):
    day = db.now()[:10]
    found = db.rows('SELECT count FROM ai_usage WHERE user_id=? AND day=?', (user_id, day))
    return found[0]['count'] if found else 0


def _bump(user_id):
    day = db.now()[:10]
    with db.connection() as con:
        con.execute(
            'INSERT INTO ai_usage(user_id,day,count) VALUES(?,?,1) '
            'ON CONFLICT(user_id,day) DO UPDATE SET count=count+1',
            (user_id, day))


def snapshot(user_id):
    quota = daily_quota()
    used = used_today(user_id)
    return {
        'daily_used': used,
        'daily_quota': quota,
        'remaining': max(0, quota - used),
        'max_upload_bytes': max_upload_bytes(),
        'min_interval_seconds': min_interval(),
    }


def reset_runtime():
    """清掉本进程里的频率和并发计数。测试和重启都会走这里。"""
    global _inflight_global
    _inflight_user.clear()
    _last_at.clear()
    _inflight_global = 0


def _reserve(user_id):
    global _inflight_global
    if _inflight_global >= max_concurrency() or _inflight_user.get(user_id, 0) >= user_concurrency():
        raise LimitError('现在同时提问的人有点多，等几秒再试一次。')
    gap = min_interval()
    last = _last_at.get(user_id)
    if gap and last is not None and time.monotonic() - last < gap:
        raise LimitError('发送太快了，歇一会儿再试。')
    if used_today(user_id) >= daily_quota():
        raise LimitError('今天的智能提问次数已经用完了。明天再来，或请管理员把每天的额度调高一点。')
    _bump(user_id)
    _inflight_user[user_id] = _inflight_user.get(user_id, 0) + 1
    _inflight_global += 1
    _last_at[user_id] = time.monotonic()


def _release(user_id):
    global _inflight_global
    _inflight_user[user_id] = max(0, _inflight_user.get(user_id, 1) - 1)
    _inflight_global = max(0, _inflight_global - 1)


@asynccontextmanager
async def turn(user_id):
    """一次提问占一个名额。列出资料不走这里。"""
    if _depth.get():
        yield
        return
    token = _depth.set(1)
    entered = False
    started = time.perf_counter()
    try:
        with _guard:
            _reserve(user_id)
            entered = True
        try:
            yield
        except Exception:
            log.info('study_fail user_id=%s', user_id)
            raise
        else:
            elapsed = int((time.perf_counter() - started) * 1000)
            log.info(
                'study_ok user_id=%s latency_ms=%s daily_used=%s',
                user_id, elapsed, used_today(user_id))
    except LimitError:
        log.info('study_limited user_id=%s', user_id)
        raise
    finally:
        if entered:
            with _guard:
                _release(user_id)
        _depth.reset(token)
