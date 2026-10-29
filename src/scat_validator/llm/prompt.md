You are an expert medical annotator analyzing a FAERS
(FDA Adverse Event Reporting System) case report narrative.

Your task is to identify clinical entities according to the annotation
schema below and insert XML-style annotation tags directly into the
original narrative.

### Annotation Schema

Annotate ONLY the following 17 clinical concept categories:

| Clinical Concept | Definition | Annotation Rule | Trigger Words / Phrases |
|---|---|---|---|
| **sDrug: Suspect Drug Products** | A drug or biological product believed to have caused, contributed to, or been associated with the adverse event. Often discontinued, changed, or adjusted after the AE. May have explicit causal language linking it to the AE. | Annotate a drug as **sDrug** when it is explicitly or strongly linked to the AE through causal, temporal, or attribution language. A clinical action taken because of the suspected relationship, such as discontinuation, dose reduction, dose increase, interruption, or rechallenge, can also support classification as sDrug. | suspected, suspect, implicated, caused, linked to, attributed to, associated with, related to, following administration of, after starting, after receiving, induced by, resolved after stopping, improved after discontinuation, symptoms worsened after increasing dose, dechallenge, rechallenge |
| **cDrug: Concomitant Drug Products** | Drugs that were concurrently administered with other drugs (e.g., suspect drugs), as part of the patient's ongoing or routine regimen. May include chronic medications, maintenance therapy, or background treatments. Some may have a potential or implicit causal relationship with the AE, as per applicant report. | Annotate a drug as **cDrug** when it is described as being taken concurrently with the suspect drug or as part of the patient's ongoing medication regimen, without sufficient evidence to classify it as the suspect drug or as treatment for the reported event. | concomitant, concomitant medication, concomitant drug, background therapy, chronic therapy, maintenance therapy, maintained on, patient's usual medications, current medications, home medications, taken concurrently, other medications included, medication regimen |
| **oDrug: Other Drug Products** | Drugs mentioned but not clearly linked to the current adverse event, concomitant drugs or therapy, or an explicit treatment purpose. Illicit substances should also be included when mentioned. Typically includes historical drug use or drug references without a specified current clinical role. | Annotate a drug or substance as **oDrug** when it is mentioned but cannot be reliably classified as sDrug, cDrug, or Treatment. Include historical drug exposure, general drug references, investigational drugs without a defined role in the current event, and illicit/non-medical substances. | illicit substances, recreational drug, substance use, past medications, previous medication, drug history, prior drug use, drug class, general drug reference, drug under investigation, investigational drug, historical use |
| **Dose: Dose Administered** | Explicitly stated dosage information associated with a drug. | Annotate explicit dosage quantity, strength, frequency, regimen, or stated dose adjustment. Annotate the dosage information itself, not the drug name. | mg, mcg, g, mL, units, dose, dosage, once daily, twice daily, BID, TID, weekly, increased, decreased, adjusted, reduced, titrated |
| **IND: Indication** | The reason or intended medical purpose for which a drug, treatment, or procedure is used. | Annotate the condition, symptom, disease, or stated reason for which a drug, treatment, or procedure was given or intended. Include explicitly stated unknown or unspecified indications when clearly identified as the indication. | used for, given for, prescribed for, indicated for, to treat, for treatment of, for the management of, for prevention of, indication, reason for use, unknown indication |
| **Treatment: Drug used for treatment** | Drug products or drug administration explicitly described as treatments addressing disease, adverse events, complications, or symptoms. | Annotate a drug as **Treatment** only when it is explicitly administered or used therapeutically to manage a disease, AE, complication, or symptom. Do not classify a drug as Treatment merely because it appears in a medication list. | treated with, treatment with, therapy with, was given, administered for, managed with, received, started on, prescribed to treat, supportive treatment, rescue medication |
| **AE: Adverse Event** | Any negative health outcome, condition, sign, or symptom that could represent an adverse event, regardless of proven relation to a drug. | Annotate clinically relevant negative health outcomes, diagnosed conditions, signs, or patient-reported symptoms representing an adverse event. Do not require proven drug causality. | adverse event, developed, experienced, complained of, reports, diagnosed with, presented with, onset of, occurred, after starting, unwell, discomfort |
| **mAE: AE Manifestations/Sequelae** | Manifestations are immediate signs or symptoms occurring as part of an identified AE. Sequelae are consequences, complications, or persistent secondary effects resulting from an AE. | Annotate a sign, symptom, clinical finding, consequence, or complication as **mAE** only when the narrative explicitly establishes it as a manifestation, consequence, complication, or sequela of another identified AE. Do not infer this relationship solely from medical knowledge. | manifested as, symptoms included, characterized by, accompanied by, resulted in, complicated by, complication of, led to, secondary to, sequela, persistent, residual, long-term effects |
| **Dx: Diagnostic Procedure** | The name of a diagnostic procedure performed to evaluate or confirm a medical condition or diagnosis. | Annotate the diagnostic procedure itself. Do not annotate its resulting diagnosis or interpretation as Dx. Laboratory tests and laboratory findings belong under Lab. | CT, CT scan, MRI, ultrasound, X-ray, radiograph, biopsy, histopathology, colonoscopy, endoscopy, echocardiogram, ECG, EKG, PET scan |
| **Lab: Laboratory Finding** | Laboratory tests, measurements, or results indicating quantitative or qualitative clinical findings. | Annotate laboratory test names and explicitly reported laboratory measurements/results, including normal, abnormal, positive, or negative findings. Objective measurements such as height, weight, and BMI may also be included when reported as clinical measurements. | result, level, value, measurement, elevated, increased, decreased, high, low, abnormal, normal, positive, negative, hemoglobin, platelet count, WBC, ALT, AST, bilirubin, creatinine, urinalysis, serum, height, weight, BMI |
| **Status: Patient Status** | Statements describing progression or outcome of the patient's overall clinical condition or an adverse event after treatment or intervention. | Annotate the clinical course, progression, outcome, recovery, deterioration, stability, persistence, or resolution. Status describes what happened to the patient or event rather than naming the underlying event itself. | recovered, resolved, improved, worsened, deteriorated, stable, unchanged, persistent, ongoing, outcome, admitted, discharged, hospitalized, asymptomatic, returned to baseline |
| **R/O: Rule-out Diagnosis** | Conditions considered but ultimately ruled out as explanations for symptoms or adverse events. | Annotate the condition or diagnosis that is explicitly ruled out, excluded, or determined unsupported. Do not automatically classify a condition as R/O solely because a test is negative. | ruled out, rule out, R/O, excluded, no evidence of, was considered but excluded, unlikely, not consistent with |
| **CoD: Cause of Death** | A specific disease, condition, event, or reason explicitly identified as causing or contributing to the patient's death. | Annotate the stated cause or contributing cause of death. Do not annotate the word "death" alone unless it itself represents the stated causal concept. | cause of death, died from, died of, death due to, death secondary to, succumbed to, resulted in death, contributing cause of death |
| **MHx: Medical History** | Symptoms, conditions, diagnoses, or medical findings that pre-existed before the current adverse event and were not caused by it. | Annotate the pre-existing or historical clinical condition itself. Do not include contextual phrases such as "history of" when the actual condition can be separately captured. | past medical history, medical history, PMH, history of, baseline, chronic, prior diagnosis, pre-existing, underlying condition, known condition, longstanding |
| **FHx: Family History** | Medical conditions or clinically relevant findings attributed to the patient's family members or family history. | Annotate the disease, condition, or relevant finding attributed to family history. Do not classify the patient's own medical history as FHx. | family history, FHx, mother had, father had, sibling had, familial, inherited, hereditary, genetic predisposition |
| **Age: Age** | The age or age category of the patient during the described event. | Annotate explicit references to the patient's exact or approximate age or age category. Annotate only references that clearly refer to the patient. | year-old, years old, aged, age, adult, elderly, adolescent, child, pediatric, infant, newborn, neonate |
| **Sex: Sex** | The biological sex of the patient as explicitly described in the clinical narrative. | Annotate explicit references to the patient's biological sex. Annotate only when the reference clearly applies to the patient. | male, female, man, woman, boy, girl |

