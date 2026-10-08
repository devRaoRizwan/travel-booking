# AppError subclass -> HTTP status. Body: {"error": {"code", "message"}}.

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain import errors

STATUS_BY_ERROR = {
    errors.BadRequest: 400,
    errors.Unauthenticated: 401,
    errors.Forbidden: 403,
    errors.NotFound: 404,
    errors.Conflict: 409,
    errors.Unprocessable: 422,
    errors.UpstreamError: 502,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(errors.AppError)
    async def handle_app_error(_: Request, error: errors.AppError):
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        return JSONResponse({"error": {"code": error.code, "message": error.message}},
                            STATUS_BY_ERROR.get(type(error), 500), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, error: RequestValidationError):
        details = [{"loc": list(issue["loc"]), "msg": issue["msg"]} for issue in error.errors()]
        return JSONResponse(
            {"error": {"code": "VALIDATION_ERROR", "message": "invalid request", "details": details}}, 422)
