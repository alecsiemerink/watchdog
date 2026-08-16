# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's **Report a vulnerability** flow rather than opening a public issue. Do not attach real recordings, webhook URLs, tailnet names, or other credentials to a report.

## Secrets and sensitive data

- Hark webhook URLs are credentials and must never be committed.
- Local configuration is stored outside the repository with mode `0600`.
- Recordings, snapshots, PID files, and logs are ignored by Git.
- Tailscale Serve is used for tailnet-only access. The project does not enable Funnel.

If a Hark webhook is exposed, rotate it in the Hark dashboard immediately. If a recording URL is accessible to an unintended tailnet identity, review the Tailscale Serve route and your tailnet access policy.

## Supported versions

Until a stable release exists, security fixes are applied to the latest commit on `main`.
