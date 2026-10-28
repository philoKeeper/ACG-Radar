# ACG Radar · 次元雷达

一个本地优先的 Bangumi 评论抓取与 AI 分析工具：先抓取作品下的吐槽和评论，再让你用自己的 AI API 基于这些内容对话。回答会带评论引用，点开可以看原文。

## 功能

- 从 Bangumi 抓取两类内容：吐槽箱短评、日志长评（评论）。
- 评论统一入库、去重、分块，建立面向中文 / 日文的本地 BM25 检索索引。
- 作品库浏览、标签 / 年份 / 评分筛选、吐槽 / 评论筛选、剧透过滤。
- 配置任意 OpenAI 兼容 API（DeepSeek、OpenAI、Moonshot、SiliconFlow、Ollama、自建服务）。
- AI 对话流式输出，引用形如 [R12]，经后端校验后才变成可点击链接。
- 全库问答先把全部作品压缩成紧凑目录并让 AI 逐部打分，再展开匹配度最高的作品；作品内问答可展开该作品最多 120 条评论。
- 全库搜索采用多阶段流程：全量作品结构化评分 → 候选精排 → 详情分析；支持评分轮数、评分维度权重、精排范围和详情深度调整。无论精度如何，目录始终包含全部作品。
- 设置页提供 Bangumi 连接检查，分别检测公开 API、R18 API Token 和 R18 评论页 Cookie。
- 每条 AI 回答显示实际 token 用量（输入 / 输出 / 总计），包括全库目录评分和最终回答的消耗。
- 未配置 AI 时仍可浏览、筛选、本地排序。
- 无 pip / npm 依赖，不需要构建。

## 快速开始

要求 Python 3.10+（SQLite 需带 FTS5），不需要 pip / npm。

Windows 发布包：

1. 解压到一个可写目录，例如 D:\ACG-Radar
2. 双击 **start.bat**
3. 浏览器自动打开 http://127.0.0.1:8765

首次启动数据库为空，点击“添加作品”开始抓取。数据保存在同目录的 data/ 中。

也可以在项目目录打开 PowerShell：

    python app.py --port 8765

演示数据（开发者验收用）：

    python app.py --demo --port 8765
    python app.py --clear-demo

如果系统提示找不到 python，可安装 Python 并勾选 Add Python to PATH，或使用 Python 的绝对路径运行。

## 配置 AI

进入“设置 → AI 接口”：

1. 选择预设（DeepSeek / OpenAI / Moonshot / SiliconFlow / Ollama），或填自定义 Base URL。
2. 填模型名；远程服务填 API Key，本地 Ollama 可以留空。
3. 点“保存 AI 设置”，再点“测试连接”。
4. 回到作品详情点“问 AI 这部作品”，或直接进入“对话”。

Base URL 应指向 OpenAI 兼容接口，例如：

    https://api.deepseek.com/v1
    https://api.openai.com/v1
    http://127.0.0.1:11434/v1

程序会自动补上 /chat/completions。Key 只保存在本机 data/secrets.json，不写入数据库、导出文件或普通 GET 设置接口；日志会打码。评论正文、作品元数据和你的问题会发送给你配置的 AI 服务商。

## R18 内容

Bangumi 只向已授权用户返回 R18 条目。首次使用：

1. 打开 https://next.bgm.tv/demo/access-token 登录并申请 Access Token。
2. 在“设置 → Bangumi 授权”填入 Token 并保存。
3. 添加作品时确认“包含 R18 / NSFW 内容”已开启。

R18 的吐槽与长评来自 Bangumi 网页，若导入后评论为空，还需要在同一个设置页填入已登录 Bangumi 的 Cookie。Token 和 Cookie 都只保存在本机 data/secrets.json。

## 添加作品

“添加作品”支持：

- Bangumi 名称搜索。
- 留空名称，按年份 / 标签 / 最低评分浏览。
- 粘贴 Bangumi 条目链接，或直接输入条目 ID。

