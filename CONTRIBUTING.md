# Contributing to Migrator

Thank you for your interest in contributing to Migrator! This document provides guidelines and instructions for contributing.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment for everyone.

## How to Contribute

### Reporting Bugs

1. **Search existing issues** to avoid duplicates
2. **Use the bug report template** when creating a new issue
3. Include:
   - Clear description of the bug
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python version, registry types)
   - Relevant logs (use `--debug` flag)

### Suggesting Features

1. **Search existing issues** for similar suggestions
2. **Create a feature request issue** with:
   - Clear description of the feature
   - Use case and motivation
   - Proposed implementation (optional)

### Pull Requests

1. **Fork** the repository
2. **Clone** your fork locally
3. **Create a branch** for your changes:

   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/bug-description
   ```

4. **Make your changes**
5. **Add tests** for new functionality
6. **Run the test suite**:

   ```bash
   pytest --cov=src
   ```

7. **Run linting**:

   ```bash
   ruff check src/
   ruff format src/
   mypy src/
   ```

8. **Commit** with a clear message:

   ```bash
   git commit -m "feat: add support for XYZ registry"
   # or
   git commit -m "fix: resolve auth issue with XYZ"
   ```

9. **Push** to your fork:

   ```bash
   git push origin feature/your-feature-name
   ```

10. **Open a Pull Request**

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- (Optional) Helm 3.8+ for chart migration testing

### Installation

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/migrator.git
cd migrator

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
.\venv\Scripts\activate   # Windows

# Install with dev dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=src --cov-report=html

# Specific file
pytest tests/test_registry_client.py -v

# Matching pattern
pytest -k "helm" -v
```

### Code Style

We use `ruff` for linting and formatting:

```bash
# Check for issues
ruff check src/

# Auto-fix issues
ruff check src/ --fix

# Format code
ruff format src/
```

Type hints are enforced with `mypy`:

```bash
mypy src/
```

## Project Structure

```
migrator/
├── src/
│   ├── main.py              # CLI entry point
│   ├── config.py            # Configuration models
│   ├── console/             # Rich UI components
│   ├── migrators/           # Migration implementations
│   ├── services/            # Core services
│   └── utilities/           # Helpers and exceptions
├── tests/                   # Test suite
├── pyproject.toml           # Project config
└── README.md
```

## Adding a New Migrator

1. Create a new file in `src/migrators/`:

   ```python
   # src/migrators/my_migrator.py
   from migrators import BaseMigrator
   
   class MyMigrator(BaseMigrator):
       @property
       def name(self) -> str:
           return "My Migrator"
       
       @property
       def description(self) -> str:
           return "Migrates XYZ"
       
       async def validate_connection(self) -> bool:
           ...
       
       async def discover(self) -> list:
           ...
       
       async def migrate_item(self, item) -> MigrationResult:
           ...
       
       async def run(self) -> MigrationSummary:
           ...
   ```

2. Add CLI command in `src/main.py`
3. Add tests in `tests/test_my_migrator.py`
4. Update README with new usage examples

## Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `test:` - Test additions/changes
- `refactor:` - Code refactoring
- `perf:` - Performance improvements
- `chore:` - Maintenance tasks

## Questions?

Feel free to open an issue for any questions about contributing.

Thank you for contributing! 🎉