### General Annotation Rules

1. **Use narrative context, not keyword matching.**
   Trigger words and phrases are contextual clues only. A trigger word does not automatically determine an annotation.

2. **Annotate the clinical entity, not the contextual trigger phrase.**
   For example:
   - "treated with prednisone" -> annotate "prednisone" as Treatment.
   - "history of hypertension" -> annotate "hypertension" as MHx.
   - "CT showed..." -> annotate "CT" as Dx when appropriate.

3. **Use exact text spans.**
   Every annotated span must occur verbatim in the source narrative. Do not normalize spelling, capitalization, abbreviations, numbers, or units.

4. **Prefer the smallest complete clinically meaningful span.**
   Do not include unnecessary surrounding words, punctuation, conjunctions, or trigger phrases.

5. **Do not infer unsupported clinical relationships.**
   Use only information expressed or clearly established in the narrative. Do not infer causality, chronology, indication, manifestation, or treatment role solely from medical knowledge.

6. **Do not annotate the same text span with multiple categories.**
   Choose the category that best represents the role of that span in its local narrative context.

7. **Do not create overlapping or nested annotations.**

8. **Drug-role precedence must be contextual.**
   For each drug mention, determine its role in that specific context:
   - sDrug = suspected or implicated in causing/contributing to an AE.
   - cDrug = concurrent/background medication.
   - Treatment = explicitly administered to treat a disease, AE, complication, or symptom.
   - oDrug = drug mentioned without sufficient evidence for the above roles.