搜索结果每页 20 部，结果底部的“继续加载更多”会追加下一页，已加载结果、勾选和导入状态会保留。

### 批量导入与筛选

- 年份范围：从 / 到；
- 评分：只显示不低于该评分的作品；
- 标签：逗号分隔；留空浏览时按“全部”提交给 Bangumi；
- 类型：全部 / 番剧 / Galgame；
- 单个作品抓取评论数：每个作品最多抓取多少条，默认 120，可按需调小；默认每 0.5 秒发起一次请求。

结果默认全部勾选，点“一键导入选中 (N)”即可批量创建作品和评论抓取任务。导入过程会显示进度、逐行状态和失败重试；一次超过 6 部会先确认。评论按最新优先抓取。

### 吐槽与评论

每个作品默认最多抓取 120 条，可在添加时自定义；优先抓取最新的吐槽和评论，其中：

- 吐槽：短评，包含作者、星级评分和正文；
- 评论：日志长评，逐篇进入日志页抓取全文。

作品详情页可以按“全部类型 / 吐槽 / 评论”筛选。任务页可以查看进度、取消和重试。

## 数据与安全

- 所有数据在本地 data/ 目录；数据库是 data/app.db。
- 只监听 127.0.0.1，不提供局域网访问，也没有账号系统。
- 非 GET 请求需要同源 Origin、X-CSRF-Token，并使用 JSON Content-Type。
- 页面使用 CSP、参数化 SQL、DOM 文本节点渲染。
- 图片直接来自 Bangumi CDN，CSP 白名单限制；加载失败显示本地占位。
- 导出 JSON 不包含 API Key。
- 抓取 Bangumi 公开页面，按作品导入并限流；不伪造登录、不绕验证码。评论版权归原作者，保留原站链接，仅用于个人研究；请遵守 Bangumi 的使用规则。

## 常用命令

    python app.py                         # 启动，默认 127.0.0.1:8765
    python app.py --port 8787             # 换端口
    python app.py --demo                  # 数据库为空时写入演示数据
    python app.py --clear-demo            # 删除演示作品及其评论
    python app.py --check-sources         # 检查 Bangumi 连通性
    python app.py --rebuild-index         # 重建 FTS 索引和统计
    python -m unittest discover -s tests -v

## 测试

    python -m unittest discover -s tests -v

测试完全离线：验证 Bangumi 吐槽 / 评论解析、去重、FTS、引用打包、AI 流式解析、设置打码和合并；不访问真实站点，也不消耗 AI 额度。

## 目录

    app.py                  CLI 入口、演示数据、连通性检查
    backend/                配置、数据库、抓取、检索、AI、API
    backend/sources/        Bangumi 适配器
    web/                    原生 HTML / CSS / ES Modules 前端
    tests/                  离线测试与 mock AI
    data/                   运行时数据库、日志（gitignore）

## 已知限制

- Bangumi 页面结构变化时，吐槽 / 评论解析可能需要调整。
- 评论质量、剧透标记由原作者决定；长评没有统一评分。
- 本地检索是 BM25 + 中日文 bigram，不是向量语义检索。
- AI 回答仍可能出错；系统提示和引用校验只能降低风险，不能保证事实正确。
- 单用户本地应用；没有多用户、云同步、定时全量抓取。

## 故障排查

- Bangumi 403 / 429：可能被限流，稍后再试或调大请求间隔；设置里可填 HTTP/HTTPS 代理。
- SSL 证书错误：更新系统证书，或按 Python 文档设置 SSL_CERT_FILE。
- AI 401：Key 错误或过期；AI 404：Base URL / 模型名不对；AI 429：服务商限流。
- 端口被占用：换 --port。
- 想重置：停止服务，备份后删除 data/app.db 和 data/secrets.json。

## 版本

1.0.1 · 本地单用户 · 仅监听 127.0.0.1
