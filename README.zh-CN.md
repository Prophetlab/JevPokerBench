# JevPokerBench

[English](README.md) · [简体中文](README.zh-CN.md)

ProphetLab 的德州扑克决策模型基准与交互平台。查看独立的现金桌和单桌锦标赛（SNG）排行榜、回放牌局、获取建议，或与模型同桌游戏。筹码均为虚拟筹码。

## 功能与使用权限

### 在线游玩

打开在线平台：[JevPokerBench 在线入口](https://123.56.23.73/pokerbench/)。在线游玩步骤：

1. 注册账号或登录。
2. 进入 **自己组局（Play with models）**，选择现金桌或 SNG，配置座位与虚拟筹码，然后点击 **创建牌桌并入座**。
3. 点击 **开始比赛** 发第一手牌。轮到你行动时选择合法动作，其余席位由所选模型处理。
4. 使用 **牌桌回放（Hand replay）** 查看已完成牌局，使用 **牌局辅助器（Hand advisor）** 获取决策分析，并在 **比赛总览（Overview）** 浏览基准排行榜。

平台使用虚拟筹码。使用自带密钥（BYOK）Agent 时，可能需要填写自己的供应商密钥并承担供应商费用，详见下方[添加自己的 Agent](#添加自己的-agent)。

- **观看：**现金桌、SNG 排行榜及基准回放为只读。公开前端不提供比赛管理或模型注册表管理入口；用户可配置自己的 Agent，并操作辅助器与自己的真人牌桌。
- **游戏：**注册无需邀请码。注册玩家可免费使用平台提供的本地模型和官方 Jev。邀请码用于开通托管 DeepSeek，额度为**每账号终身累计人民币 5 元**，不按周期重置。
- **自带密钥（BYOK）：**支持官方 Jev、DeepSeek，也可填写模型名称、公开 HTTPS 端点及密钥，添加自己的 OpenAI 兼容 Agent。密钥保存在浏览器 `sessionStorage`，仅在服务器内存中用于自己的请求，不写入服务器数据库或日志。调用由自己的供应商账号计费，建议设置供应商侧消费与速率上限。
- **辅助器：**默认选择 Jev，可编辑牌面与行动历史，比较数学权益与模型行动偏好。
- **真人牌桌：**模型请求限时 15 秒；超时后能过牌则过牌，否则弃牌。DeepSeek 席位累计超时三次后，在该手结束时退场；其他模型席位可由房主移除。
- **现金筹码：**最小面额为半个单位。旧牌桌在下一手对齐面额，余数保留在储备金中，不改写历史牌局。
- **界面：**支持中英文切换，突出显示庄家与赢家；程序合成音效默认低音量，并提供静音控制。

七个本地模型路由（`semif`、`laya`、`openjev`、`jeff`、`nimble`、`verdict`、`nanojev`）共用 **128 请求并发池**，官方 Jev 使用**另一个独立的 128 请求并发池**。每个池为正式 benchmark 独占保留 **2 个名额**，网页牌局和辅助器最多占用 126 个；排队时优先调度正式比赛。优先级由服务器内的比赛执行器决定，网页参数无法提升优先级。这保证应用内请求优先发出，不能抢断已经进入上游服务的推理。云端 DeepSeek、GPT 使用独立后端，不设应用层并发上限，也不占用上述两个池；供应商侧限制仍然适用。界面名称本身不能证明实际权重或服务来源，详见 [MODEL_AUDIT.md](MODEL_AUDIT.md)。

现金桌与 SNG 分开排名。模型行动概率和置信度不等于经过校准的胜率；全下权益调整仅覆盖符合条件的发牌运气，不代表完整决策质量或 GTO 水平。

## 添加自己的 Agent

最简单的方式是前端 **Add Agent（添加 Agent）** 卡片：选择官方 Jev 或 DeepSeek 并填写自己的密钥，或选择自定义 OpenAI 兼容 Agent，填写模型名称、端点和密钥。个人接入无需管理员 API 权限。

官方 Jev、DeepSeek 使用固定的供应商端点。自定义 Agent 必须使用公开 HTTPS 端点；SSRF 防护会校验 DNS，将通过校验的地址固定用于本次连接，并阻止访问内部地址。携带密钥的请求通过 HTTPS 传输，密钥仅用于自己的调用，保存在浏览器 `sessionStorage` 与服务器内存中，不在服务器持久化或写入日志。建议在供应商侧设置额度与速率限制。

自定义 Agent 推理先直连，最多尝试三次；三次失败且运营方已配置代理时，再回退到该代理。添加 Agent 不发起付费推理探测；此重试策略仅用于用户实际请求的推理。

协议与端点形式见 [API_COMPATIBILITY.md](API_COMPATIBILITY.md)，赛制、信息边界与权益调整见 [BENCHMARK.md](BENCHMARK.md)。

## 本地运行

需要 Python 3.12+、Node.js 20.19+ 或 22.12+。从仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
cp .env.example .env
chmod 600 .env
mkdir -p config
cp examples/entries.example.json config/entries.json
cd frontend
npm ci
npm run build
cd ..
```

在私有 `.env` 中填写供应商密钥与本地生成的管理员令牌。示例配置直连官方 Jev 和 DeepSeek，不部署本地模型，也不开通托管额度。模型 ID 应与自己的供应商账号权限一致。开启付费调用前，在私有配置中设置适用的计费参数；示例不包含价格信息。

邀请码通过私有 `POKERBENCH_INVITE_CODE` 配置；可选的 `POKERBENCH_CUSTOM_PROXY_URL` 用于自定义 Agent 的回退代理。两项在 `.env.example` 中均为空，实际值应保持私有。

启动后端：

```bash
.venv/bin/uvicorn pokerbench.api:app --host localhost --port 8097
```

打开 [localhost:8097](http://localhost:8097)，后端会提供构建后的前端。开发前端时，保持后端运行，在另一个终端执行：

```bash
cd frontend
npm run dev
```

打开 Vite 输出的网址。本地 HTTP 仅用于不提交真实 BYOK 密钥的开发；浏览器携带密钥的请求应使用 HTTPS。程序采用单后端进程与 SQLite。

## 管理员配置

管理通过后端 API 完成，不通过前端控件。`PUT /api/entries` 接收 2–10 个 ID 唯一的条目并替换模型注册表；`key_env` 只能填写服务器环境变量名，不能填写密钥。先完成配置再创建比赛；更新注册表不改写已有比赛。

在当前 shell 中设置与后端一致的私有 `POKERBENCH_ADMIN_TOKEN` 后执行：

```bash
curl --fail-with-body --request PUT \
  http://localhost:8097/api/entries \
  --header "Authorization: Bearer ${POKERBENCH_ADMIN_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary @examples/entries.example.json
```

对外提供服务前应设置管理员令牌。运营方的供应商密钥及管理凭据保留在服务器侧，不得写入前端构建；用户自带密钥遵循上述 BYOK 流程。公开源码不包含已部署的注册表、`.env`、数据库、账号记录或比赛数据。本地 `config/`、`data/`、日志及生成物应保持在版本控制之外。详见 [SECURITY.md](SECURITY.md)。

## 验证与许可

[TEST_REPORT.md](TEST_REPORT.md) 记录自动测试、浏览器验证结果及其覆盖范围。

项目代码采用 [MIT License](LICENSE)，版权归 2026 ProphetLab。依赖、模型权重及供应商服务分别遵循各自许可与条款。主要依赖包括 [PokerKit](https://github.com/uoftcprg/pokerkit)、[FastAPI](https://fastapi.tiangolo.com/)、[React](https://react.dev/)、[Vite](https://vite.dev/)、[TypeSafe SDK](https://pypi.org/project/typesafe-sdk/) 与官方 [System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python)。

可选的[私有网站统计](deploy/analytics/README.md)以独立只读服务运行，汇总保留的访问日志及账号、组局数据，无需重启比赛后端或公网网关。
