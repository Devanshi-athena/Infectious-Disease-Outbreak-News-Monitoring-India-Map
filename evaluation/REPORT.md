# Evaluation report

Outbreak News Monitor prototype · evaluated 29 October 2026 · `python -m evaluation.evaluate` reproduces the numbers (details in `results.md`).

## Summary

| What is measured | Result | Sample |
|---|---|---|
| Relevance precision (system "relevant" that really report an outbreak or response) | **83%** | 120 articles |
| Relevance recall (real outbreak reports the system keeps) | **93%** (95% after one bug fix, see below) | 120 articles |
| Relevance F1 | **0.88** (0.89 after the fix) | 120 articles |
| Event status (new / suspected / historical / unclear) | 96% | 53 relevant articles |
| Disease attribution (at least one correct · exactly correct) | 100% · 81% | 53 relevant articles |
| Inside / outside India | 100% | 53 relevant articles |
| State/UT | 98% (48/49) · 95% (57/60) | two samples |
| District level (right district, or state level when no district is supported) | 82% (40/49) · 92% (54/59) | two samples |

## Method

- **Sample:** 120 articles drawn at random (fixed seed 42) from the 495 stored articles that match a disease keyword, published 9 September – 28 October 2026 and collected 23–29 October. They come from 9 of the 10 sources (Times of India 87, Hindustan Times 11, Indian Express 8, Deccan Chronicle 5, others 1–3; none from NDTV), in proportion to what each source contributes to the database. The sample was taken *before* looking at any results.
- **Labels** (`labels.csv`): each article was read and labelled for relevance, event status, diseases and outbreak location by two AI-assisted labelling passes that saw only the article text and a written labelling guide, never the system's output. They are marked **"AI-assisted, unverified"** and should be checked by a person before the numbers are quoted as final; disagreements below are the best place to start.
- **Labelling guide:** relevant = reports a current outbreak or disease event of a monitored disease in a place, or a public-health response to such activity. Not relevant: research, awareness, policy without an event, court cases about past events, crime, passing mentions, unmonitored diseases.
- **Scored:** the automatic classification, not manual review decisions. District names are compared through the GeoNames reference data, so spelling variants count as equal.
- **Second location sample:** the earlier 60-article location set (`location_labels.csv`, spot-checked by the project owner), scored by `python -m evaluation.location_eval`.
- The classifier was **not tuned on this sample**: the misclassifications below are reported as found.

## Relevance errors (15 of 120)

**False positives (11)**
- *6 public-health responses without reported cases* (a new dengue ward, fines for mosquito breeding, dengue preparedness reviews by a collector, minister and chief minister, an anti-rabies vaccination target). The labels call these not relevant; the system calls them responses. This is a definition question rather than a bug: if such responses count as relevant, precision is 92%.
- *2 rabid-animal scares* (dog bites in Latehar; anti-rabies shots after drinking milk from a rabid cow in Gorakhpur): borderline.
- *3 real errors:* a climate study predicting dengue risk, a US student's research on cholera bacteria, and a Supreme Court case on a child's death after an anti-malaria tablet. The headlines name the disease and the text uses words like "outbreaks" and "died", which the rules read as an event.

**False negatives (4)**
- *"'Mystery' fever kills 2 sisters in Baran"*: the quotation marks broke the keyword "mystery fever" in the headline. **Fixed after the evaluation** (keywords now match across quote marks); re-processing all 555 stored articles changed only this one. Because the bug was found on this sample, the post-fix recall (95%) is slightly optimistic; the 93% measured first is the unbiased figure.
- *"138 fall ill after consuming prasad in Majuli"*: a food-poisoning outbreak whose headline names no disease.
- *Balaghat child deaths*: the headline names no disease (a known limitation).
- *"Vandalur zoo to reopen after bird flu scare"*: the reopening after an outbreak was not read as an event.

## Location errors (9 of 49)

- 3 where the label picks one district of a state-wide story (Haryana H1N1, West Bengal dengue, a UP policy item) and the system stays at state level: arguably both are acceptable.
- 2 the other way round: the system picks a district for a state-wide story (Simdega for Jharkhand measles, Jaipur for Rajasthan scrub typhus).
- 2 state-level results flagged for review where the district was available (VJTI student in Mumbai; Balaghat).
- 1 neighbouring district (Imphal East instead of Imphal West).
- 1 wrong state: "City logs 7 new dengue cases" (Prayagraj) was placed in Delhi, but flagged for review.

## Recommended next steps

1. Have a person verify `labels.csv` (about 1 hour); re-run `python -m evaluation.evaluate`.
2. Decide whether responses without reported cases count as relevant, and adjust the labels or the classifier accordingly.
3. Add research cues for "study finds" / "predicts" headlines, and court-case cues (the quoted-keyword bug is already fixed).
4. Evaluate on a fresh sample after any change, so the scores stay honest.

## Limits of this evaluation

- The labels are AI-assisted and unverified; a single person's review may move the numbers by a few points.
- 120 articles: the 95% confidence range is roughly ±9 points on precision (64 articles) and ±7 on recall (57). Times of India makes up 73% of the sample, so results mostly reflect its style of reporting.
- Only stored articles are evaluated (those matching a keyword). Outbreaks reported without any configured keyword are not counted as misses; `python -m evaluation.recall_check` compares collection coverage with Bing News separately.
