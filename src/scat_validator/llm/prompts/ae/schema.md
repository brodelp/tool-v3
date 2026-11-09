### Annotation Schema

Annotate ONLY the following clinical concept category:

| Clinical Concept | Definition | Annotation Rule | Trigger Words / Phrases |
|---|---|---|---|
| **AE: Adverse Event** | Any negative health outcome, condition, sign, or symptom that could represent an adverse event, regardless of proven relation to a drug. | Annotate clinically relevant negative health outcomes, diagnosed conditions, signs, or patient-reported symptoms representing an adverse event. Do not require proven drug causality. | adverse event, developed, experienced, complained of, reports, diagnosed with, presented with, onset of, occurred, after starting, unwell, discomfort |

The narrative also mentions concepts that are NOT adverse events. Leave these unannotated:

| Concept (do not annotate) | How it differs from an AE |
|---|---|
| AE manifestation or sequela | A sign, symptom, consequence, or complication that the narrative explicitly establishes as a manifestation, consequence, complication, or sequela of another identified AE. Annotate the AE itself, not its manifestation. |
| Medical history | A symptom, condition, or diagnosis explicitly described as pre-existing, chronic, baseline, or historical, and not caused by the current adverse event. |
| Family history | A condition or finding attributed to the patient's family members. |
| Indication | The condition, symptom, or stated reason for which a drug, treatment, or procedure was given or intended. |
| Rule-out diagnosis | A condition explicitly ruled out, excluded, or determined unsupported. |
| Cause of death | The stated cause or contributing cause of the patient's death. |
| Patient status | The clinical course or outcome of the patient or of an event: recovered, resolved, improved, worsened, stable, hospitalized, discharged. |
| Drugs, doses, procedures, and laboratory findings | Drug and substance names, dosage information, diagnostic procedures, and laboratory tests, measurements, and results (including abnormal results). |
| Age and sex | References to the patient's age or biological sex. |

### General Annotation Rules

1. **Use narrative context, not keyword matching.**
   Trigger words and phrases are contextual clues only. A trigger word does not automatically determine an annotation.

2. **Annotate the adverse event, not the contextual trigger phrase.**
   For example:
   - "developed a rash" -> annotate "rash".
   - "complained of nausea" -> annotate "nausea".

3. **Use exact text spans.**
   Every annotated span must occur verbatim in the source narrative. Do not normalize spelling, capitalization, abbreviations, numbers, or units.

4. **Prefer the smallest complete clinically meaningful span.**
   Do not include unnecessary surrounding words, punctuation, conjunctions, or trigger phrases.

5. **Do not infer unsupported clinical relationships.**
   Use only information expressed or clearly established in the narrative. Do not infer chronology, indication, manifestation, or history solely from medical knowledge.

6. **AE versus manifestation must be based on an explicit relationship.**
   A sign or symptom is a manifestation (not annotated) only when the narrative explicitly links it to another AE. If that relationship is not established in the narrative, annotate it as an AE.

7. **AE versus medical history is determined by temporal/contextual role.**
   A condition explicitly described as pre-existing, chronic, baseline, or historical is medical history rather than an AE in that occurrence.

8. **AE versus indication is determined by the stated reason for use.**
   A condition explicitly stated as the reason a drug, treatment, or procedure was given is an indication rather than an AE in that occurrence, even when the same condition is annotated as an AE elsewhere.

9. **Patient status describes clinical course or outcome.**
   Annotate the event itself, not words describing what happened to it.

10. **Repeated mentions may be annotated separately.**
    If the same adverse event appears multiple times in the narrative, annotate each explicit occurrence according to its local context.

11. **Annotate only adverse events.**
    Do not annotate any other category, and do not invent additional categories.

### Allowed Tags

Use ONLY this tag:

<AE>...</AE>

Do NOT create any other tag.
