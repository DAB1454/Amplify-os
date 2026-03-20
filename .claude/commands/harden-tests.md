# Harden Tests

## Description
Analyzes a target module and generates comprehensive test coverage, identifying untested code paths and producing pytest test files with fixtures.

## Arguments
- `module_path` (required): Path to the module to test (e.g., "packages/core/domain/campaign.py" or "packages/adapters/instagram/")
- `coverage_target` (optional): Target coverage percentage (default: 90)

## Steps

1. **Read and analyze the target module**
   - Parse the module to identify all public classes, methods, and functions
   - Map out code branches: conditionals, exception handlers, edge cases
   - Identify external dependencies that will need mocking
   - Check for existing tests and their coverage

2. **Identify untested code paths**
   - Run existing tests with coverage if they exist: `pytest --cov={module} --cov-report=term-missing`
   - List all uncovered lines and branches
   - Categorize gaps: happy path missing, error handling untested, edge cases uncovered, integration paths

3. **Generate pytest test file**
   - Create test file at the appropriate test location (mirroring source structure)
   - Generate fixtures for:
     - Domain objects with valid default data
     - Mock API responses for external services
     - Database session mocks if applicable
     - File system fixtures for asset handling
   - Write test cases covering:
     - All public method happy paths
     - Invalid input handling and validation errors
     - Boundary conditions (empty lists, max sizes, null values)
     - Exception propagation
     - State transitions
   - Use parametrize for testing multiple input variations
   - Include docstrings explaining what each test validates

4. **Run tests and report coverage**
   - Execute the new tests: `pytest {test_file} -v`
   - Run coverage analysis: `pytest --cov={module} --cov-report=term-missing {test_file}`
   - Report:
     - Total coverage percentage vs. target
     - Remaining uncovered lines (if any)
     - Test execution time
     - Any failing tests that indicate bugs in the source module
   - If coverage target is not met, generate additional tests for remaining gaps
