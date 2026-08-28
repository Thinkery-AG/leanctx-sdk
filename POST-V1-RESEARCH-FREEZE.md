# Post-v1 Research Freeze

SDK v1 closes exploratory roadmap expansion. No new P10 feature phase exists.
Engineering capacity moves to product quality and adoption.

## Allowed work after v1

- production defects, security findings, and compatibility regressions;
- installation, packaging, documentation, examples, and developer-experience
  friction demonstrated by users;
- performance or reliability work backed by reproducible measurements;
- Stable maintenance and bounded Preview hardening;
- paid-pilot or named-customer requirements with an accountable owner.

## Required intake for new research

New research requires a short decision record containing:

1. named user/customer and observed problem;
2. evidence that current Stable/Preview capabilities cannot solve it;
3. expected outcome and measurable success/failure criteria;
4. privacy, security, licensing, and public/private-boundary classification;
5. fixed time/context budget, measured repeated workflow pain, and explicit
   stop condition;
6. owner responsible for promotion, archival, or deletion of the result.

Ideas without this evidence remain backlog notes, not execution phases.

## P8/P9 disposition

P8 Cloud Receipt Board and P9 Governed Optimization remain private research.
Their current proofs may inform future decisions but create no public product
claim, API promise, hosted-service commitment, or automatic roadmap priority.
Promotion must pass the gate in `PUBLIC-PRIVATE-BOUNDARY.md`.

## Priority order

1. release integrity, security, and reproducibility;
2. clean installation and first successful use;
3. documentation accuracy and namespace clarity;
4. customer-observed DX and adoption blockers;
5. measured performance and support burden;
6. only then, qualified new research.

This freeze can be changed only by a written product decision naming scope,
owner, budget, evidence, and exit criteria.
