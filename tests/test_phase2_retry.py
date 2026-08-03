import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from phase2_contracts import (
    BudgetLimitError,
    DependencyError,
    MimeRejectedError,
    ResponseContractError,
    UrlSafetyError,
)
from phase2_fetch import RetryableHttpStatus
from phase2_retry import (
    FakeClock,
    FakeConnectTimeout,
    FakeReadTimeout,
    FakeTemporaryDnsFailure,
    RequestCounter,
    RetryPolicy,
    calculate_retry_delay,
    run_with_retry,
    should_retry,
)


def operation_from(values):
    iterator = iter(values)

    def operation():
        value = next(iterator)
        if isinstance(value, BaseException):
            raise value
        return value

    return operation


def always_raise(error):
    def operation():
        raise error

    return operation


def zero_jitter(upper):
    return Decimal("0")


class RetryPolicyTests(unittest.TestCase):
    def test_policy_is_frozen_and_defaults_to_one_retry(self):
        policy = RetryPolicy()
        self.assertEqual((1, Decimal("1"), Decimal("30")), (
            policy.max_retries,
            policy.base_seconds,
            policy.max_delay_seconds,
        ))
        with self.assertRaises(FrozenInstanceError):
            policy.max_retries = 2

    def test_rejects_invalid_retry_policy_and_request_limits(self):
        invalid_policies = (
            {"max_retries": -1},
            {"max_retries": 3},
            {"max_retries": True},
            {"base_seconds": Decimal("0")},
            {"base_seconds": Decimal("NaN")},
            {"max_delay_seconds": Decimal("31")},
        )
        for values in invalid_policies:
            with self.subTest(values=values), self.assertRaises(ValueError):
                RetryPolicy(**values)
        for limit in (0, -1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                RequestCounter(limit=limit)

    def test_classifies_only_fixed_transient_failures(self):
        policy = RetryPolicy(max_retries=1)
        retryable = (
            FakeConnectTimeout(),
            FakeReadTimeout(),
            FakeTemporaryDnsFailure(),
            RetryableHttpStatus(429),
            RetryableHttpStatus(502),
            RetryableHttpStatus(503),
            RetryableHttpStatus(504),
        )
        for error in retryable:
            with self.subTest(error=type(error).__name__):
                self.assertTrue(should_retry(error, 0, policy))

        forbidden = (
            DependencyError("generic"),
            UrlSafetyError(),
            MimeRejectedError(),
            ResponseContractError(),
            BudgetLimitError(),
            RetryableHttpStatus(500),
        )
        for error in forbidden:
            with self.subTest(error=type(error).__name__):
                self.assertFalse(should_retry(error, 0, policy))

    def test_never_retries_after_policy_max(self):
        self.assertFalse(should_retry(FakeConnectTimeout(), 1, RetryPolicy(max_retries=1)))


class DelayTests(unittest.TestCase):
    def test_uses_exponential_cap_and_injected_full_jitter(self):
        seen = []

        def fixed_jitter(upper):
            seen.append(upper)
            return upper / 2

        policy = RetryPolicy(max_retries=2, base_seconds=Decimal("20"))
        first = calculate_retry_delay(FakeConnectTimeout(), 0, policy, fixed_jitter)
        second = calculate_retry_delay(FakeConnectTimeout(), 1, policy, fixed_jitter)

        self.assertEqual((Decimal("10"), Decimal("15")), (first, second))
        self.assertEqual([Decimal("20"), Decimal("30")], seen)

    def test_accepts_retry_after_zero_and_thirty(self):
        policy = RetryPolicy()
        for value in ("0", "30"):
            with self.subTest(value=value):
                delay = calculate_retry_delay(
                    RetryableHttpStatus(429, value),
                    0,
                    policy,
                    zero_jitter,
                )
                self.assertEqual(Decimal(value), delay)

    def test_rejects_retry_after_above_limit_or_invalid(self):
        policy = RetryPolicy()
        for value in ("31", "-1", "+1", "1.5", "abc", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_retry_delay(
                    RetryableHttpStatus(429, value),
                    0,
                    policy,
                    zero_jitter,
                )

    def test_rejects_jitter_outside_full_jitter_range(self):
        policy = RetryPolicy()
        for result in (Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")):
            with self.subTest(result=result), self.assertRaises(ValueError):
                calculate_retry_delay(
                    FakeConnectTimeout(),
                    0,
                    policy,
                    lambda upper, result=result: result,
                )


class RetryExecutionTests(unittest.TestCase):
    def test_success_uses_one_request_without_sleep(self):
        clock = FakeClock()
        counter = RequestCounter(limit=1)
        result = run_with_retry(
            lambda: "ok",
            RetryPolicy(),
            clock,
            zero_jitter,
            counter,
        )
        self.assertEqual("ok", result)
        self.assertEqual(1, counter.used)
        self.assertEqual((), clock.sleeps)

    def test_retries_each_transient_failure_then_succeeds(self):
        errors = (
            FakeConnectTimeout(),
            FakeReadTimeout(),
            FakeTemporaryDnsFailure(),
            RetryableHttpStatus(429),
            RetryableHttpStatus(502),
            RetryableHttpStatus(503),
            RetryableHttpStatus(504),
        )
        for error in errors:
            clock = FakeClock()
            counter = RequestCounter(limit=2)
            result = run_with_retry(
                operation_from((error, "ok")),
                RetryPolicy(max_retries=1),
                clock,
                lambda upper: Decimal("0.5"),
                counter,
            )
            with self.subTest(error=type(error).__name__):
                self.assertEqual("ok", result)
                self.assertEqual(2, counter.used)
                self.assertEqual((Decimal("0.5"),), clock.sleeps)

    def test_never_retries_policy_mime_size_decode_or_generic_dependency(self):
        errors = (
            UrlSafetyError(),
            MimeRejectedError(),
            ResponseContractError(),
            DependencyError("generic"),
        )
        for error in errors:
            counter = RequestCounter(limit=3)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                run_with_retry(
                    always_raise(error),
                    RetryPolicy(max_retries=2),
                    FakeClock(),
                    zero_jitter,
                    counter,
                )
            self.assertEqual(1, counter.used)

    def test_honors_zero_one_and_two_retry_limits_without_infinite_retry(self):
        for retries in (0, 1, 2):
            counter = RequestCounter(limit=3)
            with self.subTest(retries=retries), self.assertRaises(FakeConnectTimeout):
                run_with_retry(
                    always_raise(FakeConnectTimeout()),
                    RetryPolicy(max_retries=retries),
                    FakeClock(),
                    zero_jitter,
                    counter,
                )
            self.assertEqual(1 + retries, counter.used)

    def test_request_hard_limit_prevents_next_operation(self):
        calls = []

        def operation():
            calls.append("called")
            raise FakeConnectTimeout()

        counter = RequestCounter(limit=1)
        with self.assertRaises(BudgetLimitError):
            run_with_retry(
                operation,
                RetryPolicy(max_retries=2),
                FakeClock(),
                zero_jitter,
                counter,
            )
        self.assertEqual(["called"], calls)
        self.assertEqual(1, counter.used)

    def test_retry_after_thirty_sleeps_but_thirty_one_stops(self):
        clock = FakeClock()
        result = run_with_retry(
            operation_from((RetryableHttpStatus(429, "30"), "ok")),
            RetryPolicy(),
            clock,
            zero_jitter,
            RequestCounter(limit=2),
        )
        self.assertEqual("ok", result)
        self.assertEqual((Decimal("30"),), clock.sleeps)

        clock = FakeClock()
        counter = RequestCounter(limit=2)
        with self.assertRaises(RetryableHttpStatus):
            run_with_retry(
                always_raise(RetryableHttpStatus(429, "31")),
                RetryPolicy(),
                clock,
                zero_jitter,
                counter,
            )
        self.assertEqual(1, counter.used)
        self.assertEqual((), clock.sleeps)

    def test_fake_clock_accumulates_only_injected_sleep(self):
        clock = FakeClock(start=Decimal("10"))
        clock.sleep(Decimal("0.5"))
        clock.sleep(Decimal("1.5"))
        self.assertEqual(Decimal("12"), clock.now())
        self.assertEqual((Decimal("0.5"), Decimal("1.5")), clock.sleeps)


if __name__ == "__main__":
    unittest.main()
