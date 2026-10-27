# README screenshots

Images referenced by the root `README.md`:

| File | What it shows |
| --- | --- |
| `hero-chat.png` | Arabic question answered with inline `[n]` markers and a side citation card (source page + `ien.edu.sa`). The hero shot. |
| `refusal.png` | An **English** question answered in English, refused because the topic isn't in the Grade 8 book, with in-curriculum suggestions and an Arabic citation card. Doubles as the bilingual demo. |
| `grade-subject.png` | Welcome screen showing the selected subject (Digital Skills) and grade (8) in the header. |
| `bilingual-search.png` | The "Searching: …" card showing an English question being searched against the Arabic textbook (the bilingual step of the walkthrough). |
| `howItWorks.png` | Pipeline flowchart (question → agent → textbook search loop → verify → answer/refusal), color-keyed by LLM / retrieval / I/O. Embedded in the "How it works" section. |

The four screenshots are embedded in the root `README.md` "Screenshots" section as a numbered walkthrough; `howItWorks.png` heads the "How it works" section.

To refresh: run the app on `backend: openrouter` (`just up`, or the Chainlit command in `SETUP.md`), capture in a clean ~1200px browser window, and overwrite the PNGs above.
