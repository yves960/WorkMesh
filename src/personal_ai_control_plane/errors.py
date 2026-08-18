class ControlPlaneError(RuntimeError):
    pass


class MulticaUnavailable(ControlPlaneError):
    pass


class MulticaCommandError(ControlPlaneError):
    def __init__(self, command: list[str], message: str, exit_code: int | None = None):
        self.command = command
        self.exit_code = exit_code
        super().__init__(message)


class WorkspaceSelectionRequired(ControlPlaneError):
    """Raised when a Multica command is workspace-scoped but no workspace can be selected safely."""


class ContextNotFound(ControlPlaneError):
    pass


class MissingTargetError(ControlPlaneError):
    """Raised when a loop run request carries no usable ForgeLoop target."""


class GuardViolation(ControlPlaneError):
    """start_loop() was invoked outside the confirmation guard.

    Task 3 hard rule: a loop run may only start after the user explicitly
    confirms a plan (/api/v1/execution/confirm). start_loop() enforces this
    itself so no future caller can silently bypass the confirm flow.
    """
