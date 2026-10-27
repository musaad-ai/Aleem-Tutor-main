# Capstone Project Proposal

**Project Title:** *Manhaji* — A Curriculum-Grounded RAG Tutor for Saudi School Students

---

## 1. Problem Statement

Saudi school students who use general-purpose LLMs (ChatGPT, Gemini, Copilot) for studying often get answers that are factually plausible but misaligned with the official Saudi curriculum: different terminology than their textbook, examples from foreign curricula, or material from other grade levels. This forces students to second-guess every answer and risks them memorizing content that will be marked wrong on official exams.

**Affected users:**

- **Students (grades 1–12)** — primary users, need study help that matches what they will be tested on.
- **Teachers** — need a fast way to revise topics and check explanations against the official curriculum.

**Scope (intentionally narrow):** answer student questions accurately, grounded in the official textbooks for the student's specific grade.

**Pilot grades** (one per stage, all core subjects, Arabic + English):

| Stage       | Grade    |
| ----------- | -------- |
| Elementary  | Grade 4  |
| Middle      | Grade 7  |
| High School | Grade 10 |

---

## 2. Review of the Status Quo

**What students use today and why it falls short:**

| Approach                                  | Limitation                                                  |
| ----------------------------------------- | ----------------------------------------------------------- |
| General LLMs (ChatGPT, Gemini)            | Not curriculum-aligned; hallucinate or use foreign content  |
| Web search / YouTube                      | Quality varies; not curriculum-grounded                     |
| Saudi edtech (Madrasati, Noon Academy)    | Mostly recorded lessons; not interactive Q&A from textbook  |
| Asking teachers / parents                 | Limited bandwidth; not 24/7                                 |

**The gap:** no widely-used tool gives students interactive, on-demand Q&A *provably grounded in their own grade-level Saudi textbook*.

**Standard technical approach — RAG (Retrieval-Augmented Generation):** the LLM is forced to base answers on documents retrieved from a trusted corpus, reducing hallucination and making answers traceable. Recent literature directly relevant to us:

- Educational RAG chatbots (e.g., GATE exam Q&A) benchmark embeddings + LLMs on faithfulness and relevance.
- Arabic RAG studies show **sentence-aware chunking** and **reranking** significantly improve recall and faithfulness on Arabic corpora; multilingual-E5, BGE-M3, and Arabic-Triplet-Matryoshka are among the strongest embedding choices.

**Metrics & benchmarks we will use** (standard in RAGAS / ARES):

- *Retrieval:* Recall@k (k = 1, 3, 5), Context Precision.
- *Generation:* Faithfulness, Answer Relevancy, Answer Correctness.
- *Framework:* RAGAS (open-source, integrates with LangChain), plus a small human-evaluation set scored by a teacher.
- *Benchmark dataset:* no public Saudi-curriculum RAG benchmark exists, so we will build our own ~150–300 question–answer–source-passage set from end-of-chapter exercises. This is itself a contribution.

**What makes our approach better:**

- **Grade-isolated retrieval** — each grade has its own vector index, so a Grade-4 student is never answered with Grade-10 material.
- **Curriculum-grounded by construction** — corpus is *only* official MoE textbooks.
- **Bilingual (Arabic + English)** from day one, using embeddings proven on Arabic.

---

## 3. Our Approach

**Input:** A natural language request (Arabic or English) from a student, teacher, or parent — typically a question, but also explanation, summary, revision, or quiz-generation requests — together with the user's grade level.

**Output:** A curriculum-grounded response drawn from the official Saudi textbook content for the user's grade — primarily direct answers, with extended modes for explanations, summaries, revision notes, practice questions, and teacher-support material. If the content isn't in the books, the system says so instead of inventing one.

**Pipeline:**

```
Request + grade
  → Intent classifier (Q&A / explain / summarize / revise / quiz / teacher-support)
  → Route to grade-specific RAG
  → Embed query → Vector search (top-k chunks) → Rerank
  → LLM generates response in the requested mode, from retrieved chunks ONLY
  → Response
```

An intent classifier (a small LLM call) maps the user's request to one of the supported response modes; each mode uses a different system prompt template (e.g., a quiz prompt asks the LLM to produce N questions with answers, while a summary prompt asks for a concise overview). All modes share the same retrieval layer and the same hard constraint: respond only from retrieved curriculum chunks, in the user's language, and refuse politely when retrieval fails.

**Tech stack:**

| Layer          | Choice                                                                                  |
| -------------- | --------------------------------------------------------------------------------------- |
| Orchestration  | LangChain                                                                               |
| Embeddings     | Multilingual-E5-large / BGE-M3 / Arabic-Triplet-Matryoshka (final pick by benchmarking) |
| Vector DB      | FAISS (dev), Chroma or Qdrant (deploy)                                                  |
| Generator LLM  | Instruction-tuned LLM with strong Arabic (Claude / GPT-4-class / Aya / ALLaM)           |
| OCR            | Tesseract (Arabic) baseline; AWS Textract / Google Document AI fallback                 |
| Evaluation     | RAGAS                                                                                   |

---

## 4. Data

**Source:** Official Saudi school textbooks for the selected grades, downloaded from the **IEN national educational portal** (https://ien.edu.sa/) and the Ministry of Education website (https://moe.gov.sa/).

**Author:** Ministry of Education, Kingdom of Saudi Arabia.

**License:** Issued by the MoE for educational use. We will use them strictly for non-commercial academic research, will not redistribute the raw books, and will request explicit permission for any deployment beyond the capstone.

**Quality:** original (downloaded from official portal), current edition in active classroom use, and authoritative — these are the exact materials students are tested on.

**Features, modalities, language, size:**

| Attribute                | Value                                                              |
| ------------------------ | ------------------------------------------------------------------ |
| Modalities               | Mix of digital-text PDFs and scanned page images (OCR needed)      |
| Languages                | Arabic (majority) + English                                        |
| Books                    | ~6 subjects × 3 grades = **~18 textbooks**                         |
| Pages                    | ~3,000–4,500 total pages (~50–200 MB)                              |
| Chunks (post-processing) | ~5,000–20,000 sentence-aware chunks of ~300–500 tokens             |

**Preprocessing pipeline:** ingest PDFs and tag by grade/subject → detect text vs. scanned pages → OCR scanned pages → clean noise (headers, page numbers) → sentence-aware chunking → embed → index per grade with metadata `{grade, subject, book, chapter, page}` for citation.

**Known risks:** Arabic OCR quality on scanned pages, equations and diagrams in math/science books (text-only RAG limitation), and labeling effort for the gold-standard eval set.

---

*End of proposal.*