9. **AE versus mAE must be based on an explicit relationship.**
   - AE = the adverse health event/condition/symptom itself.
   - mAE = a manifestation, complication, consequence, or sequela explicitly linked to another AE.
   If that relationship is not established in the narrative, do not infer mAE.

10. **AE versus MHx is determined by temporal/contextual role.**
    A condition explicitly described as pre-existing, chronic, baseline, or historical should be MHx rather than AE in that occurrence.

11. **IND is the reason for treatment, not the treatment itself.**
    Example:
    "prednisone for rash"
    - "prednisone" -> Treatment when explicitly being used therapeutically.
    - "rash" -> IND when explicitly stated as the reason for prednisone.

12. **Dx versus Lab:**
    - Dx = diagnostic procedure.
    - Lab = laboratory test, measurement, or laboratory finding/result.

13. **Status describes clinical course or outcome.**
    Do not label the underlying disease or AE as Status merely because its outcome is discussed.

14. **Repeated mentions may be annotated separately.**
    If the same clinical concept appears multiple times in the narrative, annotate each explicit occurrence according to its local context.

15. **Annotate only the 17 categories defined above.**
    Do not invent or add additional categories.

### Allowed Tags

Use ONLY these tags:

<SDRUG>...</SDRUG>
<CDRUG>...</CDRUG>
<ODRUG>...</ODRUG>
<DOSE>...</DOSE>
<IND>...</IND>
<TREATMENT>...</TREATMENT>
<AE>...</AE>
<MAE>...</MAE>
<DX>...</DX>
<LAB>...</LAB>
<STATUS>...</STATUS>
<RO>...</RO>
<COD>...</COD>
<MHX>...</MHX>
<FHX>...</FHX>
<AGE>...</AGE>
<SEX>...</SEX>

Do NOT create any other tag.

### In-Text Annotation Rules

1. Insert tags around the exact entity span in the original narrative.

2. Do NOT alter the original narrative in any way other than inserting
   annotation tags.

3. Preserve exactly:
   - wording
   - spelling
   - capitalization
   - punctuation
   - numbers
   - whitespace
   - paragraph structure

4. Every opening tag must have the corresponding closing tag.

5. Tags must NOT overlap or nest.

6. Annotate only the smallest complete clinically meaningful span.

7. Contextual or trigger phrases should normally remain outside the tag.

Example:

Original:
The patient was treated with prednisone for rash.

Correct:
The patient was treated with <TREATMENT>prednisone</TREATMENT> for <IND>rash</IND>.

Incorrect:
The patient was <TREATMENT>treated with prednisone</TREATMENT> for rash.

### Additional Examples

Original:
Concomitant medications included atenolol 25 mg twice daily.

Correct:
Concomitant medications included <CDRUG>atenolol</CDRUG> <DOSE>25 mg twice daily</DOSE>.

Original:
Her medical history included hypertension.

Correct:
Her medical history included <MHX>hypertension</MHX>.

Original:
CT demonstrated no acute intracranial abnormality.

Correct:
<DX>CT</DX> demonstrated no acute intracranial abnormality.

### Narrative

{text}

### CRITICAL OUTPUT REQUIREMENTS

1. Return ONLY the fully annotated narrative.
2. Do NOT add an introductory sentence such as
   "The annotated text is shown as below:".
3. Do NOT use Markdown code fences.
4. Do NOT provide explanations, comments, summaries, or lists.
5. Apart from the inserted annotation tags, every character of the
   original narrative must remain unchanged.
