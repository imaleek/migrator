"""
Unit tests for the Utilities module.

Tests the logger, exceptions, and decorators in the utilities package.
"""
import pytest
import logging
from unittest.mock import patch

from utilities.logger import get_logger, set_global_level
from utilities.exceptions import (
    MigratorError,
    ConfigurationError,
    RegistryConnectionError,
    RegistryAuthenticationError,
    ImageMigrationError,
    ChartMigrationError,
    RetryExhaustedError,
)
from utilities.decorators import retry, log_operation


class TestLogger:
    """Tests for logger module."""
    
    def test_get_logger(self):
        """Test getting a logger."""
        logger = get_logger("test_module")
        assert logger.name == "test_module"
    
    def test_get_logger_with_level(self):
        """Test getting a logger with specific level."""
        logger = get_logger("test_level", level=logging.DEBUG)
        assert logger.level == logging.DEBUG
    
    def test_get_logger_idempotent(self):
        """Test getting same logger twice returns same instance."""
        logger1 = get_logger("same_module")
        logger2 = get_logger("same_module")
        assert logger1 is logger2
    
    def test_set_global_level(self):
        """Test setting global log level."""
        set_global_level(logging.WARNING)
        set_global_level(logging.INFO)
        set_global_level(logging.DEBUG)


class TestMigratorError:
    """Tests for base MigratorError."""
    
    def test_basic_error(self):
        """Test basic error creation."""
        error = MigratorError("Test error")
        assert str(error) == "Test error"
    
    def test_error_with_details(self):
        """Test error with details."""
        error = MigratorError("Error", "More details")
        assert "More details" in str(error)


class TestConfigurationError:
    """Tests for ConfigurationError."""
    
    def test_basic_error(self):
        """Test basic configuration error."""
        error = ConfigurationError("username", "cannot be empty")
        assert "username" in str(error)
        assert "cannot be empty" in str(error)
    
    def test_field_property(self):
        """Test field property."""
        error = ConfigurationError("password", "too short")
        assert error.field == "password"


class TestRegistryConnectionError:
    """Tests for RegistryConnectionError."""
    
    def test_basic_error(self):
        """Test basic connection error."""
        error = RegistryConnectionError("registry.io", "timeout")
        assert "registry.io" in str(error)
        assert error.registry == "registry.io"
    
    def test_with_details(self):
        """Test connection error with details."""
        error = RegistryConnectionError("registry.io", "timeout", "check network")
        assert "check network" in str(error)


class TestRegistryAuthenticationError:
    """Tests for RegistryAuthenticationError."""
    
    def test_default_message(self):
        """Test default authentication error message."""
        error = RegistryAuthenticationError("registry.io")
        assert "registry.io" in str(error)
        assert "Authentication failed" in str(error)
    
    def test_custom_message(self):
        """Test custom authentication error message."""
        error = RegistryAuthenticationError("registry.io", "invalid token")
        assert "invalid token" in str(error)


class TestImageMigrationError:
    """Tests for ImageMigrationError."""
    
    def test_basic_error(self):
        """Test basic image migration error."""
        error = ImageMigrationError("nginx:latest", "push failed")
        assert "nginx:latest" in str(error)
        assert error.image == "nginx:latest"
    
    def test_with_details(self):
        """Test image migration error with details."""
        error = ImageMigrationError("nginx:latest", "push failed", "network issue")
        assert "network issue" in str(error)


class TestChartMigrationError:
    """Tests for ChartMigrationError."""
    
    def test_basic_error(self):
        """Test basic chart migration error."""
        error = ChartMigrationError("mychart:1.0.0", "pull failed")
        assert "mychart:1.0.0" in str(error)
        assert error.chart == "mychart:1.0.0"


class TestRetryExhaustedError:
    """Tests for RetryExhaustedError."""
    
    def test_basic_error(self):
        """Test basic retry exhausted error."""
        inner = ValueError("initial error")
        error = RetryExhaustedError("fetch", 3, inner)
        assert "fetch" in str(error)
        assert "3" in str(error)
        assert error.operation == "fetch"
        assert error.max_attempts == 3


class TestRetryDecorator:
    """Tests for retry decorator."""
    
    def test_success_first_try(self):
        """Test retry with first success."""
        @retry(max_attempts=3)
        def always_succeeds():
            return "success"
        
        result = always_succeeds()
        assert result == "success"
    
    def test_success_after_failure(self):
        """Test retry with eventual success."""
        attempts = [0]
        
        @retry(max_attempts=3, delay=0.01)
        def fails_then_succeeds():
            attempts[0] += 1
            if attempts[0] < 2:
                raise ValueError("temporary failure")
            return "success"
        
        result = fails_then_succeeds()
        assert result == "success"
        assert attempts[0] == 2
    
    def test_all_attempts_fail(self):
        """Test retry when all attempts fail."""
        @retry(max_attempts=2, delay=0.01, exceptions=(ValueError,))
        def always_fails():
            raise ValueError("persistent failure")
        
        with pytest.raises(ValueError):
            always_fails()
    
    @pytest.mark.asyncio
    async def test_async_success(self):
        """Test retry with async function."""
        @retry(max_attempts=3, delay=0.01)
        async def async_success():
            return "success"
        
        result = await async_success()
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_async_all_fail(self):
        """Test async retry when all attempts fail."""
        attempts = [0]
        
        @retry(max_attempts=2, delay=0.01, exceptions=(ValueError,))
        async def fails_always():
            attempts[0] += 1
            raise ValueError("always fails")
        
        with pytest.raises(ValueError):
            await fails_always()
        
        assert attempts[0] == 2


class TestLogOperationDecorator:
    """Tests for log_operation decorator."""
    
    def test_success(self, caplog):
        """Test log_operation on success."""
        @log_operation("test_op")
        def successful_op():
            return "done"
        
        with caplog.at_level(logging.DEBUG):
            result = successful_op()
        
        assert result == "done"
    
    def test_failure(self):
        """Test log_operation on failure."""
        @log_operation("failing_op")
        def failing_op():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            failing_op()
    
    def test_default_name(self):
        """Test log_operation with default name."""
        @log_operation()
        def my_function():
            return True
        
        result = my_function()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_async_success(self):
        """Test async log_operation."""
        @log_operation("async_op")
        async def async_op():
            return "done"
        
        result = await async_op()
        assert result == "done"
    
    @pytest.mark.asyncio
    async def test_async_failure(self):
        """Test async log_operation with failure."""
        @log_operation("async_fail")
        async def async_fail():
            raise RuntimeError("async error")
        
        with pytest.raises(RuntimeError):
            await async_fail()
