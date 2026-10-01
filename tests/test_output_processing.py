import json
import pytest
from app.llm.base import FakeProvider
from app.llm.schemas import validate_output
from app.llm.output_processing import validate_generated_output, validation_feedback
from app.llm.grounding import GroundingError
from tests.test_scripts import input_data


def generated():
    return json.loads(FakeProvider('mock').generate_script(input_data(), '').raw)


def test_generated_hook_follows_grounded_narration_without_changing_audio():
    payload = generated()
    narration = payload['scenes'][0]['narration']
    payload['hook'] = 'Tiêu đề riêng của model'
    raw = json.dumps(payload)
    output = validate_generated_output(input_data(), raw)
    assert output.hook == narration == output.scenes[0].narration
    assert any('đồng bộ hook' in warning for warning in output.warnings)
    assert json.loads(raw)['hook'] == 'Tiêu đề riêng của model'
    # Manual edits and approval must still meet the original strict contract.
    with pytest.raises(ValueError):validate_output(input_data(), raw)


def test_hook_normalization_never_bypasses_grounding():
    payload = generated()
    payload['hook'] = 'Tiêu đề riêng'
    payload['scenes'][0]['narration'] += ' trong suốt thời gian học'
    with pytest.raises(GroundingError):validate_generated_output(input_data(), json.dumps(payload))


@pytest.mark.parametrize('case',['missing_hook','empty_hook','missing_warnings','bad_json','missing_source'])
def test_normalization_cannot_hide_invalid_response(case):
    payload = generated()
    if case == 'missing_hook':del payload['hook']
    if case == 'empty_hook':payload['hook'] = ''
    if case == 'missing_warnings':del payload['warnings']
    if case == 'missing_source':payload['claims'][0]['evidence'][0]['source_id'] = 'absent'
    with pytest.raises(ValueError):validate_generated_output(input_data(), '{bad' if case == 'bad_json' else json.dumps(payload))


def test_repair_feedback_names_exact_error_without_echoing_payload():
    payload = generated();payload['hook'] = 'private-output-content'
    with pytest.raises(ValueError) as error:validate_output(input_data(), json.dumps(payload))
    feedback = validation_feedback(error.value)
    assert 'hook must equal first scene narration' in feedback
    assert 'private-output-content' not in feedback and 'input_value' not in feedback
