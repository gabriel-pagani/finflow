import pytest

from assistant.tools import TOOLS


@pytest.mark.parametrize('tool', TOOLS, ids=lambda tool: tool['name'])
def test_ferramenta_segue_o_modo_estrito(tool):
    parameters = tool['parameters']

    assert tool['strict'] is True
    assert parameters['additionalProperties'] is False
    assert set(parameters['required']) == set(parameters['properties'])


@pytest.mark.parametrize('tool', TOOLS, ids=lambda tool: tool['name'])
def test_todo_campo_aceita_null_para_o_enchimento(tool):
    for name, schema in tool['parameters']['properties'].items():
        assert 'null' in schema['type'], name
        if 'enum' in schema:
            assert None in schema['enum'], name
