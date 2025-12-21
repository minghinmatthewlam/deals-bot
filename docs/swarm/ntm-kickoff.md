# Swarm Kickoff (NTM Broadcast)

You are one of multiple AI agents working in parallel in this repo.

Goal: register in Agent Mail, pick a high-impact Beads task, reserve files, and start work without colliding with other agents.

## 0) Confirm repo root

- Run `pwd`.
- If you are not at the repo root, `cd` to the repo root.

The Agent Mail `project_key` / `human_key` must be the repo’s absolute path.

## 1) Read required docs

- `AGENTS.md` (and `CLAUDE.md` if present)
- `docs/swarm/README.md`
- `docs/swarm/agentmail-ops.md`
- `docs/tools/ntm/README.md`

## 2) Register your identity in Agent Mail

Use the Agent Mail MCP tool:

- `macro_start_session(human_key="<REPO_ABS_PATH>", program="<your-program>", model="<your-model>", task_description="swarm-kickoff")`

Notes:
- `<REPO_ABS_PATH>` is the output of `pwd` at the repo root.
- If you don’t pass `agent_name`, a new identity is created. If you’re resuming, pass your existing name.
- If you cannot access Agent Mail tools, say so clearly in your reply and stop (don’t start editing files).

## 3) Check coordination state

- Poll inbox (`fetch_inbox(...)`) and acknowledge any `ack_required` messages.
- If someone already claimed a task you were going to take, pick a different one.

## 4) Pick and claim a Bead

1) Run:
   - `bv --robot-priority`
   - `bd ready`
2) Pick one “ready” Bead that is:
   - highest priority you can complete now
   - unlikely to overlap heavily with others
3) Claim it by sending a short message in Agent Mail (thread id = bead id), e.g.:
   - “CLAIM bd-123: working on <summary>”

If there is no clear next task, reply with:
- top 5 candidates from `bv --robot-priority`
- what you think the dependencies/risks are

## 5) Reserve files before editing

Reserve the narrowest set of files/globs you need:

- `file_reservation_paths(project_key="<REPO_ABS_PATH>", agent_name="<YourName>", paths=[...], ttl_seconds=3600, exclusive=true, reason="<bead-id>")`

Then mark the bead `in_progress`:
- `bd update <id> --status in_progress`

## 6) Execute + communicate + finish

- Work in small, verifiable increments.
- Keep updates in the Agent Mail thread `<bead-id>`.
- When done:
  - `bd close <id> --reason "Completed"`
  - `release_file_reservations(project_key="<REPO_ABS_PATH>", agent_name="<YourName>")`

## Reply back now

Reply with:
- Your Agent Mail name (after registration)
- Which bead you claimed
- Which files you plan to touch (globs ok)
- Any blocker/questions

