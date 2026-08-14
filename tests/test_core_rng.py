"""Phase 2: the seeded generator. If this is wrong, replay is a lie."""

from __future__ import annotations

import pytest

from here_to_slay.core.rng import DeterministicRng, seed_from


def test_same_seed_gives_the_same_sequence() -> None:
    a, b = DeterministicRng(seed=7), DeterministicRng(seed=7)
    assert [a.randint(1, 6) for _ in range(20)] == [b.randint(1, 6) for _ in range(20)]
    assert a.state == b.state


def test_different_seeds_diverge() -> None:
    a, b = DeterministicRng(seed=1), DeterministicRng(seed=2)
    assert [a.randint(1, 100) for _ in range(20)] != [b.randint(1, 100) for _ in range(20)]


def test_string_seeds_are_stable_across_processes() -> None:
    """`hash()` is salted per process; --seed dragons must survive that."""
    assert seed_from("dragons") == seed_from("dragons")
    assert seed_from("dragons") != seed_from("dragon")
    assert DeterministicRng(seed="dragons").seed == seed_from("dragons")


def test_dice_stay_in_range() -> None:
    rng = DeterministicRng(seed=99)
    for _ in range(200):
        dice = rng.roll(2, 6)
        assert len(dice) == 2
        assert all(1 <= die <= 6 for die in dice)


def test_a_roll_is_one_log_entry() -> None:
    """The log should read like the table: 2d6 is one line, not two."""
    rng = DeterministicRng(seed=3)
    rng.roll(2, 6)
    rng.roll(3, 8)
    assert rng.advances == 2
    assert rng.calls[1].kind == "roll"
    assert rng.calls[1].detail == "3d8"


def test_every_advance_is_logged() -> None:
    rng = DeterministicRng(seed=5)
    rng.randint(1, 4)
    rng.below(10)
    rng.choice("abcd")
    rng.shuffle([1, 2, 3])
    assert [call.kind for call in rng.calls] == ["randint", "below", "choice", "shuffle"]
    assert [call.index for call in rng.calls] == [0, 1, 2, 3]


def test_randint_reaches_both_endpoints() -> None:
    rng = DeterministicRng(seed=11)
    results = {rng.randint(1, 6) for _ in range(200)}
    assert results == {1, 2, 3, 4, 5, 6}


def test_randint_of_a_single_value_is_that_value() -> None:
    rng = DeterministicRng(seed=1)
    assert rng.randint(4, 4) == 4


def test_illegal_ranges_raise() -> None:
    rng = DeterministicRng(seed=1)
    with pytest.raises(ValueError):
        rng.randint(5, 4)
    with pytest.raises(ValueError):
        rng.below(0)
    with pytest.raises(ValueError):
        rng.choice([])
    with pytest.raises(ValueError):
        rng.roll(1, 0)


def test_shuffle_is_a_permutation_and_deterministic() -> None:
    deck = list(range(40))
    a, b = DeterministicRng(seed=42), DeterministicRng(seed=42)
    first, second = list(deck), list(deck)
    a.shuffle(first)
    b.shuffle(second)
    assert first == second
    assert sorted(first) == deck
    assert first != deck  # 40! against one arrangement; a tie here is a bug


def test_clone_is_independent_but_identical() -> None:
    rng = DeterministicRng(seed=8)
    rng.randint(1, 6)
    twin = rng.clone()

    assert twin.state == rng.state
    assert twin.advances == rng.advances
    assert [twin.randint(1, 6) for _ in range(5)] == [rng.randint(1, 6) for _ in range(5)]

    twin.randint(1, 6)
    assert twin.advances == rng.advances + 1  # rollouts must not disturb the game


def test_distribution_is_not_modulo_biased() -> None:
    """Rejection sampling, not `u64 % n` — invisible in play, but it would skew
    a 1000-game fuzz run."""
    rng = DeterministicRng(seed=1234)
    counts = [0, 0, 0]
    for _ in range(6000):
        counts[rng.below(3)] += 1
    assert all(1800 < count < 2200 for count in counts), counts
