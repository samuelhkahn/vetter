# Development Process

- GitHub issues are the backlog; each issue follows `_docs/task-template.md`.
- Sam grooms issues before implementation and owns priority, scope, and acceptance criteria.

## Roles

- Software Engineer: implements one groomed issue using `_docs/team/software-engineer.md`.
- QA Engineer: independently verifies it using `_docs/team/qa-engineer.md`.

## Lifecycle

1. Sam marks an issue ready after grooming it.
2. The engineer implements and tests the issue, then requests QA.
3. QA returns PASS or FAIL against every acceptance criterion.
4. FAIL returns to the engineer with evidence; PASS returns to Sam for merge and closure.

Only Sam changes issue scope or acceptance criteria. Engineers and QA do not close issues.
