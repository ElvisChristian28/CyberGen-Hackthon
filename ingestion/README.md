# Ingestion – Stage 1: Document Text Extraction & Hidden-Content Detection

> **Part of the Prompt-Injection Defense Pipeline.**
> This package extracts every piece of text from uploaded documents (PDF, DOCX, HTML, TXT),
> separates visible text from hidden content, and hands off a structured JSON object to
> the Detection engine (Stage 2).

## Install

```bash
pip install -r ingestion/requirements.txt
# Dev:
pip install pytest
```

**Python 3.10+** required.

## CLI Usage

```bash
python -m ingestion.loader path/to/document.pdf
```

Prints pretty-printed JSON to stdout.

### Programmatic Usage

```python
from ingestion.loader import ingest

result = ingest("path/to/document.pdf")
# or pass raw bytes:
result = ingest(raw_bytes, filename="report.docx")

# Hand off to Stage 2:
from detection.scanner import scan
scan(result)
```

## Output Schema

```json
{
  "doc_id": "uuid-v4",
  "filename": "uploaded_file.pdf",
  "raw_text": "Full VISIBLE text...",
  "hidden_content": {
    "html_comments": ["<!-- comment text -->"],
    "zero_width": ["context <ZERO WIDTH SPACE> context"],
    "metadata": {"author": "...", "keywords": "..."},
    "invisible_spans": ["[page 3] white text: hidden payload"]
  },
  "chunks": [{"index": 0, "text": "..."}],
  "findings": [],
  "risk_score": 0,
  "decision": null,
  "reasons": [],
  "sanitized_text": null,
  "timestamp": "2026-10-22T10:00:00Z",
  "audit_trail": [
    {"stage": "parse", "status": "complete", "ts": "..."},
    {"stage": "hidden_scan", "status": "complete", "ts": "..."},
    {"stage": "chunk", "status": "complete", "ts": "..."},
    {"stage": "ingestion", "status": "complete", "ts": "..."}
  ]
}
```

### Key rules

| Field | Stage 1 value | Filled by |
|---|---|---|
| `raw_text` | Visible text only | Ingestion |
| `hidden_content` | All hidden items | Ingestion |
| `findings` | `[]` | Stage 2 |
| `risk_score` | `0` | Stage 2 |
| `decision` | `null` | Stage 2 |
| `reasons` | `[]` | Stage 2 |
| `sanitized_text` | `null` | Stage 2 |

## Hidden-Content Detection Techniques

### 1. White-on-White / Low-Contrast Text

- **PDF**: Iterates `page.get_text("dict")` spans. Flags spans where all RGB channels > `0xF0` (configurable via `near_white_threshold`).
- **DOCX**: Checks `run.font.color.rgb` for near-white values. Also detects `w:vanish` (hidden text property).
- **HTML**: Matches inline styles: `color:#fff`, `color:white`, `rgb(255,255,255)`, `display:none`, `visibility:hidden`, `opacity:0`, and the `hidden` HTML attribute.

### 2. HTML Comments

- Parses `Comment` nodes via BeautifulSoup.
- Stores each comment preserving the `<!-- ... -->` wrapper.
- Comment text never leaks into `raw_text`.

### 3. Zero-Width Characters

Scans **all** formats for: `U+200B` (zero-width space), `U+200C` (ZWNJ), `U+200D` (ZWJ), `U+FEFF` (BOM), `U+2060` (word joiner).

- Records ~40 chars of surrounding context.
- Strips zero-width chars from `raw_text` after recording.

### 4. Document Metadata

- **PDF**: `doc.metadata` (Author, Title, Subject, Keywords, Creator, Producer) + XMP via `doc.get_xml_metadata()`.
- **DOCX**: `core_properties` (author, title, subject, keywords, comments, last_modified_by, category) + custom XML parts.
- **HTML**: `<meta>` tags and `<title>`.
- Non-empty values stored in `hidden_content.metadata`.

### 5. Tiny / Invisible Font Sizes

- **PDF**: Spans with `size < 2.0pt` (configurable). Also flags spans positioned outside the page bounding box.
- **DOCX**: `run.font.size` below threshold.
- **HTML**: Inline `font-size` below threshold or `font-size:0`.

## Configuration

All thresholds are configurable via `IngestionConfig`:

```python
from ingestion.hidden import IngestionConfig

cfg = IngestionConfig(
    max_file_size=25 * 1024 * 1024,   # 25 MB
    near_white_threshold=0xF0,         # RGB channel threshold
    tiny_font_pt=2.0,                  # Minimum visible font size (pt)
    chunk_target_tokens=512,           # Target tokens per chunk
    chunk_overlap_tokens=50,           # Overlap between chunks
    context_chars=40,                  # Context around zero-width hits
)

result = ingest("file.pdf", config=cfg)
```

## Format Detection

Format is detected by **magic bytes first, extension second**:

| Magic / Content | Format |
|---|---|
| `%PDF-` prefix | PDF |
| `PK\x03\x04` + `word/` inside ZIP | DOCX |
| `<html`, `<!doctype`, `<body`, `<div`, etc. in first 4KB | HTML |
| Everything else | TXT |

A `.txt` file that is actually a PDF will be correctly handled as PDF.

## Text Encoding (TXT fallback)

Tries in order: UTF-8 → UTF-8-SIG → UTF-16 → Latin-1 (with `errors="replace"`).

## Chunking

- Target: ~512 tokens (configurable), 50-token overlap.
- Token estimation: `len(text.split()) * 1.3` (documented heuristic; plug in a real tokenizer via the `token_counter` parameter).
- Splitting priority: paragraph boundaries → sentence boundaries → word boundaries.
- `raw_text` is chunked; `hidden_content` is kept separate.

## Known Limitations

- **No OCR**: Image-only PDF pages are noted in the audit trail but not processed.
- **Background color heuristics**: PDF white-text detection assumes a white background. A future version could sample the page background.
- **CSS class-based hiding**: Only inline styles are checked for HTML; stylesheet rules (e.g. `.hidden { display: none }`) are not evaluated.
- **No JavaScript execution**: JS-generated content is not extracted (by design – hostile input).
- **Custom DOCX properties**: Best-effort extraction; depends on python-docx version.

## Running Tests

```bash
cd <project_root>
pytest ingestion/tests/test_ingestion.py -v
```

Tests generate fixtures programmatically (no external files needed). If `dashboard/test_docs/` exists, its files are also tested.

## Hand-off to Stage 2

```python
from detection.scanner import scan
result = ingest("document.pdf")
scan(result)  # Stage 2 fills findings, risk_score, decision, reasons, sanitized_text
```
