
# Security Guide

This guide defines the security baseline for the Ortflix Integrations service. The service accepts webhooks from media applications, sends notifications through the Telegram Bot API, and allows authorized Telegram users to start selected automation scripts. Treat it as a privileged integration service because a compromised endpoint or bot credential could trigger actions or expose service credentials.

This is a development and deployment guide, not a claim that every recommendation is currently enforced. Verify the deployed configuration and CI behavior before relying on a control.

## Architecture and Trust Boundaries

```text
Media applications
    └── authenticated HTTP webhooks ──> reverse proxy / network boundary
                                           └──> FastAPI service
                                                  ├──> allowlisted local scripts
                                                  └──> Telegram Bot API (outbound polling and messages)
Telegram users ──> Telegram Bot API ──> bot command and callback handlers
```

Telegram is used for outbound messages and polling; it is not the source of the service's FastAPI webhooks. Those webhooks use the project's `x-webhook-token` authentication. Telegram's `X-Telegram-Bot-Api-Secret-Token` applies to Telegram webhook deployments and is not a substitute for this service's webhook authentication.

Assume webhook payloads, callback data, environment variables, mounted configuration, and subprocess output may be malformed, sensitive, or hostile. Validate each at its trust boundary.

## Authentication and Authorization

- Require a high-entropy `WEBHOOK_TOKEN` for every externally reachable webhook. Keep insecure mode disabled outside isolated local development; never expose unauthenticated endpoints to an untrusted network.
- Use TLS between external webhook senders and the service, normally terminated by a maintained reverse proxy. Restrict ingress to the smallest practical set of source networks as an additional control, not as a replacement for authentication.
- Use separate credentials per integration when practical. Store secrets in a secret manager or mounted secret files, restrict who can read them, and document rotation and revocation.
- Authorize Telegram actions on the server for every command and callback. Check the configured chat and, where appropriate, the individual Telegram user ID. Hiding a button is not authorization; callback data is untrusted input.
- Deny by default. Add a new webhook, command, or manual action only with an explicit authorization rule and a test for unauthorized requests.
- Compare shared secrets using a constant-time comparison where the authentication implementation permits it. Avoid placing credentials in URLs, logs, error messages, or source control.

## Input Handling and Script Execution

- Parse requests using typed schemas, reject invalid or unexpected values, and enforce sensible request-body and field-size limits. Do not trust event names, IDs, paths, callback data, or headers supplied by callers.
- Map events and Telegram callbacks to fixed, reviewed actions. Never let a request choose an arbitrary executable, script path, command option, or filesystem path.
- Do not use `shell=True`, `os.system()`, `os.popen()`, `eval()`, or equivalent shell construction. Launch subprocesses with an argument list and `shell=False` semantics; keep arguments fixed or validate them against a narrow allowlist.
- Resolve scripts from the application-owned scripts directory. Set timeouts, bound captured output, handle non-zero exit codes, and avoid passing the full application environment to a child when a smaller environment will work.
- Run automation with the least operating-system and service-account privileges needed. Do not grant container access to the host Docker socket or broad host mounts unless a reviewed requirement makes it unavoidable.
- Make operations safe against duplicate or replayed webhook deliveries where duplicate execution could cause harm. Use idempotency keys or event-state checks when the source provides stable event identifiers.

## Secrets and Dependencies

- Keep bot tokens, webhook tokens, and service API keys out of the repository, images, command-line arguments, and diagnostic output. Prefer mounted secrets or a managed secret store; use `.env` only for local development and keep it out of version control.
- Pin or lock production dependencies reproducibly, review automated dependency updates, and update promptly for security fixes. Pin container base images by digest when reproducibility and supply-chain assurance require it; have a deliberate process for refreshing those digests.
- Scan source, dependencies, and built images in CI. Make actionable high/critical findings fail a protected build or require a documented, time-limited exception. A scan that reports findings but exits successfully is informational, not a deployment gate.
- Keep CI permissions minimal, protect release credentials, and avoid running untrusted pull-request code with secrets or write permissions.

## Container and Network Hardening

- Run the service as a dedicated non-root user. Drop unnecessary Linux capabilities and avoid privileged mode.
- Use a read-only root filesystem where compatible; provide narrowly scoped writable mounts or temporary storage only where required. Restrict mounted configuration and media paths to the minimum access needed.
- Do not publish the application port directly to the public internet. Place it behind a maintained proxy or private network, apply TLS at the boundary, and restrict who can reach the service. Binding to `127.0.0.1` is suitable only when the proxy can reach that interface; container networking may require a private interface instead.
- Keep the base image and system packages current, small, and reproducibly built. Rebuild and redeploy regularly so security updates are actually applied.
- Apply resource limits and request timeouts to reduce denial-of-service risk. Keep health checks free of secrets and avoid exposing detailed internal state.

## Logging, Errors, and Privacy

- Record security-relevant events: timestamp, source integration, event/action type, authorization result, execution result, and a correlation identifier where available. Record Telegram IDs only when operationally necessary and protect logs accordingly.
- Do not log bot/API tokens, webhook secrets, authorization headers, full sensitive payloads, or secret-bearing subprocess output. Sanitize untrusted values to prevent log injection.
- Return generic errors to webhook callers and Telegram users. Keep diagnostic details in access-controlled application logs; never send stack traces or raw internal exceptions to chat.
- Set log retention and access controls appropriate to the data collected. Prefer centralized, tamper-resistant logging where available; stdout alone does not make logs immutable.

## Security Review Checklist

Before merging a security-sensitive change or deploying a new integration, verify:

- Every reachable webhook requires authentication and validates its payload.
- Telegram commands and callbacks enforce authorization on every request.
- Only explicitly allowlisted scripts and arguments can execute, with timeouts and bounded output.
- Secrets are not committed, baked into images, or exposed through logs or user-facing errors.
- Network exposure, container privileges, writable mounts, and service credentials are minimized.
- CI scans run on the relevant changes, and blocking/exception behavior is understood.
- Tests cover invalid credentials, unauthorized users, malformed input, unknown actions, subprocess failure, and timeout behavior.
- Operators know how to rotate credentials, review logs, and respond to a suspected compromise.

## Recommended References

- [OWASP API Security Top 10](https://owasp.org/API-Security/): API authorization, authentication, resource consumption, and configuration risks.
- [OWASP Application Security Verification Standard (ASVS)](https://owasp.org/www-project-application-security-verification-standard/): a testable application-security control catalog.
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) and [Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html): practical secret and logging controls.
- [NIST Secure Software Development Framework (SSDF)](https://csrc.nist.gov/Projects/ssdf): secure development and supply-chain practices.
- [CIS Docker Benchmark](https://www.cisecurity.org/benchmark/docker): container configuration hardening.
- [Telegram Bot API: `setWebhook`](https://core.telegram.org/bots/api#setwebhook): relevant only if the project changes from polling to Telegram webhooks.
