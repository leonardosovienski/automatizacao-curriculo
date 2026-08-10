Dependabot is configured (pip + npm). Suggestions to reduce noise and improve safety:

- Change schedule from `daily` to `weekly` for non-security updates if you prefer less noise.
- Consider `open-pull-requests-limit: 5` to reduce number of simultaneous PRs.
- Optionally add `ignore` entries for packages you want to manage manually (e.g., `google-genai`) if needed.
- Consider enabling auto-merge for security/patch updates only when CI (unit + E2E) is stable.

I can open a PR that changes the dependabot schedule and limits; reply with preferences (weekly/daily, PR limit).
