import pytest

from outbreak_monitor import collector
from outbreak_monitor.location import LocationResolver


@pytest.fixture(scope="session")
def resolver():
    """One resolver for the whole test run (loading spaCy and 7,000+ place names takes a few seconds)."""
    return LocationResolver(geocoder=None)


@pytest.fixture(autouse=True)
def _shared_resolver(resolver, monkeypatch):
    # Collection tests use the shared resolver without a geocoder: no network access in tests.
    monkeypatch.setattr(collector, "build_resolver", lambda session_factory, config=None: resolver)
