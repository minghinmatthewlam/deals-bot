# Swarm Workflow (Agent Mail + Beads + bv)

This folder is **read-once onboarding** for multi-agent work in a repo.

## Required reading order (new repo / new session)

1) Repo rules: `AGENTS.md` (and `CLAUDE.md` if present)
2) Swarm workflow: `docs/swarm/README.md`
3) Agent Mail ops: `docs/swarm/agentmail-ops.md`
4) If using NTM (recommended here): `docs/tools/ntm/README.md`

## Concepts (what is source-of-truth?)

- **Beads (`bd`)**: task status, priority, dependencies.
- **Beads Viewer (`bv`)**: deterministic graph analysis + “robot flags” for impact/ordering/diff.
- **MCP Agent Mail**: coordination layer (identities, inbox/outbox, threads, acknowledgements, file reservations).

Rule of thumb:
- Use `bv` to decide what matters next.
- Use `bd` to encode that decision (status/priority/deps).
- Use Agent Mail to coordinate and avoid conflicts (reservations + threads).

## NTM (recommended launcher)

NTM is how we start multiple agents fast (tmux panes + broadcast prompts). It is **not** a replacement for Agent Mail:

- NTM: spawns agent processes + lets you broadcast prompts (`ntm send`).
- Agent Mail: durable coordination + file reservations.

Typical flow (existing repo):

1) From repo root, start coordination:
- Ensure Agent Mail server is running (`docs/swarm/agentmail-ops.md`).
- Ensure Beads is initialized/seeded from `plan.md` (see `docs/swarm/beads-seeding.md`).

2) Spawn agents (default mix: 3 Claude + 3 Codex):
- `ntm spawn <repo-folder-name> --cc=3 --cod=3`

3) Kickoff all agents (tell them to register + pick/claim a bead):
- `ntm send <repo-folder-name> --skip-first --file docs/swarm/ntm-kickoff.md`

## One-time per session: register identity

Always register under the repo’s absolute path:

- `project_key` / `human_key`: `<REPO_ABS_PATH>` (use `pwd` to get it)

Recommended call (creates a random identity if you omit `agent_name`):

- `macro_start_session(human_key="<REPO_ABS_PATH>", program="<program>", model="<model>", task_description="<what you’re doing>")`

Important:
- If you call `macro_start_session` without `agent_name` multiple times, you will create multiple identities.
- To resume/take over an existing identity, pass `agent_name="<ExistingName>"`.

## Operating loop (every time you finish a task)

1) Pick next work
- `bv --robot-priority`
- Confirm it’s actionable with `bd ready` / `bd show <id>`

2) Mark + reserve
- `bd update <id> --status in_progress`
- Reserve minimal edit surface:
  - `file_reservation_paths(project_key="<REPO_ABS_PATH>", agent_name="<YourName>", paths=[...], ttl_seconds=3600, exclusive=true, reason="<id>")`

3) Work + communicate
- Keep updates in Agent Mail thread `<id>` (thread_id = bead id).

4) Finish
- `bd close <id> --reason "Completed"`
- `release_file_reservations(project_key="<REPO_ABS_PATH>", agent_name="<YourName>")`

## Human Overseer (broadcast override)

If you need to redirect multiple agents immediately, use the web UI composer:

- Open `http://127.0.0.1:8765/mail` and click your project
- Click **Human Overseer** (or go to `/mail/<project-slug>/overseer/compose`)

Overseer messages are high-importance and include a preamble telling agents to prioritize the human’s instructions.

## Beads seeding from `plan.md`

If a repo starts with a `plan.md` but no backlog, use:
- `docs/swarm/beads-seeding.md`
