import pytest
from pathlib import Path
from agent.tools.code.tools import read_file, write_file, list_dir, delete_file, search_code, apply_patch
from agent.core.errors import NexusError

def test_write_and_read_file(tmp_path):
    path = str(tmp_path / "hello.py")
    write_file(file_path=path, content="print('hello')")
    result = read_file(file_path=path)
    assert result["content"] == "print('hello')"
    assert result["lines"] == 1

def test_write_file_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "file.py")
    write_file(file_path=path, content="x = 1")
    assert Path(path).exists()

def test_write_file_bytes_written(tmp_path):
    content = "café"  # non-ASCII to verify byte count, not char count
    path = str(tmp_path / "unicode.py")
    result = write_file(file_path=path, content=content)
    assert result["bytes_written"] == len(content.encode("utf-8"))
    assert result["bytes_written"] > len(content)  # UTF-8 encodes é as 2 bytes

def test_list_dir(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    result = list_dir(directory=str(tmp_path))
    names = [e["name"] for e in result["entries"]]
    assert "a.py" in names and "b.py" in names
    assert names == sorted(names)  # entries must be sorted

def test_delete_file(tmp_path):
    f = tmp_path / "del.py"
    f.write_text("x")
    delete_file(file_path=str(f))
    assert not f.exists()

def test_delete_file_missing_raises(tmp_path):
    with pytest.raises(NexusError, match="file not found"):
        delete_file(file_path=str(tmp_path / "nonexistent.py"))

def test_search_code(tmp_path):
    (tmp_path / "main.py").write_text("def hello(): pass\ndef world(): pass")
    result = search_code(pattern="def hello", directory=str(tmp_path))
    assert len(result["matches"]) >= 1
    assert "main.py" in result["matches"][0]["file"]

def test_apply_patch(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\ny = 2\n")
    apply_patch(file_path=str(f), old_string="x = 1", new_string="x = 99")
    assert "x = 99" in f.read_text()

def test_apply_patch_missing_string_raises(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    with pytest.raises(NexusError, match="old_string not found"):
        apply_patch(file_path=str(f), old_string="z = 99", new_string="z = 0")
