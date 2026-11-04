"""Official QQ SDK bridge. Separate process; owner allowlist; no proactive messages.
Install optional dependency: pip install 'qq-botpy>=1.2,<2'
需要自行配置官方机器人凭据；QQ 和网页使用同一个后端。
"""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent/'.env')
import botpy
import httpx
import db

class Client(botpy.Client):
    async def on_ready(self):
        print('QQ_READY: official connection established.',flush=True)

    async def on_c2c_message_create(self,message):
        owner = os.environ.get('QQ_OWNER_OPENID','')
        sender = message.author.user_openid
        if not owner:
            print('QQ bridge is not bound. Received sender openid:',sender,flush=True)
            return
        if sender != owner:
            return
        msgid = str(message.id)
        # 消息编号保存在数据库里，重启后也不重复执行同一条消息。
        with db.connection() as con:
            existing = con.execute('SELECT id FROM receipts WHERE id=?',(msgid,)).fetchone()
            if existing:
                return
            con.execute('INSERT INTO receipts(id,state) VALUES(?,?)',(msgid,'processing'))
        text = (message.content or '').strip()
        try:
            if message.attachments:
                replies = ['PDF、Word 等文件请先从管理网页上传。', '上传后在 QQ 告诉我资料编号，就能接着学习。']
            elif not text:
                replies = ['请发送文字消息。']
            else:
                async with httpx.AsyncClient(timeout=700) as client:
                    response = await client.post('http://127.0.0.1:8000/api/chat',json={'text':text},
                        auth=('qq-bridge',os.environ['APP_PASSWORD']), headers={'X-Study-Channel':'qq'})
                if response.status_code!=200:
                    replies = ['这次处理没有成功，请在管理网页查看任务记录。']
                else:
                    payload = response.json()
                    replies = payload.get('replies') or [payload['reply']]
            db.execute('UPDATE receipts SET state=?,reply=? WHERE id=?',('ready','\n\n'.join(replies),msgid))
            for seq, part in enumerate(replies[:3], start=1):
                if seq > 1:
                    await asyncio.sleep(0.8)
                if len(part) > 1800:
                    part = part[:1740]+'\n（回答较长，完整内容请在管理网页查看。）'
                await message.reply(content=part, msg_type=0, msg_seq=seq)
            db.execute('UPDATE receipts SET state=? WHERE id=?',('sent',msgid))
        except Exception as exc:
            db.execute('UPDATE receipts SET state=? WHERE id=?',('failed',msgid))
            print('QQ bridge request failed:',type(exc).__name__,flush=True)

def create_client():
    # botpy 1.2.1 calls get_event_loop() in its constructor. Python 3.14
    # requires a loop to be installed explicitly before constructing it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return Client(intents=botpy.Intents(public_messages=True),ext_handlers=False)

if __name__=='__main__':
    required = ['QQ_APP_ID','QQ_APP_SECRET','APP_PASSWORD']
    if not all(os.environ.get(k) for k in required):
        raise SystemExit('请先在.env配置QQ_APP_ID、QQ_APP_SECRET、APP_PASSWORD。')
    db.init()
    client = create_client()
    try:
        client.run(appid=os.environ['QQ_APP_ID'],secret=os.environ['QQ_APP_SECRET'])
    finally:
        client.loop.close()
