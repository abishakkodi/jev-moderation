import os

import pytest
from jev_moderation import MockClient, Moderator, evaluate, mock_response

POLICY = {
    "schemaVersion": 1,
    "version": "ads-v1",
    "mode": "replace",
    "filters": [{"id": "ads", "definition": "Unsolicited promotion of a product."}],
}


def test_application_routes_flagged_messages_to_review():
    with Moderator(policy=POLICY, client=MockClient([mock_response({"ads": 0.7})])) as moderator:
        assert moderator.moderate("Buy my product")["action"] == "review"


@pytest.mark.skipif(
    os.environ.get("TYPESAFE_LIVE_EVAL") != "1", reason="Billable inference is opt-in"
)
def test_custom_filter_quality():
    report = evaluate(
        POLICY,
        [
            {
                "id": "ad",
                "message": "Buy my unrelated product now!",
                "expectedAction": "block",
                "labels": {"ads": True},
            },
            {
                "id": "thanks",
                "message": "Thanks for your help!",
                "expectedAction": "allow",
                "labels": {"ads": False},
            },
        ],
        gates={"minAccuracy": 1},
    )
    assert report["passed"], report
