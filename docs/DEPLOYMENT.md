# Linux 首次部署

以下针对首次安装，已有部署升级前请先停止网页和QQ服务，备份代码、`.env`与数据库。不要把全新仓库直接覆盖到正在运行的服务目录。

将源码放在 `/opt/study-agent`，进入该目录：

```bash
sudo bash deploy.sh
```

脚本适用于使用 apt 和 systemd 的 Ubuntu：安装基础依赖、创建 studyagent 普通服务用户、虚拟环境和 data 目录，在首次安装时生成网页密码，并注册 study-agent 服务。会修改系统服务配置；它不是通用容器安装器。

在服务器编辑 `/opt/study-agent/.env`，填写自己的模型API Key、可用模型名称。保留网页密码，随后重启：

```bash
sudo systemctl restart study-agent
sudo systemctl is-active study-agent
sudo journalctl -u study-agent -n 50 --no-pager
```

在自己电脑上建立SSH隧道；替换用户名及服务器地址：

```bash
ssh -N -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_SERVER
```

打开 `http://127.0.0.1:8000`。需要有自己配置好的SSH凭据，仓库不会提供私钥或现成服务器访问权限。

## 可选QQ服务

在主程序安装后执行：

```bash
sudo bash setup-qq.sh
```

按提示填写自己机器人的AppID、AppSecret及主人OpenID，Secret输入时不回显。初次OpenID留空时只记录发信者身份，不执行消息；确认自己的OpenID后编辑 `.env` 并重启QQ服务。

```bash
sudo systemctl restart study-agent-qq
sudo journalctl -u study-agent-qq -n 50 --no-pager
```

`APP_PASSWORD` 改动后，网页和QQ两个服务都要重启。QQ消息长回复会截断，接收与回复能力受官方平台权限影响。

## 数据备份

默认数据库在 `data/study.sqlite3`；若设置 `STUDY_DB`，按实际路径备份。复制数据库前停止两个服务，或使用SQLite备份接口。恢复时注意studyagent用户对数据目录的读写权限。不要把备份、日志或 `.env` 提交到仓库。

```bash
sudo systemctl stop study-agent study-agent-qq
sudo -u studyagent sqlite3 /opt/study-agent/data/study.sqlite3 ".backup '/opt/study-agent/data/study.sqlite3.bak'"
# 或者直接复制文件：
sudo cp -a /opt/study-agent/data/study.sqlite3 /opt/study-agent/data/study.sqlite3.bak
```

备份文件留在服务器上，不要放进 Git。

## 从 v0.3 升到 v0.4

先在自己的电脑上拉下 v0.4.0 代码，跑通测试，再动服务器。不要把仓库里的空库覆盖到正在用的 `data/`。

```bash
python -m unittest discover -s tests -v
```

服务器上：

1. 停掉网页和 QQ：`sudo systemctl stop study-agent study-agent-qq`
2. 按上一节备份 `data/study.sqlite3` 和 `/opt/study-agent/.env`
3. 用新代码替换程序文件，保留原来的 `.env` 和 `data/`
4. 在 `.env` 里补上 v0.4 的限额（见仓库 `.env.example`）。不要把密码、密钥写进仓库。`APP_PASSWORD` 仍是管理员密码，网页用户名是 `admin`
5. 若邀请链接要写成公网地址，设置 `PUBLIC_BASE_URL=https://你的域名`，不要末尾斜杠
6. `sudo systemctl start study-agent`，需要 QQ 时再启动 `study-agent-qq`
7. 打开网页，用 admin 登录。原有资料应出现在管理员的默认课程里

升级会给旧表加上 `user_id`（默认 1，也就是管理员），并新建用户、邀请和用量几张表。v0.3 的行不会被删。

## 回退到 v0.3

只换回旧代码不够。v0.4 的库里已经有同学的行，旧程序不会按用户过滤，管理员会看到别人的资料。

回退步骤：

1. 停掉两个服务
2. 把程序文件换回 v0.3.0（标签或提交 `b026610`）
3. 用升级前的 `study.sqlite3.bak` 覆盖现在的数据库
4. 确认 `.env` 仍是原来的那份，再启动服务

没有升级前的备份时，不要指望删几张新表就能回到 v0.3 的样子。

## 给同学开 HTTPS

程序仍然只听 `127.0.0.1:8000`（见 `deploy.sh` 里的 uvicorn 参数）。不要改成对公网直接暴露。在前面加一层你自己管理的 HTTPS 反向代理，由代理把请求转到本机 8000。

下面是示意，域名和证书换成你自己的。仓库里不写真实服务器地址。

Caddy：

```
your.domain.example {
    reverse_proxy 127.0.0.1:8000
}
```

Nginx 需要把协议传给程序，登录 Cookie 才会带上 Secure：

```
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_pass http://127.0.0.1:8000;
```

然后在 `.env` 设置 `PUBLIC_BASE_URL=https://your.domain.example` 并重启。管理员在「邀请同学」里复制的链接应是 `https://.../invite#邀请码`。邀请码在 `#` 后面，访问日志里看不到。

同学试用的白话步骤见 [怎么试用](TRY.md)。SSH 隧道仍然可以：隧道那头是 `http://127.0.0.1:8000`，只适合自己看，不适合把链接发到班群。
