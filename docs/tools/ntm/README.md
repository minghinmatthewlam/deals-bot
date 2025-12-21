# NTM in This Repo (Multi‑Agent Launcher)

This repo assumes you use **NTM (Named Tmux Manager)** to run multiple agents in parallel inside a single tmux session, and **Agent Mail + Beads** for coordination and conflict avoidance.

## What NTM is for (and what it is not)

- **NTM**: starts agent processes in tmux panes and lets you broadcast prompts (`ntm send`), inspect output (`ntm copy`/`ntm save`), and manage panes (`ntm view`/`ntm zoom`).
- **Agent Mail**: durable coordination (threads, acknowledgements) + advisory file reservations (leases).
- **Beads (`bd`)** + **bv**: backlog + prioritization.

NTM is a launcher/orchestrator, not a coordination source-of-truth. Use Agent Mail + Beads for “who is doing what”.

## Assumption (already configured)

NTM is expected to already be configured on your machine (it persists in `~/.config/ntm/config.toml`), with `projects_base` pointing at the parent directory that contains your repos (e.g. `~/dev`).

## Naming convention (important for existing repos)

NTM maps a session name to a working directory:

- `<projects_base>/<session-name>`

So for an existing repo at `~/dev/acme`, you should:

- run `ntm spawn acme ...` (session name == repo folder name)

If you use a different session name, NTM will use a different working directory (and may create a new folder).

## Recommended flow (Beads + Agent Mail + NTM)

From the repo root:

1) Start Agent Mail server (see `docs/swarm/agentmail-ops.md`)
2) Ensure Beads is bootstrapped from `plan.md` (see `docs/swarm/beads-seeding.md`)
3) Spawn agents (default mix: 3 Claude + 3 Codex):
   - `ntm spawn <repo-folder-name> --cc=3 --cod=3`
4) Broadcast kickoff (asks each agent to register + pick/claim a bead):
   - `ntm send <repo-folder-name> --skip-first --file docs/swarm/ntm-kickoff.md`

## `ntm spawn` and Agent Mail registration (important nuance)

- `ntm spawn` will register a single Agent Mail identity for the **NTM session coordinator** (non-blocking; only if the server is reachable).
- It does **not** automatically register each spawned pane (Claude/Codex) as its own Agent Mail agent.

That’s why you broadcast `docs/swarm/ntm-kickoff.md`: each agent registers itself via `macro_start_session(...)`.

## “Human Overseer” messaging from NTM

Once agents are registered, you can broadcast high-priority guidance via Agent Mail from your shell:

- `ntm mail send <session> --all --thread <bead-id> "Checkpoint + status update"`

Note: `ntm mail ...` uses your **current working directory** as the Agent Mail `project_key`. Always `cd` to the repo root first.

## Useful commands (day-to-day)

- `ntm status <session>`: counts + state
- `ntm view <session>` / `ntm zoom <session> <pane>`: navigate panes
- `ntm send <session> --cc "..."`
- `ntm send <session> --cod "..."`
- `ntm send <session> --skip-first --file <path>`
- `ntm copy <session> --cc --last 200`
- `ntm save <session> -o ~/logs/<session>`
- `ntm interrupt <session>`: Ctrl+C all agent panes

## Optional: project-specific palette

If you want a per-repo `ntm palette` with your common prompts:

- `ntm config project init` (creates `.ntm/config.toml` + `.ntm/palette.md`)

Then add an entry like “Swarm kickoff” whose **prompt text** is the contents of `docs/swarm/ntm-kickoff.md` (copy/paste it), so you can resend it from the palette without hunting for files.
