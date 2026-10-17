"""
Tests for workers/executor/services.py - BuildValidator
"""
import tempfile
import shutil
from pathlib import Path
import pytest

from workers.executor.services import BuildValidator
from workers.executor.exceptions import BuildValidationError

PROJECT_ID = 'eba92dec-17a1-4a47-816e-77087ceb9531'
OTHER_PROJECT_ID = 'a7702e2d-69dd-4543-b3b6-a6df21d287c9'


class TestBuildValidator:
    """Test cases for BuildValidator class."""

    @pytest.fixture
    def temp_repo(self):
        """Create temporary repository with build structure."""
        temp_dir = tempfile.mkdtemp()
        repo_path = Path(temp_dir)

        # Create build folder structure
        build_path = repo_path / 'build'
        build_path.mkdir()
        (build_path / 'generated').mkdir()

        yield repo_path

        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def valid_build_yml(self):
        """Return valid build.yml content."""
        return f"""
version: "1.0"
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
    archetype_id: arch-001
    archetype_path: generated/data.csv
    import_path: data.csv
privacy:
  epsilon: 1.5
execution:
  timeout: 300
  environment: enclave
"""

    def test_validate_success(self, temp_repo, valid_build_yml):
        """Test successful validation of valid build folder."""
        # Write valid build.yml
        (temp_repo / 'build' / 'build.yml').write_text(valid_build_yml)

        validator = BuildValidator(str(temp_repo))

        config = validator.validate()

        assert config is not None
        assert config.version == '1.0'
        assert config.script_file == 'main.py'
        assert len(config.datasets) == 1
        assert config.datasets[0].dataset_id == PROJECT_ID
        assert config.epsilon == 1.5
        assert config.timeout == 300

    def test_validate_missing_build_folder(self, temp_repo):
        """Test validation fails when build folder is missing."""
        # Remove build folder
        shutil.rmtree(temp_repo / 'build')

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="[Bb]uild folder not found"):
            validator.validate()

    def test_validate_missing_build_yml(self, temp_repo):
        """Test validation fails when build.yml is missing."""
        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="build.yml not found"):
            validator.validate()

    def test_validate_missing_generated_folder(self, temp_repo, valid_build_yml):
        """Test validation fails when generated folder is missing."""
        (temp_repo / 'build' / 'build.yml').write_text(valid_build_yml)
        shutil.rmtree(temp_repo / 'build' / 'generated')

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="Generated folder not found"):
            validator.validate()

    def test_validate_invalid_yaml(self, temp_repo):
        """Test validation fails with invalid YAML."""
        (temp_repo / 'build' / 'build.yml').write_text("invalid: yaml: content: [")

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="Invalid YAML format"):
            validator.validate()

    def test_validate_yaml_not_dict(self, temp_repo):
        """Test validation fails when YAML is not a dictionary."""
        (temp_repo / 'build' / 'build.yml').write_text("- item1\n- item2")

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="must contain a YAML dictionary"):
            validator.validate()

    def test_validate_default_values(self, temp_repo):
        """Test that default values are used when not specified."""
        minimal_yml = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
    archetype_id: arch-001
"""
        (temp_repo / 'build' / 'build.yml').write_text(minimal_yml)

        validator = BuildValidator(str(temp_repo))

        config = validator.validate()

        assert config.version == '1.0'  # Default
        assert config.epsilon == 1.0  # Default
        assert config.timeout == 300  # Default
        assert config.environment == 'enclave'  # Default

    def test_validate_multiple_datasets_is_refused(self, temp_repo):
        """A job analyses exactly one project: several datasets are refused."""
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
    archetype_id: arch-001
  - dataset_id: {OTHER_PROJECT_ID}
    archetype_id: arch-002
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="exactly one dataset under 'datasets', but lists 2") as excinfo:
            validator.validate()
        assert 'submit a separate job for each project' in str(excinfo.value)

    @pytest.mark.parametrize('datasets', ['datasets: []', 'datasets:', 'datasets: {}', ''])
    def test_validate_no_dataset_is_refused(self, temp_repo, datasets):
        """A job with no dataset cannot be routed to a project's reviewers."""
        (temp_repo / 'build' / 'build.yml').write_text(f"analysis:\n  script_file: main.py\n{datasets}\n")

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="exactly one dataset under 'datasets', but lists none"):
            validator.validate()

    @pytest.mark.parametrize('dataset_id', [
        'ds-001',
        'eba92dec17a14a47816e77087ceb9531',
        '{eba92dec-17a1-4a47-816e-77087ceb9531}',
        'eba92dec-17a1-4a47-816e-77087ceb953',
        ' eba92dec-17a1-4a47-816e-77087ceb9531',
        '12345',
        'true',
    ])
    def test_validate_dataset_id_must_be_a_uuid(self, temp_repo, dataset_id):
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: '{dataset_id}'
    archetype_id: arch-001
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="'dataset_id' must be the platform project id, a UUID"):
            validator.validate()

    @pytest.mark.parametrize('dataset_id', ['12345', 'true', '[1, 2]'])
    def test_validate_dataset_id_that_is_not_text_is_refused(self, temp_repo, dataset_id):
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {dataset_id}
    archetype_id: arch-001
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="'dataset_id' must be the platform project id"):
            validator.validate()

    def test_validate_dataset_id_is_stored_lower_case(self, temp_repo):
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID.upper()}
    archetype_id: Arch-001
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        config = BuildValidator(str(temp_repo)).validate()

        assert config.datasets[0].dataset_id == PROJECT_ID
        assert config.datasets[0].archetype_id == 'Arch-001'

    @pytest.mark.parametrize('archetype_id, valid', [
        ("'" + 'a' * 255 + "'", True),
        ("'" + 'a' * 256 + "'", False),
        ('12345', False),
        ('[arch-001]', False),
    ])
    def test_validate_archetype_id_is_text_of_at_most_255_characters(self, temp_repo, archetype_id, valid):
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
    archetype_id: {archetype_id}
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        if valid:
            assert validator.validate().datasets[0].archetype_id == 'a' * 255
        else:
            with pytest.raises(BuildValidationError, match="'archetype_id' must be text of at most 255 characters"):
                validator.validate()

    def test_validate_dataset_missing_dataset_id(self, temp_repo):
        """Test validation fails when dataset missing dataset_id."""
        yml_content = """
analysis:
  script_file: main.py
datasets:
  - archetype_id: arch-001
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="missing 'dataset_id'"):
            validator.validate()

    def test_validate_dataset_missing_archetype_id(self, temp_repo):
        """Test validation fails when dataset missing archetype_id."""
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="missing 'archetype_id'"):
            validator.validate()

    def test_validate_dataset_not_dict(self, temp_repo):
        """Test validation fails when dataset is not a dictionary."""
        yml_content = """
analysis:
  script_file: main.py
datasets:
  - "just a string"
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        with pytest.raises(BuildValidationError, match="must be a dictionary"):
            validator.validate()

    def test_validate_dataset_optional_fields(self, temp_repo):
        """Test that optional dataset fields are parsed correctly."""
        yml_content = f"""
analysis:
  script_file: main.py
datasets:
  - dataset_id: {PROJECT_ID}
    archetype_id: arch-001
    import_path: data.csv
    function_name: load_data
    archetype_path: generated/data.csv
"""
        (temp_repo / 'build' / 'build.yml').write_text(yml_content)

        validator = BuildValidator(str(temp_repo))

        config = validator.validate()

        ds = config.datasets[0]
        assert ds.import_path == 'data.csv'
        assert ds.function_name == 'load_data'
        assert ds.archetype_path == 'generated/data.csv'
