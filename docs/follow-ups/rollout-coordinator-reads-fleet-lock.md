# Follow-up for herdr-engineering: let the rollout coordinator read `fleet.lock`

Status: open, to be filed as an issue in the herdr-engineering repository. Slice 3 of herdr-fleet
changes nothing there (plan 0002, OPEN QUESTION 6, risk R5).

## The problem: two writers of the Pi revision

Both of these stage a Pi release and register it in `~/.pi/agent/settings.json` `packages`, using
the same stage helper and the same on-disk layout:

1. `herdr-fleet apply` (role `pi-runtime`) stages `repos.pi-engineering` from `fleet.lock`.
2. `herdr-eng fleet rollout plan --pi-revision X` / `apply` stages the revision the operator gave
   the coordinator, through its own playbook (`pi-runtime-stage.yml`).

If `X` differs from `repos.pi-engineering`, each run re-points the registration at its own
revision. The next `herdr-fleet check` reports `pi-runtime revision: expected <lock sha>, found
<X>` and the next rollout reports the opposite. The lock and the rollout flip-flop, and a verify
after either one fails.

## Rule until this is fixed (slice 3)

The revision passed to a rollout must equal `repos.pi-engineering` in the reviewed `fleet.lock`.
Change the Pi revision by editing the lock (a reviewed change), applying it, and then rolling that
same revision out. Never roll out a revision that is not in the lock.

## What herdr-fleet already guarantees

- `pi-runtime` stages and registers only. It never reloads, restarts or signals Pi, never sends
  Pi control requests, never touches sessions or missions, and never deletes an older release.
- Its fingerprint says what is staged and registered, not what a running session has loaded. A host
  with the pinned release staged while sessions still run the previous one is not drift.

## Requested change in herdr-engineering

- The rollout coordinator reads the Pi revision (and the herdr-engineering revision) from
  `fleet.lock` instead of taking `--pi-revision` as free input, and stages it by calling
  `herdr-fleet apply --roles pi-runtime` (or an equivalent library call), so there is one writer.
- Activation (reload, mission recovery, session-aware ordering) stays in the coordinator; it is the
  other half of the split and is planned as slice 4 of herdr-fleet.
- A `--pi-revision` that disagrees with the lock becomes an error that names both revisions.
