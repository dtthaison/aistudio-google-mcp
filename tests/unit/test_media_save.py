import pytest

from aistudio._media.save import check_folder, free_name, write_file
from aistudio.exceptions import ValidationError


def test_write_file_never_clobbers_and_says_where_it_landed(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"old")
    landed = write_file(tmp_path / "a.mp4", b"new", overwrite=False)
    assert landed == tmp_path / "a-2.mp4"
    assert (tmp_path / "a.mp4").read_bytes() == b"old" and landed.read_bytes() == b"new"


def test_free_name_and_check_folder(tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"1")
    assert free_name(tmp_path / "x.jpg") == tmp_path / "x-2.jpg"
    with pytest.raises(ValidationError):
        check_folder(tmp_path / "missing")
