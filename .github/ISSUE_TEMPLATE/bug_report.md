name: Bug report
description: Something is broken or behaves differently from the documentation
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to report a bug. Please use synthetic data only.
        For security vulnerabilities, follow SECURITY.md instead of opening a public issue.
  - type: textarea
    id: what
    attributes:
      label: What happened
      description: A clear description of the bug.
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: What you expected
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: Steps to reproduce
      placeholder: |
        1. ...
        2. ...
        3. ...
    validations:
      required: true
  - type: input
    id: commit
    attributes:
      label: Commit or version
      description: Output of `git rev-parse HEAD` if possible.
  - type: textarea
    id: logs
    attributes:
      label: Relevant output
      description: Paste the gate or test output. Redact phone numbers and tokens.
      render: shell
