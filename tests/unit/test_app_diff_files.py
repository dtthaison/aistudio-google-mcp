"""Pure file diffing: no network, no browser."""

from aistudio._app.diff import FileChange, diff_files, summarize


def test_unchanged_files_are_left_out():
    assert diff_files({"a.txt": b"same\n"}, {"a.txt": b"same\n"}) == []


def test_a_changed_file_gets_a_unified_diff_and_line_counts():
    old = {"src/App.tsx": b"one\ntwo\nthree\n"}
    new = {"src/App.tsx": b"one\n2\nthree\nfour\n"}
    [change] = diff_files(old, new)
    assert (change.path, change.status, change.binary) == ("src/App.tsx", "changed", False)
    assert (change.added, change.removed) == (2, 1)
    assert change.diff.splitlines() == [
        "--- a/src/App.tsx", "+++ b/src/App.tsx", "@@ -1,3 +1,4 @@",
        " one", "-two", "+2", " three", "+four"]


def test_added_and_removed_files_diff_against_dev_null():
    changes = diff_files({"gone.txt": b"x\ny\n"}, {"new.txt": b"a\n"})
    assert [(c.path, c.status, c.added, c.removed) for c in changes] == [
        ("gone.txt", "removed", 0, 2), ("new.txt", "added", 1, 0)]
    assert changes[0].diff.splitlines()[:2] == ["--- a/gone.txt", "+++ /dev/null"]
    assert changes[1].diff.splitlines()[:2] == ["--- /dev/null", "+++ b/new.txt"]


def test_results_are_sorted_by_path():
    paths = [c.path for c in diff_files({}, {"b": b"1", "a": b"1", "c/d": b"1"})]
    assert paths == ["a", "b", "c/d"]


def test_binary_files_are_counted_but_not_shown():
    changes = diff_files({"i.png": b"\x89PNG\x00\x01"}, {"i.png": b"\x89PNG\x00\x02",
                                                       "j.bin": b"\xff\xfe\x00"})
    assert [(c.path, c.status, c.binary, c.diff) for c in changes] == [
        ("i.png", "changed", True, ""), ("j.bin", "added", True, "")]
    assert all(c.added == c.removed == 0 for c in changes)


def test_non_utf8_without_nul_is_binary_too():
    [change] = diff_files({}, {"latin.txt": b"caf\xe9\n"})
    assert change.binary and change.diff == ""


def test_a_missing_final_newline_is_marked():
    [change] = diff_files({"a": b"x\n"}, {"a": b"x"})
    assert change.diff.splitlines()[-3:] == [
        "-x", "+x", "\\ No newline at end of file"]


def test_an_empty_file_added_has_no_hunk():
    [change] = diff_files({}, {"empty.txt": b""})
    assert (change.status, change.added, change.removed, change.diff) == ("added", 0, 0, "")


def test_summary_counts_files_and_lines():
    changes = diff_files(
        {"a": b"1\n", "b": b"1\n", "same": b"s", "gone": b"1\n2\n"},
        {"a": b"1\n2\n", "b": b"x\n", "same": b"s", "new": b"1\n2\n3\n"})
    assert summarize(changes) == {
        "added": 1, "removed": 1, "changed": 2, "lines_added": 5, "lines_removed": 3}
    assert summarize([]) == {"added": 0, "removed": 0, "changed": 0,
                             "lines_added": 0, "lines_removed": 0}


def test_to_dict_is_plain_data():
    [change] = diff_files({}, {"a": b"x\n"})
    assert isinstance(change, FileChange)
    assert set(change.to_dict()) == {"path", "status", "binary", "added", "removed", "diff"}
