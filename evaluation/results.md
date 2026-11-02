# Evaluation results

Labelled articles: 120 (missing from the database: 0)

## Relevance

| | Labelled relevant | Labelled irrelevant |
|---|---|---|
| **System relevant** | 54 (true positives) | 11 (false positives) |
| **System irrelevant** | 3 (false negatives) | 52 (true negatives) |

- Precision: 83% · Recall: 95% · F1: 0.89

## On the 54 articles both call relevant

- Event status matches: 52/54 = 96%
- Diseases exactly the same: 44/54 = 81%; at least one disease shared: 54/54 = 100%
- Inside / outside India correct: 54/54 = 100%
- State/UT correct (located in India by the labels): 49/50 = 98%
- District level correct (district, or state level when the label has no district): 41/50 = 82%
- Flagged for location review: 4/50

## Disagreements

| Kind | Headline | Label | System |
|---|---|---|---|
| location | 84 BSF personnel hospitalised in Imphal after ‘food poisoning’ | Manipur / Imphal West | Manipur / Imphal East |
| relevance | Dengue ward set up in MGMGH | irrelevant | relevant (public_health_response) |
| location | Suspected rubella case in Simdega puts health dept on alert; MR vaccination drive intensif | Jharkhand / (state level) | Jharkhand / Simdega |
| relevance | District sets target of 18.5k anti-rabies vaccinations | irrelevant | relevant (public_health_response) |
| relevance | Vandalur zoo to reopen tomorrow after bird flu scare | relevant | irrelevant (other) |
| relevance | Trichy corpn levies Rs24,000 penalties on 8 houses for dengue source | irrelevant | relevant (public_health_response) |
| location | City logs 7 new dengue cases | Uttar Pradesh / Prayagraj | Delhi / (state level) [flagged] |
| relevance | El Niño may increase dengue risk in Kerala, IITM study finds; climate model predicts 16-20 | irrelevant | relevant (outbreak_report) |
| relevance | ‘Rabid’ dog bites 16 in Latehar, victims given anti-rabies shots | irrelevant | relevant (outbreak_report) |
| relevance | 450 people in Gorakhpur receive anti-rabies shots after consuming charanamrit made from su | irrelevant | relevant (outbreak_report) |
| relevance | Collector inspects flood-prone areas, reviews dengue drive | irrelevant | relevant (public_health_response) |
| relevance | 138 fall ill after consuming prasad at religious ceremony in Majuli | relevant | irrelevant (other) |
| relevance | Minister Arunraj reviews civic works, dengue preparedness in two zones | irrelevant | relevant (public_health_response) |
| location | H1N1 cases rise in Haryana, but statewide spread remains unclear | Haryana / Hisar | Haryana / (state level) |
| relevance | Meet Isabel Garcia, the 18-year-old North Carolina student who found 3 coral bacteria that | irrelevant | relevant (outbreak_report) |
| location | VJTI students protest over hostel conditions after student’s death, demand rectors’ resign | Maharashtra / Mumbai | Maharashtra / (state level) [flagged] |
| relevance | Seven-year-old died in Andhra after anti-malaria dose. Now top court asks High Court to mo | irrelevant | relevant (outbreak_report) |
| location | Amid at least 32 tribal kids’ death in MP’s Balaghat, fake doc yet to be arrested | Madhya Pradesh / Balaghat | Madhya Pradesh / (state level) |
| location | Scrub Typhus cases surpass 2025 tally in nine months | Rajasthan / (state level) | Rajasthan / Jaipur |
| relevance | Three for Baiga kids die in MP’s Balaghat, Cong MLA puts toll at 55 | relevant | irrelevant (passing_mention) |
| location | Bengal sees late-season dengue surge, records 5.5k new cases in Oct | West Bengal / Kolkata | West Bengal / (state level) [flagged] |
| relevance | CM reviews preparedness for northeast monsoon and dengue prevention | irrelevant | relevant (public_health_response) |
| location | UP govt to create policy for judicious antibiotic use | Uttar Pradesh / Lucknow | Uttar Pradesh / (state level) |
