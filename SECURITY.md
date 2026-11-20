# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately via **GitHub Private Vulnerability Reporting** (Security tab -> Report a vulnerability) or directly to the repository maintainer. Do not open public issues with exploit details, tokens, or browser session logs.

## Security Model & Boundaries

- **Local-First Architecture**: AIroom binds to `127.0.0.1` by default. Exposing it on `0.0.0.0` or untrusted networks is discouraged without a TLS reverse proxy.
- **API Token**: On first startup, the backend generates a 32-byte URL-safe bearer token (`AIROOM_TOKEN_PATH`, permissions `0600`). Treat this token as a secret; it grants full access to REST and WebSocket endpoints.
- **Chrome DevTools Protocol (CDP)**: The CDP port (`AIROOM_CDP_URL`, default `http://127.0.0.1:9223`) provides full control over the dedicated browser profile (`AIROOM_CHROME_DIR`). **Never expose the CDP port beyond localhost.**
- **CORS**: CORS restricts browser origins and is not a substitute for bearer token authentication.
- **Browser Profile Isolation**: AIroom uses a dedicated `--user-data-dir` (`airoom-chrome`) isolated from your personal browser profile. Never commit `backend/data/`, `api_token`, `*.db`, or `airoom-chrome/`.
