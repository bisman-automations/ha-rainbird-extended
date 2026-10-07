"""Plan a sequence of zone runs, optionally split into cycle and soak."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """Run a zone for some seconds, or (zone None) wait while zones soak."""

    zone: int | None
    seconds: int


def plan_steps(
    runtimes: list[tuple[int, int]], cycle_seconds: int = 0, soak_seconds: int = 0
) -> list[Step]:
    """Turn (zone, seconds) runs into steps.

    Without a cycle length each zone runs once, in order. With one, every
    zone's runtime is split into cycles of at most that long and the zones
    take turns, one cycle each per round, so one zone soaks while the others
    water. A wait is added between rounds only where needed for every zone to
    soak at least soak_seconds between its own cycles.
    """
    runtimes = [(zone, seconds) for zone, seconds in runtimes if seconds > 0]
    if cycle_seconds <= 0:
        return [Step(zone, seconds) for zone, seconds in runtimes]

    remaining = dict(runtimes)
    order = [zone for zone, _ in runtimes]
    rounds: list[list[tuple[int, int]]] = []
    while any(remaining.values()):
        this_round = []
        for zone in order:
            if remaining[zone] > 0:
                chunk = min(cycle_seconds, remaining[zone])
                remaining[zone] -= chunk
                this_round.append((zone, chunk))
        rounds.append(this_round)

    steps: list[Step] = []
    for index, this_round in enumerate(rounds):
        steps.extend(Step(zone, seconds) for zone, seconds in this_round)
        if index + 1 < len(rounds):
            wait = _soak_wait(this_round, rounds[index + 1], soak_seconds)
            if wait > 0:
                steps.append(Step(None, wait))
    return steps


def _soak_wait(
    this_round: list[tuple[int, int]],
    next_round: list[tuple[int, int]],
    soak_seconds: int,
) -> int:
    """Wait needed so each zone in the next round has soaked long enough."""
    wait = 0
    for position, (zone, _) in enumerate(this_round):
        next_position = next(
            (i for i, (z, _) in enumerate(next_round) if z == zone), None
        )
        if next_position is None:
            continue
        # Time between this zone's cycle ending and its next one starting.
        gap = sum(s for _, s in this_round[position + 1 :]) + sum(
            s for _, s in next_round[:next_position]
        )
        wait = max(wait, soak_seconds - gap)
    return wait


def plan_blowout(
    zones: list[int], cycles: int, on_seconds: int, rest_seconds: int
) -> list[Step]:
    """Each zone in turn: cycles bursts of on_seconds, resting between bursts.

    The rest also separates one zone's last burst from the next zone's first,
    so the compressor recovers before every burst; only the end has none.
    """
    steps: list[Step] = []
    for zone in zones:
        for _ in range(cycles):
            if steps and rest_seconds > 0:
                steps.append(Step(None, rest_seconds))
            steps.append(Step(zone, on_seconds))
    return steps
