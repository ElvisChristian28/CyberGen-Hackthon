# Secure RAG — Document Security Gateway

**CYBERGEN 2026 | Problem Statement 4: Secure RAG — Detecting Malicious Documents Before AI Reads Them**

> **Project status:** Development scaffold / planned integration. This README documents the intended workflow and Person 4 deliverables; it does **not** claim that the scanner, dashboard, or vector database is already implemented.

## 1. Overview

Retrieval-Augmented Generation (RAG) applications often ingest external PDFs, reports, documents, and text files. Some documents may contain indirect prompt-injection instructions, sensitive personal information, or other suspicious content. If ingested without checks, that content can influence an AI assistant or expose data.

**Secure RAG** is a proposed protective layer that inspects documents **before** they enter a retrieval pipeline. It is designed to surface detection evidence, assign an explainable risk assessment, and take one of three actions:

- **Allow** — approve a document for ingestion when checks pass.
- **Sanitize** — remove or mask identified unsafe content, then re-scan and review the cleaned version.
- **Quarantine** — hold a potentially malicious or unresolved document outside the vector database for review.

The system's intended flow is:

```text
Document Upload
      |
      v
Text Extraction & Parsing
      |
      v
Prompt-Injection / PII / Risk Checks
      |
      v
Risk Score + Evidence + Decision
      |
      +---- ALLOW ---------------------> Approved Document --> Vector DB
      |
      +---- SANITIZE --> Re-scan/Review --> Approved Version --> Vector DB
      |
      +---- QUARANTINE ---------------> Review Queue (not indexed)
```

> **Safety rule:** Neither an original quarantined document nor an unapproved sanitized document should enter the vector database.

## 2. Person 4 Responsibilities

Person 4 owns the **dashboard, test data, and presentation assets**.

### Dashboard

- Create a web interface for uploading documents (target formats: PDF, DOCX, TXT).
- Display each scan's risk score, severity, findings, and evidence-based reasons.
- Provide a quarantine queue with document status and review actions.
- Visualize the document pipeline from **Original → Scanned → Sanitized/Approved → Vector DB**.
- Show original-versus-sanitized content when available, while masking any sensitive text in public demos.

### Test data

Prepare a clearly labeled **synthetic** document set covering:

| Category | Purpose | Expected behavior (to validate) |
| --- | --- | --- |
| Clean | Ordinary factual document with no malicious directives or PII | Allow if checks pass |
| Injected | Explicit instructions attempting to redirect the AI assistant | Flag and quarantine or sanitize |
| PII-heavy | Fictional personal identifiers, email addresses, phone numbers | Flag PII; mask or quarantine based on policy |
| Subtle / tricky | Indirect instructions hidden in otherwise useful content | Surface suspicious passages for review |

Create a test manifest with `sample_id`, `category`, `expected_decision`, `actual_decision`, `reason`, and `pass_fail`. These are **test expectations**, not measured accuracy claims.

### Final deliverables

- Presentation slides describing the problem, architecture, features, demo, testing, and limitations.
- A repeatable live demo script.
- This README and setup instructions.
- A final project report documenting design decisions, results, limitations, and next steps.

## 3. Proposed Tech Stack

| Layer | Suggested technology |
| --- | --- |
| Web UI | Streamlit |
| Language | Python 3.10+ |
| Tabular views | Pandas |
| Dashboard charts (optional) | Plotly |
| PDF text extraction | pypdf |
| DOCX text extraction | python-docx |
| Risk analysis | Teammates' scanning/detection module or API |
| Vector ingestion | Teammates' approved ingestion component |

**Note:** These are development choices, not confirmed dependencies for a finished product.

## 4. Suggested Repository Structure

```text
secure-rag/
├── app.py                       # Streamlit entry point (to implement)
├── dashboard/
│   ├── upload.py                # Upload page
│   ├── risk_analysis.py         # Risk score + evidence
│   ├── quarantine.py            # Review queue
│   └── pipeline.py              # Pipeline/status visualization
├── test_documents/
│   ├── clean/
│   ├── injected/
│   ├── pii/
│   └── tricky/
├── presentation/                # Slides and demo script
├── reports/                     # Test results and final report
├── requirements.txt             # Python dependencies (to add)
└── README.md
```

