import zlib

from sentry_sdk.envelope import Envelope, Item, PayloadRef


def test_replay_recordings(mini_sentry, relay_chain):
    relay = relay_chain(min_relay_version="latest")

    project_id = 42
    mini_sentry.add_basic_project_config(
        project_id, extra={"config": {"features": ["organizations:session-replay"]}}
    )

    replay_id = "515539018c9b4260a6f999572f1661ee"

    envelope = Envelope(headers=[["event_id", replay_id]])
    envelope.add_item(Item(payload=PayloadRef(bytes=b"test"), type="replay_recording"))

    relay.send_envelope(project_id, envelope)

    envelope = mini_sentry.captured_events.get(timeout=1)
    assert len(envelope.items) == 1

    session_item = envelope.items[0]
    assert session_item.type == "replay_recording"

    replay_recording = session_item.get_bytes()
    assert replay_recording == b"test"


def test_chunked_replay_recordings_processing(
    mini_sentry, relay_with_processing, replay_recordings_consumer, outcomes_consumer
):
    project_id = 42
    org_id = 0
    replay_id = "515539018c9b4260a6f999572f1661ee"
    relay = relay_with_processing()
    mini_sentry.add_basic_project_config(
        project_id, extra={"config": {"features": ["organizations:session-replay"]}}
    )
    replay_recordings_consumer = replay_recordings_consumer()
    outcomes_consumer = outcomes_consumer()

    # The smallest chunked payload possible.
    bits = b"1" * (1000 * 1000 - 1999)

    envelope = Envelope(
        headers=[
            [
                "event_id",
                replay_id,
            ],
            ["attachment_type", "replay_recording"],
        ]
    )
    envelope.add_item(Item(payload=PayloadRef(bytes=bits), type="replay_recording"))

    relay.send_envelope(project_id, envelope)

    replay_recording_contents = {}
    replay_recording_ids = []
    replay_recording_num_chunks = {}

    for _ in range(2):
        chunk, v = replay_recordings_consumer.get_chunked_replay_chunk()
        replay_recording_contents[v["id"]] = (
            replay_recording_contents.get(v["id"], b"") + chunk
        )
        if v["id"] not in replay_recording_ids:
            replay_recording_ids.append(v["id"])
        num_chunks = 1 + replay_recording_num_chunks.get(v["id"], 0)
        assert v["chunk_index"] == num_chunks - 1
        replay_recording_num_chunks[v["id"]] = num_chunks

    id1 = replay_recording_ids[0]

    assert replay_recording_contents[id1] == bits

    replay_recording = replay_recordings_consumer.get_chunked_replay()

    assert replay_recording["type"] == "replay_recording"
    assert replay_recording["replay_recording"] == {
        "chunks": replay_recording_num_chunks[id1],
        "id": id1,
        "size": len(replay_recording_contents[id1]),
    }
    assert replay_recording["replay_id"] == replay_id
    assert replay_recording["project_id"] == project_id
    assert replay_recording["org_id"] == org_id
    assert replay_recording["key_id"] == 123
    assert replay_recording["retention_days"] == 90
    assert replay_recording["received"]
    assert type(replay_recording["received"]) == int


def test_nonchunked_replay_recordings_processing(
    mini_sentry, relay_with_processing, replay_recordings_consumer, outcomes_consumer
):
    project_id = 42
    org_id = 0
    replay_id = "515539018c9b4260a6f999572f1661ee"
    relay = relay_with_processing()
    mini_sentry.add_basic_project_config(
        project_id, extra={"config": {"features": ["organizations:session-replay"]}}
    )
    replay_recordings_consumer = replay_recordings_consumer()
    outcomes_consumer = outcomes_consumer()

    envelope = Envelope(
        headers=[
            [
                "event_id",
                replay_id,
            ],
            ["attachment_type", "replay_recording"],
        ]
    )
    payload = recording_payload(b"[]")
    envelope.add_item(Item(payload=PayloadRef(bytes=payload), type="replay_recording"))

    relay.send_envelope(project_id, envelope)

    # Get the non-chunked replay-recording message from the kafka queue.
    replay_recording = replay_recordings_consumer.get_not_chunked_replay()
    assert replay_recording["replay_id"] == replay_id
    assert replay_recording["project_id"] == project_id
    assert replay_recording["key_id"] == 123
    assert replay_recording["org_id"] == org_id
    assert type(replay_recording["received"]) == int
    assert replay_recording["retention_days"] == 90
    assert replay_recording["payload"] == payload
    assert replay_recording["type"] == "replay_recording_not_chunked"

    outcomes_consumer.assert_empty()


def recording_payload(bits: bytes):
    compressed_payload = zlib.compress(bits)
    return b'{"segment_id": 0}\n' + compressed_payload
