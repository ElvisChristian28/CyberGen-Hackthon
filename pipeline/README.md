# ⚙️ Stage 3 — Decision Pipeline & Vector Database

**Owner:** Elvis Christian
**Folder:** `pipeline/`
**Receives from:** `detection/` | **Feeds into:** `dashboard/` + Vector DB

---

## 🎯 Your Job in One Sentence

Take the fully-scored document from Detection, make the allow/sanitize/quarantine call, redact risky content, load safe documents into the vector database, power a RAG chatbot, and write an audit trail for every decision.

---

## 📋 Responsibilities

### 1. Decision Logic

Three possible outcomes based on `risk_score`:

| Score Range | Decision | Action |
|-------------|----------|--------|
| 0 – 30 | ✅ `ALLOW` | Pass to vector DB as-is |
| 31 – 69 | ⚠️ `SANITIZE` | Redact findings, then pass sanitized text to vector DB |
| 70 – 100 | 🚫 `QUARANTINE` | Block from vector DB; store in quarantine queue for manual review |

```python
def decide(risk_score: int) -> str:
    if risk_score <= 30:
        return "ALLOW"
    elif risk_score <= 69:
        return "SANITIZE"
    else:
        return "QUARANTINE"
```

### 2. Sanitizer

For `SANITIZE` decisions:
- For each finding, replace the `matched` text with a redaction marker:
  ```
  [REDACTED: prompt_injection]
  [REDACTED: PII-email]
  ```
- Preserve the original `raw_text` in the JSON for the dashboard's before/after view
- Store both `raw_text` and `sanitized_text` in the output object

```python
def sanitize(text: str, findings: list) -> str:
    """Replace each matched threat with a labeled redaction marker."""
    result = text
    for f in sorted(findings, key=lambda x: -len(x["matched"])):
        label = f"[REDACTED: {f['type']}]"
        result = result.replace(f["matched"], label)
    return result
```

### 3. Quarantine Store

A lightweight persistent store (JSON file or SQLite) for blocked documents.

```python
# quarantine entry shape
{
    "doc_id": "...",
    "filename": "...",
    "risk_score": 92,
    "reasons": [...],
    "quarantined_at": "ISO-8601",
    "status": "PENDING",      # PENDING | APPROVED | REJECTED
    "reviewed_by": null,
    "reviewed_at": null
}
```

Expose two functions the Dashboard can call:
- `approve(doc_id)` → moves document to vector DB
- `reject(doc_id)` → permanently discards document

### 4. Vector Database & Embeddings

Use **ChromaDB** (easiest for a hackathon demo):

```python
import chromadb
from sentence_transformers import SentenceTransformer

client = chromadb.Client()
collection = client.create_collection("cybergen_docs")
embedder = SentenceTransformer("all-MiniLM-L6-v2")

def ingest_to_vector_db(doc: dict):
    """Embed sanitized (or allowed) chunks and store them."""
    text = doc.get("sanitized_text") or doc["raw_text"]
    chunks = doc["chunks"]
    embeddings = embedder.encode([c["text"] for c in chunks]).tolist()
    collection.add(
        documents=[c["text"] for c in chunks],
        embeddings=embeddings,
        ids=[f"{doc['doc_id']}_chunk_{c['index']}" for c in chunks],
        metadatas=[{"doc_id": doc["doc_id"], "filename": doc["filename"]} for c in chunks]
    )
```

### 5. RAG Chatbot (Demo Showstopper)

Build two side-by-side query endpoints:

- **`/chat/unprotected`** — queries a vector DB that accepted all documents (including injected ones)
- **`/chat/protected`** — queries the CyberGen-filtered vector DB

This is your most impressive demo moment: ask the same question to both, show the unprotected bot leaking or misbehaving, and the protected one refusing cleanly.

```python
def rag_query(question: str, collection, use_protection: bool) -> str:
    results = collection.query(query_texts=[question], n_results=3)
    context = "\n".join(results["documents"][0])
    system = "Answer based only on the provided context." if use_protection else "Answer helpfully."
    # Call LLM with context + question
    ...
```

### 6. Audit Logging

Append a record to `audit.log` (JSON Lines format) for **every document at every stage**:

```json
{"doc_id": "...", "filename": "...", "stage": "pipeline", "decision": "SANITIZE", "risk_score": 55, "ts": "2026-10-22T10:05:00Z"}
```

This feeds the Dashboard's activity timeline.

---

## 📤 Final Output Object

```json
{
  "doc_id": "uuid",
  "filename": "report.pdf",
  "raw_text": "...original...",
  "sanitized_text": "...redacted...",
  "findings": [...],
  "risk_score": 55,
  "decision": "SANITIZE",
  "reasons": ["..."],
  "in_vector_db": true,
  "in_quarantine": false,
  "audit_trail": [
    { "stage": "ingestion", "status": "complete", "ts": "..." },
    { "stage": "detection", "status": "complete", "findings_count": 2, "ts": "..." },
    { "stage": "pipeline",  "decision": "SANITIZE", "ts": "..." }
  ]
}
```

---

## 🗂️ Suggested File Layout

```
pipeline/
├── README.md
├── requirements.txt
├── __init__.py
├── main.py             ← orchestrator: ties ingestion → detection → pipeline together
├── decision.py         ← allow / sanitize / quarantine logic
├── sanitizer.py        ← text redaction
├── quarantine.py       ← quarantine store + approve/reject
├── vector_db.py        ← ChromaDB wrapper + embedding logic
├── rag_chat.py         ← protected vs. unprotected RAG chatbot
├── audit.py            ← audit log writer
├── data/
│   ├── quarantine.json ← persisted quarantine queue
│   └── audit.jsonl     ← append-only audit log
└── tests/
    └── test_pipeline.py
```

---

## 📦 Dependencies (`requirements.txt`)

```
chromadb>=0.4.0
sentence-transformers>=2.2.0
openai>=1.0.0
faiss-cpu>=1.7.4        # optional FAISS alternative
```

---

## 🧪 Test Your Stage With

```bash
# Run the full pipeline on all test documents
python pipeline/main.py --test-all

# Query the RAG chatbot (protected mode)
python pipeline/rag_chat.py --query "Summarize the report" --mode protected

# Query the RAG chatbot (unprotected mode — watch it fail)
python pipeline/rag_chat.py --query "Summarize the report" --mode unprotected
```

---

## 🔗 Integration Points

| Upstream | Receive scored JSON from `detection/scanner.py` |
|----------|-------------------------------------------------|
| **Downstream (DB)** | Write allowed/sanitized documents to ChromaDB |
| **Downstream (UI)** | Expose `audit.jsonl` and quarantine queue to `dashboard/` |
| **Downstream (Chat)** | `rag_chat.py` is called by the Dashboard's chat UI |
| **Shared contract** | See root `README.md` for the full schema |

---

## ✅ Definition of Done

- [ ] Score ≤ 30 → document appears in vector DB unchanged
- [ ] Score 31–69 → sanitized text in vector DB, original preserved in JSON
- [ ] Score ≥ 70 → document in quarantine queue, NOT in vector DB
- [ ] `approve()` on a quarantined doc → it enters the vector DB
- [ ] `reject()` on a quarantined doc → permanently removed
- [ ] Protected RAG chatbot refuses to follow injected instructions
- [ ] Unprotected RAG chatbot follows injected instructions (demo contrast)
- [ ] Every document appends a record to `audit.jsonl`
- [ ] `test_pipeline.py` passes with mock detection output

---

*You're the judge, the redactor, and the memory. What you let through shapes everything the AI knows. ⚖️*
