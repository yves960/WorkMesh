import sqlite3
from typing import Never

from fastapi import HTTPException

from .errors import (
    ContextNotFound,
    ControlPlaneError,
    MulticaCommandError,
    MulticaUnavailable,
)

ControlError = ControlPlaneError | sqlite3.IntegrityError
CONTROL_ERRORS = (ControlPlaneError, sqlite3.IntegrityError)


def raise_http(exc: ControlError) -> Never:
    if isinstance(exc, ContextNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MulticaUnavailable):
        raise HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, MulticaCommandError):
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "exit_code": exc.exit_code},
        )
    if isinstance(exc, ControlPlaneError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=409, detail=str(exc))
