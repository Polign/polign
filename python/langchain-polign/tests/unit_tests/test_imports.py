from langchain_polign import PolignVectorStore, __version__


def test_exports() -> None:
    assert PolignVectorStore.__name__ == "PolignVectorStore"
    assert __version__
