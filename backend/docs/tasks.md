# docIS Sprint Tasks

## Deliverables

0. Git repository containing the source code.

- [x] **T 0.1 — Initialize Git Repository:** Set up project structure, `.gitignore`, and initial commit.
- [x] **T 0.2 — Database Integration:** Configure PostgreSQL connection, async SQLAlchemy engine, session management, and base ORM models.
- [x] **T 0.3 — Database Migrations:** Integrate Alembic, configure migration management, and create the initial database migration.
- [x] **T 0.4 — CI Pipeline:** Set up automated linting, type checking, and test workflows.
- [x] **T 0.5 — Dockerize Backend:** Create Dockerfile and Docker Compose configuration for FastAPI and PostgreSQL.
- [x] **T 0.6 — Environment Configuration:** Set up environment-based configuration and secure database credentials management.


1.1 Document ingestion :

- [x] **T 1.1.1- Upload multiple PDF files**
- [x] **T 1.1.2- Upload JPG, PNG and TIFF images**
- [x] **T 1.1.3- Upload XLSX, XLS and CSV files**
- [x] **T 1.1.4 Reject unsupported and corrupted files**
- [x] **T 1.1.5 Store original documents safely**

1.2 Document processing

- [x] **T 1.2.1-  Extract digital PDF text**
    - password protected files rejected
- [x] **T 1.2.2-  OCR scanned PDF pages and images**
- [x] **T 1.2.3- Parse multiple Excel sheets**
- [x] **T 1.2.4-  Parse CSV files**
- [x] **T 1.2.5-  Track background processing progress**
- [x] **T 1.2.6-  Retry failed processing jobs**

2. AI Information Extraction  and validation


-  [x] **T 2..1-  Extract all required invoice fields**
-  [x] **T 2..2-  Extract invoice line items**
-  [x] **T 2..3-  Return schema-valid JSONs**
-  [x] **T 2..4-  Represent missing fields as nulls**
-  [x] **T 2..5-  Validate subtotal, tax and total**
-  [x] **T 2..6-  Detect duplicate invoices**
-  [x] **T 2..7-  Detect duplicate invoices**
-  [x] **T 2..7-  Flag uncertain results for review**

3. Application functionality

-  [] **T 3.1-  View uploaded documents**
-  [] **T 3.2-  Inspect extracted information**
-  [] **T 3.2-  Correct extracted fields manually**
-  [] **T 3.4-  Approve and reject documents**
-  [] **T 3.5-  Compare PDF against PDF**
-  [] **T 3.6-  Compare PDF against Excel**
-  [] **T 3.7-  Search documents by keywords**
-  [] **T 3.8-  Search documents by keywords**
-  [] **T 3.8-  Perform semantic search**

4. Testing and delivery

- [] **T4.1 - Automated backend tests**
- [] **T4.2 -Measure extraction accuracy**
- [] **T4.3 -Measure processing time and failure rate**
- [] **T4.4 - Document setup instructions**
- [] **T4.5 - Architecture and database diagrams**
- [] **T4.6 - Frontend integration tests**
- [] **T4.7 -Record application walkthrough**




5. Evaluation evaluation metrics

| Metric | What it measures |
| --- | --- |
| **Field-level accuracy** | Percentage of extracted fields matching ground truth |
| **Exact invoice accuracy** | Percentage of invoices where all required fields are correct |
| **Numeric accuracy** | Accuracy of subtotal, tax, total, quantity and unit price |
| **Date accuracy** | Correctly extracted and normalized dates |
| **Line-item accuracy** | Correctly extracted descriptions, quantities and amounts |
| **Validation precision** | Percentage of reported validation errors that are genuine |
| **Validation recall** | Percentage of injected or known errors detected |
| **Processing time** | Average time to process one document |
| **Failure rate** | Percentage of documents that fail processing |

### Useful Formulas

$$
\text{Field Accuracy} = \frac{\text{Correctly Extracted Fields}}{\text{Total Ground-Truth Fields}} \times 100
$$

$$
\text{Exact Invoice Accuracy} = \frac{\text{Invoices with All Required Fields Correct}}{\text{Total Invoices}} \times 100
$$

$$
\text{Validation Precision} = \frac{TP}{TP + FP}
$$

$$
\text{Validation Recall} = \frac{TP}{TP + FN}
$$
























