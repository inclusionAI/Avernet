---
status: research-draft
source_date: 2026-10-21
prepared_on: 2026-11-13
---

# Difficulty assessment and calibration proposals

## Problem and scope

Question difficulty should describe how hard it is to produce an evidence-backed
answer under the stated starting information, available tools and permission
boundary. It should not be inferred from question length, tool-call count,
latency or one model's isolated failure.

The same user request can have different difficulty under different conditions.
A question that includes the relevant error and the applicable rule may only
require extraction. The same question with only an object identifier may require
finding the right record, correlating it with an execution and ruling out
competing explanations.

This note proposes a review method; it is not an adopted scoring algorithm,
question update, grader implementation or measured calibration result. It does
not replace [`benchmark-question-meta.md`](../benchmark-question-meta.md) and
does not change existing question metadata.

## What difficulty is, and is not

| Concept | Boundary in this proposal |
| --- | --- |
| Question difficulty | Necessary judgment needed to reach the reviewed, evidence-backed answer |
| Answer or process score | How one Actor performed on one attempt; assessed separately |
| Execution cost | Calls, delay and read volume; useful to measure independently, not a difficulty proxy |
| Environment failure or missing material | A condition to report or repair, not difficulty by itself |

Difficulty is relative to the agreed Actor input and tool environment. The
review must first check that the task has a reachable answer under those
conditions. If the task is intentionally about recognizing insufficient
information, that recognition is the target; the reviewer must not silently
require an unavailable root cause.

## Materials required for assessment

A Judge assessing difficulty should have access to:

- the exact user question and starting context shown to the Actor;
- the Actor-visible tools, files, knowledge sources and permission boundary;
- the reviewed answer and its evidence references;
- necessary intermediate results, such as the relevant execution, object
  relationship or applicable rule; and
- at least one feasible route from the starting context to the reviewed answer,
  including reasonable alternatives when they materially affect difficulty.

The reference answer and complete evidence are Judge-side material. They must not
be added to the Actor projection merely because they are available for
calibration. If the reference and reliable evidence conflict, record a review
issue instead of silently replacing one or inventing an Actor error.

## Review sequence

Use the following sequence for each question:

1. Define the required answer boundary: status, rule explanation, diagnosis or
   another explicitly requested result. Do not upgrade a status lookup into a
   root-cause investigation without evidence that the task requires it.
2. List the intermediate results that the answer must depend on. Each result
   should map to an observable source or evidence condition.
3. Check how an Actor can discover those results from the starting context. Do
   not assume hidden identifiers, exact search terms or the reviewed answer.
4. Consider a reasonable alternative route. A necessary condition, not the
   author's incidental tool order, is what should affect difficulty.
5. Score the four dimensions below, giving a concrete reason for the score and
   why the next level is not warranted.
6. Check for double counting. A multi-step investigation may touch several
   dimensions, but each increment needs its own evidence.

## Four dimensions

Each dimension is an integer from 0 to 3. These are difficulty attributes, not
answer grades.

### Evidence location: where the key material is found

| Score | Boundary |
| --- | --- |
| 0 | The evidence is in context or at an exact supplied location. |
| 1 | A known object or keyword leads through a routine lookup. |
| 2 | The initial information is imprecise; the Actor must revise a query, choose a source or filter inapplicable results with evidence. |
| 3 | The evidence entry point is not known; the Actor must discover successive clues and use them to continue the search. This is not mechanical field passing. |

Document length, search latency and the number of returned records do not add
points by themselves.

### Rule reasoning: how facts become the conclusion

| Score | Boundary |
| --- | --- |
| 0 | The answer is a direct extraction of an explicit fact. |
| 1 | One clear rule or simple condition is applied. |
| 2 | Multiple conditions, exceptions or state layers must be combined. |
| 3 | The Actor must establish a non-obvious intermediate conclusion before a dependent deduction, and explain why an apparently plausible alternative does not hold. |

Technical vocabulary or long documentation is not sufficient for a higher score
when the applicable answer is stated directly.

### Information linking: whether facts belong to the same case

| Score | Boundary |
| --- | --- |
| 0 | One object's complete evidence is sufficient. |
| 1 | A clear identifier connects a small number of records without ambiguity. |
| 2 | Object, time, version or state must be checked so multiple sources are
correctly associated. |
| 3 | Similar events, ambiguous ownership or an apparent conflict require extra
evidence to rule out the wrong association. |

