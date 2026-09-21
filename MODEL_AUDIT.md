# Model routing and verification

This document defines routing and evidence requirements. It contains no deployment inventory, private endpoint, run identifier or live verification result.

| Route | Intended connection | Verification requirement |
| --- | --- | --- |
| Seven local model routes: `semif`, `laya`, `openjev`, `jeff`, `nimble`, `verdict`, `nanojev` | Independently configured model services | Check the service's actual model identity and available checkpoint revision. Do not infer identity from its seat name or protocol alias. |
| Official Jev | TypeSafe's official API using the native Jev protocol | Select a supported model ID and inspect the returned identity. |
| Cloud DeepSeek | Official DeepSeek API through the official System One Adapter | Keep its cloud route independent of local services; verify the returned model and configured reasoning mode. |
| Cloud GPT | The configured cloud provider through the official System One Adapter | Keep its cloud route independent of local services; identify the provider and returned model before comparing results. |
| User-supplied agent | A user-selected OpenAI-compatible model at a public HTTPS endpoint | Validate and pin DNS, block internal destinations, and use only that user's key for their own requests. A supplied model name is not verified model identity. |

Official Jev and DeepSeek BYOK use fixed provider endpoints. The frontend **Add Agent** card additionally accepts a model name, public HTTPS endpoint and key for a custom OpenAI-compatible agent. User keys use browser `sessionStorage` and server memory only, never server databases or logs; recommend provider-side spending and rate limits. The two-entry example registry covers official Jev/DeepSeek and is an installation template, not evidence that the full roster is deployed or verified. The Advisor defaults to the Jev entry.

The seven local model routes share one 128-request concurrency pool. Official Jev has a separate 128-request pool. Cloud DeepSeek and GPT run on independent backends without an application-level concurrency cap and consume neither pool. Provider limits still apply; these settings do not imply measured throughput or latency.

Custom-agent inference makes up to three direct attempts before falling back to the proxy configured by `POKERBENCH_CUSTOM_PROXY_URL`, if available. Fallback changes transport, not the selected provider or model. Agent setup performs no paid inference probes.

Where supported, configure `expected_model` and `expected_revision` from independently checked service metadata. An alias or self-reported revision is weaker evidence than inspecting the served checkpoint. For hosted services, response metadata does not independently prove underlying weights. Do not silently substitute another provider or model after an identity mismatch.

Record model/adapter versions, decoding settings, legal-action handling and any abstention-conditioning policy for reproducible comparisons. Publish only sanitized methodology and deliberately released results. A model's probability distribution or confidence value is not, by itself, calibration evidence, poker strength or a measured win probability.

Identity checks, public access controls and failure behavior remain subject to the release checks in [TEST_REPORT.md](TEST_REPORT.md). No completed model audit or live inference success is claimed here.
