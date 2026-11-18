# Evaluation results

Labelled articles: 120 (missing from the database: 0)

## Relevance

| | Labelled relevant | Labelled irrelevant |
|---|---|---|
| **System relevant** | 61 (true positives) | 3 (false positives) |
| **System irrelevant** | 1 (false negatives) | 55 (true negatives) |

- Precision: 95% · Recall: 98% · F1: 0.97

## On the 61 articles both call relevant

- Event status matches: 58/61 = 95%
- Diseases exactly the same: 50/61 = 82%; at least one disease shared: 60/61 = 98%
- Inside / outside India correct: 61/61 = 100%
- State/UT correct (located in India by the labels): 54/57 = 95%
- District level correct (district, or state level when the label has no district): 45/57 = 79%
- Flagged for location review: 3/57

## Disagreements

| Kind | Headline | Label | System |
|---|---|---|---|
| location | 84 BSF personnel hospitalised in Imphal after ‘food poisoning’ | Manipur / Imphal West | Manipur / Imphal East |
| location | Suspected rubella case in Simdega puts health dept on alert; MR vaccination drive intensif | Jharkhand / (state level) | Jharkhand / Simdega |
| location | Norovirus On Prowl; Hosps, OPDs Start Getting Cases | West Bengal / Kolkata | Gujarat / Surat |
| location | City logs 7 new dengue cases | Uttar Pradesh / Prayagraj | Delhi / (state level) [flagged] |
| relevance | El Niño may increase dengue risk in Kerala, IITM study finds; climate model predicts 16-20 | irrelevant | relevant (outbreak_report) |
| disease | ‘Rabid’ dog bites 16 in Latehar, victims given anti-rabies shots | Animal bite | Human Rabies |
| location | Minister Arunraj reviews civic works, dengue preparedness in two zones | Tamil Nadu / Chennai | Tamil Nadu / Tenkasi |
| location | Cow dies after 'dog bite', sparks rabies scare as 450 consume milk-based prasad in UP | Uttar Pradesh / Gorakhpur | Uttar Pradesh / Kheri |
| location | H1N1 cases rise in Haryana, but statewide spread remains unclear | Haryana / Hisar | Haryana / (state level) |
| relevance | Meet Isabel Garcia, the 18-year-old North Carolina student who found 3 coral bacteria that | irrelevant | relevant (outbreak_report) |
| location | VJTI students protest over hostel conditions after student’s death, demand rectors’ resign | Maharashtra / Mumbai | Maharashtra / (state level) [flagged] |
| relevance | Seven-year-old died in Andhra after anti-malaria dose. Now top court asks High Court to mo | irrelevant | relevant (outbreak_report) |
| location | Amid at least 32 tribal kids’ death in MP’s Balaghat, fake doc yet to be arrested | Madhya Pradesh / Balaghat | Madhya Pradesh / (state level) |
| location | Scrub Typhus cases surpass 2025 tally in nine months | Rajasthan / (state level) | Rajasthan / Jaipur |
| relevance | Three for Baiga kids die in MP’s Balaghat, Cong MLA puts toll at 55 | relevant | irrelevant (passing_mention) |
| location | CM reviews preparedness for northeast monsoon and dengue prevention | Tamil Nadu / (state level) | Gujarat / (state level) [flagged] |
| location | UP govt to create policy for judicious antibiotic use | Uttar Pradesh / Lucknow | Uttar Pradesh / (state level) |
