# 📥 Stage 1 — Ingestion & Extraction

**Owner:** Teammate A
**Folder:** `ingestion/`
**Feeds into:** `detection/` (via the shared JSON contract)

---

## 🎯 Your Job in One Sentence

Take any document the user uploads, pull out every piece of text (including the hidden ones), and hand a clean, chunked, structured object to the Detection engine.

---

## 📋 Responsibilities

### 1. File Upload & Parsing
Accept and parse these formats:

| Format | Library |
|--------|---------|
| PDF | `PyMuPDF` (`fitz`) |
| DOCX | `python-docx` |
| HTML | `BeautifulSoup4` |
| TXT | Built-in `open()` |

### 2. Hidden Content Detection
This is the most important part of your stage. Attackers hide injection text in:

| Technique | How to Detect |
|-----------|--------------|
| White-on-white text | Extract text regardless of color; compare visible vs. full text |
| HTML comments | Parse `<!-- ... -->` nodes separately |
| Zero-width characters | Regex scan for `\u200b`, `\u200c`, `\u200d`, `\ufeff` |
| Document metadata | Read PDF metadata fields (`/Author`, `/Subject`, `/Keywords`, custom XMP) |
| Tiny / invisible font sizes | Extract font size per text span from PDF render tree |

### 3. Text Cleaning & Chunking
- Strip control characters and normalize whitespace
- Split into chunks (recommended: ~512 tokens with 50-token overlap)
- Preserve chunk index so downstream stages can cite location

---

## 📤 Output — What You Hand Off

Produce this object and pass it to `detection/`:

```json
{
  "doc_id": "uuid-v4-here",
  "filename": "uploaded_file.pdf",
  "raw_text": "Full visible text...",
  "hidden_content": {
    "html_comments": ["<!-- ignore previous instructions -->"],
    "zero_width": ["suspicious\u200btext"],
    "metadata": { "Author": "attacker", "Keywords": "inject here" },
    "invisible_spans": ["white text content"]
  },
  "chunks": [
    { "index": 0, "text": "chunk content here..." },
    { "index": 1, "text": "next chunk..." }
  ],
  "findings": [],
  "risk_score": 0,
  "decision": null,
  "reasons": [],
  "sanitized_text": null,
  "timestamp": "2026-10-22T10:00:00Z",
  "audit_trail": [
    { "stage": "ingestion", "status": "complete", "ts": "2026-10-22T10:00:01Z" }
  ]
}
```

> The `findings`, `risk_score`, `decision`, and `reasons` fields are **filled by Detection (Stage 2)**. Initialize them as empty/null.

---

## 🗂️ Suggested File Layout

```
ingestion/
├── README.md
├── requirements.txt
├── __init__.py
├── loader.py          ← format dispatcher (PDF / DOCX / HTML / TXT)
├── extractors/
│   ├── pdf.py         ← PyMuPDF extraction + font-size hidden text
│   ├── docx.py        ← python-docx + metadata
│   ├── html.py        ← BeautifulSoup + comment extraction
│   └── txt.py         ← plain text + zero-width scan
├── hidden.py          ← zero-width char & metadata scanner
├── chunker.py         ← text splitting logic
└── tests/
    └── test_ingestion.py
```

---

## 📦 Dependencies (`requirements.txt`)

```
PyMuPDF>=1.23.0
python-docx>=1.1.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

---

## 🧪 Test Your Stage With

```bash
python -m ingestion.loader path/to/subtle_injection.html
```

Expected: The hidden HTML comment `<!-- ignore previous instructions -->` appears in `hidden_content.html_comments`.

---

## 🔗 Integration Points

| Upstream | None — you are Stage 1 |
|----------|------------------------|
| **Downstream** | Pass your JSON object to `detection/scanner.py` |
| **Shared contract** | See root `README.md` for the full schema |

---

## ✅ Definition of Done

- [ ] All 4 file formats parse without crashing
- [ ] All 5 hidden-content techniques are detected in test documents
- [ ] Output JSON validates against the shared contract schema
- [ ] `test_ingestion.py` passes for all test documents from `dashboard/test_docs/`
- [ ] Chunking produces ≥ 2 chunks on a 1-page document

---

*You're the gatekeeper. If hidden content slips past you, nothing downstream can catch it. 🔍*
