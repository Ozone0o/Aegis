# Security policy

Please report vulnerabilities privately through a GitHub Security Advisory for
this repository, or contact the project maintainers privately through the
repository owner. Do not open a public issue for an exploitable recovery,
command-execution, or state-file issue.

Include the affected version, a minimal reproduction, the expected impact, and
safe mitigation details. Remove credentials, robot addresses, logs containing
personal data, and deployment secrets before sharing artifacts.

Aegis can invoke operator-configured recovery actions. Deployments must use
argument-list commands with least privilege; `unsafe_shell` is an explicit
exception that requires independent review and an allowlist.
