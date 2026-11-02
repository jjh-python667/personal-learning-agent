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
