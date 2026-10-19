import json

from review_writer_api.stream_diagnostics import observe_event, request_options


def test_observes_alternative_answer_locations_without_retaining_content():
    state = {}
    observe_event(state, {"choices": [{"delta": {"reasoning_content": "private thought"},
        "message": {"content": "answer-only-in-final-message"}}], "output_text": "alternate answer"})
    assert state["wire_fields"]["$.choices[].message.content:string"]["chars"] == 28
    assert state["wire_fields"]["$.output_text:string"]["nonempty"] == 1
    assert "private thought" not in json.dumps(state)
    assert "alternate answer" not in json.dumps(state)


def test_distinguishes_standard_text_from_array_content_and_nonfirst_choices():
    state = {}
    observe_event(state, {"choices": [{"index": 1, "delta": {"content": [{"text": "hello"}]}}]})
    assert state["other_choice_events"] == 1
    assert state["wire_fields"]["$.choices[].delta.content[].text:string"]["chars"] == 5


def test_field_budget_and_depth_are_bounded_and_sensitive_keys_sanitized():
    state = {}
    for i in range(300):
        observe_event(state, {f"field_{i}": "text", "sensitive key with spaces": "secret"})
    assert len(state["wire_fields"]) <= 128
    assert state["wire_fields_capped"]
    assert "sensitive key" not in json.dumps(state)


def test_request_options_do_not_retain_prompts_or_secrets():
    assert request_options({"stream": True, "messages": "secret", "api_key": "secret",
        "thinking": {"type": "disabled"}, "max_tokens": 8192}) == {
        "stream": True, "thinking_type": "disabled", "max_tokens": 8192}
