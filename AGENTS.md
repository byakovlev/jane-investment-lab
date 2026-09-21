# Jane — Collaboration workflow

## Session start

- Read this file, ROADMAP.md, and STATUS.md.
- Check the Git branch and working tree before making changes. Preserve all
  unrelated pre-existing changes.
- Use STATUS.md for the current next task. Work only on the task Boris has agreed
  with the planning conversation; do not automatically start the next task.

## Planning, implementation, and review

- Boris uses a separate ChatGPT conversation for planning and review.
- Local Codex inspects the repository, implements one agreed task at a time, and
  runs relevant tests.
- The sessions do not automatically share context. Boris transfers task
  instructions and results between them.
- Code edits within the agreed task do not require advance diff approval.
- After completing the task and validation, stop and report changes, test results,
  and remaining limitations. Do not start another task.
- Boris decides whether to keep, revise, or roll back that task.
- Rollbacks must preserve unrelated pre-existing changes.

## Git and data

- Do not commit unless explicitly instructed.
- Boris performs GitHub pushes himself, prompted by the planning chat.
- Purchased data stays outside GitHub.
- Follow these commit and push rules for all roadmap tasks.

## Resource limits

- This Mac has 16 GB RAM. Avoid unrestricted full-dataset pandas loads.
- Use bounded requests and SQL summaries for large-data validation.
