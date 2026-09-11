from race_status import (
    CLASSIFIED_STATUSES,
    DNS_STATUSES,
    DNF_STATUSES,
    DISQUALIFIED_STATUSES,
    RaceStatusCategory,
    WITHDRAWN_STATUSES,
    classify_status,
    is_classified,
    is_dnf,
)


def test_all_classified_statuses_are_classified():
    for status in CLASSIFIED_STATUSES:
        assert classify_status(status) == RaceStatusCategory.CLASSIFIED
        assert is_classified(status) is True
        assert is_dnf(status) is False


def test_all_dnf_statuses_are_dnf():
    for status in DNF_STATUSES:
        assert classify_status(status) == RaceStatusCategory.DNF
        assert is_classified(status) is False
        assert is_dnf(status) is True


def test_dns_statuses_are_not_dnf():
    for status in DNS_STATUSES:
        assert classify_status(status) == RaceStatusCategory.DNS
        assert is_classified(status) is False
        assert is_dnf(status) is False


def test_withdrawn_statuses_are_not_dnf():
    for status in WITHDRAWN_STATUSES:
        assert classify_status(status) == RaceStatusCategory.WITHDRAWN
        assert is_classified(status) is False
        assert is_dnf(status) is False


def test_disqualified_statuses_are_not_dnf():
    for status in DISQUALIFIED_STATUSES:
        assert classify_status(status) == RaceStatusCategory.DISQUALIFIED
        assert is_classified(status) is False
        assert is_dnf(status) is False


def test_unknown_status_fails_closed():
    assert classify_status(None) == RaceStatusCategory.UNKNOWN
    assert classify_status("Some Future Status") == RaceStatusCategory.UNKNOWN
    assert is_classified(None) is False
    assert is_classified("Some Future Status") is False
    assert is_dnf(None) is False
    assert is_dnf("Some Future Status") is False
