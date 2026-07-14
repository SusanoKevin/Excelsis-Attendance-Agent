import pytest
from dotenv import load_dotenv

load_dotenv()  # must run before any src.* imports that read env vars


def pytest_addoption(parser):
    parser.addoption("--run-all", action="store_true", default=False,
                     help="Also run integration tests (requires Ollama)")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Ollama running locally")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-all"):
        skip = pytest.mark.skip(reason="pass --run-all to run integration tests")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
