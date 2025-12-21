# Seeding Beads from `plan.md` (template)

This doc is for bootstrapping a repo when no custom seeding script exists.

## Preconditions

- `plan.md` exists at the repo root. If not, stop and ask the human to provide it (or confirm the correct filename).
- Beads is installed (`bd`) and initialized (`.beads/` exists). If not, run `bd init`.

## Goal

Turn `plan.md` into a dependency-aware backlog:

- 1 top-level epic for plan execution.
- 1 epic per phase.
- Phase children: “Relevant docs”, “Steps”, “Checkpoints”.
- One child per numbered step and per checkpoint item.
- Dependencies that create an executable order (no cycles).

## Safety: redact secrets

If `plan.md` contains passwords/tokens:
- Do not paste them into Beads.
- Replace with `<see plan.md>` in bead descriptions.

## Copy/paste prompt for a new coding agent

> Read `AGENTS.md` first. Then locate and read `plan.md`. If `plan.md` does not exist, stop and ask me for it.
>
> If `.beads/` is missing, run `bd init`.
>
> Create a detailed Beads backlog derived from `plan.md`:
> - One top-level epic: “Plan execution (imported from plan.md)”
> - One epic per `## Phase …` section
> - Under each phase: a “Relevant docs” task, a “Steps” task, and a “Checkpoints” task (if present)
> - Under “Steps”: one child task per numbered step
> - Under “Checkpoints”: one child task per checkbox item
>
> Add dependencies:
> - Steps are sequential within a phase (Step N+1 blocks Step N)
> - Checkpoints are blocked on the last step in that phase
> - Phase N is blocked on Phase N-1 checkpoints (unless plan explicitly allows parallelism)
>
> Redact any secrets in issue descriptions (use `<see plan.md>`).
>
> When done, run `bd dep cycles`, `bd ready`, and `bv --robot-plan`, then report:
> - Total issues created
> - The first 10 ready issues
> - Any plan ambiguities or mismatches you recommend fixing before coding

