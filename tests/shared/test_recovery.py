import tempfile
from pathlib import Path

from shared import recovery


def test_flag_lives_beside_the_database_and_must_be_a_file():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "shared_backend.db"
        flag = recovery.recovery_flag_path(db)
        assert flag == Path(tmp) / recovery.RECOVERY_FILENAME
        assert not recovery.is_armed(flag)
        flag.mkdir()  # a folder with that name is not proof
        assert not recovery.is_armed(flag)
        flag.rmdir()
        flag.write_text("")
        assert recovery.is_armed(flag)
        recovery.disarm(flag)
        assert not recovery.is_armed(flag)
        recovery.disarm(flag)  # already gone: no error
