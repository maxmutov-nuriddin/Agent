---
name: verification-before-completion
description: Before claiming any work is done, fixed, correct or passing, verify it with fresh evidence (run the check, re-read the file, re-open the source) and report exactly what was and was not verified
agents: developer, qa, fact_checker
source: https://github.com/obra/superpowers/blob/main/skills/verification-before-completion/SKILL.md
license: MIT (c) 2025 Jesse Vincent
---
Core rule: evidence before claims. No completion claim without fresh verification evidence from this same step. If you did not run the check just now, you cannot say it passes.
Gate, before saying "done", "fixed", "correct" or expressing satisfaction:
1. IDENTIFY what proves the claim (a command, a file, a source, a calculation).
2. RUN or OPEN it fully and freshly: run_command for tests/build, read_file or list_files for deliverables, fetch_url for sources.
3. READ the whole result: exit status, failure count, the actual text.
4. COMPARE with the claim. If it does not confirm it, report the real status with the evidence. If it does, state the claim together with the evidence.
5. Only then make the claim. Skipping a step is not verifying.
What counts as proof (and what does not):
- Tests pass: test output with 0 failures. Not: an earlier run, or "should pass".
- Build works: build command ended successfully. Not: "linter passed".
- Bug fixed: the original symptom now passes. Not: "I changed the code".
- Requirement met: a line-by-line checklist against the original request. Not: "tests pass, so it is complete".
- A teammate's work is done: you opened the files yourself. Not: the teammate reported success.
- A fact or number is right: you re-opened the source and saw it there, with the date. Not: you remember it.
- A link works: you fetched it and got a normal page.
Red flags, stop and verify: the words "should", "probably", "seems to"; "Zo'r!", "Tayyor!" before checking; trusting another agent's report; checking only part of the work; "just this once"; feeling tired of the task. Different wording does not escape the rule.
In the final answer include a status list per requirement: done and verified (how), done but not verified (why), not done. Never write that something works unless you checked it in this step.
