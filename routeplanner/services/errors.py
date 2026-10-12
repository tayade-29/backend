class PlannerError(Exception):
    """An error whose message is safe to show to the user."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class UpstreamError(PlannerError):
    """A third-party API (Google, Open-Meteo) failed or returned garbage."""

    def __init__(self, message):
        super().__init__(message, status=502)
