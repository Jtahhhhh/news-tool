"""Provider wire contract, independent of runtime/content validation.

groq-strict-v1 preserves the tested JSON Schema keywords. Never silently drop
constraints on an API error; introduce a new profile and test it explicitly.
The internal Output model remains authoritative, including semantic checks
which cannot be represented by the provider's JSON Schema.
"""
from copy import deepcopy

GROQ_SCHEMA_PROFILE = 'groq-strict-v1'


def groq_schema(internal):
    schema = deepcopy(internal)

    def check(node):
        if isinstance(node, list):
            for child in node:
                check(child)
        elif isinstance(node, dict):
            if node.get('type') == 'object':
                if node.get('additionalProperties') is not False:
                    raise ValueError('Groq strict schema requires closed objects')
                if set(node.get('required', [])) != set(node.get('properties', {})):
                    raise ValueError('Groq strict schema requires all properties')
            for child in node.values():
                check(child)

    check(schema)
    return schema
