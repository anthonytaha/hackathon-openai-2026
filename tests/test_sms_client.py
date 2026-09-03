import httpx
import pytest

from app.services.sms_client import SmsError, send_processing_complete_sms


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://sms.example.test/send")


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def _send(client: FakeClient):
    return send_processing_complete_sms(
        recipient="+33123456789",
        source_object_path="incoming/call.mp3",
        output_object_path="processed/call/job/processed.mp3",
        job_id="job-1",
        api_url="https://sms.example.test/send",
        api_key="secret-key",
        client=client,
    )


def test_sms_request_success() -> None:
    response = httpx.Response(200, json={"message_id": "abc"}, request=_request())
    client = FakeClient(response=response)

    result = _send(client)

    assert result.success is True
    assert result.status_code == 200
    _, kwargs = client.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["json"]["job_id"] == "job-1"
    assert kwargs["json"]["to"] == "+33123456789"


@pytest.mark.parametrize("status_code", [400, 401, 429, 500, 503])
def test_sms_api_http_failure(status_code: int) -> None:
    response = httpx.Response(status_code, text="provider details", request=_request())

    with pytest.raises(SmsError, match=f"HTTP {status_code}"):
        _send(FakeClient(response=response))


@pytest.mark.parametrize(
    "error, expected",
    [
        (httpx.ReadTimeout("slow", request=_request()), "timed out"),
        (httpx.ConnectError("offline", request=_request()), "Could not reach"),
    ],
)
def test_sms_network_exception(error: Exception, expected: str) -> None:
    with pytest.raises(SmsError, match=expected):
        _send(FakeClient(error=error))
