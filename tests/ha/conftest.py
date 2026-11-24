"""Real Home Assistant setup tests.

    pip install pytest-homeassistant-custom-component
    cd tests/ha && ln -s ../../custom_components . && pytest -s
"""
import pytest
import pytest_socket
pytest_plugins = "pytest_homeassistant_custom_component"

@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, socket_enabled):
    pytest_socket._remove_restrictions()  # real UDP to fake/dead gateways
    yield
