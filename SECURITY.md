# Security

## Report a vulnerability

If GitHub private vulnerability reporting is enabled, use the repository's **Security → Report a vulnerability** action. Otherwise, open an issue requesting a private contact without exploit details or sensitive data. Do not include credentials, invitation codes, account records or private hand data in public reports. No response-time commitment is specified.

## Access boundaries

- Public cash/SNG standings and benchmark replays are read-only. Users can configure personal agents and interact with the Advisor and rooms they own. Registration does not require an invitation.
- Invitations grant a lifetime CNY 5 hosted DeepSeek allowance per account. Registration, invitation redemption and BYOK do not confer operator privileges.
- Operator configuration and benchmark mutations require backend authorization. There is no public frontend administration interface. Room actions, removal and private views must enforce ownership on the server.
- Private hole cards, undealt cards, seeds and model-call details must not leak through room state, replay, streaming or export responses. Only the viewer's permitted information and cards legitimately shown during play may be exposed.

## Credential handling

User keys for official Jev, DeepSeek and custom OpenAI-compatible agents are stored in browser `sessionStorage`, transmitted over HTTPS and used in server memory for that user's own requests only. They must not enter databases, files, logs, analytics, saved room state, exports or frontend bundles, or be shared with other users' requests. `sessionStorage` is accessible to same-origin JavaScript; prevent script injection and clear keys when no longer needed. Set spending and rate limits at the provider for each user key.

Official Jev and DeepSeek use fixed provider endpoints. The frontend Add Agent card also accepts a user's model name, public HTTPS endpoint and key for a custom OpenAI-compatible agent. Custom endpoint validation must resolve and check DNS, then pin an approved public address to the outbound connection. Block loopback, private, link-local and other nonpublic IPv4/IPv6 destinations. DNS rebinding and redirects must not bypass these checks or send keys to an unintended destination. Accepting a custom endpoint grants no access to internal services or operator credentials.

Custom-agent inference tries direct transport up to three times before using the optional operator-configured `POKERBENCH_CUSTOM_PROXY_URL`. Proxy fallback must preserve destination validation and per-user key isolation; it is not permission to reach internal services. Adding an agent performs no paid inference probes. Treat the configured proxy as trusted infrastructure and keep its real configuration private. `POKERBENCH_INVITE_CODE` and `POKERBENCH_CUSTOM_PROXY_URL` are blank in the public environment template.

## Concurrency

The seven local model routes share a 128-request pool; official Jev has its own 128-request pool. DeepSeek and GPT use independent backends with no application-level concurrency cap and do not consume either pool. This does not remove provider-side limits or spending controls. Concurrency isolation must preserve per-user credential and allowance isolation.

Operator keys belong in private environment configuration. `.env.example` contains empty credential fields, while `examples/entries.example.json` contains environment-variable names and public provider URLs only. Generate the admin token locally and require it for nonlocal administration. Use HTTPS for real BYOK and a single backend process for the SQLite application.

## Publication boundary

Do not publish deployed configuration, `.env`, databases, account/session records, invitation records, model-call logs, private match data or generated verification artifacts. Source releases include templates, not operational exports. Review both the proposed tree and its reachable Git history before release; removing a file from the latest tree does not remove its history.

These are the release security requirements. [TEST_REPORT.md](TEST_REPORT.md) identifies pending verification; this document is not a security certification.
