"""Validator for the exact JSON Schema vocabulary used by annotation contract 0.1.

Contract validation is transport validation. Go remains responsible for task state,
geometry bounds, object visibility and annotation business rules.
"""
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

_SPEC = json.loads((Path(__file__).resolve().parents[2] / 'docs/contracts/annotation-service.openapi.json').read_text())
CONTRACT = {'schemas': _SPEC['components']['schemas'], 'parameters': _SPEC['components']['parameters'],
            'paths': {p: s for p, s in _SPEC['paths'].items() if p.startswith('/v1/projects/')}}
SCHEMAS = CONTRACT['schemas']


class ContractViolation(ValueError):
    pass


def finite(value):
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def validate(value, schema, path='$'):
    def check(condition, reason):
        if not condition:
            raise ContractViolation(f'{path}: {reason}')
    if '$ref' in schema:
        return validate(value, SCHEMAS[schema['$ref'].rsplit('/', 1)[1]], path)
    if 'anyOf' in schema:
        for branch in schema['anyOf']:
            try:
                validate(value, branch, path)
                break
            except ContractViolation:
                pass
        else:
            raise ContractViolation(f'{path}: no permitted variant')
    for branch in schema.get('allOf', []):
        if 'if' in branch:
            try:
                validate(value, branch['if'], path)
                matches = True
            except ContractViolation:
                matches = False
            validate(value, branch.get('then' if matches else 'else', {}), path)
        else:
            validate(value, branch, path)
    kind = schema.get('type')
    checks = {'object': isinstance(value, dict), 'array': isinstance(value, list),
              'string': isinstance(value, str), 'integer': type(value) is int,
              'number': type(value) in (int, float) and finite(value),
              'boolean': type(value) is bool, 'null': value is None}
    if kind:
        check(checks.get(kind, False), f'expected {kind}')
    if 'const' in schema:
        check(value == schema['const'], 'unexpected constant')
    if 'enum' in schema:
        check(value in schema['enum'], 'unexpected enum value')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        check(set(schema.get('required', [])) <= value.keys(), 'missing fields')
        if schema.get('additionalProperties') is False:
            check(value.keys() <= props.keys(), 'unexpected fields')
        for key, item in value.items():
            if key in props:
                validate(item, props[key], f'{path}/{key}')
    if isinstance(value, list):
        check(schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', math.inf), 'array length')
        if schema.get('uniqueItems'):
            check(len({json.dumps(x, sort_keys=True) for x in value}) == len(value), 'duplicate items')
        if 'items' in schema:
            for index, item in enumerate(value):
                validate(item, schema['items'], f'{path}/{index}')
    if isinstance(value, str):
        check(schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', math.inf), 'string length')
        if 'pattern' in schema:
            check(re.search(schema['pattern'], value) is not None, 'string pattern')
        try:
            if schema.get('format') == 'uuid':
                UUID(value)
            if schema.get('format') == 'date-time':
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                check('T' in value and parsed.utcoffset() == timezone.utc.utcoffset(parsed), 'expected UTC RFC3339')
        except (ValueError, TypeError):
            raise ContractViolation(f'{path}: invalid {schema.get("format")}') from None
    if type(value) in (int, float):
        check(finite(value), 'nonfinite number')
        check(schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf), 'number range')
        if 'exclusiveMinimum' in schema:
            check(value > schema['exclusiveMinimum'], 'exclusive minimum')
        if 'exclusiveMaximum' in schema:
            check(value < schema['exclusiveMaximum'], 'exclusive maximum')


def validate_named(value, name):
    validate(value, SCHEMAS[name])


# Public creation deliberately excludes authoritative schema/size/hash fields.
PUBLIC_CREATE = {
    'type': 'object', 'additionalProperties': False,
    'required': ['request_id', 'title', 'assignee_id', 'reviewer_id', 'label_schema_version', 'image_version_ids'],
    'properties': {
        **{k: SCHEMAS['CreateTaskRequest']['properties'][k]
           for k in ('request_id', 'title', 'assignee_id', 'reviewer_id')},
        'label_schema_version': {'type': 'integer', 'minimum': 1, 'maximum': 2147483647},
        'image_version_ids': {'type': 'array', 'minItems': 1, 'maxItems': 10000,
                              'uniqueItems': True, 'items': {'type': 'string', 'format': 'uuid'}},
    },
}
