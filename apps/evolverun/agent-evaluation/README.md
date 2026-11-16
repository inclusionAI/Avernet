# Offline agent evaluation

## Ownership

This package owns reusable deterministic question gates, bounded historical-candidate
selection and lexical similarity. It has no network,
provider credentials, platform submission, model fallback or production write path.
Enterprise adapters consume the installed package; do not maintain copied implementations.

The existing diagnosis skill Session Report evaluator remains independently owned in
`../clawweb-skills/`. This package is not a replacement LLM Judge runtime.

## Install and validate

From this directory, with Python 3.10 or newer in an isolated environment:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Contracts

- `agent_evaluation.synthesis.candidate_gates`: eight question-Meta fields; answer
  and process rubrics each sum to 100. Actor projection contains only id/question.
- `baseline_filter`: caller-pinned SHA256, exact tags and explicitly supplied aliases;
  unknown tags are not evidence that related questions do not exist.
- `baseline_similarity`: lexical similarity for candidate review, not semantic acceptance.


Shape validation and fixture labels are not semantic review, actor execution,
production readiness, or approval of a question bank. Keep historical dataset versions
and experiment scores outside this source package.

## Contribution boundary

Change reusable rules here with regression tests. Change enterprise orchestration and
source connectors in the enterprise repository. Rule changes should be reviewed
separately from transport/model changes; this migration does not alter timeouts.
