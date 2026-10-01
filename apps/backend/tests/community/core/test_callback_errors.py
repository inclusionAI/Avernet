from agentclaw.community.core.errors import (
    CallbackAuthError, CallbackCorrelationError, DomainError,
)


def test_callback_errors_are_domain_errors():
    assert issubclass(CallbackAuthError, DomainError)
    assert issubclass(CallbackCorrelationError, DomainError)


def test_callback_errors_carry_detail():
    e1 = CallbackAuthError("bad sig")
    assert e1.detail == "bad sig"
    e2 = CallbackCorrelationError("unregistered")
    assert e2.detail == "unregistered"