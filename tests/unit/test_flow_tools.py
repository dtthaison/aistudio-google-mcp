from aistudio._flow.rows import decode_tool_files, decode_tools


def row(tool_id, name, *, description="d", version="v1", kind=1, author=None, uses=None,
        tags=None):
    cells = [None] * 27
    cells[0], cells[2], cells[3], cells[5] = tool_id, name, description, version
    cells[7], cells[11], cells[12] = [1793419530, 0], f"applets/{tool_id}", kind
    cells[17], cells[20], cells[22] = author, uses, tags
    return cells


def test_tools_keep_who_made_them_and_their_version():
    got = decode_tools([[row("t1", "Mine"),
                         row("t2", "Whisk", kind=2, author="Google", uses=9254),
                         row("community-t3", "Forge", kind=3, author="A", uses=857,
                             tags=["Spotlight"])]])
    assert [(t.id, t.name, t.kind, t.version_id, t.author, t.uses, t.tags) for t in got] == [
        ("t1", "Mine", "mine", "v1", None, None, []),
        ("t2", "Whisk", "template", "v1", "Google", 9254, []),
        ("community-t3", "Forge", "community", "v1", "A", 857, ["Spotlight"])]


def test_a_tool_of_an_unknown_kind_is_kept_and_says_so():
    (tool,) = decode_tools([[row("t1", "New", kind=9)]])
    assert tool.kind == "kind:9"


def test_rows_without_an_id_or_name_are_skipped():
    assert decode_tools([[row(None, "x"), row("t1", None), "junk"]]) == []


def test_no_list_means_no_tools():
    assert decode_tools(None) == []


def test_tool_files_are_path_type_and_text():
    payload = [["t1", "v1"], "<changes/>", None,
               [["src/App.tsx", "text/typescript-jsx", "export default 1"],
                ["index.html", "text/html", "<html>"]], []]
    got = decode_tool_files(payload)
    assert [(f.path, f.mime_type, f.text) for f in got] == [
        ("src/App.tsx", "text/typescript-jsx", "export default 1"),
        ("index.html", "text/html", "<html>")]


def test_a_file_row_without_a_path_or_text_is_skipped():
    payload = [None, None, None, [[None, "text/css", "x"], ["a.css", "text/css", None]]]
    assert decode_tool_files(payload) == []


def test_a_tool_with_no_saved_version_has_no_files():
    assert decode_tool_files(None) == []
