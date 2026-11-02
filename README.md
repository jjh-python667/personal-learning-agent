# 学习搭子 · Personal Learning Agent

**v0.1.0 · 第一版 · 大一阶段的个人学习项目**

这是一个围绕自己学习资料使用的个人 Agent。可以上传资料，通过网页或官方 QQ 机器人提问，并按步骤完成“读资料 → 讲解 → 出题 → 答题 → 继续”。

项目使用 Python、FastAPI、SQLite 和 DeepSeek API，网页使用 HTML、CSS、JavaScript，没有前端构建步骤。代码、测试与部署过程使用了 AI 编程助手辅助，第一版保留了适合学习的简单模块划分和关键注释。这是一个可运行的学习原型，还有明确的使用限制。

## 第一版能做什么

- 上传 PDF、DOCX、UTF-8 TXT 和 Markdown，保存提取后的文字。
- 检索资料、引用 PDF 页码或 Word 片段编号，读取指定位置。
- 根据资料讲解、出题、批改，并保存错题。
- 建立分块学习任务，记录已覆盖位置、当前题目和下一块位置；支持暂停与继续。
- 学习进度先进入待确认区，需要用户在网页确认，才成为正式学习记录。
- 网页管理资料、任务、题目、记录和提醒；QQ 单聊复用同一个后端。
- SQLite 保存数据；模型失败时不推进该块的学习游标，已成功的步骤可复用。

## 本地运行

建议使用 Python 3.11。代码曾在 Windows Python 3.9.13 跑通过测试，用户部署环境为 Ubuntu / Python 3.14；不同环境仍需实际验证依赖安装。

```bash
git clone https://github.com/jjh-python667/personal-learning-agent.git
cd personal-learning-agent
python -m venv .venv
```

激活虚拟环境：

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

安装依赖，把 `.env.example` 复制为 `.env`（可直接通过文件管理器复制），再填写自己的配置：

```bash
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

配置中的 `DEEPSEEK_API_KEY` 用于模型调用；`DEEPSEEK_MODEL` 填账户当前可用的模型名；`APP_PASSWORD` 自行设置为足够长的随机密码。修改配置后需重启。未配置 API Key 时，仍可手动管理资料。

打开 <http://127.0.0.1:8000>。配置了网页密码后，用户名可填 `study`，密码为 `APP_PASSWORD`。目前只校验密码，不是多用户账号系统。

也可使用 Windows 的 `start.ps1` 完成虚拟环境创建、依赖安装及启动。

## 建议这样体验

1. 在“资料库”上传自己有权使用的资料，记住网页中的资料编号。
2. 发“请列出我的资料”，检查清单。
3. 发“带我学完编号1的PDF，每次两页，讲完出一道题”。编号按实际情况替换。
4. 回答当前题目，再说“继续”，进入下一块。
5. 说“暂停”或“继续任务1”，也可使用网页“学习任务”中的按钮。
6. 重启服务，再查看任务和已保存资料。多个可继续任务会要求选择编号。

Word 没有固定页码，任务按提取后的片段推进。已覆盖表示程序处理过对应内容，不代表用户已经掌握知识。

## 代码怎么分工

- `main.py`：网页接口、密码检查、上传文件和人工确认操作。
- `agent.py`：请求模型，检查工具参数，执行工具，再把结果交回模型。
- `lesson.py`：学习任务状态和分块流程，决定什么时候推进游标。
- `db.py`：数据库连接、保存记录和关键词检索。
- `word_import.py`：从 DOCX 提取文字，限制文件大小和 XML 内容。
- `qq_bridge.py`：接收绑定用户的 QQ 消息，转给同一个网页后端。
- `static/`：管理网页。
- `tests/`：使用临时数据库和模拟模型响应的测试。

普通对话允许模型选择工具；资料列表请求和学习任务的关键步骤由程序控制。模型生成的文本不直接决定数据库进度。详细入门说明见 [代码阅读笔记](docs/CODE_GUIDE.md)。

## 测试与当前状态

```bash
python -m unittest discover -s tests -v
```

整理第一版时，本地 **29/29** 项测试通过。测试模拟模型响应，不花 API 额度，不读取生产数据库，也不能证明模型所有回答都正确。

用户的服务器曾完成真实 DeepSeek 请求和 QQ 文字收发；分块任务的历史冒烟报告记录了本地真实 API 验证。本次三项恢复流程修复已安装至用户 ECS，并收到 `LESSON_FIX_OK`，修复后的真实网页/QQ 完整验收尚未独立确认。详见 [验证记录](docs/VALIDATION.md)。

## QQ 与服务器部署

QQ 是可选功能，需要自己在 QQ 开放平台创建官方机器人，并具备对应场景权限：

```bash
python -m pip install -r requirements-qq.txt
python qq_bridge.py
```

先在 `.env` 配置 `QQ_APP_ID`、`QQ_APP_SECRET`、`APP_PASSWORD`。首次未填写 `QQ_OWNER_OPENID` 时不会执行收到的请求，只在终端记录发送人的 OpenID。自己发送一条测试消息，确认发信身份后配置该值并重启。只允许绑定的主人使用，QQ 不需要个人账号密码。

Linux 首次安装及 systemd 配置见 [部署说明](docs/DEPLOYMENT.md)。网页默认只监听本机，远程使用时通过 SSH 隧道访问。公开互联网访问需要自行配置 HTTPS 等保护，不能直接把默认服务开放给所有人。

## 第一版的限制

- 单用户设计，网页和 QQ 共用资料及任务；不是多人教学平台。
- 不支持扫描件 OCR、旧版 `.doc`、Word 图片与复杂排版。文件最多 12MB；PDF 最多120页，Word 提取文字最多30万字符。
- 资料检索使用关键词与中文相邻双字，不是语义向量检索。
- 单页给模型的文本最多6000字符；超长页不会全部覆盖，第一版尚未做页内细分。不要用于需要完整逐字审阅的任务。
- 有唯一待答题任务时，普通消息可能被当作答案；建议先答题或暂停再闲聊。多任务时可在网页选择具体题目作答。
- 网页“继续”返回的详细讲解未完整展示在任务卡片；完整交互建议通过对话或 QQ 进行。
- 批改由模型生成，可能出错；答题后需再说“继续”，不会自动连续翻完。
- 提醒只在打开网页时显示，不承诺 QQ 主动推送。
- 普通工具对话仍可能漏调用工具；列表与任务路径做了程序约束，不代表所有请求均可靠。
- 运行中任务不能跨多个后端进程并发调度，请保持一个 worker。不支持自动恢复重启前正在运行的模型请求。

## 隐私与开源

API Key、网页密码、QQ Secret 都留在后端。上传内容在请求模型处理时会发送给 DeepSeek，使用前请确认资料可以发送。数据库包含聊天和个人资料，不要提交到 GitHub。

本仓库不包含真实配置、私人文档、生产数据库、SSH密钥或服务器地址。许可证为 [MIT](LICENSE)。依赖库仍遵守各自许可证。

参考了 [DeepSeek Super-agent 原型](https://github.com/deepseek-ai/dsh-superagent-prototype) 的任务状态、持久记录与执行证据设计；本项目没有引入其源码，也没有实现多执行器或 GitHub 工单系统。模型接口参考 [DeepSeek 文档](https://api-docs.deepseek.com/)，QQ 接入使用 [腾讯官方 botpy SDK](https://github.com/tencent-connect/botpy)。

版本记录见 [CHANGELOG](CHANGELOG.md)。欢迎反馈问题；这个仓库首先是一个学习与复盘项目。
