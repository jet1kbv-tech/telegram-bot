import os
from pathlib import Path

from bot import config, storage


def test_data_file_has_one_environment_aware_configuration_source() -> None:
    assert config.DATA_FILE == Path(os.getenv("DATA_FILE", "data.json"))
    assert storage.DATA_FILE is config.DATA_FILE
