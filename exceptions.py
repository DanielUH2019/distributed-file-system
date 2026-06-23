"""Custom exceptions for the DFS client."""


class ClientError(Exception):
    """Base exception for client failures."""


class ConfigurationError(ClientError):
    """Raised when environment configuration is invalid."""


class InvalidFilenameError(ClientError):
    """Raised when a filename fails sanitization."""


class InvalidFileTypeError(ClientError):
    """Raised when a file is not an allowed text type."""


class UploadError(ClientError):
    """Raised when chunk upload or registration fails."""


class DownloadError(ClientError):
    """Raised when a file cannot be read from storage."""


class DfsFileNotFoundError(ClientError):
    """Raised when a file is not found in the naming server."""
