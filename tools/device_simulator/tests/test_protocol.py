from itertools import islice

from portero_simulator.protocol import backoff_delays, build_hmac


def test_challenge_response_matches_backend_vector() -> None:
    """Changing the canonical bytes must break the backend-compatible digest."""

    assert build_hmac("secret", "PI-000001", "boot-123", "nonce-456") == (
        "ad78e27952573ad8aa6ea6d2593f5db5ee69fe1102aaaf6aca4016dc43210975"
    )


def test_backoff_sequence_is_capped() -> None:
    """Reconnect failures must not grow the retry delay beyond the configured cap."""

    assert list(islice(backoff_delays(max_seconds=30, jitter=False), 7)) == [
        1,
        2,
        4,
        8,
        15,
        30,
        30,
    ]


def test_backoff_jitter_can_be_deterministic() -> None:
    """Enabling jitter must use the injected source without exceeding the cap."""

    values = iter((0.5, 1.5, 2.0))

    assert list(
        islice(
            backoff_delays(
                max_seconds=2,
                jitter=True,
                jitter_source=lambda _lower, _upper: next(values),
            ),
            3,
        )
    ) == [0.5, 1.5, 2.0]
