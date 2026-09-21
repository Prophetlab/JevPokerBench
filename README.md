# JevPokerBench

[English](README.md) · [简体中文](README.zh-CN.md)

ProphetLab's Texas Hold'em benchmark and playground for decision models. Watch separate cash-game and sit-and-go (SNG) leaderboards, replay hands, ask for advice, or play against models. Chips are virtual.

## Features and access

- **Watch:** cash/SNG leaderboards and benchmark replays are read-only. The public interface provides no benchmark or model-registry administration. You can configure your own agents and interact with the Advisor and your own rooms.
- **Play:** registration needs no invitation. Registered players can use the hosted local models and official Jev for free. An invitation enables a **lifetime CNY 5 hosted DeepSeek allowance per account**, not a recurring allowance.
- **Bring your own key:** use official Jev/DeepSeek or add your own OpenAI-compatible agent with a model name, public HTTPS endpoint and key. Keys use browser `sessionStorage` and server memory for your own requests only; they are not written to server databases or logs. Provider billing applies to your own account; set provider-side spending and rate limits.
- **Advisor:** Jev is the default. Edit a hand and action history to compare mathematical equity with model action preferences.
- **Human rooms:** model requests time out after 15 seconds; the seat checks if legal, otherwise folds. A DeepSeek seat retires at hand end after three cumulative seat timeouts. The host can remove other model seats.
- **Cash chips:** the minimum denomination is half a unit. Legacy amounts align at the next hand, with any remainder retained in reserve; historical hands are not rewritten.
- **Presentation:** English/Chinese UI, prominent dealer and winner markers, and programmatically synthesized sounds with low default volume and a mute control.

The seven local model routes (`semif`, `laya`, `openjev`, `jeff`, `nimble`, `verdict`, `nanojev`) share a **128-request concurrency pool**. Official Jev has a **separate 128-request pool**. Each pool reserves **two slots exclusively for formal benchmarks**; visitor rooms and advice can occupy at most 126. Queued benchmark requests are admitted first. Priority comes from the server-side match runner, never browser input. This controls application admission; it cannot preempt inference already queued at an upstream service. Cloud DeepSeek and GPT use independent backends with no application-level concurrency cap and consume neither pool; provider limits still apply. A displayed model name alone does not establish the weights or provider behind a service; see [MODEL_AUDIT.md](MODEL_AUDIT.md).

Cash and SNG results remain separate. Model action probabilities and confidence are not calibrated win probabilities. All-in equity adjustments cover eligible runout luck, not complete decision quality or GTO strength.

## Add your own agent

The easiest setup is the frontend **Add Agent** card. Choose official Jev or DeepSeek and enter your own key, or choose a custom OpenAI-compatible agent and enter its model name, endpoint and key. No operator API access is needed for this personal setup.

Official Jev/DeepSeek use fixed provider endpoints. Custom agents require public HTTPS endpoints; SSRF protection validates DNS, pins the approved address for the connection and blocks internal destinations. Requests carrying your key use HTTPS and are made only for you. Keys remain in browser `sessionStorage` and server memory, with no server persistence or logging. Use provider-side limits to control your own usage.

Custom-agent inference tries the direct connection up to three times, then falls back to the operator-configured proxy if those attempts fail and a proxy is configured. Adding an agent does not issue paid inference probes; this retry policy applies only to requested inference.

## Local setup

Requires Python 3.12+ and Node.js 20.19+ or 22.12+. Run from the repository root:

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

Populate the provider keys and a locally generated admin token in your private `.env`. The example config connects directly to official Jev and DeepSeek; it does not provision local models or hosted allowances. Choose model IDs supported by your provider accounts. Configure applicable accounting rates privately before enabling paid requests; the example supplies no pricing information.

Set `POKERBENCH_INVITE_CODE` privately for invitation access. The optional `POKERBENCH_CUSTOM_PROXY_URL` configures the custom-agent fallback proxy. Both fields are blank in `.env.example`; keep real values private.

Start the backend:

```bash
.venv/bin/uvicorn pokerbench.api:app --host localhost --port 8097
```

Open [localhost:8097](http://localhost:8097). The backend serves the built frontend. For frontend development, keep the backend running and use a second terminal:

```bash
cd frontend
npm run dev
```

Open the URL printed by Vite. Local HTTP is for development without real BYOK submissions; use HTTPS for key-bearing browser requests. This application uses a single backend process with SQLite.

## Operator configuration

Administration uses the backend API, not frontend controls. `PUT /api/entries` replaces the model registry with 2–10 uniquely identified entries; `key_env` names a server environment variable and must never contain a key. Use the sample before creating runs. Updating the registry does not rewrite existing runs.

With the same private `POKERBENCH_ADMIN_TOKEN` available in your shell:

```bash
curl --fail-with-body --request PUT \
  http://localhost:8097/api/entries \
  --header "Authorization: Bearer ${POKERBENCH_ADMIN_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary @examples/entries.example.json
```

Set the admin token before exposing the service beyond local development. Operator provider keys and administration credentials stay server-side and must not be embedded in frontend builds; user-supplied keys follow the BYOK flow above. The public source includes no deployed registry, `.env`, database, account records, or match data. Keep local `config/`, `data/`, logs and generated artifacts out of version control. See [SECURITY.md](SECURITY.md).

## Verification and license

[TEST_REPORT.md](TEST_REPORT.md) lists commands and release checks. The report records the verified results and their scope.

Project code is licensed under the [MIT License](LICENSE), copyright 2026 ProphetLab. Dependencies and model weights retain their own licenses and provider terms. Built with [PokerKit](https://github.com/uoftcprg/pokerkit), [FastAPI](https://fastapi.tiangolo.com/), [React](https://react.dev/), [Vite](https://vite.dev/), [TypeSafe SDK](https://pypi.org/project/typesafe-sdk/) and the official [System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python).
