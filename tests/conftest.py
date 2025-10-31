import os
import importlib
import pytest


@pytest.fixture(scope="function")
def app_client():
    # Configure env for tests BEFORE importing the app module
    os.environ["SERVERLESS"] = "false"
    os.environ["USE_FILE_LOGS"] = "false"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["LOG_LEVEL"] = "DEBUG"

    # Import the app module fresh for each test
    import app as app_module
    importlib.reload(app_module)

    app = app_module.app
    app.testing = True
    with app.test_client() as client:
        yield client
