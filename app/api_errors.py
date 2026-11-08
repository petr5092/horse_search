from uuid import uuid4
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.config import settings


def error_detail(status, code, message, details=None):
    code = 'unauthenticated' if status == 401 else code
    details = dict(details) if isinstance(details, dict) else {}
    if code == 'validation_error':
        violations = details.get('violations')
        if not isinstance(violations, list) or not violations:
            details['violations'] = [{'path': '/', 'message': message}]
    return {'code': code, 'message': message, 'details': details}


def api_error(status, code, message, details=None):
    return HTTPException(status, error_detail(status, code, message, details))


def json_pointer(location):
    location = list(location)
    if location and location[0] in {'body', 'query', 'path', 'header', 'cookie'}:
        location = location[1:]
    return '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in location)


def install_api_errors(app):
    def response(request, status, detail, headers=None):
        if not isinstance(detail, dict) or 'code' not in detail:
            detail = error_detail(status,
                {401:'unauthenticated',403:'forbidden',404:'not_found',409:'conflict',422:'validation_error'}.get(status, 'request_error'),
                str(detail))
        else:
            detail = error_detail(status, detail['code'], detail['message'], detail.get('details'))
        return JSONResponse(status_code=status, content=jsonable_encoder({'error': {
            **detail, 'request_id': request.state.request_id}}), headers=headers)

    @app.middleware('http')
    async def trace_and_cookie_csrf(request, call_next):
        request.state.request_id = str(uuid4())
        is_api = request.url.path == '/api' or request.url.path.startswith('/api/')
        # get_token selects a nonempty cookie before Authorization. A Bearer
        # header therefore cannot exempt a request carrying a selected cookie.
        cookie_auth = request.cookies.get('access_token') or request.cookies.get('booking_access_token')
        if is_api and request.method not in {'GET', 'HEAD', 'OPTIONS'} and cookie_auth:
            origins = request.headers.getlist('origin')
            same_origin = f'{request.url.scheme}://{request.url.netloc}'
            trusted = set(settings.API_CORS_ORIGINS) | {same_origin}
            if len(origins) != 1 or origins[0] not in trusted:
                result = response(request, 403, error_detail(403, 'forbidden', 'Untrusted request origin'))
                result.headers['X-Request-ID'] = request.state.request_id
                return result
        result = await call_next(request)
        result.headers['X-Request-ID'] = request.state.request_id
        return result

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        if not request.url.path.startswith('/api/'):
            return await http_exception_handler(request, exc)
        return response(request, exc.status_code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if not request.url.path.startswith('/api/'):
            return await request_validation_exception_handler(request, exc)
        # Only paths and messages leave the validator: input values, bodies,
        # validator contexts and exception objects must not be exposed.
        violations = [{'path': json_pointer(error['loc']), 'message': error['msg']}
                      for error in exc.errors()]
        return response(request, 422, error_detail(422, 'validation_error', 'Invalid request', {'violations': violations}))
