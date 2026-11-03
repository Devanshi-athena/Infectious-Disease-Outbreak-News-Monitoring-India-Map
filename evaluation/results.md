# Evaluation results

Labelled articles: 120 (missing from the database: 0)

## Relevance

| | Labelled relevant | Labelled irrelevant |
|---|---|---|
| **System relevant** | 62 (true positives) | 3 (false positives) |
| **System irrelevant** | 3 (false negatives) | 52 (true negatives) |

- Precision: 95% · Recall: 95% · F1: 0.95

## On the 62 articles both call relevant

- Event status matches: 59/62 = 95%
- Diseases exactly the same: 52/62 = 84%; at least one disease shared: 62/62 = 100%
- Inside / outside India correct: 62/62 = 100%
- State/UT correct (located in India by the labels): 56/58 = 97%
- District level correct (district, or state level when the label has no district): 48/58 = 83%
- Flagged for location review: 5/58

## Disagreements

| Kind | Headline | Label | System |
|---|---|---|---|
| location | 84 BSF personnel hospitalised in Imphal after ‘food poisoning’ | Manipur / Imphal West | Manipur / Imphal East |
| location | Suspected rubella case in Simdega puts health dept on alert; MR vaccination drive intensif | Jharkhand / (state level) | Jharkhand / Simdega |
| relevance | Vandalur zoo to reopen tomorrow after bird flu scare | relevant | irrelevant (other) |
| location | City logs 7 new dengue cases | Uttar Pradesh / Prayagraj | Delhi / (state level) [flagged] |
| relevance | El Niño may increase dengue risk in Kerala, IITM study finds; climate model predicts 16-20 | irrelevant | relevant (outbreak_report) |
| relevance | 138 fall ill after consuming prasad at religious ceremony in Majuli | relevant | irrelevant (other) |
| location | Minister Arunraj reviews civic works, dengue preparedness in two zones | Tamil Nadu / Chennai | Tamil Nadu / Tenkasi |
| location | H1N1 cases rise in Haryana, but statewide spread remains unclear | Haryana / Hisar | Haryana / (state level) |
| relevance | Meet Isabel Garcia, the 18-year-old North Carolina student who found 3 coral bacteria that | irrelevant | relevant (outbreak_report) |
| location | VJTI students protest over hostel conditions after student’s death, demand rectors’ resign | Maharashtra / Mumbai | Maharashtra / (state level) [flagged] |
| relevance | Seven-year-old died in Andhra after anti-malaria dose. Now top court asks High Court to mo | irrelevant | relevant (outbreak_report) |
| location | Amid at least 32 tribal kids’ death in MP’s Balaghat, fake doc yet to be arrested | Madhya Pradesh / Balaghat | Madhya Pradesh / (state level) |
| location | Scrub Typhus cases surpass 2025 tally in nine months | Rajasthan / (state level) | Rajasthan / Jaipur |
| relevance | Three for Baiga kids die in MP’s Balaghat, Cong MLA puts toll at 55 | relevant | irrelevant (passing_mention) |
| location | CM reviews preparedness for northeast monsoon and dengue prevention | Tamil Nadu / (state level) | Gujarat / (state level) [flagged] |
| location | UP govt to create policy for judicious antibiotic use | Uttar Pradesh / Lucknow | Uttar Pradesh / (state level) |
