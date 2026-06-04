import pytest
from pathlib import Path
from agent.tools.code.tools import read_file, write_file, list_dir, delete_file, search_code, apply_patch

def test_write_and_read_file(tmp_path):
    path = str(tmp_path / "hello.py")
    write_file(file_path=path, content="print('hello')")
    result = read_file(file_path=path)
    assert result["content"] == "print('hello')"

def test_list_dir(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    result = list_dir(directory=str(tmp_path))
    names = [e["name"] for e in result["entries"]]
    assert "a.py" in names and "b.py" in names

def test_delete_file(tmp_path):
    f = tmp_path / "del.py"
    f.write_text("x")
    delete_file(file_path=str(f))
    assert not f.exists()

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
