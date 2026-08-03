"""Pure bounded retry policy with fake clock, sleep, and injected jitter."""

from dataclasses import dataclass
from decimal import Decimal

from phase2_contracts import BudgetLimitError, DependencyError
from phase2_fetch import RETRYABLE_HTTP_STATUSES, RetryableHttpStatus


class FakeConnectTimeout(DependencyError):
    """A deterministic fake connect timeout."""


class FakeReadTimeout(DependencyError):
    """A deterministic fake read timeout."""


class FakeTemporaryDnsFailure(DependencyError):
    """A deterministic fake temporary DNS failure."""


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 1
    base_seconds: Decimal = Decimal("1")
    max_delay_seconds: Decimal = Decimal("30")

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 2
        ):
            raise ValueError("max_retries must be an integer from zero through two")
        for name, value, maximum in (
            ("base_seconds", self.base_seconds, Decimal("30")),
            ("max_delay_seconds", self.max_delay_seconds, Decimal("30")),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
            if value <= 0 or value > maximum:
                raise ValueError(f"{name} is outside its hard limit")


@dataclass
class RequestCounter:
    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("request limit must be a positive integer")
        if isinstance(self.used, bool) or not isinstance(self.used, int):
            raise ValueError("used request count must be an integer")
        if not 0 <= self.used <= self.limit:
            raise ValueError("used request count is outside the limit")

    def reserve(self) -> None:
        if self.used >= self.limit:
            raise BudgetLimitError("request hard limit reached")
        self.used += 1


class FakeClock:
    """Record deterministic sleeps without accessing wall time."""

    def __init__(self, start: Decimal = Decimal("0")) -> None:
        if not isinstance(start, Decimal) or not start.is_finite():
            raise ValueError("fake clock start must be a finite Decimal")
        self._current = start
        self._sleeps: list[Decimal] = []

    @property
    def sleeps(self) -> tuple[Decimal, ...]:
        return tuple(self._sleeps)

    def now(self) -> Decimal:
        return self._current

    def sleep(self, seconds: Decimal) -> None:
        if not isinstance(seconds, Decimal) or not seconds.is_finite() or seconds < 0:
            raise ValueError("fake sleep must be a non-negative finite Decimal")
        self._sleeps.append(seconds)
        self._current += seconds


def _retry_after_seconds(error: RetryableHttpStatus) -> Decimal | None:
    if error.retry_after is None:
        return None
    raw = error.retry_after.strip()
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError("Retry-After must be an integer from zero through thirty")
    value = Decimal(raw)
    if value > 30:
        raise ValueError("Retry-After exceeds the hard limit")
    return value


def should_retry(error: Exception, retry_index: int, policy: RetryPolicy) -> bool:
    """Classify a failure without sleeping or changing counters."""

    if retry_index >= policy.max_retries:
        return False
    if isinstance(error, RetryableHttpStatus):
        if error.status_code not in RETRYABLE_HTTP_STATUSES:
            return False
        try:
            _retry_after_seconds(error)
        except ValueError:
            return False
        return True
    return isinstance(
        error,
        (FakeConnectTimeout, FakeReadTimeout, FakeTemporaryDnsFailure),
    )


def calculate_retry_delay(
    error: Exception,
    retry_index: int,
    policy: RetryPolicy,
    jitter,
) -> Decimal:
    """Calculate one deterministic delay using only injected jitter."""

    if isinstance(error, RetryableHttpStatus):
        retry_after = _retry_after_seconds(error)
        if retry_after is not None:
            return retry_after
    upper = min(
        policy.max_delay_seconds,
        policy.base_seconds * (2**retry_index),
    )
    delay = jitter(upper)
    if (
        not isinstance(delay, Decimal)
        or not delay.is_finite()
        or delay < 0
        or delay > upper
    ):
        raise ValueError("injected jitter must be within the full-jitter range")
    return delay


def run_with_retry(
    operation,
    policy: RetryPolicy,
    clock: FakeClock,
    jitter,
    counter: RequestCounter,
):
    """Run one injected fake operation with bounded attempts and requests."""

    retry_index = 0
    while True:
        counter.reserve()
        try:
            return operation()
        except Exception as error:
            if not should_retry(error, retry_index, policy):
                raise
            if counter.used >= counter.limit:
                raise BudgetLimitError("request hard limit reached") from error
            delay = calculate_retry_delay(error, retry_index, policy, jitter)
            clock.sleep(delay)
            retry_index += 1
