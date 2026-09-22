"""Exercise two synthetic calls through the running HTTP service, without Vapi."""

import json
import os
import urllib.error
import urllib.request
import uuid


def main():
    url = (
        os.environ.get("VERIFY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        + "/vapi/webhook"
    )
    token = os.environ["VAPI_WEBHOOK_TOKEN"]
    phone_id = os.environ["VAPI_PHONE_NUMBER_ID"]
    first, second = "verify-" + uuid.uuid4().hex, "verify-" + uuid.uuid4().hex
    # NANP fictional number; synthetic facts remain in this demo subject's memory.
    customer = {"number": "+12025550199"}

    def send(kind, call_id, **fields):
        payload = {
            "message": {
                "type": kind,
                "call": {
                    "id": call_id,
                    "type": "inboundPhoneCall",
                    "phoneNumberId": phone_id,
                    "customer": customer,
                },
                **fields,
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.load(response)

    def remember(tool_id, value):
        response = send(
            "tool-calls",
            first,
            toolCallList=[
                {
                    "id": tool_id,
                    "function": {
                        "name": "remember",
                        "arguments": {
                            "predicate": "name",
                            "value": value,
                        },
                    },
                }
            ],
        )["results"][0]
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    send("assistant-request", first)
    old = remember("original", "Alex")
    remember("correction", "Sam")
    assert remember("original", "Alex") == old, (
        "duplicate delivery did not return the original result"
    )
    send("end-of-call-report", first)
    config = send("assistant-request", second)["assistant"]
    context = config["model"]["messages"][-1]["content"]
    assert '"Sam"' in context and '"Alex"' not in context, (
        "corrected name was not loaded"
    )
    send("end-of-call-report", second)
    print(
        "Passed: saved Alex, corrected to Sam, ignored an old retry, and loaded Sam on a second call."
    )


if __name__ == "__main__":
    main()
