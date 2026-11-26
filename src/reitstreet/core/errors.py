"""Exception types. Each one tells the job runner how to react."""


class ReitStreetError(Exception):
    """Base class."""


class ConfigError(ReitStreetError):
    """Bad or missing configuration. Fatal: the run stops before doing anything."""


class FatalSourceError(ReitStreetError):
    """The source refuses us (e.g. SEC 403). Stop the whole run; retrying makes it worse."""


class TransientError(ReitStreetError):
    """Network blip, 5xx, timeout after retries. The item fails; the next run retries it."""


class ParseError(ReitStreetError):
    """Content was fetched but could not be parsed. Goes to the dead-letter queue."""


class LockHeldError(ReitStreetError):
    """Another run of the same job holds the lock."""
