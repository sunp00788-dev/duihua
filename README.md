# 对话式生图项目

对画。员工登录后用对话生图、改图，也可以根据文字或文档生成思维导图。密钥只放在服务器上。

## 目录

```text
.
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── server.py              # 入口
├── router.py              # 请求怎么分流
├── prompts.py             # 提示词
├── image.py               # 生图与蒙版
├── llm.py                 # 文本模型
├── mindmap.py             # 思维导图
├── store.py               # SQLite
├── static/                # 页面
├── tests/
└── start.bat              # 本机直接跑，不经过 Docker
```

## 运行

需要 Docker。

```bash
cp .env.example .env
# 填入 MIGE_API_KEY 和 ADMIN_PASSWORD
docker compose up -d --build
```

浏览器打开 `http://127.0.0.1:8765`。思维导图在 `/mind.html`。

数据在 Docker 卷 `duihua-data` 里，不进 Git。

不用 Docker 时，安装 Python 3.12 后执行：

```bash
pip install -r requirements.txt
python server.py
```

Windows 也可以双击 `start.bat`。

## 测试

不联网，也不写数据库。

```bash
python tests/test_image_pipeline.py
python tests/test_routing.py
```

## 配置

| 变量 | 作用 |
| --- | --- |
| `MIGE_API_KEY` | 中转站密钥，必填 |
| `MIGE_BASE_URL` | 接口地址，默认 `https://api.migeapi.com` |
| `HOST` | 监听地址，默认 `0.0.0.0` |
| `PORT` | 端口，默认 `8765` |
| `ADMIN_USER` | 首次启动的管理员用户名 |
| `ADMIN_PASSWORD` | 首次启动的管理员密码 |
