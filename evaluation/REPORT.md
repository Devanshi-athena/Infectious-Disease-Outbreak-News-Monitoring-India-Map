# Evaluation report

Outbreak News Monitor prototype, evaluated on 3 November 2026. Run `python -m evaluation.evaluate` to reproduce the numbers; the full list of disagreements is in `results.md`.

## Summary

| What was measured | Result | Articles |
|---|---|---|
| Relevance precision (articles marked relevant that really are) | **95%** | 120 |
| Relevance recall (relevant articles the system keeps) | **95%** | 120 |
| Relevance F1 | **0.95** | 120 |
| Event status (new / suspected / historical / unclear) | 95% | 62 |
| Disease (at least one correct / exactly right) | 100% / 84% | 62 |
| Inside or outside India | 100% | 62 |
| State / UT | 97% (56/58) | 58 |
| District level (right district, or state level when no single district applies) | 83% (48/58) | 58 |

A separate set of 60 articles labelled for location only (`location_labels.csv`, `python -m evaluation.location_eval`) gives 95% for state and 92% for district.

## How it was done

- **Sample.** 120 articles picked at random (fixed seed) from the 495 stored articles that match a disease keyword, published 9 September – 28 October 2026. They come from 9 of the 10 sources in proportion to what each contributes: Times of India 87, Hindustan Times 11, Indian Express 8, Deccan Chronicle 5, the rest 1–3 each (none from NDTV).
- **Labels** (`labels.csv`). Each article was labelled for relevance, event status, diseases and outbreak location from the article text alone, without looking at what the system had decided. The labels were drafted with AI assistance and then checked by Devanshi.
- **What counts as relevant.** An article is relevant if it reports a current outbreak or disease event of a monitored disease in a place, or a concrete prevention, surveillance or preparedness action against a monitored disease in an area (for example a dengue ward being set up, fines for mosquito breeding, an anti-rabies vaccination drive), even without case numbers. Research, explainers, policy without an event, court cases about past events, crime and passing mentions are not relevant.
- **Diseases.** Labels name the disease the article supports and nothing more, the same standard the system uses for news and official reports. A suspected "brain fever" death is labelled AES / unspecified encephalitis, not Japanese Encephalitis (it still falls in the monitored "Japanese Encephalitis / AES" group). Jaundice alone is not hepatitis A/E (it has its own "cause not confirmed" group), dysentery is not acute diarrhoeal disease in general, and bird flu is not seasonal influenza.
- **Scoring.** The automatic result is scored, not manual review decisions. Place names are compared through the reference data, so spelling variants count as the same place.

## Where the system disagrees with the labels

**Relevance (6 of 120)**

Counted as relevant, but not:
- a climate study predicting dengue risk in Kerala;
- a US student's research on cholera bacteria;
- a Supreme Court case about a child's death after an anti-malaria tablet.

These headlines name the disease and the text uses words like "outbreaks" or "died", which the rules read as an event.

Missed:
- "138 fall ill after consuming prasad in Majuli" and the Balaghat child deaths: the headlines don't name a disease.
- "Vandalur zoo to reopen after bird flu scare": the reopening after an outbreak wasn't read as an event.

**Location (10 of 58)**
- In 3 cases a state-wide story was labelled with one district (Haryana H1N1, a UP policy item, Balaghat), while the system stayed at state level. Either reading is defensible.
- In 2 cases it was the other way round: the system picked a district for a state-wide story (Simdega, Jaipur).
- 1 neighbouring district (Imphal East instead of Imphal West), and 1 wrong district within the right state (Tenkasi instead of Chennai).
- 1 state-level result where the district was available (the VJTI student in Mumbai).
- 2 wrong states, both flagged for review: "City logs 7 new dengue cases" (Prayagraj, placed in Delhi) and "CM reviews preparedness…" (Tamil Nadu, placed in Gujarat).

## Changes made along the way

- Keywords now match across quote marks. "'Mystery' fever kills 2 sisters in Baran" was missed before; after the change it is caught, and no other stored article changed. Because this bug was found on the sample, recall measured before the fix (one more miss) is the more conservative figure.
- The classifier was not otherwise tuned on this sample.

## Suggested next steps

1. Add research cues for headlines like "study finds" or "model predicts", and court cues for rulings about past deaths.
2. Re-run the evaluation on a fresh sample after any rule change, so the numbers stay honest.

## Limits

- 120 articles is a small sample: the 95% confidence range is roughly ±5 points on precision and recall.
- Times of India makes up 73% of the sample, so the results mostly reflect its style of reporting.
- Only stored articles are evaluated. An outbreak reported without any configured keyword is never collected, so it can't show up as a miss here. `python -m evaluation.recall_check` compares coverage with Bing News separately.
