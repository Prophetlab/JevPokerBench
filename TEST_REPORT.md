# Verification instructions

Verified on 2026-09-21: **567 backend and public-gateway tests passed; 26 frontend tests passed; TypeScript and the production build passed.** One existing Starlette deprecation warning remains. Browser checks passed at desktop and mobile sizes in English and Chinese, including the production bundle through the public subpath gateway.

Browser regression checks also verify SNG live viewing advances to the next tournament while manual historical replay stays put, and switching accounts cannot type into an unfinished logout.

Cash-reset tests cover the 2,000-unit threshold, conservation of each player's total funds, retired-seat exclusion, legacy policy preservation, active-hand cutover and restart without duplicate buy-ins.

A regression test verifies lossless compact-JSON retry for the local endpoint’s explicit input-length rejection without changing decision inputs or cached decisions.

Live public HTTPS checks verified independent accounts, anonymous sessions, refresh persistence, cookie security attributes and server-side logout revocation using disposable accounts; no model inference was requested. Production npm dependency audit reported no known vulnerabilities at this check.

Custom protocol tests cover Chat Completions, Responses and Claude Messages, native authentication, URL prefixes, complete versus truncated/refused/tool responses and credential-free validation errors. These use mocks, not live provider compatibility claims.

Trusted-proxy tests verify that one visitor exhausting an IP login limit does not block a different visitor, and that untrusted forwarded headers cannot change the rate-limit identity.

Spectator tests also verify shared rendering for unchanged match revisions and immediate refresh after a persisted update.

Model responses and high-concurrency traffic in these checks were mocked. They verify application behavior, key isolation and routing; they do not measure live model throughput. Custom-endpoint tests cover three direct TCP attempts before the configured proxy, pinned public addresses, TLS identity, and rejected private destinations.

## Automated checks

After following the README setup, run from the repository root:

```bash
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
```

Use an isolated test configuration and disposable data. Do not run verification against ongoing games or real accounts.

For browser checks, start an isolated backend and built frontend, then run:

```bash
cd frontend
npx playwright install chromium
PB_BASE_URL=http://localhost:8097 npm run test:browser
```

Browser checks needing completed replays should use synthetic fixtures. Keep screenshots and generated reports private. Do not treat existing suites as proof that every new feature is covered; add missing coverage and record results before release. Live provider calls require a separate deliberate check using private credentials.

## Release acceptance criteria

| Area | Required checks |
| --- | --- |
| Public access | Cash/SNG standings and benchmark replays stay read-only. No frontend admin controls. Personal agent setup, Advisor use and owned-room actions do not grant permission to mutate benchmark runs or the operator registry. |
| Accounts and allowance | Registration succeeds without invitation. Hosted local models and official Jev remain available without invitation. Invitation redemption grants no more than a lifetime CNY 5 hosted DeepSeek allowance per account, including retries, concurrent requests and repeated redemption. |
| BYOK and Add Agent | The frontend card accepts official Jev/DeepSeek keys or a custom OpenAI-compatible model name, public HTTPS endpoint and key without operator access. Keys use browser `sessionStorage` and server memory for the submitting user's requests only. Inspect logs, databases, saved state, exports and errors for leaks; isolate concurrent users' keys and display provider-limit guidance. |
| Endpoint safety | Official Jev/DeepSeek endpoints remain fixed. Custom endpoints require public HTTPS, DNS validation and connection pinning. Reject internal, loopback, link-local and other nonpublic IPv4/IPv6 destinations, including DNS rebinding and redirect bypasses. Keys reach only the intended provider. |
| Advisor and routing | Jev is the default. Cloud DeepSeek/GPT routes remain independent of the seven local model routes: `semif`, `laya`, `openjev`, `jeff`, `nimble`, `verdict`, `nanojev`. Identity mismatches do not silently switch providers. |
| Proxy fallback | Use mocked transports to verify up to three direct attempts, then fallback only after their failure when `POKERBENCH_CUSTOM_PROXY_URL` is configured. A successful direct request stops retries. Fallback retains the selected endpoint/model, SSRF protections and user-key isolation. Adding an agent issues no paid inference probes. |
| Concurrency | Local routes share one pool capped at 128 active requests; official Jev has a separate pool capped at 128. Each pool reserves two slots for formal benchmarks. Tests hold all 126 visitor slots while two formal calls dispatch, verify priority over queued visitors and cancellation cleanup, and reject browser priority spoofing. Saturating either does not consume capacity in the other. DeepSeek/GPT use independent backends without an application-level concurrency cap and do not acquire either pool. Provider limits and account allowances remain effective. Use mocked providers to check scheduling without live load. |
| Room timeouts | A model request times out after 15 seconds and checks if legal, otherwise folds. Three cumulative timeouts retire only that DeepSeek seat at hand end. The host can remove other model seats. Late responses and retries do not execute a second action. |
| Cash accounting | Half-unit amounts apply at the next hand. Legacy remainders stay in reserve; total funds are conserved. Active or historical hands are not rewritten and restart does not apply conversion twice. |
| Privacy and ownership | Cross-account room actions and private views are denied. State, replay, stream and export preserve hidden-card boundaries. |
| UI and sound | Both languages work at desktop/mobile sizes. Dealer/winner markers remain legible. Adapter versions, retry metadata and inaccessible export links stay out of the overview; technical details are collapsed. Synthesized sounds start at low volume; mute suppresses playback. |
| Packaging | Examples parse and match the configuration schema. Credential fields, `POKERBENCH_INVITE_CODE` and `POKERBENCH_CUSTOM_PROXY_URL` are empty. No deployed registry, runtime data, private paths, identifiers or credential-bearing history is included. |

Live provider availability and limits depend on the deployment. The automated suites do not spend real provider funds.