Joining tables on an unambiguous primary key is not automatically difficult.
Conversely, two plausible events with similar timestamps can be difficult even
when each individual lookup is easy.

### Path decision: what to investigate next

| Score | Boundary |
| --- | --- |
| 0 | The route is fixed and no meaningful choice is required. |
| 1 | A simple branch points clearly to the next step. |
| 2 | Several reasonable explanations or investigation directions exist; the
Actor must choose the next evidence that distinguishes them. |
| 3 | Multiple investigation choices depend on new evidence; the Actor must
revise hypotheses and decide when the available evidence is sufficient to stop. |

A large tool menu is not itself a branching problem. Only alternatives that are
reasonable for the reviewed task count.

## Avoiding double counting

The same lookup can expose different burdens, but the reasons must be distinct.
For example, “find the session, obtain its execution identifier, then inspect a
log” could be evidence-location 1 and information-linking 1 when each relation
is explicitly given. If several candidate identifiers could refer to the event,
information-linking may be 2 or 3. If the first log cannot distinguish two
causes and the Actor must choose a discriminating follow-up, path-decision may
increase. If the evidence source itself is unknown, that is evidence-location,
not an automatic increase in every other dimension.

A useful check is to reveal the evidence directly. If the task becomes easy,
the main burden is locating it. If the records are found but could be confused,
the burden is linking. Only when the correct records are established but the
answer still requires a non-trivial derivation should rule reasoning carry the
main difficulty.

## Initial overall band proposal

Do not begin with a seemingly precise 0–100 score. As a first calibration
proposal, use the highest necessary dimension score as the initial band:

| Highest dimension | Initial band | Meaning |
| --- | --- | --- |
| 0 | Simple | Mostly direct reading or extraction |
| 1 | Routine | Basic retrieval, application or correspondence with a clear route |
| 2 | Challenging | At least one material filtering, linking, reasoning or route-choice burden |
| 3 | Difficult | A necessary exploratory or deep reasoning burden exists |

The highest score must represent a necessary difficulty, not an avoidable route.
Other scores describe complexity within the band but do not automatically raise
it. Thus `[0,3,0,0]` can be difficult, while `[2,2,2,2]` is initially a
high-complexity challenging question. Whether multiple medium burdens should
raise a band requires representative-question comparisons; this proposal adds
no unvalidated weights or automatic upgrade.

If a downstream system requires a 0–100 value, first validate the bands and
anchor examples, then define a conversion. Numeric precision should not exceed
the evidence supporting the judgment.

## Calibration and acceptance work

A written rubric is not evidence that ratings are stable. Before adopting a
calibration rule, review a small set of boundary questions with pinned inputs,
references and evidence:

1. **Repeat assessment:** rate the same attempt independently and record
   dimension and band disagreements.
2. **Rewording:** change wording without changing information or required work;
   the difficulty should not materially change.
3. **Condition contrast:** add key evidence or remove irrelevant material and
   check only the expected dimensions move.
4. **Alternative routes:** provide a discoverable simpler route and verify that
   the Judge does not continue to reward an incidental complex route.
5. **Boundary review:** for large dimension or band disagreements, return to the
   concrete evidence and score definitions rather than averaging them away.

Track per-dimension agreement, band agreement and the types of boundary that
produce disagreement. Do not claim a target agreement rate or a completed
calibration experiment until it has actually run.

## Difficulty distribution and unresolved decisions

Rate questions independently before arranging a benchmark set. A roughly
balanced distribution may be a useful selection target, but ratings must not be
changed to fill quotas. If a band lacks valid questions, retain that gap or add
new fact-supported tasks whose answers are reachable; do not hide evidence,
remove permissions or manufacture an inaccessible “difficult” case.

Open decisions include whether a set of medium dimensions needs a cumulative
upgrade, how partial information affects difficulty when insufficiency is the
intended answer, and whether any later 0–100 conversion is useful. Correctness,
process quality, efficiency and environment completeness remain separate
assessment questions. This note makes no claim that an evaluation run,
production change or business acceptance occurred.

## Source and provenance

Source: “TeamClaw Benchmark 题目难度评估方式（讨论稿）”, dated 2026-10-21,
read for this note on 2026-11-13. SHA-256:
`b940850e1acf14f7182fae00251bb0558fee9511323d09056eb5434c0084a515`.

The source is a local design discussion, not an adopted scoring contract. This
public note preserves its proposal status and omits internal identities,
endpoints, raw trajectories, credentials and business data. The digest
identifies the design input; it is not execution or acceptance evidence.
