name: Feature request
description: Propose a capability or a change in behaviour
labels: ["enhancement"]
body:
  - type: markdown
    attributes:
      value: |
        Every feature must either win or protect a sale, save the owner time, or make the
        business smarter about its customers. Explain which one, and how it would be tested.
  - type: textarea
    id: problem
    attributes:
      label: The problem
      description: What pain does this address, for which user?
    validations:
      required: true
  - type: textarea
    id: proposal
    attributes:
      label: Proposed change
    validations:
      required: true
  - type: textarea
    id: safety
    attributes:
      label: Safety and protected-zone impact
      description: Does it touch tenancy, money, privacy, auth, tests or the gate?
  - type: textarea
    id: test
    attributes:
      label: How it would be proven
      description: Acceptance tests, evals, or a property test.
