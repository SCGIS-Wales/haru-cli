---
inclusion: always
---

# Security

- Authentication: IAM Identity Center via OAuth 2.0 Authorization Code flow with PKCE (S256). Register a public client, use a 127.0.0.1 loopback redirect, validate the `state` parameter, and never accept non-loopback redirects.
- Tokens: cache under `~/.aws/sso/cache` with 0600 permissions in the botocore-compatible schema; rely on botocore's SSOTokenProvider for refresh; treat access and refresh tokens as secrets.
- Credentials: resolve AWS credentials from the SSO session and the standard credential chain; never store long-lived keys.
- No secrets in configuration or source. YAML carries only references (`${env:VAR}`, ARNs, profile names). Reject inline secrets at config load.
- Data residency: default to `us.` Bedrock inference profiles; do not use `global.` endpoints without explicit approval.
- Guardrails: Bedrock Guardrails enabled by default with input redaction; do not disable to pass tests.
- Logging: never log tokens, credentials, or customer data; redact prompts where required; keep audit-friendly structured logs.
- Dependencies: keep boto3 and all dependencies current; enable Renovate/Dependabot; review the ruff security (S) rules.
- This is a regulated financial services firm: prefer least privilege, fail closed, and make every security-relevant action auditable.