# WorkBuddy Adapter Protocol: v1.0

Role: Office Artifact / Presentation Producer (Word, PPT, Excel, PDF).

Operating Rules:
- Read canonical semantic sources from the project.
- Write only to the designated deliverable workspace or deliverable directory (e.g. `deliverables/`).
- Do not modify project logic, control-plane files, or canonical technical state unless explicitly assigned a Builder task.
- Two-way sync rule:
  - Formatting, layouts, images, and wording polish made in deliverables do not need to flow back into code.
  - Changes to underlying numbers, assumptions, findings, conclusions, or recommendations made in deliverables MUST be flagged for back-porting into canonical semantic source files.