The folder tree is a **suggested layout**, not a statement that the files already exist.

## 5. Local Development Setup (Windows)

From PowerShell in the project directory:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install streamlit pandas plotly python-docx pypdf
```

After `app.py` has been created, launch the dashboard with:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Streamlit will print a local URL to open in your browser. **This command will not work until the dashboard entry point has been implemented.**

## 6. Integration Contract (Proposed)

The UI should use the real backend scanner once available. Agree on its request and response shape with the other team members before integration. The following JSON is **illustrative mock data**, not a scan result:

```json
{
  "document_id": "demo-001",
  "filename": "example.txt",
  "risk_score": 87,
  "risk_level": "high",
  "decision": "quarantine",
  "findings": [
    {
      "type": "indirect_prompt_injection",
      "reason": "Document contains an instruction aimed at the assistant rather than document readers.",
      "location": "paragraph 3",
      "evidence_excerpt": "[redacted sample]"
    }
  ],
  "pipeline_status": "quarantined",
  "sanitized_document_available": false,
  "indexed_in_vector_db": false
}
```

Implementation expectations:

1. Show the exact backend findings and evidence without inventing reasons.
2. Distinguish **scanner output** from **human review decisions**.
3. Never use a score alone as proof that a document is malicious.
4. Treat document text as untrusted input; never execute instructions found inside it.
5. Index only content that has passed the selected approval workflow.

## 7. Demo and Testing Plan

Suggested live demonstration:

1. Upload a **clean** synthetic document and show its scan findings and approval path.
2. Upload an **injected** synthetic document and show its highlighted suspicious passage, score, and quarantine decision.
3. Upload a **PII-heavy** synthetic document and show masking/sanitization if implemented.
4. Open the **quarantine queue**, review the flagged item, and show that it is **not** indexed.
5. Use the **pipeline view** to demonstrate the difference between an original document and its approved sanitized version.
6. Present the actual test manifest and any measured results; explicitly identify unsupported cases.

For each test, record at least the expected decision, actual decision, relevant evidence, and pass/fail status. Report false positives and false negatives rather than claiming perfect detection.

## 8. Security and Privacy Principles

- Use **fictional/synthetic PII** in public demos and shared test files.
- Keep quarantined documents separate from approved retrieval content.
- Mask sensitive excerpts in the dashboard and exported reports.
- Validate file types and enforce upload size limits appropriate for the environment.
- Handle malformed PDFs, DOCX files, and extraction failures gracefully.
- Apply least-privilege access to reviewer actions and stored evidence.
- Do not treat a model-generated explanation as ground truth; retain underlying evidence.
- Do not send private documents to external AI services without explicit authorization.

## 9. Delivery Checklist

- [ ] Streamlit dashboard and document uploader
- [ ] Real detector integration with risk score and explainable findings
- [ ] Quarantine queue and reviewer status workflow
- [ ] Original → scanned → sanitized/approved → vector DB pipeline visualization
- [ ] Clean, injected, PII-heavy, and tricky synthetic test documents
- [ ] Test manifest and results with documented limitations
- [ ] Presentation slides and demo script
- [ ] Final project report and verified setup instructions

## 10. Problem Statement Alignment

This project targets the CYBERGEN 2026 Secure RAG challenge: **inspect document content, detect suspicious instructions and possible PII, explain risk, sanitize or quarantine unsafe documents, and demonstrate controlled ingestion into a vector database.**

**Project:** Secure RAG — Document Security Gateway  
**Event:** CYBERGEN 2026  
**Track:** Problem Statement 4  
**Person 4 scope:** Dashboard, test data, and presentation  

---

*Update this README as features are implemented. Replace mock examples, planned architecture, and unchecked tasks only after validating the corresponding functionality.*
