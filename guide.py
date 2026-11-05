"""第一次使用：选示例或自己的资料，再走完问、分条、学、答、继续。"""
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parent / 'samples'
ORDER = ['ask', 'split', 'learn', 'answer', 'continue', 'done']
HINTS = {
    'ask': '先发一个具体问题。例如：示例里说的循环是什么？',
    'split': '看搭子是不是分成两三条气泡回复你。',
    'learn': '选中示例资料，点「开始连学」，或发送：带我学完编号1的资料，每次两页，开启连学',
    'answer': '看到题目后发送：答：你的答案',
    'continue': '再发「继续」，进入下一段。',
    'done': '这一轮走完了。可以换自己的资料再来一遍。',
}
FEEDBACK = {
    'split': '问这一步完成了。接着看回复有没有分成几条。',
    'learn': '分条回复已经出现。接下来开始学一段。',
    'answer': '学习任务开始了。看到题目就作答。',
    'continue': '答案已经交上去了。再发「继续」。',
    'done': '走完了：问、分条、学、答、继续。',
}
FAILURE_TIPS = [
    '如果提示还没配置模型：可以先看资料。提问需要管理员在服务器配好密钥并重启。',
    '如果提示今天次数用完：等到明天，或请管理员把 AI_DAILY_QUOTA 调大。',
    '如果提示发送太快或人太多：歇几秒再发，不要连点。',
    '如果上传失败：看红色提示。常见是文件太大、加密 PDF，或扫描件里没有文字。',
    '如果突然要重新登录：邀请可能过期或被收回，向管理员再要一个链接。',
]
TITLES = [('ask', '问'), ('split', '分条'), ('learn', '学'), ('answer', '答'), ('continue', '继续')]


def _row():
    found = db.rows('SELECT * FROM users WHERE id=?', (db.user_id(),))
    return found[0] if found else None


def install_samples():
    count = 0
    for path in sorted(ROOT.glob('*.md')):
        text = path.read_text(encoding='utf-8').strip()
        if not text:
            continue
        first = text.splitlines()[0]
        title = first.lstrip('#').strip() if first.startswith('#') else path.stem
        db.add_document(title[:120], [text])
        count += 1
    return count


def choose(choice):
    row = _row()
    if row is None:
        raise ValueError('请先登录。')
    if row['guide_choice']:
        raise ValueError('你已经选过开始方式了。')
    if choice == 'sample':
        if install_samples() < 1:
            raise ValueError('示例资料没放进来。可以改选「上传自己」。')
    elif choice != 'upload':
        raise ValueError('请选择体验示例或上传自己。')
    db.execute(
        'UPDATE users SET guide_choice=?, guide_step=? WHERE id=?',
        (choice, 'ask', row['id']))


def view(doc_count):
    row = _row()
    step = (row or {}).get('guide_step') or 'ask'
    if step not in ORDER:
        step = 'ask'
    choice = (row or {}).get('guide_choice')
    rank = ORDER.index(step)
    steps = []
    for key, title in TITLES:
        steps.append({
            'id': key, 'title': title, 'hint': HINTS[key],
            'done': ORDER.index(key) < rank or step == 'done',
            'current': key == step,
        })
    return {
        'needs_choice': bool(row) and choice is None and doc_count == 0,
        'choice': choice,
        'step': step,
        'steps': steps,
        'hint': HINTS.get(step, ''),
        'feedback': FEEDBACK.get(step, ''),
        'failure_tips': FAILURE_TIPS,
    }


def note(replies, trace, text=None):
    """根据这一轮实际发生的事，把引导往前推一截。"""
    row = _row()
    if not row or not row['guide_choice']:
        return
    cur = row['guide_step'] or 'ask'
    if cur not in ORDER:
        cur = 'ask'
    if text is None:
        recent = db.rows(
            'SELECT content FROM messages WHERE session_id=? AND user_id=? AND role=? ORDER BY id DESC LIMIT 1',
            (db.session_id(), db.user_id(), 'user'))
        text = recent[0]['content'] if recent else ''
    text = (text or '').strip()
    tools = {item.get('tool') for item in (trace or [])}
    target = cur

    def reach(step):
        nonlocal target
        if ORDER.index(step) > ORDER.index(target):
            target = step

    if text:
        reach('split')
    if len(replies or []) >= 2:
        reach('learn')
    if tools & {'lesson_start', 'lesson_advance', 'lesson_resume'}:
        reach('answer')
    if tools & {'lesson_grade', 'answer_quiz'}:
        reach('continue')
    if 'lesson_grade' in tools and 'lesson_advance' in tools:
        reach('done')
    elif target == 'continue' and text.startswith('继续'):
        reach('done')
    if target != cur:
        db.execute('UPDATE users SET guide_step=? WHERE id=?', (target, row['id']))


def note_event(kind):
    row = _row()
    if not row or not row['guide_choice']:
        return
    if kind == 'answer_quiz':
        note(['已记录'], [{'tool': 'answer_quiz'}], text='答')
    elif kind == 'lesson_advance' and row['guide_step'] == 'continue':
        note(['已记录'], [{'tool': 'lesson_advance'}], text='继续')
