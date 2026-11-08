"""Public annotation documentation derived from the single reviewed Go contract.

Only the HTTP boundary changes here: user authentication, public creation inputs,
editor URLs and gateway errors. Annotation DTOs continue to come from OpenAPI 0.1.
"""
from copy import deepcopy

from .client import OPERATIONS
from .validation import _SPEC, PUBLIC_CREATE

PREFIX = '/api/projects/{project_id}/annotation'
SCHEMA_PREFIX = 'Annotation'


def public_copy(value):
    """Inline shared responses/parameters and namespace every annotation schema ref."""
    if isinstance(value, list):
        return [public_copy(child) for child in value]
    if not isinstance(value, dict):
        return value
    reference = value.get('$ref')
    if reference:
        if reference.startswith('#/components/schemas/'):
            return {**{k: public_copy(v) for k, v in value.items() if k != '$ref'},
                    '$ref': '#/components/schemas/' + SCHEMA_PREFIX + reference.rsplit('/', 1)[1]}
        if reference.startswith(('#/components/responses/', '#/components/parameters/')):
            _, _, category, name = reference.split('/')
            return public_copy({**_SPEC['components'][category][name],
                                **{k: v for k, v in value.items() if k != '$ref'}})
        raise ValueError('Unsupported annotation contract reference: ' + reference)
    return {key: public_copy(child) for key, child in value.items()}


def public_schemas():
    schemas = {SCHEMA_PREFIX + name: public_copy(schema)
               for name, schema in _SPEC['components']['schemas'].items() if name != 'Health'}
    create = deepcopy(PUBLIC_CREATE)
    example = next(iter(_SPEC['paths']['/v1/projects/{project_id}/tasks']['post']
                        ['requestBody']['content']['application/json']['examples'].values()))['value']
    create['examples'] = [{**{key: example[key] for key in ('request_id', 'title', 'assignee_id', 'reviewer_id')},
                           'label_schema_version': example['label_schema']['version'],
                           'image_version_ids': [image['image_version_id'] for image in example['items']]}]
    schemas['AnnotationCreateTaskRequest'] = public_copy(create)
    context = schemas['AnnotationTaskItemContext']
    context['properties'].update({
        'image_url': {'type': 'string', 'format': 'uri',
                      'description': 'Temporary inline URL for this authorized immutable working image.'},
        'image_url_expires_at': {'type': 'string', 'format': 'date-time',
                                 'description': 'UTC expiry of image_url; request the context again to refresh it.'},
    })
    context['required'] = list(context['required']) + ['image_url', 'image_url_expires_at']
    for sample in context.get('examples', []):
        sample.update(image_url='http://localhost:9000/working-image.png?temporary=example',
                      image_url_expires_at='2026-11-08T12:05:00Z')
    return schemas


def public_operation(operation):
    result = public_copy(operation.spec)
    result['operationId'] = 'annotation_' + result['operationId']
    result['tags'] = ['annotation']
    # Path-level parameters from Go include the trusted context headers. Public
    # clients supply only path/query values and their own authenticated session.
    result['parameters'] = [public_copy(parameter) for parameter in operation.parameters
                            if public_copy(parameter)['in'] in ('path', 'query')]
    result['security'] = [{'AnnotationUserBearer': []}, {'AnnotationSessionCookie': []},
                          {'AnnotationLegacySessionCookie': []}]
    if 'summary' in result:
        result['summary'] = result['summary'].replace('actor', 'user')
    for code, description in (('502', 'Annotation service unavailable or invalid upstream response.'),
                              ('504', 'Annotation service timeout; retry mutations with the same request_id and body.')):
        result['responses'][code] = {'description': description, 'content': {'application/json': {
            'schema': {'$ref': '#/components/schemas/AnnotationError'}}}}
    if operation.method == 'POST' and operation.template == '/tasks':
        result['summary'] = 'Create a task from project image version IDs'
        result['description'] = ('Project admin/manager only. The platform resolves the stored label schema and ready '
                                 'working images. Reuse the same request_id and public body after an uncertain result.')
        result['requestBody'] = {'required': True, 'content': {'application/json': {
            'schema': {'$ref': '#/components/schemas/AnnotationCreateTaskRequest'}}}}
    if operation.template.endswith('/context'):
        response = result['responses']['200']
        response.setdefault('headers', {})['Cache-Control'] = {
            'description': 'Editor context URLs must not be cached.',
            'schema': {'type': 'string', 'const': 'no-store'}}
        for example in response['content']['application/json'].get('examples', {}).values():
            example['value'].update(image_url='http://localhost:9000/working-image.png?temporary=example',
                                    image_url_expires_at='2026-11-08T12:05:00Z')
    return result


def install_annotation_openapi(app):
    """Install once after including routers; preserve FastAPI's normal schema generation."""
    if getattr(app.state, 'annotation_openapi_installed', False):
        return
    default_openapi = app.openapi
    app.openapi_schema = None

    def openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = default_openapi()
        components = schema.setdefault('components', {})
        schemas = components.setdefault('schemas', {})
        additions = public_schemas()
        if set(additions) & set(schemas):
            raise RuntimeError('Annotation OpenAPI schema namespace collision')
        schemas.update(additions)
        schemes = components.setdefault('securitySchemes', {})
        schemes.update({
            'AnnotationUserBearer': {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT',
                                     'description': 'User access_token returned by /auth/login.'},
            'AnnotationSessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'access_token',
                'description': 'User login session. Unsafe cookie-authenticated requests require a trusted Origin.'},
            'AnnotationLegacySessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'booking_access_token',
                'description': 'Legacy user login session; the same trusted Origin rule applies.'},
        })
        for operation in OPERATIONS:
            path = PREFIX + operation.template
            schema['paths'][path][operation.method.lower()] = public_operation(operation)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
    app.state.annotation_openapi_installed = True
