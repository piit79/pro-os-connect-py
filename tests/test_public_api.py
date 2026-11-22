import pro_os_connect

PUBLIC_API = {
    "TCP_PORT",
    "ProOSConnect",
    "ProOSConnectConnectError",
    "ProOSConnectError",
    "ProOSConnectInternalError",
    "ProOSConnectRequestError",
    "ProOSConnectResponseError",
    "ProOSConnectStatusError",
    "ProOSConnectTimeoutError",
    "StatusCode",
    "TelemetryFields",
    "__version__",
}


def test_all_matches_public_api():
    """Changing the public API must be deliberate: update PUBLIC_API, the README and the version"""
    assert set(pro_os_connect.__all__) == PUBLIC_API
    assert len(pro_os_connect.__all__) == len(PUBLIC_API)  # No duplicates


def test_all_names_exist():
    for name in pro_os_connect.__all__:
        assert hasattr(pro_os_connect, name), name


def test_star_import_exports_only_public_api():
    namespace: dict = {}
    exec("from pro_os_connect import *", namespace)
    assert set(namespace) - {"__builtins__"} == PUBLIC_API


def test_telemetry_fields_is_the_common_class():
    from pro_os_connect.common import TelemetryFields

    assert pro_os_connect.TelemetryFields is TelemetryFields


def test_status_code_is_the_common_class():
    from pro_os_connect.common import StatusCode

    assert pro_os_connect.StatusCode is StatusCode
